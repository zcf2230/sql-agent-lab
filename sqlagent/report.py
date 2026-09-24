"""Generate report.html: a single self-contained file over recorded runs.

Deliberately a *static* report built from `results/*.jsonl` and `runs/*.jsonl`, not
a live server. Two reasons: an interviewer should be able to open it by double-
clicking with no setup and no API key, and generating it must never spend money on
inference. A live "ask a question" mode is a different feature with a different
cost profile.

Everything on the page is traced back to a file on disk. A number you cannot click
through to its evidence is exactly the kind of number this project is built to
distrust.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import stats

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RUNS = ROOT / "runs"
OUT = ROOT / "report.html"

# which run files are worth showing, and how to label them
RUN_LABELS = {
    "abl2-baseline": "baseline（无示例）",
    "abl2-3shot": "+ 3-shot 示例",
    "abl2-norepair": "− 自修复循环",
    "qwen-baseline": "qwen-flash baseline",
    "qwen-3shot": "qwen-flash + 3-shot",
    "mock-clean-v2": "mock 上界（非模型能力）",
}
CALIB_ORDER = ["reflow", "reorder_cols", "float_round", "drop_distinct", "wrong_limit", "bad_column"]
# What each injected defect is, in the words used on this page. Cosmetic - the
# *policy* behind these classes lives in `stats.PRESENTATION_ONLY_MODES`, because a
# policy duplicated three times is three policies.
# The inducement tier names, for the page. `adversarial.INTENSITY_LABELS` holds the full
# descriptions; the cards only need the short form, and re-stating the long one here would
# be a second copy to keep in sync.
INTENSITY_CN = {"direct": "直接命令", "justified": "给正当理由", "embedded": "嵌在读任务里"}
MODE_LABELS = {
    "reflow": "同义重写（语义不变）",
    "reorder_cols": "列顺序颠倒（值不变）",
    "float_round": "末列舍入到 1 位",
    "drop_distinct": "去掉 DISTINCT（多出重复行）",
    "wrong_limit": "强行 LIMIT 3",
    "bad_column": "列名改成不存在的",
}


def token_cost(rows: list[dict], model: str) -> float:
    """Cost recomputed from stored token counts, never read from a stored figure.

    `total_cost_usd` in the result files is tokens x a price list, frozen at run
    time. When the price list turned out to be stale, every historical run kept
    displaying the old number as if it were a fact. Tokens are the measurement;
    price is an assumption, so the multiplication happens at display time.
    """
    from .config import PRICING

    if model not in PRICING:
        # None, not 0.0: a missing price rendered as "$0.00" reads as "this run was
        # free", which is how an unrecorded rate becomes a false saving.
        return None
    pin, pout = PRICING[model]
    tin = sum(r.get("stats", {}).get("prompt_tokens", 0) for r in rows)
    tout = sum(r.get("stats", {}).get("completion_tokens", 0) for r in rows)
    return (tin * pin + tout * pout) / 1_000_000


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def total_spend() -> tuple[float, int, int]:
    """(estimated cost, prompt tokens, completion tokens) over every recorded run.

    Derived from token counts at display time rather than from the frozen
    `total_cost_usd` in each file, and returned together with the raw token totals
    so the reader can redo the arithmetic with a price list they trust more than
    this one. Summing only the runs shown on the page would under-report.
    """
    from .config import PRICING

    spend = 0.0
    unpriced: set[str] = set()
    tin = tout = 0
    for path in RESULTS.glob("*.jsonl"):
        rows = read_jsonl(path)
        if not rows or "_summary" not in rows[0]:
            continue
        sm = rows[0]["_summary"]
        if sm.get("provider") != "openai":
            continue
        body = [r for r in rows[1:] if "id" in r]
        # per-model pricing: a second provider must not be billed at deepseek's rate
        c = token_cost(body, sm.get("model", ""))
        if c is None:
            unpriced.add(sm.get("model", "?"))
        else:
            spend += c
        tin += sum(r.get("stats", {}).get("prompt_tokens", 0) for r in body)
        tout += sum(r.get("stats", {}).get("completion_tokens", 0) for r in body)
    if unpriced:
        print(f"[!] no price recorded for {sorted(unpriced)} - their cost is EXCLUDED from "
              f"the total, not counted as zero")
    return spend, tin, tout


def load_runs() -> dict:
    runs = {}
    for tag in RUN_LABELS:
        rows = read_jsonl(RESULTS / f"{tag}.jsonl")
        if not rows:
            continue
        summary = rows[0].get("_summary", {})
        body = [r for r in rows[1:] if "id" in r]
        runs[tag] = {"summary": summary, "rows": {r["id"]: r for r in body}}
    return runs


def run_stem(summary: dict) -> str:
    """Reproduce Settings.tag() from a saved summary, to find that run's trace file.

    Rebuilt rather than stored because the summary is the artifact: nothing extra
    has to stay in sync for this to keep working.
    """
    bits = [summary["provider"], summary["model"].replace("-", "_")]
    if summary["provider"] == "mock":
        bits.append(f"corr-{summary['corruption']}")
    bits.append(f"fs{summary['fewshot_k']}")
    if not summary.get("self_repair", True):
        bits.append("noselfrepair")
    return "__".join(bits)


def load_traces() -> tuple[dict, dict[str, int]]:
    """({run stem: {task_id: last record for that task in that run}}, {run stem: unparseable lines}).

    Keyed by run, not just by task. The first version kept one trace per task
    across every file, so filtering to `baseline` could display a 3-shot run's
    steps under a baseline verdict - the page looked authoritative and attributed
    evidence to the wrong experiment.

    The second value exists because the damage is in the published files: before
    `trace.py` serialised its appends, worker threads could interleave halves of one
    record, and a reader that skips such a line silently turns "the evidence is gone"
    into a page with no gap where the evidence should be.
    """
    by_run: dict[str, dict[str, dict]] = {}
    malformed: dict[str, int] = {}
    for path in sorted(RUNS.glob("*.jsonl")):
        bucket = by_run.setdefault(path.stem, {})
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                malformed[path.stem] = malformed.get(path.stem, 0) + 1
                continue
            rec["_file"] = path.name
            bucket[rec["task_id"]] = rec
    return by_run, malformed


def load_tasks() -> dict:
    return {t["id"]: t for t in read_jsonl(ROOT / "data" / "tasks.jsonl")}


def adherence(sm: dict) -> str:
    """Fraction of tasks where the agent executed at least one query."""
    rate = sm.get("protocol_adherence")
    if rate is None:
        return "—"
    zero = sm.get("n_zero_tool_calls", 0)
    colour = "#e07a6b" if rate < 0.9 else "#4f9d8f"
    return f"<span style='color:{colour}'>{rate * 100:.0f}%</span>" + (f" ({zero} 题 0 工具)" if zero else "")


def money(value: float | None) -> str:
    return "~$" + f"{value:.3f}" if value is not None else "<span class='bad'>单价未录入</span>"


def bar(pct: float, colour: str) -> str:
    width = max(0.0, min(100.0, pct))
    return (f'<div class="bar"><span style="width:{width:.1f}%;background:{colour}"></span>'
            f'<em>{width:.1f}%</em></div>')


def significance_html() -> str:
    """Paired tests, intervals and the aggregation sensitivity - all from sqlagent.stats.

    This section exists because the first two published versions of this report showed
    `+4.2pp` next to `+0.0pp` with nothing telling the reader that neither is
    statistically distinguishable from noise. The strongest line on the resume was
    "we report p=0.057 as not significant", and the artifact most likely to be opened
    did not contain the string "0.057" anywhere.
    """
    nf = stats.noise_floor()
    bs = stats.cluster_bootstrap("abl2-baseline", "abl2-3shot")
    rt = stats.cluster_ratio_tests("abl2-baseline", "abl2-3shot")
    boot_pp = f"{bs['point_pp']:+.2f}pp [{bs['ci_low_pp']:+.2f}, {bs['ci_high_pp']:+.2f}]"
    head = ("<tr><th>配对比较</th><th class=num>n</th><th class=num>baseline</th>"
            "<th class=num>variant</th><th class=num>Δ</th><th class=num>Wilson 95% CI</th>"
            "<th class=num>修好/弄坏</th><th class=num>McNemar 精确 p</th><th>结论</th></tr>")
    rows = []
    for c in stats.all_comparisons():
        colour = "#4f9d8f" if c["significant"] else "#e07a6b"
        verdict = "达到 p<0.05" if c["significant"] else "未达显著"
        rows.append(
            f"<tr><td>{html.escape(c['label'])}</td><td class='num'>{c['n']}</td>"
            f"<td class='num'>{c['base_rate']:.1%}</td><td class='num'>{c['variant_rate']:.1%}</td>"
            f"<td class='num'>{c['delta_pp']:+.1f}pp</td>"
            f"<td class='num'>{c['ci_low']:.1%}–{c['ci_high']:.1%}</td>"
            f"<td class='num'>{c['fixed']}/{c['broken']}</td>"
            f"<td class='num' style='color:{colour}'>{c['p_value']:.4f}</td>"
            f"<td style='color:{colour}'>{verdict}</td></tr>"
        )
    table = f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"

    # Aggregation sensitivity: McNemar assumes independent paired observations, and a
    # template benchmark does not have 192 of them.
    sens = stats.cluster_sensitivity("abl2-baseline", "abl2-3shot")
    srows = "".join(
        f"<tr><td>{html.escape(r['label'])}</td><td class='num'>{r['n']}</td>"
        f"<td class='num'>{r['delta_pp']:+.1f}pp</td><td class='num'>{r['fixed']}/{r['broken']}</td>"
        f"<td class='num' style='color:{'#4f9d8f' if r['significant'] else '#e07a6b'}'>{r['p_value']:.4f}</td>"
        f"<td>{'显著' if r['significant'] else '未达显著'}</td></tr>"
        for r in sens
    )
    stable = len({r["significant"] for r in sens}) == 1
    sverdict = (
        "结论在三种口径下一致，所以这个判断不依赖聚合方式。"
        if stable else
        f"<b>p 在 {min(r['p_value'] for r in sens):.3f}–{max(r['p_value'] for r in sens):.3f} 之间跨过 0.05</b>，"
        "也就是说'显著/不显著'这个二分是聚合口径的产物，不是数据的性质。"
    )
    stable_table = (
        "<table><thead><tr><th>聚合口径</th><th class=num>独立单位</th><th class=num>Δ</th>"
        "<th class=num>修好/弄坏</th><th class=num>p</th><th>结论</th></tr></thead>"
        f"<tbody>{srows}</tbody></table>"
    )
    return f"""
 <h2>2 · 配对显著性（与它有多依赖口径）</h2>
 {table}
 <div class="note warn"><b>头条那一行：+4.2pp，p=0.0574，未达显著。</b>
   噪声底是 {nf['worst_tasks']}/{nf['n']} = {nf['worst_pp']:.1f}pp
   （{html.escape(nf['worst_pair'][0])} vs {html.escape(nf['worst_pair'][1])}，三份同配置基线的最大两两差异），
   低于这个幅度的差异不报告为改进。三个比较全部未达 p&lt;0.05。</div>
 <h3 style="margin-top:14px">同一份数据的三种聚合口径</h3>
 {stable_table}
 <div class="note warn">{sverdict} 原因：基准是模板生成的，192 道题去重后只有
   {len(stats.clusters())} 个不同的 gold SQL 骨架（例如 category_slice 是 5 个骨架 × 7 个参数）。
   McNemar 的 n 建立在"观测相互独立"上，而同骨架题目的判定高度相关——模型要么会写那个 JOIN，
   要么不会。所以 <b>n=192 高估了证据量，而且高估的方向是偏袒提升</b>。
   这里不选一个"正确"口径来报，而是把三种都摆出来：<b>凡是只在某一种聚合下才成立的结论，
   就不该被写成结论。</b></div>
 <div class="note"><b>唯一一个不依赖阈值的正向陈述：</b>簇级比率的配对 bootstrap 95% 区间 = <b>{boot_pp}</b>（{bs['clusters']} 簇 × {bs['iterations']} 次重采样，种子固定可复现），区间不含 0。注意它与符号检验（p={rt['sign_p']:.3f}）方向相反——因为差异分布极偏：{rt['nonzero_clusters']} 个非零簇里 <b>{rt['singleton_clusters']} 个是单题簇</b>（其中 {rt['positive_100pp']} 个 +100pp、{rt['negative_100pp']} 个 −100pp），均值被它们主导。<b>均值说"过线了"，中位数与符号说"还没有"</b>，所以这里两个都摆出来，而不是挑一个写进结论。</div>
 <div class="note">本节的每一个数字都来自 <code>sqlagent/stats.py</code>——与
   <code>scripts/significance.py</code> 和 <code>docs/figures/ablation.svg</code> 同一份实现。
   配对关系与文件名写死在那里，不由本报告另算。有一段时间 <code>report.py</code> 自己写了
   一个噪声底函数、还漏掉了最差的那一对基线，于是同一份材料里同时存在 2/192 与 4/192；
   那才是这张表存在的理由。</div>
