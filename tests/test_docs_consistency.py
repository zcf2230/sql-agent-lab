"""Assertions about the documents themselves.

The third review's central finding was not any single number: it was that a claim gets
corrected in one place and left standing in four others, and that the places left
standing are disproportionately the ones a reader reaches first - `HANDOFF.md` §0 is
the 30-second summary, `report.html` is the artifact a recruiter is told to open, and
both still carried a ratio that three rounds of review had each ordered deleted.

That failure cannot be fixed by more care, because the person who forgets is the same
person who wrote the sentence. It can only be fixed by a check that reads the carriers.

So this module treats the documents as data: withdrawn claims must not appear in the
sections that state conclusions, and no document may pin a count that the codebase
itself can change.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDOFF = (ROOT / "docs" / "HANDOFF.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RESUME = (ROOT / "docs" / "RESUME.md").read_text(encoding="utf-8")
ARTICLE = (ROOT / "docs" / "ARTICLE.md").read_text(encoding="utf-8")
REPORT = (ROOT / "report.html").read_text(encoding="utf-8", errors="replace")


def handoff_carriers() -> str:
    """§0 through §12: the parts that state current conclusions.

    §13 and §15 are the review-response record. A withdrawn claim *belongs* there -
    that is where "we used to say this and stopped" lives - so scanning the whole file
    would either be impossible to satisfy or would delete the audit trail.
    """
    cut = HANDOFF.find("## 13.")
    assert cut > 0, "HANDOFF.md lost its §13 anchor; the carrier boundary is now undefined"
    return HANDOFF[:cut]


# Retired by review, each with the round that retired it. If any of these reappears in a
# conclusion-carrying section, the fix was local and the rest of the material drifted.
WITHDRAWN = [
    ("越权尝试率 23%", "第一轮：n=26 太小，比率不可写（第二轮、第三轮各自重申过一次）"),
    ("6 (23%)", "同上 - 报告卡片曾把它算成 agent_fail_rate 直接印出来"),
    ("没有任何一项达到 p<0.05", "第三轮：该二分依赖聚合口径，骨架下 p 跨过 0.05"),
    ("改写只部分缓解", "第三轮：改写鲁棒性一次都没测，'部分缓解'是在描述没做过的实验"),
    ("仍然没有答案", "§17 补测成功侧后撤回：那是成功侧还没测完时的说法，现在两侧都有数"),
    # Fourth review, F8: a prohibition that quotes the banned string verbatim turns the
    # carrier list itself into a carrier. These are the exact strings, so the sites that
    # used to restate them as warnings now have to describe them instead.
    ("全被拦下", "§0 第一屏的压缩形式，把信用记给了从未被写路径挑战过的护栏"),
    ("全部拦下", "第一轮撤回过一次，§0 换了个近义词活着 - 差一个字接不住等于没设"),
    ("拦截率 100%", "第一版 README 的原句，被禁之后仍出现在别处的引文里"),
    ("100% 拦截", "同上，另一个词序"),
    ("混进准确率而无人察觉", "第四轮 F6：语法上只承诺'有人察觉'，听感上承诺了'没混进'"),
    ("四份同配置基线", "第四轮 F7：噪声底集合与 COMPARISONS 注释互斥，现为三份"),
    # The same defect in the English carrier: the withdrawn-needle list was all Chinese,
    # so README kept saying "Four ... six pairwise" long after stats.BASELINES held three.
    ("Four nominally identical", "第四轮 F7 的英文载体，第五轮 F2 抓到"),
    ("six pairwise", "同上：三对，不是六对"),
    ("2/192 道题的判定翻了", "§15★1 已撤回的乐观值，第五轮 F2 发现它活在已发布的文章里"),
    ("7 个单题簇", "第四轮 F1：实测 8，且 §16.4 曾用一句假解释替它开脱"),
    ("而非模型散文中的 SQL", "第一轮：agent.py 存在 _sql_from_prose 回退，全称否定句碰上一个反例"),
    # §20 ran the agent on BIRD, so the sentence that used to bound the claim is now false
    # wherever it appears outside the record of it having been superseded.
    ("没有跑过 agent 做 BIRD", "§20 跑了 60 题并给出 41.7%；这句话只在「它曾经成立」的引文里合法"),
]


def test_withdrawn_claims_do_not_appear_where_conclusions_are_stated():
    offenders = []
    # ARTICLE is the carrier with the widest audience and, until now, the only one not
    # scanned: the withdrawn 23% ratio lived in its title through three rounds of review
    # that each added a new forbidden string to this list.
    for needle, why in WITHDRAWN:
        for name, text in [("HANDOFF §0-§12", handoff_carriers()), ("README", README),
                           ("RESUME", RESUME), ("ARTICLE", ARTICLE), ("report.html", REPORT)]:
            if needle in text:
                offenders.append(f"{name} 仍含 {needle!r}（撤回理由：{why}）")
    assert not offenders, chr(10).join(offenders)


def test_transcribed_statistics_in_the_docs_equal_the_function_that_computes_them():
    """A withdrawn claim is easy to catch - it is a fixed string. A number that is
    *correct but copied* is harder: nothing goes red when the computation moves. The
    fourth review found exactly this in the resume's own "加分句" ("7 个单题簇",
    measured 8), and the correct value had been in §16.4 all along.

    So every cluster-count sentence still standing in a conclusion-carrying document is
    checked against `stats.cluster_ratio_tests()`, and §13 onward is exempt because that
    is where superseded numbers are supposed to remain, quoted and labelled."""
    from sqlagent import stats

    want = stats.cluster_ratio_tests("abl2-baseline", "abl2-3shot")["singleton_clusters"]
    offenders = []
    for name, text in [("HANDOFF §0-§12", handoff_carriers()), ("RESUME", RESUME),
                       ("README", README)]:
        for m in re.finditer(r"(\d+) 个(?:是)?单题簇", text):
            if int(m.group(1)) != want:
                line = text[:m.start()].count(chr(10)) + 1
                offenders.append(f"{name}:{line} 写 {m.group(1)}，实测 {want}")
    assert not offenders, "文档里的簇计数与 stats 不一致：" + chr(10).join(offenders)


def test_the_bird_figures_in_the_docs_equal_the_artifact_that_measured_them():
    """Same failure, new experiment: a draft of this material pinned `rejudge.py`'s
    coverage as "12 runs / 1344 + 328", while the script reports 15 / 2112 / 328. Copied
    numbers never go red on their own, so the external-benchmark figures are parsed out of
    the documents here and compared against `results/bird-judge.jsonl`."""
    import json

    path = ROOT / "results" / "bird-judge.jsonl"
    if not path.exists():
        import pytest
        pytest.skip("results/bird-judge.jsonl missing; run `python scripts/bird_judge.py`")
    s = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["_summary"]
    per = s["per_mode"]

    offenders = []
    # the §19 per-mode table: `| `mode` 描述 | 可比观测 | ...`
    for mode, cell in re.findall(r"\|\s*`(\w+)`[^|\n]*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|", HANDOFF):
        if mode in per and int(cell) != per[mode]["checked"]:
            line = HANDOFF[:HANDOFF.find(f"`{mode}`")].count(chr(10)) + 1
            offenders.append(f"HANDOFF:{line} {mode} 可比观测写 {cell}，产物 {per[mode]['checked']}")
    for needle, what in [(f"{s['gold_self_correct']}/{s['questions_selected']}", "gold 自洽"),
                         (str(per["drop_distinct"]["mine_strict"]), "公开口径看不见的重复行条数"),
                         (str(s["checked"]), "注入观测总数")]:
        if needle not in HANDOFF:
            offenders.append(f"HANDOFF 里没有 {needle!r}（{what}）")
    for stale in ("1344", "1,344", "1672", "1,672", "12 次运行"):
        for name, text in _bird_carriers():
            if stale in text:
                offenders.append(f"{name} 仍写着 {stale!r}——那是手抄的覆盖数，"
                                 f"以 `python scripts/rejudge.py` 自己打印的为准")
    assert not offenders, chr(10).join(offenders)


def test_the_agent_bird_numbers_in_the_docs_equal_the_run_that_produced_them():
    """The first screen, the README limits list and the resume all now state 41.7% next to
    89.1%, the gap between them, and the "0 disagreements" control. Every one of those is a
    copy of `results/bird-agent*.jsonl`; the same rule that caught 0.023 in three carriers
    applies to a number that has never been written down before today."""
    import json

    from sqlagent import stats

    def summary(name):
        path = ROOT / "results" / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8").splitlines()[0])["_summary"]

    run, both = summary("bird-agent.jsonl"), summary("bird-agent-official.jsonl")
    if not run or not both:
        import pytest
        pytest.skip("no agent-on-BIRD artifacts; see scripts/bird_tasks.py")
    n, mine, off = both["judged"], both["mine_pass"], both["official_pass"]
    base = stats.run_summary("abl2-baseline")["pass_at_1"]
    agent = mine / n
    lo, hi = stats.wilson(mine, n)
    needles = {f"{agent:.1%}": "BIRD pass@1", f"{base:.1%}": "自制 pass@1",
               f"{(base - agent) * 100:.1f}pp": "两个数的差",
               f"${run['total_cost_usd']:.4f}": "本轮真实花费"}
    offenders = []
    # HANDOFF and README are evidence documents: every derived figure belongs in them.
    # The resume is not - the fifth review's readability finding was precisely that its
    # BIRD bullet carried twelve numbers, and the reason nobody had trimmed it is that
    # this test demanded presence. So the resume is checked for the two headline rates
    # and for staleness, not for the whole needle set.
    for name, text in [("HANDOFF", HANDOFF), ("README", README)]:
        for needle, what in needles.items():
            if needle not in text:
                offenders.append(f"{name} 缺 {needle!r}（{what}）")
        if f"{lo:.1%}" not in text and f"{lo:.0%}" not in text:
            offenders.append(f"{name} 没有 BIRD 区间的下界 {lo:.1%}")
    for needle in (f"{agent:.1%}", f"{base:.1%}"):
        if needle not in RESUME:
            offenders.append(f"RESUME 缺 {needle}（{needles[needle]}）")
    if mine != off:
        offenders.append("两套口径判定不同，文档里的「0 分歧」这句必须改写")
    assert not offenders, "agent-on-BIRD 的数字与产物不一致：" + chr(10).join(offenders)


def _bird_carriers():
    """Documents that state the external-benchmark numbers in prose.

    `report.html` is in here only as its section-5 slice: the file also embeds every
    per-task trace, and a four-digit token count in somebody's step 3 is not a claim
    about re-judging coverage.
    """
    start, end = REPORT.find("5 · 判分器在别人"), REPORT.find("6 · 失败归因")
    section = REPORT[start:end] if 0 <= start < end else ""
    article = (ROOT / "docs" / "ARTICLE.md").read_text(encoding="utf-8")
    interview = (ROOT / "docs" / "INTERVIEW.md").read_text(encoding="utf-8")
    return [("HANDOFF", HANDOFF), ("README", README), ("RESUME", RESUME),
            ("ARTICLE", article), ("INTERVIEW", interview), ("report.html §5", section)]


def test_the_aggregation_p_range_in_prose_is_the_one_stats_computes():
    """Three carriers said "按骨架聚合后 p 在 0.023–0.227". The upper end is right and the
    lower end is not: the three defensible aggregations give 0.0574 / 0.2266 / 0.0391, and
    0.023 is a *different* statistic (cluster-level Wilcoxon with continuity correction)
    that someone copied into the wrong sentence. One of the three sites is the interview
    line in the resume, which is the hardest place in the repo to notice drift."""
    from sqlagent import stats

    ps = [r["p_value"] for r in stats.cluster_sensitivity("abl2-baseline", "abl2-3shot")]
    want = f"{min(ps):.3f}–{max(ps):.3f}"
    offenders = []
    for name, text in [("HANDOFF", HANDOFF), ("RESUME", RESUME), ("README", README),
                       ("REVIEW_TEMPLATE", (ROOT / "docs" / "REVIEW_TEMPLATE.md").read_text(encoding="utf-8"))]:
        for m in re.finditer(r"p 在 \*{0,2}([\d.]+)–([\d.]+)\*{0,2} 之间跨过 0\.05", text):
            if f"{m.group(1)}–{m.group(2)}" != want:
                line = text[:m.start()].count(chr(10)) + 1
                offenders.append(f"{name}:{line} 写 {m.group(1)}–{m.group(2)}，实测 {want}")
    assert not offenders, "聚合口径的 p 区间与 stats 不一致：" + chr(10).join(offenders)


def test_the_mistake_log_stays_numbered_in_order():
    """§9 promises "完整" and tells a reviewer to count the rows and check the numbering is
    continuous. That promise is only worth what a test makes it worth: appending a row
    against the wrong anchor produced 34, 35, 37, 36 - rows that exist but read as if
    they were missing."""
    rows = [int(m.group(1)) for m in re.finditer(r"^([0-9]+) \|", HANDOFF, re.M)]
    assert rows, "no mistake-log rows found; the anchor of this test moved"
    assert rows == list(range(1, len(rows) + 1)), f"§9 行号不连续或顺序错：{rows}"


def test_no_document_pins_the_test_count():
    """Every place that wrote "N passed" has been wrong at least once, and the count
    moves on the same day someone adds a test - which is the action this project takes
    most often. The check is `pytest` is green, not a number copied into prose."""
    pinned = []
    for name, text in [("README.md", README), ("RESUME.md", RESUME), ("HANDOFF.md", HANDOFF)]:
        for m in re.finditer(r"(\d{2,4}) (?:个)?(?:单元测试|tests|passed)", text):
            line = text[:m.start()].count(chr(10)) + 1
            pinned.append(f"{name}:{line} -> {m.group(0)!r}")
    assert not pinned, "计数不要写进文档，让命令去数：" + chr(10).join(pinned)


def test_the_guard_is_measured_on_the_axis_the_model_never_touched():
    """`scripts/guard_corpus.py` is the answer to "the write path was never tested".
    It is $0 and deterministic, so there is no excuse for it not running in CI."""
    import os
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "guard_corpus.py")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "穿透率 0.00%" in proc.stdout and "误拒   0" in proc.stdout


def test_the_report_shows_the_statistics_the_resume_claims_are_the_strongest_line():
    """Round two found the report contained no p value at all while the resume called
    "we report p=0.057 as not significant" its best line. Re-checked here because the
    report is regenerated by a script and can regress without any test failing."""
    for needle in ["0.0574", "McNemar", "Wilson", "未达显著", "bootstrap"]:
        assert needle in REPORT, f"report.html 不含 {needle!r}"


# One optional bold marker around a cell: the security table bolds the zeros it wants a
# reader to notice, and the total row bolds everything.
_CELL = r"\s*\*{0,2}(\d+)\*{0,2}\s*\|"
_ROW = re.compile(r"^\|\s*\*{0,2}([a-z_]+)\*{0,2}\s*\|" + _CELL * 6 + r"\s*$", re.M)


def _security_section() -> str:
    start, end = README.find("## Security posture"), README.find("## Credentials")
    assert 0 <= start < end, "README 的安全节锚点不见了，下面的核对会退化成扫全文"
    return README[start:end]


def test_the_readme_security_table_is_the_adversarial_artifact_it_claims():
    """Fifth review, F1 - the one that mattered. The README table was still the 26-probe
    run (total 26, 6 attempts, 0 claims) while the sentence *above* it said 123 probes and
    the sentence *below* said 98 of 123. A table with a `total` row reads as complete, so
    anyone who read only the table got a different project than anyone who read the prose,
    and no test looked at the table at all.

    Every cell is now compared against `results/adversarial.jsonl`'s own `_summary`;
    `test_adversarial.py` separately proves that summary follows from the stored answers."""
    import json

    path = ROOT / "results" / "adversarial.jsonl"
    if not path.exists():
        import pytest
        pytest.skip("no adversarial run on disk; `python -m sqlagent.adversarial` needs a key")
    s = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["_summary"]
    sec = _security_section()

    seen = {m.group(1): [int(x) for x in m.groups()[1:]] for m in _ROW.finditer(sec)}
    assert "total" in seen, "README 安全表没有 total 行了"
    per_category = {k: v for k, v in seen.items() if k != "total"}
    want = {c: [v["n"], v["attempted"], v["fail"], v["guard"], v["executed"], v["claim"]]
            for c, v in s["by_category"].items()}
    offenders = []
    if set(per_category) != set(want):
        offenders.append(f"表里的类别与产物不一致：只在一边 = {set(per_category) ^ set(want)}")
    for c in set(per_category) & set(want):
        if per_category[c] != want[c]:
            offenders.append(f"{c}: 表 {per_category[c]} vs 产物 {want[c]}")
    total = [s["probes"], s["unsafe_attempted"], s["agent_fail"], s["guard_caught"],
             s["unsafe_executed"], s["claimed_done"]]
    if seen.get("total") != total:
        offenders.append(f"total 行 {seen.get('total')} vs 产物 {total}")
    # the columns the table cannot show but the prose must not contradict
    for needle, what in [(f"{s['write_shapes']} ", "写形状条数"),
                         (f"{s['probes']} probes", "探测总数"),
                         (f"{s['probes'] - s['unsafe_attempted']} of {s['probes']}",
                          "模型没动手的比例")]:
        if needle not in sec:
            offenders.append(f"安全节里没有 {needle!r}（{what}）")
    assert not offenders, "README 安全表与产物不一致：" + chr(10).join(offenders)


def test_the_noise_floor_prose_matches_the_baselines_stats_declares():
    """Fifth review, F2. `stats.BASELINES` holds three runs, so the floor has three pairs -
    yet README said "Four nominally identical ... six pairwise", and the published article
    carried `2/192`, the exact value §15★1 withdrew two rounds ago. The Chinese needle list
    caught the first and missed the second because README is written in English: a
    withdrawn-claim check that only speaks one language protects one carrier."""
    from sqlagent import stats

    floor = stats.noise_floor()
    n_runs, pairs = len(stats.BASELINES), len(floor["pairs"])
    lo = min(d for d, _, _ in floor["pairs"])
    hi = floor["worst_tasks"]
    offenders = []
    if "Three same-config baseline runs" not in README:
        offenders.append(f"README 没写 {n_runs} 份基线（stats.BASELINES 现在是 {n_runs} 个）")
    if f"{pairs} pairwise" not in README or f"span {lo} to {hi} of {floor['n']} tasks" not in README:
        offenders.append(f"README 的对数或跨度与实测不符：应为 {pairs} 对、{lo}–{hi} 题")
    if f"{hi}/192" not in ARTICLE or f"{lo} 题" not in ARTICLE:
        offenders.append(f"ARTICLE 的噪声底跨度应为 {lo}–{hi} 题（最差 {hi}/192）")
    if f"{hi} 题 = {floor['worst_pp']:.1f}pp" not in HANDOFF:
        offenders.append(f"HANDOFF §4.2 的示例输出应为 {hi} 题 = {floor['worst_pp']:.1f}pp")
    for name, text in [("README", README), ("ARTICLE", ARTICLE), ("RESUME", RESUME)]:
        for stale in ("2/192 道题的判定翻了", "Four nominally identical", "six pairwise"):
            if stale in text:
                offenders.append(f"{name} 仍写着 {stale!r}")
    assert not offenders, "噪声底散文与 stats 不一致：" + chr(10).join(offenders)


def test_the_column_policy_decomposition_is_the_artifact_not_a_sentence_about_it():
    """§26 is a number that did not exist before the fifth review, and it is the kind of
    number that drifts quietly: `55.0%` is a *control* policy, and the moment a reader (or
    a later me) mistakes it for a score the whole section inverts. So the report block,
    §5 and §26 are all compared against `results/bird-gap.jsonl`, and the two guards that
    keep it a superset are asserted here rather than trusted."""
    import json

    from sqlagent import report

    path = ROOT / "results" / "bird-gap.jsonl"
    if not path.exists():
        import pytest
        pytest.skip("no results/bird-gap.jsonl; run `python scripts/bird_gap.py`")
    g = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["_summary"]
    n, strict, lenient = g["n"], g["strict_pass"], g["lenient_pass"]
    assert n and strict <= lenient, "a relaxation that scores lower is a bug, not a finding"
    assert strict + g["closed_by_relaxing_columns"] == lenient, "the two policies do not add up"
    assert sum(g["still_wrong_reasons"].values()) == g["still_wrong"]

    want = {f"{strict / n:.1%}", f"{lenient / n:.1%}", str(g["closed_by_relaxing_columns"])}
    offenders = []
    html = report.agent_on_bird_html()
    for needle in want:
        if needle not in html:
            offenders.append(f"report.html 的 BIRD 节缺 {needle!r}")
    if "不是成绩，是对照" not in html and "对照政策" not in html:
        offenders.append("报告里这段没有把 55.0% 标成对照政策，读者会当成绩")
    sec26 = HANDOFF[HANDOFF.find("## 26."):]
    assert sec26, "HANDOFF 丢了 §26 锚点"
    for needle in want:
        if needle not in sec26:
            offenders.append(f"HANDOFF §26 缺 {needle!r}")
    if f"{lenient / n:.1%}" not in HANDOFF[:HANDOFF.find("## 25.")]:
        offenders.append("§5 的主张→证据映射行没跟着 §26：它承诺「量过了」就得带上那个数")
    assert not offenders, "列政策分解的引用与产物不一致：" + chr(10).join(offenders)


def test_the_quoted_probe_seed_output_is_what_the_command_prints():
    """§4.2 quotes a command's expected output, and the quote said `wrote 26 probes` after
    the set had grown to 123. Expected-output blocks are the prose most likely to be right
    and least likely to be re-read, so this pins it to the generator itself ($0, no key)."""
    from sqlagent.adversarial import probes

    n = len(list(probes()))
    assert f"wrote {n} probes" in HANDOFF, (
        f"`--seed-tasks` 现在写 {n} 条，§4.2 的示例输出没跟着改")
    assert f"| **total** | **{n}** |" in _security_section(), "README 安全表的 total 行数没跟着改"
