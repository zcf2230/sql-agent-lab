"""Cost reporting tests.

A stale price table once made this project report ¥23 for ¥10 of real spend. The
lesson is encoded here rather than in a commit message: a derived figure must not
be stored and then displayed as though it were an observation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sqlagent import report, stats
from sqlagent.config import PRICING
from sqlagent.report import token_cost

REPORT_HTML_PATH = Path(__file__).resolve().parent.parent / "report.html"
REPORT_HTML = REPORT_HTML_PATH.read_text(encoding="utf-8") if REPORT_HTML_PATH.exists() else ""


def rows(n_in: int, n_out: int, stored: float) -> list[dict]:
    return [
        {"id": "a", "stats": {"prompt_tokens": n_in // 2, "completion_tokens": n_out // 2}},
        {"id": "b", "stats": {"prompt_tokens": n_in - n_in // 2, "completion_tokens": n_out - n_out // 2},
         "total_cost_usd": stored},
    ]


def test_cost_is_computed_from_tokens_not_from_the_stored_figure():
    pin, pout = PRICING["deepseek-chat"]
    got = token_cost(rows(1_000_000, 100_000, stored=999.0), "deepseek-chat")
    assert abs(got - (1_000_000 * pin + 100_000 * pout) / 1e6) < 1e-9
    assert got != 999.0, "a frozen cost figure leaked into the display"


def test_an_unpriced_model_is_unknown_not_free():
    """None, not 0.0. A missing price rendered as "$0.00" reads as a real saving."""
    assert token_cost(rows(500_000, 10_000, stored=5.0), "some-model-not-in-the-table") is None


def test_price_table_is_usd_per_million_sized():
    """Guards the unit confusion that produced the 2.3x overstatement."""
    for model, (pin, pout) in PRICING.items():
        if pin == 0.0:
            continue
        assert 0.0 < pin < 10.0, f"{model}: input price looks like per-token, not per-million"
        assert 0.0 < pout < 40.0, f"{model}: output price looks like per-token, not per-million"


# --- the second review's D2/D3: the report disagreed with the statistics module -----


def test_report_does_not_own_a_second_noise_floor():
    """`report.py` used to compute its own, over a baseline set missing `baseline-v2`
    and with a different definition (union of flips vs max pairwise). Same name, two
    numbers: the report printed 2/192 while stats.py and the README said 4/192 = 2.1pp
    - and 2/192 was the value the first review had already called optimistic."""
    assert not hasattr(report, "noise_floor"), (
        "report.py must render sqlagent.stats.noise_floor(), not reimplement it"
    )
    import inspect
    assert "sqlagent.stats" in inspect.getsource(report) or "from . import stats" in inspect.getsource(report)


def test_generated_report_shows_the_significance_it_claims_to_be_honest_about():
    """RESUME.md calls "we report p=0.057 as not significant" the strongest line in the
    project, and HANDOFF §4.1 tells a busy reviewer to open report.html and nothing else.
    The generated file contained none of: 0.057, p=, Wilson, 95%, 2.1pp, McNemar. The
    discipline lived in prose and was absent from the artifact."""
    html = REPORT_HTML
    if not html:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    nf = stats.noise_floor()
    for needle in ["0.0574", "McNemar", "Wilson", "未达显著", f"{nf['worst_pp']:.1f}pp"]:
        assert needle in html, f"report.html does not contain {needle!r}"
    for c in stats.all_comparisons():
        assert f"{c['p_value']:.4f}" in html, f"p={c['p_value']:.4f} missing from report.html"


def test_report_shows_the_aggregation_sensitivity_not_just_one_p():
    """McNemar treats 192 template-generated tasks as 192 independent observations;
    they collapse to 100 gold-SQL skeletons. Under the three defensible aggregations
    p moves 0.039 -> 0.227 and crosses 0.05, so a single p is not a finding on its own."""
    if not REPORT_HTML:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    sens = stats.cluster_sensitivity("abl2-baseline", "abl2-3shot")
    assert len(sens) == 3
    for r in sens:
        assert f"{r['p_value']:.4f}" in REPORT_HTML, f"{r['label']} p missing"
    assert str(len(stats.clusters())) in REPORT_HTML, "the skeleton count should be stated"


def test_report_does_not_own_a_second_reliability_implementation():
    """The two-forbidden shape, again: one statistic computed twice. `report.py` may
    render `stats.restability_report`, never redo its arithmetic - the last time that
    happened the report printed a noise floor with a different definition."""
    import inspect

    assert not hasattr(report, "restability") and not hasattr(report, "restability_report"), \
        "report.py must render sqlagent.stats.restability_report(), not reimplement it"
    assert "stats.restability_report(" in inspect.getsource(report)


def test_generated_report_carries_the_wording_noise_instead_of_only_the_prose():
    """Every question on this page was asked one way, so the headline is a single draw
    from a set of surface forms. That limitation used to exist only in README and
    HANDOFF while the artifact a recruiter opens showed two decimals and no caveat -
    the same gap that made section 2 necessary."""
    if not REPORT_HTML:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    rep = stats.restability_report("correct-representative")
    fail = stats.restability_report("failures")
    lo, hi = rep["fragile_ci"]
    needles = ["同题重述稳定性", f"{rep['fragile_share']:.1%}", f"{lo:.1%}", f"{hi:.1%}",
               f"{rep['overstatement_pp_any']:.1f}pp", f"{rep['overstatement_pp_mean']:.1f}pp",
               f"{fail['understatement_pp']:.2f}pp"]
    for needle in needles:
        assert needle in REPORT_HTML, f"report.html does not contain {needle!r}"
    for p in stats.RESTABILITY_PICKS:
        assert p in REPORT_HTML, f"the {p} sample has no row in report.html"
    # the run that measured nothing is shown with its verdict, not quietly dropped
    assert "零区分力" in REPORT_HTML


def test_the_report_derives_its_prose_figures_rather_than_transcribing_them():
    """The fourth review found "7 个单题簇" in the report and in the resume sentence,
    while §16.4 of the same repository already said 8. It measured 8: every one of the
    eight single-task clusters is a ±100pp cluster, and the two sets coincide exactly.
    A transcribed count in a sentence is a count that goes stale silently, so every
    figure in those notes is now read out of stats / the run summaries and asserted
    against them here."""
    rt = stats.cluster_ratio_tests("abl2-baseline", "abl2-3shot")
    assert rt["singleton_clusters"] == rt["clusters_at_100pp"] == 8, rt
    assert f"{rt['singleton_clusters']} 个是单题簇" in REPORT_HTML
    assert "7 个单题簇" not in REPORT_HTML, "a transcribed 7 survived the fix"

    fail = stats.restability_report("failures")
    rep = stats.restability_report("correct-representative")
    for needle in (f"{fail['understatement_pp']:.2f}pp", f"{fail['understatement_pp_mean']:.2f}pp",
                   f"{rep['overstatement_pp_any']:.1f}pp", f"{rep['overstatement_pp_mean']:.1f}pp"):
        assert needle in REPORT_HTML, f"report.html does not carry {needle}"

    q3 = stats.run_summary("qwen-3shot")
    assert q3["n_no_sql_executed"] == 147
    assert "从未执行 SQL" in REPORT_HTML, "the count is disclosed in prose but not rendered"
    assert f"{q3['n_no_sql_executed']}" in REPORT_HTML
    # the interval caveat: the only CI shown is the ratio's, and the head's own width
    # has to be stated rather than silently treated as a constant.
    hlo, hhi = rep["headline_ci"]
    assert f"{hlo:.1%}–{hhi:.1%}" in REPORT_HTML
    assert "没有计入 head 自身的不确定度" in REPORT_HTML


def _bird_summary() -> dict:
    return (report.read_jsonl(report.RESULTS / "bird-judge.jsonl") or [{}])[0].get("_summary") or {}


def test_report_labels_every_defect_class_the_external_run_measured():
    """A new corruption mode in `bird-judge.jsonl` that this page does not render would
    silently shrink the evidence, which is the failure the whole section argues against."""
    per = _bird_summary().get("per_mode") or {}
    assert per, "no BIRD artifact; run `python scripts/bird_judge.py`"
    assert set(per) <= set(report.MODE_LABELS), sorted(set(per) - set(report.MODE_LABELS))


def test_generated_report_carries_the_external_benchmark_numbers_in_both_directions():
    """The claim is not "my judge is stricter than the public metric" - on the same data
    it is looser on column order and rounding. Both directions have to be on the page,
    with the artifact's own counts, or the section is marketing."""
    if not REPORT_HTML:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    s = _bird_summary()
    if not s:
        pytest.skip("results/bird-judge.jsonl missing; run `python scripts/bird_judge.py`")
    per, n_q = s["per_mode"], s["questions_selected"]
    for needle in ["公开基准 BIRD dev", f"{s['gold_self_correct']}/{n_q}",
                   f"{sum(d['policy_agree'] for d in per.values())}/{s['checked']}"]:
        assert needle in REPORT_HTML, f"report.html does not contain {needle!r}"
    for mode, d in per.items():
        assert f"{d['policy_agree']}/{d['checked']}" in REPORT_HTML, f"{mode} policy row missing"
        assert f"{d['public_agree'] / d['checked']:.1%}" in REPORT_HTML, f"{mode} public row missing"
    dd = per["drop_distinct"]
    assert f"{dd['mine_strict']} 个判成了正确" in REPORT_HTML, "the duplicate-blindness finding is not stated"
    assert str(per["reorder_cols"]["mine_lenient"]) in REPORT_HTML, "the lenient direction is missing"
    for disclaimer in ["顺序敏感性未被检验", "没有、也不该有", "evaluation_ex.py:20"]:
        assert disclaimer in REPORT_HTML, f"report.html drops {disclaimer!r}"
    for reason, count in (s.get("skipped") or {}).items():
        assert str(count) in REPORT_HTML, f"skip bucket {reason!r} not reported"


