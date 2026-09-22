"""Statistics tests, anchored to values an external reviewer computed independently.

The reviewer re-derived these numbers from results/*.jsonl before this code existed,
so they are an oracle rather than something this module got to define for itself.
That matters: a stats module whose tests were written by the same author as the
module proves only self-consistency.
"""

from __future__ import annotations

import math

import pytest

from sqlagent import stats
from sqlagent.stats import mcnemar_exact, noise_floor, wilson


def fake(pairs):
    return dict(pairs)


def test_mcnemar_matches_the_reviewers_hand_calculation():
    # 11 fixed / 3 broken out of 192 paired tasks
    a, b = {}, {}
    for i in range(11):
        a[f"fix{i}"], b[f"fix{i}"] = False, True
    for i in range(3):
        a[f"brk{i}"], b[f"brk{i}"] = True, False
    for i in range(178):
        a[f"same{i}"], b[f"same{i}"] = True, True
    fixed, broken, p = mcnemar_exact(a, b)
    assert (fixed, broken) == (11, 3)
    assert abs(p - 0.0574) < 1e-3, "reviewer independently computed 0.0574"


def test_mcnemar_on_identical_runs_is_one():
    a = {"x": True, "y": False}
    assert mcnemar_exact(a, dict(a)) == (0, 0, 1.0)


def test_mcnemar_exact_not_normal_approximation():
    """1 vs 0 discordant pairs must be far from significant, which chi-square gets wrong."""
    _, _, p = mcnemar_exact({"a": False, "b": True}, {"a": True, "b": True})
    assert abs(p - 1.0) < 1e-9


def test_wilson_interval_matches_the_readme_quoted_ci():
    lo, hi = wilson(179, 192)
    assert abs(lo * 100 - 88.8) < 0.15 and abs(hi * 100 - 96.0) < 0.15


def test_wilson_dominates_the_wald_interval_near_the_boundary():
    """The normal approximation collapses at p=1.0; Wilson degrades honestly."""
    lo, hi = wilson(192, 192)
    assert lo > 0.94 and hi <= 1.0


def test_recorded_comparisons_are_reproducible():
    got = {c["label"]: (c["fixed"], c["broken"], round(c["p_value"], 4)) for c in stats.all_comparisons()}
    assert got["deepseek: 3-shot vs baseline"] == (11, 3, pytest.approx(0.0574))
    assert got["deepseek: self-repair OFF vs baseline"] == (0, 0, 1.0)
    assert got["qwen-flash: 3-shot vs baseline"][2] == pytest.approx(0.1877, abs=1e-3)


def test_noise_floor_matches_the_observed_worst_pair():
    nf = noise_floor()
    assert nf["worst_tasks"] == 4 and abs(nf["worst_pp"] - 2.1) < 0.05
    assert nf["worst_pair"] == ("baseline-v2", "baseline-v3")


def test_pinned_runs_all_exist():
    """A renamed or deleted results file must fail loudly, not silently drop a row."""
    for tags in ((b for _, b, _ in stats.COMPARISONS), stats.BASELINES):
        for tag in tags:
            assert (stats.RESULTS / f"{tag}.jsonl").exists(), f"pinned run {tag} missing"


def test_paired_runs_cover_the_same_tasks():
    for _, base, variant in stats.COMPARISONS:
        a = stats.load_verdicts(base)
        b = stats.load_verdicts(variant)
        assert set(a) == set(b), f"{base} and {variant} are not on the same task set"


def test_missing_run_raises_rather_than_reporting_zero():
    with pytest.raises(FileNotFoundError):
        stats.load_verdicts("this-run-does-not-exist")
