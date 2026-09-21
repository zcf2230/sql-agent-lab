"""End-to-end loop tests driven by the mock provider.

These exercise prompt -> tool dispatch -> execution -> grading as one path, which
is what proves the harness itself is sound. They deliberately use the real
database file; if it is missing the tests skip rather than fake a pass.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sqlagent.agent import SqlAgent
from sqlagent.config import DATA_DIR, Settings
from sqlagent.db import list_tables
from sqlagent.eval.scoring import grade
from sqlagent.llm import MockProvider
from sqlagent.tools import Toolbox

DB = DATA_DIR / "learning_platform.db"

# has a real column reference, otherwise `bad_column` has nothing to corrupt
GOLD = "SELECT COUNT(*) AS n FROM users WHERE is_deleted = 0"
GOLD_MULTI = "SELECT id FROM users"  # needed where the defect only shows up in row count
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run `python -m sqlagent.data.build_db` first")


def run(corruption: str, self_repair: bool = True, max_steps: int = 6, gold: str = GOLD):
    settings = Settings(provider="mock", corruption=corruption, self_repair=self_repair, max_steps=max_steps,
                       db_path=str(DB))
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    agent = SqlAgent(settings, Toolbox(conn, settings))
    provider = MockProvider(gold_sql=gold, tables=["users"], corruption=corruption,
                            self_repair=self_repair)
    result = agent.run("How many users are there?", f"mock-{corruption}-{self_repair}", provider)
    verdict = grade(conn, gold, result.final_sql)
    conn.close()
    return result, verdict


def test_happy_path_scores_correct():
    result, verdict = run("none")
    assert verdict.correct, verdict.reason
    assert result.final_sql and "COUNT" in result.final_sql.upper()
    assert result.stop_reason == "final_answer"


def test_a_failed_query_is_repaired_and_still_lands():
    result, verdict = run("bad_column", self_repair=True)
    assert result.stats["sql_errors"] >= 1
    assert verdict.correct, verdict.detail
    assert result.stats["sql_attempts"] >= 2


def test_without_self_repair_the_same_failure_stays_failed():
    result, verdict = run("bad_column", self_repair=False)
    assert not verdict.correct
    assert result.stats["sql_errors"] >= 1


def test_truncating_a_multi_row_result_is_caught():
    _, verdict = run("wrong_limit", gold=GOLD_MULTI)
    assert not verdict.correct and verdict.reason == "row_count_mismatch"


def test_a_defect_that_changes_nothing_is_invisible_by_definition():
    """LIMIT 3 on a one-row answer cannot be detected by anyone.

    Recorded so the calibration table is read correctly: a lenient score under a
    'should be caught' mode usually means the defect never reached the result.
    """
    result, verdict = run("wrong_limit", gold=GOLD)
    assert verdict.correct and verdict.detail["gold_rows"] == verdict.detail["pred_rows"]


def test_semantically_identical_rewrite_still_scores():
    result, verdict = run("reflow")
    assert verdict.correct, verdict.reason


def test_the_agent_uses_a_tool_before_answering():
    result, _ = run("none")
    assert "get_schema" in result.stats["tool_sequence"]
    assert result.stats["tool_calls"] >= 2


def test_cost_is_attributed_per_run():
    result, _ = run("none")
    assert result.prompt_tokens > 0
    assert result.cost_usd >= 0.0


def test_read_only_connection_cannot_write():
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM users WHERE id = 1")
    conn.close()
    assert len(list_tables(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True))) == 20


def test_a_hallucinated_column_returns_an_error_not_a_crash():
    """Regression: this escaped as a QueryError and killed the whole task.

    Asking for a column that does not exist is one of the most frequent real model
    mistakes, so the tool layer has to hand it back as readable feedback the repair
    loop can act on. Crashing instead both loses the task and hides the behaviour
    you most want to measure.
    """
    from sqlagent.config import Settings
    from sqlagent.tools import Toolbox

    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    box = Toolbox(conn, Settings(db_path=str(DB)))
    for args in ({"table": "study_sessions", "column": "device_type"},
                 {"table": "nosuchtable", "column": "id"},
                 {"table": "users", "column": "nope"}):
        out = box.call("sample_values", args)
        assert out["ok"] is False and out["error_type"], args
    conn.close()