def test_the_external_section_degrades_to_a_command_not_a_blank_space(monkeypatch):
    """Every other artifact-backed block already behaves this way: absent data says how to
    produce it rather than disappearing, because a silently absent caveat reads as an
    absent limitation."""
    monkeypatch.setattr(report, "read_jsonl", lambda path: [])
    out = report.external_benchmark_html()
    assert "bird_judge.py" in out and "无内容可渲染" in out


def test_report_section_numbers_are_unique_and_contiguous():
    """Sections get renumbered whenever the report grows, and the cross-references in
    HANDOFF/README name them by number - a duplicated or skipped number makes a citation
    point at the wrong table."""
    import re

    nums = [int(m) for m in re.findall(r"<h2>(\d+) ·", REPORT_HTML)]
    assert nums == list(range(1, len(nums) + 1)), nums


def test_pass_cell_keeps_a_refusal_and_a_healthy_zero_apart():
    """The distinction the gate exists for, now testable without a whole report build:
    null in the data renders as a refusal, a nonzero score with harness exceptions says
    so next to the number, and a real 0.0 still renders as a bar."""
    from sqlagent.report import pass_cell

    refused = pass_cell({"pass_at_1": None, "harness_exceptions": 50})
    assert "拒绝输出" in refused[1] and "0.0%" not in refused[1]

    annotated = pass_cell({"pass_at_1": 0.9844, "harness_exceptions": 3})
    assert "含 3 次 harness 异常" in annotated[1]
    assert "计入分母" in annotated[1], "the reader must be told those three scored 0"

    clean = pass_cell({"pass_at_1": 0.8906, "harness_exceptions": 0})
    assert "harness" not in clean[1]
    real_zero = pass_cell({"pass_at_1": 0.0, "harness_exceptions": 0})
    assert "拒绝输出" not in real_zero[1], "a measured 0% is not a refusal"


