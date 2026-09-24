"""Tracked artifacts must be re-derivable without churn.

`docs/HANDOFF.md` §4.2 tells a reviewer to run `scripts/calibrate.py` and compare the
output line by line. Until the artifact stopped recording wall-clock latency and
cache-hit flags, that comparison produced a 386-line diff per file with zero
behavioural content, and "did the judge change?" was unanswerable by eye - which is
the question the diff exists to answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parent.parent / "results"
MODES = ["reflow", "reorder_cols", "float_round", "drop_distinct", "wrong_limit", "bad_column"]

# Presentation-only defects: the judge must accept these by policy. Read from stats so
# the sweep, the report and this file cannot each carry their own copy of the policy -
# the report's copy was missing `reflow`, which is 110 of the 168 policy rows.
from sqlagent.stats import PRESENTATION_ONLY_MODES as PRESENTATION_ONLY


def rows(mode: str) -> list[dict]:
    path = RESULTS / f"calib-{mode}.jsonl"
    if not path.exists():
        pytest.skip(f"results/calib-{mode}.jsonl missing; run `python scripts/calibrate.py`")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.parametrize("mode", MODES)
def test_the_artifact_records_judgement_not_how_this_machine_ran(mode):
    body = rows(mode)[1:]
    assert body, f"calib-{mode} has no per-task rows"
    for r in body:
        assert "cached" not in r, "a cache-hit flag makes every re-run rewrite the file"
        assert "wall_ms" not in r.get("stats", {}), "latency is not reproducible, so it is not evidence"
    summary = rows(mode)[0]["_summary"]
    assert summary["provider"] == "mock"
    assert "mock" in summary["model"], (
        f"the sweep calls no model; recording the ambient default ({summary['model']!r}) "
        "reads as a claim about which model was calibrated"
    )


@pytest.mark.parametrize("mode", MODES)
def test_every_recorded_verdict_is_still_implied_by_its_own_evidence(mode):
    """The artifact must remain self-consistent under the documented policy:
    a defect that materially changed the result must be rejected, one that changed
    nothing must be accepted, presentation-only differences are accepted by policy.
    Re-deriving this from the row rather than trusting a summary column is the point."""
    po = mode in PRESENTATION_ONLY
    checked = 0
    for r in rows(mode)[1:]:
        if r.get("trivial") or r.get("result_changed") is None:
            continue
        expected = po or not r["result_changed"]
        assert r["correct"] is expected, (
            f"{r['id']}: judge said {r['correct']}, policy derived {expected} "
            f"from result_changed={r['result_changed']}"
        )
        checked += 1
    assert checked > 0, f"calib-{mode} contains no testable observation"


def test_the_sweep_still_covers_every_defect_class():
    """An empty category reads as 'the judge passed it' unless you check otherwise."""
    per_mode = {m: sum(1 for r in rows(m)[1:] if not r.get("trivial") and r.get("result_changed") is not None)
                for m in MODES}
    assert sum(per_mode.values()) >= 700, per_mode
    empty = [m for m, n in per_mode.items() if n == 0]
    assert not empty, f"defect classes with no testable observation: {empty}"


def test_the_two_calibration_populations_add_up_to_the_published_split():
    """The report quotes "582 adversarial + 168 policy self-demonstrating", and the 168
    is defined as exactly the presentation-only classes. The report used to mark only
    `reorder_cols` as policy - 58 of those 168 - so a future reflow row whose
    re-serialisation did change the result would be published as a false accept while
    policy says it must be accepted. This pins the set to the split it is supposed to
    produce, and pins the split to the artifacts.
    """
    # "可测样本" as the report defines it: a row the injected defect could actually reach.
    tested = {m: sum(1 for r in rows(m)[1:]
                     if not r.get("trivial") and r.get("result_changed") is not None)
              for m in MODES}
    policy = sum(tested[m] for m in PRESENTATION_ONLY)
    adversarial = sum(n for m, n in tested.items() if m not in PRESENTATION_ONLY)
    assert policy == 168 and adversarial == 582, tested
    assert PRESENTATION_ONLY == {"reflow", "reorder_cols"}, \
        "adding a policy class changes the published 582/168 split; update the prose too"


def test_the_mock_artifacts_do_not_carry_a_machine_dependent_version():
    """`summarise()` records the SQLite build that judged a run, which is right for real
    runs and wrong for these: HANDOFF §4.2 tells a reviewer to re-run the sweep and
    expect a clean tree, and a stored executor version would make a Linux re-run look
    like the judge changed behaviour.
    """
    for mode in MODES:
        summary = rows(mode)[0]["_summary"]
        assert "sqlite_version" not in summary, f"calib-{mode} stores the machine's SQLite"


RESTABILITY_PICKS = ["families", "failures", "correct-representative", "correct-boundary"]

@pytest.mark.parametrize("pick", RESTABILITY_PICKS)
def test_each_restability_run_has_its_own_artifact_and_is_the_run_it_claims(pick):
    """Two `--pick` values used to write the same results file, and the second run
    silently replaced a committed, published artifact - the numbers in `docs/HANDOFF.md`
    §17 were briefly backed by a file describing a different set of questions. The name
    now carries the pick, and this check re-joins each artifact to the task set it ran,
    so a future overwrite fails a test instead of quietly republishing a document."""
    root = RESULTS.parent
    res, data = (root / "results" / f"restability-deepseek-chat-{pick}.jsonl",
                 root / "data" / f"tasks_restability-{pick}.jsonl")
    assert res.exists(), f"{res.name} missing: §17 quotes a result with no artifact"
    assert data.exists(), f"{data.name} missing: the run cannot be re-derived"
    rows_ = [json.loads(l) for l in res.read_text(encoding="utf-8").splitlines() if l.strip()]
    body = [r for r in rows_[1:] if "id" in r]
    assert {r["id"] for r in body} == {json.loads(l)["id"] for l in
                                       data.read_text(encoding="utf-8").splitlines() if l.strip()}, \
        f"{res.name} is not the run of {data.name}"
    bases = {r["id"].split("#")[0] for r in body}
    thin = [b for b in bases if sum(1 for r in body if r["id"].startswith(b + "#")) != 4]
    assert not thin, f"asked fewer than four ways, so no stability rate: {thin}"
    assert sum(r.get("cost_usd") or 0.0 for r in body) > 0, "a $0 run measured nothing"


def test_no_ad_hoc_script_survives_in_the_root_or_in_scripts():
    """Two scratch scripts (`hd.py`, a doc-editing one-off; `stat_check.py`, an ad-hoc
    aggregation since absorbed into `stats.cluster_sensitivity`) were committed and
    published, referenced by nothing. A reviewer reads the repository root as the
    project's shape, so a one-off sitting there reads as part of the design - which is
    the same defect as a results file nothing regenerates, just in code instead of data.
    """
    root = RESULTS.parent
    strays = sorted(p.name for p in root.glob("*.py"))
    assert not strays, f"scratch scripts in the repo root: {strays}"

    docs = "\n".join(p.read_text(encoding="utf-8")
                     for p in [root / "README.md", *sorted((root / "docs").glob("*.md"))])
    orphans = [p.name for p in sorted((root / "scripts").glob("*.py")) if p.name not in docs]
    assert not orphans, f"scripts/ files no document tells anyone to run: {orphans}"
