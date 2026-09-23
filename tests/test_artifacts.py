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

# Presentation-only defects: the judge must accept these by policy.
PRESENTATION_ONLY = {"reorder_cols"}


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
