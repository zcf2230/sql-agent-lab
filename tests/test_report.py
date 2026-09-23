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