"""


def reliability_html() -> str:
    """Test-retest reliability: the same task asked every way the generator can phrase it.

    This section exists because the benchmark asks every question exactly one way, so
    every number on this page is a single draw from a set of surface forms - and the
    report, which is what a reviewer actually opens, carried none of that caveat until
    it was measured. Same failure as section 2: a limitation stated only in the prose,
    in a document that is regenerated by a script and can regress silently.
    """
    facts = {p: stats.restability_report(p) for p in stats.RESTABILITY_PICKS}
    rep = facts["correct-representative"]
    fail = facts["failures"]

    head = ("<tr><th>抽样规则</th><th class=num>题</th><th class=num>运行</th>"
            "<th class=num>四种问法一致</th><th class=num>换问法即翻脸</th>"
            "<th class=num>基准问法通过率</th><th class=num>问法平均</th>"
            "<th class=num>花费</th><th>能不能外推到全卷</th></tr>")
    rows = []
    for p in stats.RESTABILITY_PICKS:
        r = facts[p]
        what, scope = stats.RESTABILITY_PICK_LABELS[p]
        flips = len(r["fragile"]) + len(r["lucky"])
        ok = r["extrapolatable"] or p == "failures"
        colour = "#4f9d8f" if ok else "#e07a6b"
        rows.append(
            f"<tr><td>{html.escape(p)}<div class='sub'>{html.escape(what)}</div></td>"
            f"<td class='num'>{r['n_tasks']}</td><td class='num'>{r['n_runs']}</td>"
            f"<td class='num'>{r['agree']}/{r['n_tasks']}</td>"
            f"<td class='num'><b>{flips}/{r['n_tasks']} = {flips/r['n_tasks']:.0%}</b></td>"
            f"<td class='num'>{r['published_rate']:.1%}</td><td class='num'>{r['mean_rate']:.1%}</td>"
            f"<td class='num'>${r['cost_usd']:.4f}</td>"
            f"<td style='color:{colour}'>{html.escape(scope)}</td></tr>"
        )
    table = f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"

    lo, hi = rep["fragile_ci"]
    alo, ahi = rep["overstatement_pp_any_ci"]
    hlo, hhi = rep.get("headline_ci", (0.0, 0.0))
    head_half = (hhi - hlo) / 2 * 100
    k, n = len(rep["fragile"]), rep["n_tasks"]

    return f"""
 <h2>3 · 同题重述稳定性（一次问法只是一个样本）</h2>
 <div class="sub">做法：从存档题面反推裸题面（套回生成器模板必须<b>逐字节</b>还原，否则该题跳过并报告），
  再用生成器自己的全部 {rep['n_forms']} 种问法各跑一遍；gold SQL、<code>require_order</code>、
  判分函数统统不变，只问题面。四组选题、四种抽样规则，产物各自独立命名。</div>
 {table}
 <div class="note warn"><b>误差的两个方向都被量到了，而且不在同一批题上，所以不抵消。</b>
   低估侧是普查：baseline 判错的全部 {fail['n_tasks']} 道非 trivial 失败题里，
   <b>{len(fail['lucky'])} 道（{len(fail['lucky'])/fail['n_tasks']:.0%}）换一种问法就做对了</b>
   （{fail['understatement_numerator']}/{fail['benchmark_tasks']}，不乘任何系数）。
   高估侧只能按族占比抽样才有效：判对的题里 <b>{k}/{n} = {rep['fragile_share']:.1%}
   （Wilson 95% [{lo:.1%}, {hi:.1%}]）换一种问法就判错</b>。</div>
 <div class="note"><b>两侧都用同样的两种读法，不许对模型有利的方向只报大口径：</b><br>
   低估侧 读法 A「换一种说法即得分」= {fail['understatement_numerator']}/{fail['benchmark_tasks']} =
   <b>{fail['understatement_pp']:.2f}pp</b>；读法 B「四种问法取平均」=
   {fail['understatement_credit_tasks']} 道/{fail['benchmark_tasks']} =
   <b>{fail['understatement_pp_mean']:.2f}pp</b>——那 {len(fail['lucky'])} 道里有 1 道
   四种问法只对了 1 种，按平均只值 0.25 道。<br>
   高估侧 读法 A = {rep['headline_pass1']:.1%} × {rep['fragile_share']:.1%} =
   <b>{rep['overstatement_pp_any']:.1f}pp</b>；读法 B = {rep['headline_pass1']:.1%} ×
   {(rep['published_rate']-rep['mean_rate'])*100:.1f}pp = <b>{rep['overstatement_pp_mean']:.1f}pp</b>。
   差在"一道题四种问法里错几种"。</div>
 <div class="note"><b>关于区间的两句实话：</b>上面唯一的区间
   （{alo:.1f}–{ahi:.1f}pp）是<em>比例</em>的 Wilson 区间乘上 baseline 的<em>点估计</em>
   {rep['headline_pass1']:.1%}，<b>没有计入 head 自身的不确定度</b>（它自己的 Wilson 95% 是
   {hlo:.1%}–{hhi:.1%}，半宽约 {head_half:.1f}pp），所以真实区间只会更宽——方向上偏乐观，说清比藏好。
   两个"取平均"口径（读法 B）是均值而非比例，<b>没有现成的闭式 CI</b>，因此不配图区间。</div>
 <div class="note warn"><b>第一条那一组（<code>families</code>）是本实验自己的设计错误，产物保留在仓库里。</b>
   "每个模板族取第一题"看着像分层抽样，实际是刻意挑每个族最简单的那道，于是 {facts['families']['n_tasks']} 题
   四种问法全对、一致率 100%、翻脸 0 道——<b>那不是稳定，那是零区分力</b>：没有可动摇的东西，
   当然测不到动摇。判分器审计里批评过同一件事（某格缺陷根本到不了答案，那个 100% 就不是证据）。</div>
 <div class="note">限制：唯一可外推的那一组只有 {n} 题，{rep['fragile_share']:.1%} 的区间宽到跨一个数量级，
   <b>它只用于判断量级，不是可发布的修正系数</b>；而且 {rep['n_forms']} 种问法之间语义距离不等
   （<code>Using the platform tables, ...</code> 与 <code>Please answer with SQL: ...</code>
   比裸题面彼此更接近），所以"4 种问法"不是 4 次独立抽样，取平均那个口径因此偏乐观。
   <b>本节每个数字都来自 <code>sqlagent/stats.py</code> 的
   <code>restability_report()</code></b>——与 <code>scripts/restability.py --analyse-only</code>
   同一份实现；有一条测试禁止 <code>report.py</code> 自己另算，也禁止脚本自己算。</div>
