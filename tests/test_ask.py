"""The single-question entry point.

It exists because the repo had no way to type a question into the system it describes,
which is a strange property for a project whose claim is "product, not script". These tests
keep it honest on the two ways a demo module usually goes wrong: drifting from the real
code path, and quietly inventing a question it can answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sqlagent.ask import match_task, run_question
from sqlagent.config import DATA_DIR, Settings

TASKS = DATA_DIR / "tasks.jsonl"
needs_tasks = pytest.mark.skipif(not TASKS.exists(), reason="run `python -m sqlagent.data.build_tasks`")


def _settings() -> Settings:
    return Settings(provider="mock", model="mock", db_path=str(DATA_DIR / "learning_platform.db"))


@needs_tasks
def test_it_answers_through_the_same_agent_the_benchmark_uses():
    question = json.loads(TASKS.read_text(encoding="utf-8").splitlines()[0])["question"]
    out = run_question(question, _settings(), verbose=False)
    assert out["final_sql"], "the mock answers with the gold, so a missing SQL means a broken path"
    assert out["stats"]["steps"] >= 1 and "sql_attempts" in out
    assert all(isinstance(a["sql"], str) for a in out["sql_attempts"]), \
        "every attempted statement is kept, including the ones the guard refused"


@needs_tasks
def test_only_an_exact_benchmark_question_gets_a_verdict():
    """Offering gold for a paraphrase would be the interesting failure: the answer can
    legitimately differ, and the demo would score it as wrong."""
    first = json.loads(TASKS.read_text(encoding="utf-8").splitlines()[0])
    assert match_task(first["question"])["id"] == first["id"]
    assert match_task(first["question"] + " (roughly)") is None
    assert match_task("how many users are there, give or take") is None


def test_a_missing_task_file_is_no_question_rather_than_a_crash(tmp_path):
    assert match_task("anything", tasks_path=tmp_path / "absent.jsonl") is None


def test_quiet_mode_prints_only_the_sql():
    """It is meant to be pipeable into `sqlagent.eval.runner`'s cache-free checks, so the
    step listing has to be suppressable without changing what the agent did."""
    import subprocess
    import sys

    question = json.loads(TASKS.read_text(encoding="utf-8").splitlines()[0])["question"]
    proc = subprocess.run([sys.executable, "-m", "sqlagent.ask", question,
                           "--provider", "mock", "--quiet"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(Path(__file__).resolve().parent.parent),
                          env={**__import__("os").environ, "PYTHONUTF8": "1"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().upper().startswith("SELECT")
    assert "最终 SQL" not in proc.stdout
