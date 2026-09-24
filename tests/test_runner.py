"""Runner summary gating.

A benchmark run that fails inside the harness produces exactly the same shape of
output as a model that performs terribly: a low pass@1. The first Qwen run came back
as 0.0000 with 192/192 exceptions, and nothing in the summary distinguished it from a
real result until this gate existed.
"""

from __future__ import annotations

import sqlite3

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


def test_a_refused_run_is_null_in_the_data_not_zero_on_disk():
    """Withholding used to be true only of the CLI and the report: the JSON still said
    `pass_at_1: 0.0`, which is exactly the reading the gate exists to block - a later
    reader or script sees a model that got nothing right. Fourth review, F3."""
    rows = [row(f"t{i}", error=True) for i in range(50)]
    out = summarise(rows, Settings())
    assert out["pass_at_1"] is None, "a refusal must be null, not the number 0"
    assert out["valid"] is False


def test_a_few_crashes_still_score_and_the_count_travels_with_the_record():
    rows = [row(f"t{i}", correct=(i >= 3), error=(i < 3)) for i in range(192)]
    out = summarise(rows, Settings())
    assert out["valid"] is True and out["harness_exceptions"] == 3
    # Under the gate but not under the 4/192 noise floor: the reader has to be shown
    # the count, because three fake zeros moved the headline by 1.6pp.
    assert out["failure_taxonomy"]["harness_exception"] == 3
    assert abs(out["pass_at_1"] - 189 / 192) < 1e-4


def test_the_run_records_which_sqlite_judged_it():
    """Every verdict comes from executing SQL, so it belongs to a SQLite build the way
    the allowlist belongs to a parser version - and unlike the parser, this one was in
    no artifact at all. Recorded per run; `scripts/calibrate.py` strips it from the
    tracked mock artifacts on purpose, so a reviewer on another OS does not produce a
    diff that looks like the judge changing behaviour."""
    out = summarise([row("a", correct=True)], Settings())
    assert out["sqlite_version"] == sqlite3.sqlite_version


def test_dataset_digest_ignores_line_endings(tmp_path):
    """The second review's D1. `code_hash()` normalises newlines and explains why in its
    docstring; `dataset_hash` did not, so rebuilding the task set on Windows (text mode
    -> CRLF) changed the digest for zero behavioural reason. That invalidated the whole
    result cache - contradicting the README's "a repeated run is free" - and rewrote
    `config_hash` inside six committed artifacts, so "did the judge change?" could not
    be answered by reading the diff the documented verification steps produce."""
    from sqlagent.eval.runner import dataset_digest

    body = '{"id": "a-001", "gold_sql": "SELECT 1"}\n{"id": "a-002", "gold_sql": "SELECT 2"}\n'
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    lf.write_bytes(body.encode("utf-8"))
    crlf.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    assert lf.read_bytes() != crlf.read_bytes(), "the fixture must actually differ on disk"
    assert dataset_digest(lf) == dataset_digest(crlf), "same content, different line endings, different digest"


def _mini_db(path, value: str) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE city (v TEXT)")
    con.execute("INSERT INTO city VALUES (?)", (value,))
    con.commit()
    con.close()


def _birdish_settings(tmp_path, **over):
    from sqlagent.config import Settings

    return Settings(provider="mock", model="mock", db_root=str(tmp_path),
                    db_path=str(tmp_path / "a.db"), dataset_hash="multidb-test", **over)


def _tasks():
    return [
        {"id": "q-a", "question": "which city?", "gold_sql": "SELECT v FROM city",
         "gold_tables": ["city"], "db": "a.db"},
        {"id": "q-b", "question": "which city?", "gold_sql": "SELECT v FROM city",
         "gold_tables": ["city"], "db": "b.db"},
    ]


def test_a_task_can_name_its_own_database(tmp_path, monkeypatch):
    """BIRD puts every question in a different SQLite file, so the agent's schema tools,
    its SQL execution and the judge's gold run must all follow that one path. Get it
    wrong and a verdict is computed against a database the model never saw - which still
    produces a number."""
    from sqlagent.eval import runner as runner_module
    from sqlagent.eval.runner import run_one
    from sqlagent.llm import MockProvider

    monkeypatch.setattr(runner_module, "CACHE_DIR", tmp_path / "cache")
    _mini_db(tmp_path / "a.db", "Beijing")
    _mini_db(tmp_path / "b.db", "Paris")
    settings = _birdish_settings(tmp_path)
    tasks = _tasks()

    def factory(task):
        return MockProvider(gold_sql=task["gold_sql"], tables=["city"],
                            corruption="none", self_repair=True)

    rows = [run_one(t, settings, factory, tasks) for t in tasks]
    assert [r.get("reason") for r in rows] == ["match", "match"], rows
    assert [r["db"] for r in rows] == ["a.db", "b.db"], "the row must say which DB judged it"
    assert summarise(rows, settings)["n_databases"] == 2


def test_a_single_database_set_keeps_its_old_row_shape(tmp_path, monkeypatch):
    """The `db` field is added only when a task names one, so nothing about the 192-task
    artifacts' shape changes under this commit."""
    from sqlagent.eval import runner as runner_module
    from sqlagent.eval.runner import run_one
    from sqlagent.llm import MockProvider

    monkeypatch.setattr(runner_module, "CACHE_DIR", tmp_path / "cache")
    _mini_db(tmp_path / "a.db", "Beijing")
    task = {"id": "q-1", "question": "which city?", "gold_sql": "SELECT v FROM city",
            "gold_tables": ["city"]}

    def factory(t):
        return MockProvider(gold_sql=t["gold_sql"], tables=["city"],
                            corruption="none", self_repair=True)

    row = run_one(task, _birdish_settings(tmp_path), factory, [task])
    assert row["correct"] and "db" not in row
    assert summarise([row], _birdish_settings(tmp_path))["n_databases"] == 1


def test_a_task_pointing_at_a_missing_database_fails_loudly(tmp_path, monkeypatch):
    """Wrong `--db-root` must not degrade into "the model cannot write SQL": the agent
    would answer against a database it never got a schema for, and every verdict would
    still compute."""
    from sqlagent.eval import runner as runner_module
    from sqlagent.eval.runner import run_one
    from sqlagent.llm import MockProvider

    monkeypatch.setattr(runner_module, "CACHE_DIR", tmp_path / "cache")
    _mini_db(tmp_path / "a.db", "Beijing")
    task = {"id": "q-x", "question": "which city?", "gold_sql": "SELECT v FROM city",
            "gold_tables": ["city"], "db": "gone.db"}

    def factory(t):
        return MockProvider(gold_sql=t["gold_sql"], tables=["city"],
                            corruption="none", self_repair=True)

    row = run_one(task, _birdish_settings(tmp_path), factory, [task])
    assert row["reason"] == "harness_exception", row
    assert "--db-root" in row["error"], "the message has to name the flag that fixes it"