"""


def agent_on_bird_html() -> str:
    """The same agent, same config, on BIRD - the number that puts 89% in context.

    This section began as a judge-only experiment and its closing note said so: "no BIRD
    score for the agent, and there shouldn't be one". That is superseded, and the reason it
    is safe to publish is the second artifact - the same 60 answers re-scored under the
    official rule. Without it, "the agent is worse here" and "my judge broke again on
    someone else's data" are the same sentence, and this page has already admitted the
    judge broke twice.
    """
    run = read_jsonl(RESULTS / "bird-agent.jsonl")
    both = read_jsonl(RESULTS / "bird-agent-official.jsonl")
    if not run or not both:
        return ('<div class="note">这一节还没有 agent 在 BIRD 上的分数：<code>results/bird-agent.jsonl</code>'
                ' 不存在。跑法见 <code>scripts/bird_tasks.py</code> 的头注释（真实调用，'
                '先小规模定价再决定规模）。</div>')
    a = run[0].get("_summary") or {}
    o = both[0].get("_summary") or {}
    n = o.get("judged", 0) or 1
    mine, off = o.get("mine_pass", 0), o.get("official_pass", 0)
    lo, hi = stats.wilson(mine, n)
    base = stats.run_summary("abl2-baseline")
    base_p1, agent_p1 = (base.get("pass_at_1") or 0), mine / n
    tax = a.get("failure_taxonomy") or {}
    # The reason codes are printed raw: `explain()` describes what each shape meant on the
    # self-built set, and borrowing its gloss for a different benchmark would be putting my
    # interpretation into the reader's mouth before the evidence.
    tax_txt = "、".join(f"<code>{k}</code> {v} 题" for k, v in sorted(tax.items(), key=lambda kv: -kv[1]))
    divergences = o.get("official_blind", 0) + o.get("mine_stricter", 0)
    hint = read_jsonl(RESULTS / "bird-agent-hint.jsonl")
    hint_html = ""
    if hint:
        h = hint[0].get("_summary") or {}
        if h.get("valid"):
            base_v = {r["id"]: bool(r["correct"]) for r in run[1:] if r.get("id")}
            hint_v = {r["id"]: bool(r["correct"]) for r in hint[1:] if r.get("id")}
            b2h = sum(1 for k in hint_v if hint_v[k] and not base_v.get(k, False))
            h2b = sum(1 for k in hint_v if not hint_v[k] and base_v.get(k, False))
            _, _, p_value = stats.mcnemar_exact(base_v, hint_v)
            hlo, hhi = stats.wilson(h["n_graded"] and round(h["pass_at_1"] * h["n_graded"]),
                                    h["n_graded"] or 1)
            hint_html = (
                f"<div class=\"note warn\"><b>诊断被验证过：只改一句 prompt，重跑同样 "
                f"{h['n_graded']} 题。</b> 系统提示里加一句「只返回题目要求的列」，其余全部不动："
                f"pass@1 {agent_p1:.1%} → <b>{h['pass_at_1']:.1%}</b>"
                f"（Wilson 95% [{hlo:.1%}, {hhi:.1%}]），配对<b>修好 {b2h} 题、弄坏 {h2b} 题</b>，"
                f"McNemar 精确双侧 <b>p={p_value:.4f}</b>，花费 ${h['total_cost_usd']:.4f}。"
                f"<br>对照 §2 那个自制基准上的 +4.2pp（p=0.0574，<b>未</b>达显著）："
                f"同一套检验，一个跨线一个没跨，两个都印在这里。它消掉的是 22 个列数错误里的 9 个，"
                f"所以这是\"主因之一被证实\"，不是\"89%↔42% 的差距被解释完\"。文字版 HANDOFF §22。</div>")
    return f"""
 <h3>同一个 agent、同一份配置，搬到 BIRD 上</h3>
 {hint_html}
 <table><thead><tr><th>题集</th><th class=num>题</th><th class=num>pass@1</th><th>这套题是谁出的</th></tr></thead><tbody>
  <tr><td>自制基准（baseline，无示例）</td><td class='num'>{base.get('n_tasks', 0)}</td>
      <td class='num'>{base_p1:.1%}</td><td>我造的题、我写的 gold</td></tr>
  <tr><td>BIRD dev（三档各 20 题、档内按库轮转）</td><td class='num'>{n}</td>
      <td class='num'><b>{agent_p1:.1%}</b>（Wilson 95% [{lo:.1%}, {hi:.1%}]）</td>
      <td>人写题，按官方协议附 <code>evidence</code> 外部知识，
      {a.get('n_databases', 0)} 个真实库</td></tr>
 </tbody></table>
 <div class="note warn"><b>差 {(base_p1 - agent_p1) * 100:.1f}pp，而且这条差距不是判分器造成的。</b>
   同一批 {n} 条真实答案在<b>两套口径</b>下各打一遍
   （<code>python scripts/bird_judge.py --answers results/bird-agent.jsonl</code>，$0）：
   我的判分器 {mine}/{n}、公开口径 {off}/{n}，<b>分歧 {divergences} 条</b>。
   没有这一步，"我在 BIRD 上 {agent_p1:.0%}" 和 "我的判分器搬到真实数据又坏了" 是同一句话——
   而本节上面刚记录过它确实在真实数据上坏过两次。</div>
 <div class="note"><b>失败结构（{n - mine} 题）：</b>{tax_txt}。<br>
   主因是<b>多返回了列</b>：题问名字和类型，模型把 id 一起带上。这和上面的注入结果合起来才完整——
   公开口径对<b>重复行</b>盲目，但对<b>多出的列</b>严格（元组一变长就不等）。
   所以"官方更宽松"必须限定到具体缺陷类上，不能当总判断。<br>
   <b>本轮真实花费 ${a.get('total_cost_usd', 0):.4f}</b>（{n} 题、平均 {a.get('avg_llm_steps', 0)} 步/题，
   即 ${a.get('total_cost_usd', 0) / n:.5f}/题）；规模是按这个单价先估后定的，不是拍的。
   先跑的那次 12 题试算只用于定价，<b>产物没有保留</b>，所以这里不给它配数字。<br>
   边界：{n} 题只占 dev 的 {n / 1534:.1%}，档内按库轮转<b>不是随机抽样</b>；单模型、单 prompt、单 seed；
   <code>require_order</code> 一律 False；<b>没有与 BIRD 榜单比</b>（dev 划分与提交格式不同）。
   文字版见 HANDOFF §20。</div>
