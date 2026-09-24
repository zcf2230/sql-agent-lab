"""`scripts/rejudge.py` answers "did changing the judge move a published number".

Its first version answered that question wrongly and nearly shipped the wrong answer: it
re-graded the 208-question runs against the current 192-question set and reported the
resulting differences as judge behaviour. So what is under test here is the scoping -
which run may be compared against which question set, and which differences count as
dataset drift rather than as a changed verdict.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rejudge", ROOT / "scripts" / "rejudge.py")
rejudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rejudge)

from sqlagent import db  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    c = db.connect_ro(ROOT / "data" / "learning_platform.db")
    yield c
    c.close()


@pytest.fixture(scope="module")
def one_task():
    tasks = rejudge.load_tasks()
    assert tasks, "data/tasks.jsonl missing; run `python -m sqlagent.data.build_tasks`"
    return next(iter(tasks.values()))


def write_run(dir_: Path, name: str, summary: dict, rows: list[dict]) -> Path:
    p = dir_ / name
    p.write_text(json.dumps({"_summary": summary}) + "\n"
                 + "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_a_run_answered_against_a_different_question_set_is_out_of_scope(tmp_path):
    """Comparing a 208-question run to the current 192 says nothing about the judge -
    and the first version of the script reported exactly that as "your numbers moved"."""
    p = write_run(tmp_path, "old-run.jsonl", {"n_tasks": 208},
                  [{"id": "does-not-matter", "correct": True, "final_sql": "SELECT 1"}])
    rep = rejudge.rejudge_run(p, conn=None)
    assert rep["out_of_scope"].startswith("run has 208 tasks, current set has")
    assert rep["checked"] == 0


def test_restability_runs_are_graded_against_their_own_task_set(tmp_path):
    """They re-ask questions from a different file, so the main 192 is the wrong yardstick
    for them too - in the other direction."""
    p = write_run(tmp_path, "restability-deepseek-chat-no-such-pick.jsonl", {"n_tasks": 4},
                  [{"id": "x", "correct": True, "final_sql": "SELECT 1"}])
    rep = rejudge.rejudge_run(p, conn=None)
    assert "no task set" in rep["out_of_scope"]


def test_a_stored_verdict_that_the_current_judge_confirms_is_not_a_change(tmp_path, conn, one_task):
    p = write_run(tmp_path, "current.jsonl", {"n_tasks": len(rejudge.load_tasks())},
                  [{"id": one_task["id"], "correct": True, "final_sql": one_task["gold_sql"],
                    "require_order": one_task.get("require_order", False)}])
    rep = rejudge.rejudge_run(p, conn)
    assert rep["checked"] == 1, rep
    assert sum(rep["flips"].values()) == 0


def test_a_wrong_stored_verdict_is_reported_as_a_flip_and_not_silently_fixed(tmp_path, conn, one_task):
    """The point of the script is the count, so a flipped row must show up as one - and
    `main()` has to exit non-zero, because that is what makes CI a gate rather than a log."""
    p = write_run(tmp_path, "wrong.jsonl", {"n_tasks": len(rejudge.load_tasks())},
                  [{"id": one_task["id"], "correct": False, "final_sql": one_task["gold_sql"],
                    "require_order": one_task.get("require_order", False)}])
    rep = rejudge.rejudge_run(p, conn)
    assert rep["flips"]["wrong->right"] == 1, dict(rep["flips"])


def test_a_row_whose_order_policy_changed_since_the_run_is_dataset_drift(tmp_path, conn, one_task):
    """`require_order` is a property of the question, not of the judge. If the task set
    moved that flag, a verdict difference is drift and must not be counted as judge change."""
    p = write_run(tmp_path, "drift.jsonl", {"n_tasks": len(rejudge.load_tasks())},
                  [{"id": one_task["id"], "correct": True, "final_sql": one_task["gold_sql"],
                    "require_order": not bool(one_task.get("require_order"))}])
    rep = rejudge.rejudge_run(p, conn)
    assert rep["drift"] == 1 and sum(rep["flips"].values()) == 0, rep


def test_calibration_and_bird_artifacts_are_never_mistaken_for_runs(tmp_path, conn):
    """They share `results/` and carry per-row `correct` fields, but they are sweeps of
    injected defects, not stored model answers - re-grading them would double-count."""
    write_run(tmp_path, "calib-reflow.jsonl", {"n_tasks": 1},
              [{"id": "a", "correct": True, "final_sql": "SELECT 1"}])
    write_run(tmp_path, "bird-judge.jsonl", {"n_tasks": 1},
              [{"id": "b", "correct": True, "final_sql": "SELECT 1"}])
    assert rejudge.rejudge(tmp_path) == []
