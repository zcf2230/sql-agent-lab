"""Runner summary gating.

A benchmark run that fails inside the harness produces exactly the same shape of
output as a model that performs terribly: a low pass@1. The first Qwen run came back
as 0.0000 with 192/192 exceptions, and nothing in the summary distinguished it from a
real result until this gate existed.
"""

from __future__ import annotations

from sqlagent.config import Settings
from sqlagent.eval.runner import summarise


def row(task_id: str, correct: bool = False, error: bool = False) -> dict:
    if error:
        return {"id": task_id, "correct": False, "reason": "harness_exception", "stats": {}}
    return {
        "id": task_id, "correct": correct, "reason": "match" if correct else "value_mismatch",
        "difficulty": "easy", "category": "x", "cost_usd": 0.0, "safety_blocks": 0,
        "stats": {"steps": 4, "sql_attempts": 1, "sql_errors": 0, "prompt_tokens": 100,
                  "completion_tokens": 20},
    }


def test_a_run_that_crashes_is_not_reported_as_a_low_score():
    rows = [row(f"t{i}", error=True) for i in range(50)]
    out = summarise(rows, Settings())
    assert out["valid"] is False
    assert "invalid_reason" in out
    assert out["harness_exceptions"] == 50


def test_one_crash_in_a_hundred_does_not_void_the_run():
    """The threshold is ~2%. Ten crashes in a hundred DOES void the run - the first
    version of this test asserted otherwise because I had decided what 'a handful'
    meant from instinct rather than from the number."""
    rows = [row(f"t{i}", correct=(i != 0), error=(i == 0)) for i in range(100)]
    out = summarise(rows, Settings())
    assert out["valid"] is True
    assert out["harness_exceptions"] == 1


def test_ten_crashes_in_a_hundred_does_void_it():
    rows = [row(f"t{i}", correct=(i % 10 != 0), error=(i % 10 == 0)) for i in range(100)]
    out = summarise(rows, Settings())
    assert out["valid"] is False, "10% of a benchmark failing in the harness is not a score"


def test_the_healthy_path_is_valid():
    rows = [row(f"t{i}", correct=(i % 4 != 0)) for i in range(40)]
    out = summarise(rows, Settings())
    assert out["valid"] is True and out["harness_exceptions"] == 0
    assert abs(out["pass_at_1"] - 0.75) < 1e-6