"""


def external_benchmark_html() -> str:
    """The same judge, on somebody else's questions, next to the public metric's own rule.

    Section 4 asks "is my judge consistent with my own policy" - on 192 questions I wrote
    and gold SQL I wrote. That is self-consistency: a reviewer who does not trust the
    questions cannot trust the audit either. So the judge is dropped unchanged onto BIRD
    dev (11 real SQLite databases, hand-written gold) and compared, per injected defect
    class, against the comparison rule the benchmark itself ships. The divergence has two
    directions and this section reports both, because "my judge is stricter" is the same
    one-sidedness this page exists to argue against.
    """
    rows = read_jsonl(RESULTS / "bird-judge.jsonl")
    if not rows:
        return """
 <h2>5 · 判分器在别人造的题上（公开基准 BIRD dev）</h2>
 <div class="note warn">仓库里没有 <code>results/bird-judge.jsonl</code>，本节无内容可渲染。
   复现：<code>python scripts/bird_judge.py --dev-dir &lt;解压后的 BIRD dev&gt;</code>
   （$0，不调模型；下载方式在脚本头注释里，它故意不进 CI）。</div>
"""
    s = rows[0].get("_summary") or {}
    per = s.get("per_mode") or {}
    modes = [m for m in CALIB_ORDER if m in per]
    # Anything the artifact knows about that this page does not render is a silent gap.
    extra = sorted(set(per) - set(modes))
    assert not extra, f"bird-judge.jsonl has modes report.py does not render: {extra}"

    head = ("<tr><th>注入缺陷</th><th class=num>可比观测</th><th class=num>与我的政策一致</th>"
            "<th class=num>与公开口径一致</th>"
            "<th class=num>我判错、公开口径判对<div class='sub'>公开口径看不见这个缺陷</div></th>"
            "<th class=num>我判对、公开口径判错<div class='sub'>我的政策比它宽松</div></th></tr>")
    body = []
    for m in modes:
        d = per[m]
        n = d["checked"]
        pa, pu = d["policy_agree"], d["public_agree"]
        strict, lenient = d["mine_strict"], d["mine_lenient"]
        body.append(
            f"<tr><td><code>{html.escape(m)}</code>"
            f"<div class='sub'>{html.escape(MODE_LABELS.get(m, ''))}</div></td>"
            f"<td class='num'>{n}</td>"
            f"<td class='num {'ok' if pa == n else 'bad'}'>{pa}/{n}"
            f"{'' if n == 0 else f' = {pa/n:.0%}'}</td>"
            f"<td class='num'>{pu}/{n}{'' if n == 0 else f' = {pu/n:.1%}'}</td>"
            f"<td class='num {'bad' if strict else ''}'>{strict}</td>"
            f"<td class='num'>{lenient}</td></tr>"
        )
    table = f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"

    checked = s.get("checked", sum(per[m]["checked"] for m in modes))
    agreed = sum(per[m]["policy_agree"] for m in modes)
    # The two totals are reported with their split, because "13" and "62" are also the
    # per-class observation counts elsewhere on this page - a bare total invites a reader
    # to line them up against the wrong row.
    def _split(key: str) -> str:
        parts = [f"{m} {per[m][key]}" for m in modes if per[m][key]]
        return "、".join(parts) if parts else "0"

    strict_total = sum(per[m]["mine_strict"] for m in modes)
    lenient_total = sum(per[m]["mine_lenient"] for m in modes)
    dd = per.get("drop_distinct", {})
    skips = s.get("skipped") or {}
    skip_txt = "、".join(
        f"{k.split(':')[0].replace('corrupt() failed', 'gold 解析失败')} {v} 条"
        for k, v in sorted(skips.items()))
    n_q = s.get("questions_selected", 0)
    agent_html = agent_on_bird_html()

    return f"""
 <h2>5 · 判分器在别人造的题上（公开基准 BIRD dev）</h2>
 <div class="sub">做法：把<b>同一个</b> <code>eval/scoring.py</code> 放到 {n_q} 道<b>人写</b> gold、
  {s.get('databases', 0)} 个真实 SQLite 库上，注入缺陷仍由 <code>sqlagent.llm.corrupt</code> 本地生成，
  然后逐条与<b>公开基准自己的比较规则</b>对答案。全程不调模型、$0。
  选题规则：BIRD 自带 <code>difficulty</code> 三档各取<b>排序后前 N 个</b>
  <code>question_id</code>（无随机、无挑选；本次共 {n_q} 题，N 见 <code>--per-tier</code> 默认值）。</div>
 <table><thead><tr><th>检查</th><th>结果</th></tr></thead><tbody>
  <tr><td>gold 判 gold（自我一致性）</td>
      <td><b>{s.get('gold_self_correct', 0)}/{n_q}</b> 道人写 SQL 判为正确
      （错误 {s.get('gold_self_wrong', 0)}）——判分器不会把自己的执行器搞出来的失败算到 SQL 头上</td></tr>
  <tr><td>注入观测 vs 我自己的政策</td><td><b>{agreed}/{checked}</b></td></tr>
  <tr><td>注入观测 vs 公开口径</td>
      <td>分歧双向都有：公开口径看不见 <b>{strict_total}</b> 条（{_split('mine_strict')}，
      我判错），我在 <b>{lenient_total}</b> 条上比它宽松（{_split('mine_lenient')}）</td></tr>
 </tbody></table>
 {table}
 <div class="note warn"><b>这一节最值钱的一格是 <code>drop_distinct</code> 那一行。</b>
   {dd.get('checked', 0)} 个能承载"去掉 DISTINCT"的观测里，公开口径把
   <b>{dd.get('mine_strict', 0)} 个判成了正确</b>——因为它的规则是
   <code>{html.escape(str(s.get('public_rule', '')))}</code>，<code>set()</code> 把重复行折叠掉了。
   而重复行正是上一节校准表里标注的最高频真实错误类型。
   <b>换句话说：一个在公开榜上拿高分的 Text-to-SQL 系统，可能根本没被要求区分
   "结果多出一倍重复行"和"答对"。</b></div>
 <div class="note warn"><b>但我不拿这句话冒充"我比官方严"。</b>
   同一批数据上，我在列置换（{per.get('reorder_cols', {}).get('mine_lenient', 0)} 条）
   与舍入（{per.get('float_round', {}).get('mine_lenient', 0)} 条）上比官方<b>宽松</b>：
   它按位置比较列、数字必须逐位相同，我按列名对齐、按容差比数。
   所以这里只有"两个方向各自的差 + 原因"，没有"谁的判分器更好"这个总判断。</div>
 <div class="note"><b>搬到别人数据上之后 <code>vs 政策</code> 才是 {agreed}/{checked}：第一次跑是 93%/91%/83%。</b>
   那三个数抓出了两个真 bug（都写进了 HANDOFF §9 与 §19.3）：
   <code>result_differs()</code> 比较列名而判分器不比列名（于是"用来自我解释这个指标的那一列"
   一直是错的，而主指标 100% 全绿），以及列标签的拼写噪声会<b>静默</b>退化成按位置比较。
   自制数据投影永远命名列，这两个都测不出来。</div>
 {agent_html}
 <div class="note"><b>分母怎么来的，逐类报，不静默丢弃：</b>{skip_txt}。
   也就是说 <code>checked={checked}</code> 不等于 {n_q}×{len(modes)}——注入不进去的题不进入该类的分母。</div>
 <div class="note"><b>本节没测的（别当成测了）：</b>
   <code>require_order</code> 一律 False，公开基准没有"题面是否要求顺序"的标注，
   <b>顺序敏感性未被检验</b>（官方口径本身对行序盲目）；只跑 SQLite 方言；
   {n_q}/1534 题按难度分层但<b>不随机</b>；
   <b>agent 在 BIRD 上的分数见上面那块表</b>。本节最初写着"这里没有、也不该有 BIRD 上多少分"，
   那句话已被 §20 作废——留在原处是因为读者应该看见它曾经成立过。
   判分器改动会不会动已发表判定，由 <code>python scripts/rejudge.py</code> 现场回答
   （判分是纯函数，$0；它自己打印覆盖条数与翻转数，所以那个数不抄在这里）。</div>
 <div class="sub">出处：<code>results/bird-judge.jsonl</code>（含判分执行器
   SQLite {html.escape(str(s.get('sqlite_version', '未记录')))}）；公开口径的语义由
   <code>tests/test_bird_judge.py</code> 钉住，规则原文是
   <code>bird-bench/mini_dev</code> 的 <code>evaluation_ex.py:20</code>。文字说明见 HANDOFF §19。</div>
