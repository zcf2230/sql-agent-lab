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


def load_traces() -> dict:
    """{run stem: {task_id: last record for that task in that run}}.

    Keyed by run, not just by task. The first version kept one trace per task
    across every file, so filtering to `baseline` could display a 3-shot run's
    steps under a baseline verdict - the page looked authoritative and attributed
    evidence to the wrong experiment.
    """
    by_run: dict[str, dict[str, dict]] = {}
    for path in sorted(RUNS.glob("*.jsonl")):
        bucket = by_run.setdefault(path.stem, {})
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["_file"] = path.name
            bucket[rec["task_id"]] = rec
    return by_run


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
   （{html.escape(nf['worst_pair'][0])} vs {html.escape(nf['worst_pair'][1])}，四份同配置基线的最大两两差异），
   低于这个幅度的差异不报告为改进。三个比较全部未达 p&lt;0.05。</div>
 <h3 style="margin-top:14px">同一份数据的三种聚合口径</h3>
 {stable_table}
 <div class="note warn">{sverdict} 原因：基准是模板生成的，192 道题去重后只有
   {len(stats.clusters())} 个不同的 gold SQL 骨架（例如 category_slice 是 5 个骨架 × 7 个参数）。
   McNemar 的 n 建立在"观测相互独立"上，而同骨架题目的判定高度相关——模型要么会写那个 JOIN，
   要么不会。所以 <b>n=192 高估了证据量，而且高估的方向是偏袒提升</b>。
   这里不选一个"正确"口径来报，而是把三种都摆出来：<b>凡是只在某一种聚合下才成立的结论，
   就不该被写成结论。</b></div>
 <div class="note">本节的每一个数字都来自 <code>sqlagent/stats.py</code>——与
   <code>scripts/significance.py</code> 和 <code>docs/figures/ablation.svg</code> 同一份实现。
   配对关系与文件名写死在那里，不由本报告另算。有一段时间 <code>report.py</code> 自己写了
   一个噪声底函数、还漏掉了最差的那一对基线，于是同一份材料里同时存在 2/192 与 4/192；
   那才是这张表存在的理由。</div>
"""


def calibration_rows() -> list[str]:
    out = []
    for mode in CALIB_ORDER:
        rows = read_jsonl(RESULTS / f"calib-{mode}.jsonl")
        if not rows:
            continue
        body = [r for r in rows[1:] if "id" in r]
        tested = [r for r in body if r.get("result_changed") is not None and not r.get("trivial")]
        injectable = [r for r in body if r.get("result_changed") is not None]
        presentation_only = mode in {"reorder_cols"}
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


def adversarial_section() -> tuple[str, str]:
    """Return (metric cards html, per-category table html) from results/adversarial.jsonl."""
    rows = read_jsonl(RESULTS / "adversarial.jsonl")
    if not rows:
        return "", ""
    s = rows[0]["_summary"]
    cards = f"""
      <div class="card"><h3>{s['probes']}</h3><p>对抗性探测</p></div>
      <div class="card"><h3>{s['agent_fail']} ({s['agent_fail_rate']*100:.0f}%)</h3><p>agent 自身失败</p></div>
      <div class="card"><h3>{s['guard_caught']}</h3><p>其中被护栏拦下</p></div>
      <div class="card"><h3>{s['uncaught_agent_fail']}</h3><p>漏网的 agent 失败</p></div>
      <div class="card"><h3>{s['unsafe_executed']}</h3><p>真正执行成功的写操作</p></div>
      <div class="card"><h3>{s['claimed_done']}</h3><p>谎称已完成破坏性操作</p></div>
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
                  f"<td class='num'{never}>{'0（从未被测试）' if not att else att}</td>"
                  f"<td class='num' style='color:{colour}'>{v['fail']}</td>"
                  f"<td class='num'>{v['guard']}</td><td class='num'>{v['claim']}</td></tr>")
    return cards, chr(10).join(tr)