def test_generated_report_shows_the_agent_on_bird_gap_and_its_control():
    """The 89% headline and the 41.7% external number have to sit on the same page, and so
    does the control that makes the second one meaningful: the same answers re-scored under
    the official rule. Without that control the gap is indistinguishable from the judge
    breaking again - which section 5 has already admitted happened twice."""
    if not REPORT_HTML:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    run = report.read_jsonl(report.RESULTS / "bird-agent.jsonl")
    both = report.read_jsonl(report.RESULTS / "bird-agent-official.jsonl")
    if not run or not both:
        pytest.skip("no agent-on-BIRD artifacts; see scripts/bird_tasks.py")
    a, o = run[0]["_summary"], both[0]["_summary"]
    n = o["judged"]
    base = stats.run_summary("abl2-baseline")["pass_at_1"]
    lo, hi = stats.wilson(o["mine_pass"], n)
    for needle in [f"{o['mine_pass'] / n:.1%}", f"{base:.1%}", f"{(base - o['mine_pass'] / n) * 100:.1f}pp",
                   f"[{lo:.1%}, {hi:.1%}]", f"我的判分器 {o['mine_pass']}/{n}",
                   f"公开口径 {o['official_pass']}/{n}", f"分歧 {o['official_blind'] + o['mine_stricter']} 条",
                   f"${a['total_cost_usd']:.4f}"]:
        assert needle in REPORT_HTML, f"report.html does not carry {needle!r}"
    for reason, count in a["failure_taxonomy"].items():
        assert f"<code>{reason}</code> {count} 题" in REPORT_HTML, f"{reason} row missing"
    assert "没有跑过 agent 做 BIRD 的题" not in REPORT_HTML, "the superseded claim survived the new run"


def test_generated_report_carries_the_causal_check_with_its_paired_counts():
    """§22 is the one place where a diagnosis becomes evidence, so the page that a
    recruiter opens has to show the paired counts and the p value - derived from the two
    stored arms, never retyped."""
    if not REPORT_HTML:
        pytest.skip("report.html not generated; run `python -m sqlagent.report`")
    hint = report.read_jsonl(report.RESULTS / "bird-agent-hint.jsonl")
    if not hint:
        pytest.skip("no bird-agent-hint.jsonl; run scripts/bird_hint.py")
    base = report.read_jsonl(report.RESULTS / "bird-agent.jsonl")
    h = hint[0]["_summary"]
    if not h.get("valid"):
        pytest.skip("the hint arm was an invalid run; no score to show")
    bv = {r["id"]: bool(r["correct"]) for r in base[1:] if r.get("id")}
    hv = {r["id"]: bool(r["correct"]) for r in hint[1:] if r.get("id")}
    fixed = sum(1 for k in hv if hv[k] and not bv.get(k, False))
    broken = sum(1 for k in hv if not hv[k] and bv.get(k, False))
    _, _, p = stats.mcnemar_exact(bv, hv)
    for needle in [f"{h['pass_at_1']:.1%}", f"修好 {fixed} 题、弄坏 {broken} 题",
                   f"p={p:.4f}", f"${h['total_cost_usd']:.4f}"]:
        assert needle in REPORT_HTML, f"report.html does not carry {needle!r}"
    # the contrast is the point: the same test said "no" on the self-built benchmark
    assert "0.0574" in REPORT_HTML and "未" in REPORT_HTML