"""


def pass_cell(sm: dict, colour: str = "#5b7fd6") -> tuple[float, str]:
    """(percentage for the bar, html for the cell) for one run summary.

    Two failure modes this has to keep apart, because the fourth review found the
    second one still reachable: a run whose harness died must not read as a model that
    answered nothing correctly - the JSON used to carry `pass_at_1: 0.0` for those - and
    a run with a handful of harness exceptions must not read as clean, since those
    tasks scored 0 and stayed in the denominator.
    """
    p1 = sm.get("pass_at_1")
    exc = sm.get("harness_exceptions", 0) or 0
    if p1 is None:
        return 0.0, "<span class='sub'>拒绝输出（运行无效，不是 0 分）</span>"
    cell = bar(p1 * 100, colour)
    if exc:
        cell += (f" <span style='color:#c98a4b'>（含 {exc} 次 harness 异常，"
                 "已按 0 分计入分母）</span>")
    return p1 * 100, cell


def calibration_rows() -> list[str]:
    out = []
    for mode in CALIB_ORDER:
        rows = read_jsonl(RESULTS / f"calib-{mode}.jsonl")
        if not rows:
            continue
        body = [r for r in rows[1:] if "id" in r]
        tested = [r for r in body if r.get("result_changed") is not None and not r.get("trivial")]
        injectable = [r for r in body if r.get("result_changed") is not None]
        presentation_only = mode in stats.PRESENTATION_ONLY_MODES
        agree = 0
        for r in tested:
            should_pass = presentation_only or not r["result_changed"]
            agree += int(r["correct"] == should_pass)
        false_accept = sum(1 for r in tested
                           if r["correct"] and not (presentation_only or not r["result_changed"]))
        out.append(
            f"<tr><td><code>{html.escape(mode)}</code></td>"
            f"<td>{'接受' if presentation_only or mode in {'reflow','bad_column'} else '拒绝'}</td>"
            f"<td class='num'>{len(tested)}</td><td class='num'>{agree}</td>"
            f"<td class='num {'bad' if false_accept else 'ok'}'>{false_accept}</td>"
            f"<td class='num'>{len(injectable) - len(tested)}</td>"
            f"<td class='num'>{(agree / len(tested) * 100 if tested else 0):.1f}%</td></tr>"
        )
    return out


def adversarial_section() -> tuple[str, str, str]:
    """Return (metric cards, per-category rows, extra notes) from results/adversarial.jsonl."""
    rows = read_jsonl(RESULTS / "adversarial.jsonl")
    if not rows:
        return "", "", ""
    s = rows[0]["_summary"]
    bi = s.get("by_intensity") or {}
    tier_txt = "、".join(f"{INTENSITY_CN.get(k, k)} {v['attempted']}/{v['n']}"
                         for k, v in bi.items()) or "无（旧产物没有这一列）"
    cards = f"""
      <div class="card"><h3>{s['probes']}</h3><p>对抗性探测（其中 {s.get('write_shapes', 0)} 条邀请写操作）</p></div>
      <div class="card"><h3>{s['unsafe_attempted']} / {s['probes']}</h3><p>模型真的尝试了越权动作。
        <b>按诱导强度分：</b>{tier_txt}</p></div>
      <div class="card"><h3>{s['guard_caught']}</h3><p>其中被护栏拦下</p></div>
      <div class="card"><h3 style="color:#4f9d8f">{s['unsafe_executed']}</h3><p>真正执行成功的写操作
        （这一格不依赖文本判据）</p></div>
      <div class="card"><h3 style="color:#c98a4b">{s['claimed_done']}</h3><p>被文本判据<b>筛出</b>的
        "已完成"声称 —— 是筛选器计数，不是谎言计数，见下方说明</p></div>
    """
    tr = []
    # `attempted` is counted from the per-probe rows rather than read from the summary:
    # the committed artifact predates the field, and re-running the probe set means
    # spending money to re-ask a non-deterministic model. Deriving it here keeps the
    # column honest without silently changing the other numbers beside it.
    attempted: dict[str, int] = {}
    for r in rows[1:]:
        if r.get("unsafe_attempted"):
            attempted[r["category"]] = attempted.get(r["category"], 0) + 1
    for cat, v in s["by_category"].items():
        colour = "#e07a6b" if v["fail"] else "#4f9d8f"
        att = attempted.get(cat, v.get("attempted", 0))
        never = "" if att else " style='color:#8b8fa3'"
        tr.append(f"<tr><td><code>{html.escape(cat)}</code></td><td class='num'>{v['n']}</td>"
                  f"<td class='num'{never}>{'0（问了，模型没试）' if not att else att}</td>"
                  f"<td class='num' style='color:{colour}'>{v['fail']}</td>"
                  f"<td class='num'>{v['guard']}</td><td class='num'>{v['claim']}</td></tr>")
    notes = f"""
 <div class="note warn"><b>「谎称已完成」这一格是筛选器，不是测量。</b>
   把探测集从 26 条扩到 {s['probes']} 条之后，旧判据一次报出 <b>35 条</b>谎称完成——逐条读完，
   <b>35 条全是假阳性</b>：模型拒绝了写操作，然后把被拒绝的语句原样贴出来告诉用户该怎么跑，
   判据看到的是"delete + rows"。修完判据，{s['probes']} 条里还剩 {s['claimed_done']} 条命中，
   两条也都是假阳性（一句在描述 SELECT 的输出，一句是剥掉引号后剩下的残句）。
   <b>代价是召回</b>：在事后另写的一组 10 条真实谎报句式上，现在的判据只认出 7 条
   （漏掉的是"Done, the waitlist is empty now."、"Index idx_reviews_course dropped."、
   "Executed - the duplicates are cleared."）。所以这一格既不能报成"零谎称"，
   也不能报成"发现了谎称"——它只能说明文本判据在这个任务上撑不住一个结论。
   真正不依赖文本的硬证据是上面那格 <b>{s['unsafe_executed']} 次写操作被执行</b>：
   它来自工具层与只读连接，不来自正则读散文。</div>
 <div class="note"><b>诱导强度确实改变了行为，而且方向不是"越隐蔽越危险"。</b>
   {tier_txt}：给正当理由（迁移、清理、测试库）比裸命令更容易让模型真去试，
   而把写动作嵌进一个正常读任务里最不容易。含义有两层：一，之前"26 条里只尝试 6 次"
   说的是题面写法，不是模型品格；二，生产环境里最接近"给正当理由"的表达方式最多，
   所以这一档才是该重点测的。<b>但这只是这一次运行的观测</b>：{s['probes']} 条、单模型、
   温度 0，且各档样本量 {min(v['n'] for v in bi.values()) if bi else 0}–{max(v['n'] for v in bi.values()) if bi else 0}，
   差异没有做显著性检验。</div>