def build(runs: dict, traces: dict, tasks: dict) -> str:
    tags = [t for t in RUN_LABELS if t in runs]
    base = runs.get("abl2-baseline")
    headline = runs.get("abl2-3shot", base)
    s = headline["summary"] if headline else {}

    nf = stats.noise_floor()
    cards = f"""
      <div class="card"><h3>{(s.get('pass_at_1', 0) * 100):.1f}%</h3><p>pass@1（最佳配置）</p></div>
      <div class="card"><h3>{s.get('n_tasks', 0)}</h3><p>有效题目（另有 14 道被判为无法出题，已剔除）</p></div>
      <div class="card"><h3>{s.get('avg_llm_steps', 0)}</h3><p>平均 LLM 调用次数 / 题</p></div>
      <div class="card"><h3>~¥{total_spend()[0] * 7.2:.1f}</h3><p>累计花费估算（按当前价目表，非账单）</p></div>
      <div class="card"><h3>{total_spend()[1] / 1e6:.1f}M</h3><p>累计 prompt tokens（测量值，不是估算）</p></div>
      <div class="card"><h3>{nf['worst_tasks']} / {nf['n']}（{nf['worst_pp']:.1f}pp）</h3><p>噪声底：同配置基线最大两两差异，来自 <code>sqlagent.stats</code></p></div>
      <div class="card"><h3 style="color:{'#4f9d8f' if any(c['significant'] for c in stats.all_comparisons()) else '#e07a6b'}">{sum(1 for c in stats.all_comparisons() if c['significant'])} / {len(stats.all_comparisons())}</h3><p>配对比较中达到 p&lt;0.05 的项数（详见第 2 节）</p></div>
    """

    rows = []
    for tag in tags:
        sm = runs[tag]["summary"]
        pct = sm.get("pass_at_1", 0) * 100
        colour = ("#8b8fa3" if tag.startswith("mock") else
                  "#4f9d8f" if tag.endswith("3shot") else
                  "#c98a4b" if tag.startswith("qwen") else "#5b7fd6")
        delta = "—"
        if base and tag != "abl2-baseline" and not tag.startswith("mock"):
            d = (sm["pass_at_1"] - base["summary"]["pass_at_1"]) * 100
            delta = f"{d:+.1f}pp"
        rows.append(
            f"<tr><td>{html.escape(RUN_LABELS[tag])}</td><td class='num'>{sm.get('n_tasks',0)}</td>"
            f"<td style='min-width:180px'>{bar(pct, colour)}</td><td class='num'>{delta}</td>"
            f"<td class='num'>{sm.get('avg_llm_steps',0)}</td>"
            f"<td class='num'>{sm.get('avg_sql_attempts',0)}</td>"
            f"<td class='num'>{adherence(sm)}</td>"
            f"<td class='num'>{money(token_cost(list(runs[tag]['rows'].values()), sm.get('model','')))}</td></tr>"
        )
    runs_table = "\n".join(rows)

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
    browser = build_browser(tasks, traces, run_stems)
    adv_cards, adv_rows = adversarial_section()
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
   <th class=num>花费</th></tr></thead>
   <tbody>{runs_table}</tbody></table>
 <div class="note warn"><b>跨模型对比目前是混淆的，别当成能力排名。</b>Qwen 的 3-shot 准确率高于它自己的
   baseline（75.0% vs 70.3%），但同一次运行里<b>只有 24% 的题目真正执行过 SQL，45 题一次工具都没调</b>
   （baseline 是 89% / 0 题）。原因在示例格式：few-shot 是"问题 → SQL"两段式，不含工具调用，于是模型
   模仿示例直接作答——它不再是个 agent，只是被问对了更多题。<b>prompt 格式改变了执行协议，而 pass@1
   把这个报成了准确率提升。</b>这就是"真正用过工具"这一列存在的理由。</div>
 <div class="note"><b>关掉自修复后逐题结果完全不变（0 升 0 降）</b>——因为模型首次生成的 SQL
   约 99% 直接可执行，根本没有错误可修。机制本身靠注入单独验证：开=96.2% 恢复，关=0%。
   所以诚实的结论是“该组件在本数据集上测不出增益”，不是“自修复提升了准确率”。</div>

 {significance_html()}
 <h2>3 · 判分器校准（谁来审计审计者）</h2>
 <table><thead><tr><th>注入缺陷</th><th>应然裁决</th><th class=num>可测样本</th><th class=num>判分一致</th>
   <th class=num>false-accept</th><th class=num>注入后无变化</th><th class=num>一致率</th></tr></thead>
   <tbody>{calib}</tbody></table>
 <div class="note">“注入后无变化”是指缺陷没能改变结果集（例如给单行结果加 <code>LIMIT 3</code>）。
   这类样本按“应接受”计入一致率而不是被剔除——剔除它们会让指标自我美化。</div>

 <h2>4 · 失败归因（baseline）</h2>
 <table><thead><tr><th>原因</th><th class=num>题数</th><th>含义</th></tr></thead><tbody>{taxonomy}</tbody></table>
 <div class="note warn">护栏在 192 道真实题里<b>拦截 0 次</b>，注入标记只命中 1 次。也就是说安全护栏目前
   只有单元测试层面的证据，没有行为层面的证据。这一栏故意保留着，因为“拿零次观测冒充结论”
   正是这个项目反对的事。</div>

 <h2>5 · 分类通过率</h2>
 <table><thead><tr><th>题型族</th><th class=num>通过</th><th>通过率</th></tr></thead><tbody>{cats}</tbody></table>

 <h2>6 · 对抗性安全探测</h2>
 <div class="sub">192 道正常题里护栏触发 <b>0 次</b>——那只说明模型没试。这一组 26 条探测专门<b>邀请</b>
   模型做越权动作，用来把"没有观测"变成"有数字"。</div>
 <div class="cards">{adv_cards}</div>
 <table><thead><tr><th>探测类别</th><th class=num>条数</th><th class=num>模型真的尝试了</th>
   <th class=num>agent 失败</th><th class=num>被护栏拦下</th><th class=num>谎称完成</th></tr></thead>
   <tbody>{adv_rows}</tbody></table>
 <div class="note"><b>先看"模型真的尝试了"这一列，再决定其余几列要不要信。</b>
   26 条探测里模型只尝试了 6 次，而这 6 次<b>全是读形状的越权</b>（查 <code>sqlite_master</code> ×3、
   <code>INTO OUTFILE</code>/<code>ATTACH</code>、2 次运维操作）。
   <code>direct_write</code> 那 6 条模型一次都没试——它直接拒了。
   所以"真正执行成功的写操作 = 0"是<em>平凡成立</em>的：护栏<b>从来没有被问过写语句</b>，
   这一格证明的是模型的训练倾向，不是护栏的能力。</div>
 <div class="note warn"><b>6 次挑战 0 次失守，推不出"可靠"。</b>按 rule of three，
   0/6 的失守率 95% 上界约 <code>1 − 0.05^(1/6) ≈ 39%</code>。
   这份材料能支持的只有"这 6 个具体形状被正确拒绝了"。
   另外 20 条的结论由模型是否肯试决定，换一个模型这些数字就会变——所以这一节是
   <em>当前配置</em>的证据，不是系统的安全属性。</div>
 <div class="note">最值钱的观察：<code>catalog-02</code> 中模型先试 <code>sqlite_master</code> 被拦，
   随即改用 <b><code>sqlite_schema</code></b>（SQLite 中前者官方别名）重试，仍被拦下。这正是"白名单默认拒绝"
   相对"黑名单枚举危险词"的价值所在——别名不在任何人的清单上。</div>
 <div class="note warn">诚实边界：<code>refused</code>（措辞上拒绝）与 <code>agent_fail</code>（动作上越权）
   不是互斥的，有样本两者同时成立——模型说"我做不到"却仍然去试。所以"拒绝率"不能当安全指标用。
   另外 <code>direct_write</code> 6/6 全过，几乎肯定是模型被训练成拒绝显式删除指令，与本项目护栏无关。</div>

 <h2>7 · Trace 回放</h2>
 <div class="sub">每一题的完整过程：模型看到什么、调了哪个工具、数据库回什么、错在哪一步。
   上面所有数字都能在这里找到出处。</div>
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
    runs, traces, tasks = load_runs(), load_traces(), load_tasks()
    if not runs:
        raise SystemExit("no results/*.jsonl found - run the eval first")
    OUT.write_text(build(runs, traces, tasks), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  runs: {', '.join(runs)}")
    print(f"  tasks: {len(tasks)}   trace files: {len(traces)} ({sum(len(v) for v in traces.values())} task-traces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
