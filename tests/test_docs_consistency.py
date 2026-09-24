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
    ("7 个单题簇", "第四轮 F1：实测 8，且 §16.4 曾用一句假解释替它开脱"),
    ("而非模型散文中的 SQL", "第一轮：agent.py 存在 _sql_from_prose 回退，全称否定句碰上一个反例"),
]


def test_withdrawn_claims_do_not_appear_where_conclusions_are_stated():
    offenders = []
    for needle, why in WITHDRAWN:
        for name, text in [("HANDOFF §0-§12", handoff_carriers()), ("README", README),
                           ("RESUME", RESUME), ("report.html", REPORT)]:
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