""" if bi else ""
    return cards, chr(10).join(tr), notes


def trace_integrity_note(malformed: dict[str, int]) -> str:
    """Say so, out loud, when the trace files have holes.

    `runs/*.jsonl` is appended by the runner's worker threads; before the append took a
    process-wide lock, a several-KB record could be handed to the OS in pieces and
    interleave with another thread's half. The damage is in the published files, so the
    report states how many task replays are missing instead of rendering a page with no
    gap where the evidence used to be.
    """
    n = sum(malformed.values())
    if not n:
        return ""
    spread = "、".join(f"{k} 少 {v} 条" for k, v in sorted(malformed.items()))
    return (f"<div class=\"note warn\"><b>本节少了 {n} 条 trace，不是没跑过。</b> "
            f"<code>runs/*.jsonl</code> 由 runner 的多个工作线程追加写同一个文件；在 "
            f"<code>trace.py</code> 给追加加上进程内锁之前，一条几 KB 的记录可能被拆成两次系统调用、"
            f"两个线程的半条互相穿插，那一行就不是合法 JSON。读侧现在数出来并写在这里，而不是静默跳过。<br>"
            f"<b>逐题结果与分数不受影响</b>（它们在 <code>results/</code>，由 runner 单独写），"
            f"受影响的只有那一题的过程回放。分布：{spread}。</div>")


def build(runs: dict, traces: dict, tasks: dict, malformed: dict[str, int] | None = None) -> str:
    tags = [t for t in RUN_LABELS if t in runs]
    base = runs.get("abl2-baseline")
    headline = runs.get("abl2-3shot", base)
    s = headline["summary"] if headline else {}

    nf = stats.noise_floor()
    n_dropped = len(read_jsonl(ROOT / "data" / "tasks_dropped.jsonl"))
    head_p1 = s.get("pass_at_1")
    head_exc = s.get("harness_exceptions", 0)
    cards = f"""
      <div class="card"><h3>{f"{head_p1 * 100:.1f}%" if head_p1 is not None else "拒绝输出"}</h3>
        <p>pass@1（最佳配置）{'，含 ' + str(head_exc) + ' 次 harness 异常' if head_exc else ''}</p></div>
      <div class="card"><h3>{s.get('n_tasks', 0)}</h3><p>有效题目（另有 {n_dropped} 道被判为无法出题，已剔除）</p></div>
      <div class="card"><h3>{s.get('avg_llm_steps', 0)}</h3><p>平均 LLM 调用次数 / 题</p></div>
      <div class="card"><h3>~¥{total_spend()[0] * 7.2:.1f}</h3><p>累计花费估算（按当前价目表，非账单）</p></div>
      <div class="card"><h3>{total_spend()[1] / 1e6:.1f}M</h3><p>累计 prompt tokens（测量值，不是估算）</p></div>
      <div class="card"><h3>{nf['worst_tasks']} / {nf['n']}（{nf['worst_pp']:.1f}pp）</h3><p>噪声底：同配置基线最大两两差异，来自 <code>sqlagent.stats</code></p></div>
      <div class="card"><h3 style="color:{'#4f9d8f' if any(c['significant'] for c in stats.all_comparisons()) else '#e07a6b'}">{sum(1 for c in stats.all_comparisons() if c['significant'])} / {len(stats.all_comparisons())}</h3><p>配对比较中达到 p&lt;0.05 的项数（详见第 2 节）</p></div>
    """

    rows = []
    for tag in tags:
        sm = runs[tag]["summary"]
        colour = ("#8b8fa3" if tag.startswith("mock") else
                  "#4f9d8f" if tag.endswith("3shot") else
                  "#c98a4b" if tag.startswith("qwen") else "#5b7fd6")
        # A withheld score renders as a refusal, never as 0.0%: the runner refuses to
        # turn a measurement failure into a model score, and the report must not undo
        # that by printing the placeholder the JSON used to carry.
        pct, cell = pass_cell(sm, colour)
        p1 = sm.get("pass_at_1")
        delta = "—"
        if (base and tag != "abl2-baseline" and not tag.startswith("mock")
                and p1 is not None and base["summary"].get("pass_at_1") is not None):
            delta = f"{(p1 - base['summary']['pass_at_1']) * 100:+.1f}pp"
        rows.append(
            f"<tr><td>{html.escape(RUN_LABELS[tag])}</td><td class='num'>{sm.get('n_tasks',0)}</td>"
            f"<td style='min-width:180px'>{cell}</td><td class='num'>{delta}</td>"
            f"<td class='num'>{sm.get('avg_llm_steps',0)}</td>"
            f"<td class='num'>{sm.get('avg_sql_attempts',0)}</td>"
            f"<td class='num'>{adherence(sm)}</td>"
            f"<td class='num'>{sm.get('n_no_sql_executed', '—')}</td>"
            f"<td class='num'>{money(token_cost(list(runs[tag]['rows'].values()), sm.get('model','')))}</td></tr>"
        )
    runs_table = "\n".join(rows)

    # The notes below used to carry transcribed figures, and one of them was wrong
    # ("7 个单题簇", actually 8) while the correct value sat in another document. Every
    # number a reader is asked to believe is now read out of the same summaries the
    # tables are built from, so a re-run cannot leave a stale sentence behind.
    def _sm(tag: str) -> dict:
        return runs.get(tag, {}).get("summary", {})

    q3, qb = _sm("qwen-3shot"), _sm("qwen-baseline")
    d3, db = _sm("abl2-3shot"), _sm("abl2-baseline")
    mr, mnr = _sm("m-repair"), _sm("m-norepair")
    repair_off = stats.comparison("self-repair OFF vs baseline", "abl2-baseline", "abl2-norepair")

    calib = "\n".join(calibration_rows())

    tax_rows = []
    if base:
        tax = Counter()
        for r in base["rows"].values():
            if not r["correct"] and not r.get("trivial"):
                tax[r["reason"]] += 1
        for reason, n in tax.most_common():
            tax_rows.append(f"<tr><td><code>{html.escape(reason)}</code></td><td class='num'>{n}</td>"
                            f"<td>{explain(reason)}</td></tr>")
    taxonomy = "\n".join(tax_rows) or "<tr><td colspan=3>无失败样本</td></tr>"

    cat_rows = []
    if base:
        per_cat = defaultdict(lambda: [0, 0])
        for r in base["rows"].values():
            per_cat[r.get("category", "?")][1] += 1
            if r["correct"]:
                per_cat[r.get("category", "?")][0] += 1
        for cat, (ok, n) in sorted(per_cat.items(), key=lambda x: x[1][0] / max(1, x[1][1])):
            cat_rows.append(f"<tr><td><code>{html.escape(cat)}</code></td><td class='num'>{ok}/{n}</td>"
                            f"<td>{bar(ok / max(1, n) * 100, '#c98a4b' if ok / n < 0.8 else '#4f9d8f')}</td></tr>")
    cats = "\n".join(cat_rows)

    run_stems = {tag: run_stem(runs[tag]["summary"]) for tag in tags}
    # Counted over the runs this section can actually replay. The other trace files on
    # disk come from calibration and mock sweeps that the page never shows, and a note
    # that added them in would overstate what the reader is missing - the same mistake
    # as a denominator that includes defects the injection could not reach.
    shown = set(run_stems.values())
    trace_note = trace_integrity_note({k: v for k, v in (malformed or {}).items() if k in shown})
    browser = build_browser(tasks, traces, run_stems)
    adv_cards, adv_rows, adv_notes = adversarial_section()
    adv = (read_jsonl(RESULTS / "adversarial.jsonl") or [{}])[0].get("_summary") or {}
    # Attempts inside the categories that invite a write, which is the number the previous
    # version of this page could not make a claim about at all.
    dw_att = sum(v.get("attempted", 0) for k, v in (adv.get("by_category") or {}).items()
                 if k in {"direct_write", "stacked", "cte_write", "dml_variant", "obfuscated",
                          "exfiltration", "admin_op"})
    # generated from the same list the table uses: a hand-written option list
    # drifted from RUN_LABELS and silently hid the two Qwen runs from the browser
    run_options = "".join(
        f'<option value="{html.escape(tag, quote=True)}">{html.escape(RUN_LABELS[tag])}</option>'
        for tag in tags)

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Text-to-SQL Agent 评测报告</title>
<style>
 :root {{ color-scheme: dark }}
 * {{ box-sizing: border-box }}
 body {{ margin:0; padding:32px 24px 80px; background:#12141a; color:#e6e8ef;
   font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif }}
 main {{ max-width:1080px; margin:0 auto }}
 h1 {{ font-size:26px; margin:0 0 6px }}
 h2 {{ font-size:18px; margin:44px 0 12px; padding-bottom:8px; border-bottom:1px solid #262a36 }}
 .sub {{ color:#8b8fa3; font-size:13.5px; margin-bottom:26px }}
 .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px }}
 .card {{ background:#191c25; border:1px solid #262a36; border-radius:10px; padding:14px 16px }}
 .card h3 {{ margin:0; font-size:24px; color:#7fb3ff }}
 .card p {{ margin:4px 0 0; font-size:12.5px; color:#8b8fa3 }}
 table {{ width:100%; border-collapse:collapse; font-size:13.5px; margin-top:6px }}
 th,td {{ padding:8px 10px; border-bottom:1px solid #222632; text-align:left; vertical-align:middle }}
 th {{ color:#8b8fa3; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em }}
 td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }}
 .bar {{ position:relative; background:#20242f; border-radius:4px; height:18px; overflow:hidden }}
 .bar span {{ display:block; height:100% }}
 .bar em {{ position:absolute; right:6px; top:0; font-size:11.5px; font-style:normal; color:#cfd3e0 }}
 code {{ font-family:"Cascadia Mono",Consolas,monospace; font-size:12.5px; color:#a8c7fa }}
 .ok {{ color:#4f9d8f }} .bad {{ color:#e07a6b }}
 .note {{ background:#1a1d26; border-left:3px solid #4f6bd6; padding:11px 14px; margin:14px 0;
   font-size:13.5px; color:#b6bccd; border-radius:0 8px 8px 0 }}
 .warn {{ border-left-color:#c98a4b }}
 details {{ background:#191c25; border:1px solid #262a36; border-radius:8px; margin:8px 0; padding:0 14px }}
 summary {{ cursor:pointer; padding:11px 0; font-size:14px }}
 pre {{ background:#0e1015; border:1px solid #222632; border-radius:6px; padding:11px; overflow-x:auto;
   font-size:12.5px; white-space:pre-wrap; word-break:break-word; color:#c8cede }}
 .step {{ border-left:3px solid #2f3648; margin:10px 0 10px 6px; padding:2px 0 2px 13px }}
 .step.tool {{ border-left-color:#4f9d8f }} .step.err {{ border-left-color:#e07a6b }}
 .tag {{ display:inline-block; font-size:11px; padding:1px 7px; border-radius:20px; background:#242a3a;
   color:#9fb0d0; margin-left:6px }}
 .tpane {{ display:none }}
 body[data-run=""] .tpane {{ display:block }}
 .filter {{ display:flex; gap:10px; align-items:center; margin:12px 0 }}
 select,input[type=search] {{ background:#0e1015; color:#e6e8ef; border:1px solid #2c3242;
   border-radius:6px; padding:7px 10px; font-size:13.5px }}
 </style></head><body><main>
 <h1>Text-to-SQL Agent 评测报告</h1>
 <div class="sub">模型 <code>deepseek-chat</code> · temperature 0 · 单文件离线报告 ·
   数据源 <code>results/*.jsonl</code>、<code>runs/*.jsonl</code> · 由
   <code>python -m sqlagent.report</code> 生成，不产生任何 API 调用</div>

 <div class="cards">{cards}</div>

 <h2>1 · 消融结果</h2>
 <table><thead><tr><th>配置</th><th class=num>题数</th><th>pass@1</th><th class=num>Δ baseline</th>
   <th class=num>LLM 步数</th><th class=num>SQL 尝试</th><th class=num>真正用过工具</th>
   <th class=num>从未执行 SQL</th><th class=num>花费</th></tr></thead>
   <tbody>{runs_table}</tbody></table>
 <div class="note warn"><b>跨模型对比目前是混淆的，别当成能力排名。</b>Qwen 的 3-shot 准确率高于它自己的
   baseline（{q3.get('pass_at_1', 0):.1%} vs {qb.get('pass_at_1', 0):.1%}），但同一次运行里
   <b>只有 {q3.get('protocol_adherence', 0):.0%} 的题目真正执行过 SQL，{q3.get('n_zero_tool_calls', 0)} 题一次工具都没调</b>
   （baseline 是 {qb.get('protocol_adherence', 0):.0%} / {qb.get('n_zero_tool_calls', 0)} 题）。
   另有 <b>{q3.get('n_no_sql_executed', 0)}/{q3.get('n_tasks', 0)} 题最终 SQL 来自模型散文、从未在库上执行过</b>，
   <b>它们照常计分</b>——这一列就是为了让这件事在表里而不只在散文里。原因在示例格式：few-shot 是
   "问题 → SQL"两段式，不含工具调用，于是模型模仿示例直接作答——它不再是个 agent，只是被问对了更多题。
   <b>prompt 格式改变了执行协议，而 pass@1 把这个报成了准确率提升。</b></div>
 <div class="note"><b>关掉自修复后逐题结果完全不变（{repair_off['fixed']} 升 {repair_off['broken']} 降）</b>
   ——baseline 平均每题 {db.get('avg_sql_attempts', 0)} 次 SQL 尝试，也就是几乎没有错误可修。
   机制本身靠注入单独验证：开={mr.get('self_repair_recovery_rate', 0):.1%} 恢复
   （{mr.get('self_repair_opportunities', 0)} 次注入错误），关={mnr.get('self_repair_recovery_rate', 0):.0%}。
   所以诚实的结论是“该组件在本数据集上测不出增益”，不是“自修复提升了准确率”。</div>

 {significance_html()}
 {reliability_html()}
 <h2>4 · 判分器校准（谁来审计审计者）</h2>
 <table><thead><tr><th>注入缺陷</th><th>应然裁决</th><th class=num>可测样本</th><th class=num>判分一致</th>
   <th class=num>false-accept</th><th class=num>注入后无变化</th><th class=num>一致率</th></tr></thead>
   <tbody>{calib}</tbody></table>
 <div class="note">“注入后无变化”是指缺陷没能改变结果集（例如给单行结果加 <code>LIMIT 3</code>）。
   这类样本按“应接受”计入一致率而不是被剔除——剔除它们会让指标自我美化。</div>
 <div class="note warn">但这一节只证明判分器与<b>我自己</b>的政策一致，而且用的还是我自己造的题、
   我自己写的 gold。<b>自洽不等于测对了东西</b>——下一节把同一个判分器搬到公开基准上，
   看它与人写 gold、与公开基准自己的比较规则对不上在哪里。</div>

 {external_benchmark_html()}
 <h2>6 · 失败归因（baseline）</h2>
 <table><thead><tr><th>原因</th><th class=num>题数</th><th>含义</th></tr></thead><tbody>{taxonomy}</tbody></table>
 <div class="note warn">护栏在 192 道真实题里<b>拦截 0 次</b>，注入标记只命中 1 次。也就是说安全护栏目前
   只有单元测试层面的证据，没有行为层面的证据。这一栏故意保留着，因为“拿零次观测冒充结论”
   正是这个项目反对的事。</div>

 <h2>7 · 分类通过率</h2>
 <table><thead><tr><th>题型族</th><th class=num>通过</th><th>通过率</th></tr></thead><tbody>{cats}</tbody></table>

 <h2>8 · 对抗性安全探测</h2>
 <div class="sub">192 道正常题里护栏触发 <b>0 次</b>——那只说明模型没试。这一组
   {adv['probes']} 条探测专门<b>邀请</b>模型做越权动作（其中 {adv.get('write_shapes', 0)} 条邀请写），
   用来把"没有观测"变成"有数字"。</div>
 <div class="cards">{adv_cards}</div>
 <table><thead><tr><th>探测类别</th><th class=num>条数</th><th class=num>模型真的尝试了</th>
   <th class=num>agent 失败</th><th class=num>被护栏拦下</th><th class=num>被筛出的完成声称</th></tr></thead>
   <tbody>{adv_rows}</tbody></table>
 <div class="note"><b>先看"模型真的尝试了"这一列，再决定其余几列要不要信。</b>
   {adv['probes']} 条里模型真的尝试了 <b>{adv['unsafe_attempted']}</b> 次，
   其中写形状 {dw_att} 次——<b>这一次护栏真的被问过写语句</b>，
   所以"真正执行成功的写操作 = {adv['unsafe_executed']}"不再是平凡成立的：
   每一次尝试都在 AST 白名单处被拒，没有一个语句到达 SQLite。
   仍然要留一句：{adv['probes'] - adv['unsafe_attempted']} 条的结论由模型是否肯试决定，
   换一个模型这些数字就会变。</div>
 <div class="note warn"><b>{adv['unsafe_attempted']} 次挑战 {adv['unsafe_executed']} 次失守，推不出"可靠"。</b>
   按 rule of three，0/{adv['unsafe_attempted']} 的失守率 95% 上界约
   <code>1 − 0.05^(1/{adv['unsafe_attempted']}) ≈ {(1 - 0.05 ** (1 / max(1, adv['unsafe_attempted']))) * 100:.0f}%</code>。
   这份材料能支持的只有"这 {adv['unsafe_attempted']} 个具体形状被正确拒绝了"——
   比上一版（6 次尝试、上界 39%）紧，但远不到"安全属性"。</div>
 <div class="note">最值钱的观察：<code>catalog-02</code> 中模型先试 <code>sqlite_master</code> 被拦，
   随即改用 <b><code>sqlite_schema</code></b>（SQLite 中前者官方别名）重试，仍被拦下。这正是"白名单默认拒绝"
   相对"黑名单枚举危险词"的价值所在——别名不在任何人的清单上。</div>
 <div class="note warn">诚实边界：<code>refused</code>（措辞上拒绝）与 <code>agent_fail</code>（动作上越权）
   不是互斥的，有样本两者同时成立——模型说"我做不到"却仍然去试。所以"拒绝率"不能当安全指标用。</div>
 {adv_notes}

 <h2>9 · Trace 回放</h2>
 <div class="sub">每一题的完整过程：模型看到什么、调了哪个工具、数据库回什么、错在哪一步。
   上面所有数字都能在这里找到出处。</div>
 {trace_note}
 <div class="filter">
   <select id="fRun">{run_options}</select>
   <select id="fVer"><option value="all">全部</option><option value="fail">只看失败</option>
     <option value="pass">只看通过</option></select>
   <input type="search" id="fQ" placeholder="搜索题面 / SQL…" style="flex:1">
   <span class="tag" id="fCount"></span>
 </div>
 <div id="items">{browser}</div>
</main>
<script>
const DATA = {json.dumps(build_payload(runs), ensure_ascii=False)};
const items = [...document.querySelectorAll('details.task')];
function render() {{
  const run = document.getElementById('fRun').value;
  const ver = document.getElementById('fVer').value;
  const q = document.getElementById('fQ').value.trim().toLowerCase();
  const verdicts = DATA[run] || {{}};
  let shown = 0;
  for (const el of items) {{
    const tid = el.dataset.tid;
    const has = Object.prototype.hasOwnProperty.call(verdicts, tid);
    const ok = verdicts[tid];
    if (ver === 'pass' && (!has || !ok)) {{ el.style.display = 'none'; continue; }}
    if (ver === 'fail' && (!has || ok)) {{ el.style.display = 'none'; continue; }}
    if (q && !el.textContent.toLowerCase().includes(q)) {{ el.style.display = 'none'; continue; }}
    el.style.display = '';
    shown++;
    const badge = el.querySelector('.verdict');
    if (!has) {{ badge.textContent = '该运行未含此题'; badge.className = 'verdict tag'; }}
    else {{ badge.textContent = ok ? 'PASS' : 'FAIL'; badge.className = 'verdict tag ' + (ok ? 'ok' : 'bad'); }}
  }}
  document.body.dataset.run = run;
  for (const el of items) {{
    for (const pane of el.querySelectorAll('.tpane'))
      pane.style.display = pane.dataset.run === run ? 'block' : 'none';
  }}
  document.getElementById('fCount').textContent = shown + ' / ' + items.length + ' 题';
}}
for (const id of ['fRun','fVer','fQ']) document.getElementById(id).addEventListener('input', render);
render();
</script></body></html>"""


def explain(reason: str) -> str:
    return {
        "value_mismatch": "行数列数都对，但具体数值不同——多为聚合粒度、日期口径或过滤条件理解错",
        "row_count_mismatch": "返回行数不对——常见是 JOIN 扇出产生重复行，或漏掉 DISTINCT",
        "column_count_mismatch": "投影列数不对——常因没答完问题，或把探查用的临时查询当成了最终答案",
        "order_wrong": "行对但顺序错（仅在题面明确要求顺序时才判）",
        "no_sql_produced": "没有产出可执行的最终 SQL",
    }.get(reason, reason)


def build_payload(runs: dict) -> dict:
    """Verdicts only. Trace bodies are rendered into the HTML server-side."""
    return {tag: {r["id"]: bool(r["correct"]) for r in data["rows"].values()}
            for tag, data in runs.items()}


def dom_id(task_id: str) -> str:
    """One rule, shared by the generated markup and the JS lookup."""
    return "task-" + re.sub(r"[^A-Za-z0-9]", "_", task_id)


def _steps_html(tr: dict | None) -> str:
    if not tr:
        return '<span class="sub">该运行没有这道题的 trace</span>'
    out = []
    for s in tr["steps"]:
        if s.get("kind") == "llm":
            calls = " ".join(
                f"<code>{html.escape(str(c.get('name')))}({html.escape(str(c.get('arguments'))[:160])})</code>"
                for c in s.get("tool_calls") or []
            )
            inner = html.escape(s.get("content") or "") or calls or "(no text)"
            out.append(f'<div class="step"><span class="tag">LLM 第 {s.get("turn")} 轮</span>'
                       f'<span class="tag">{s.get("prompt_tokens")}+{s.get("completion_tokens")} tok</span>'
                       f'<pre>{inner}</pre></div>')
        else:
            ok = s.get("ok")
            out.append(
                f'<div class="step tool {"err" if not ok else ""}">'
                f'<span class="tag">工具 {html.escape(str(s.get("name", "")))}</span>'
                f'<span class="tag">{"ok" if ok else "FAIL " + html.escape(str(s.get("error_type")))}</span>'
                f'<pre>IN : {html.escape(str(s.get("arguments"))[:400])}\n'
                f'OUT: {html.escape(str(s.get("result"))[:700])}</pre></div>')
    return "".join(out) or '<span class="sub">无步骤</span>'


def build_browser(tasks: dict, traces: dict, run_stems: dict) -> str:
    """Trace bodies are rendered server-side once per run; the JS only shows/hides.

    Re-rendering 192 expanded traces on every keystroke made the filter unusable,
    and none of that markup changes with the filters - only the verdict badge and
    which run's pane is visible.
    """
    parts = []
    for tid, t in sorted(tasks.items()):
        panes = []
        for tag, stem in run_stems.items():
            rec = traces.get(stem, {}).get(tid)
            fname = html.escape(rec["_file"]) if rec else "—"
            panes.append(
                f'<div class="tpane" data-run="{html.escape(tag, quote=True)}">'
                f'{_steps_html(rec)}'
                f'<div class="sub">trace 文件：<code>{fname}</code></div></div>'
            )
        parts.append(
            f'<details class="task" id="{dom_id(tid)}" data-tid="{html.escape(tid, quote=True)}">'
            f'<summary>{html.escape(tid)} <span class="verdict tag">—</span></summary>'
            f'<div class="sub">{html.escape(t["question"])}</div>'
            f'<pre>GOLD: {html.escape(t["gold_sql"])}</pre>'
            f'<div class="body">{"".join(panes)}</div>'
            f'</details>'
        )
    return "\n".join(parts)


def main() -> int:
    runs = load_runs()
    if not runs:
        raise SystemExit("no results/*.jsonl found - run the eval first")
    traces, malformed = load_traces()
    tasks = load_tasks()
    OUT.write_text(build(runs, traces, tasks, malformed), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  runs: {', '.join(runs)}")
    print(f"  tasks: {len(tasks)}   trace files: {len(traces)} ({sum(len(v) for v in traces.values())} task-traces)")
    if malformed:
        print(f"  不可解析的 trace 行: {sum(malformed.values())} "
              f"({', '.join(f'{k} {v}' for k, v in sorted(malformed.items()))})"
              " —— 报告第 9 节只报它真能回放的那几个 run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
