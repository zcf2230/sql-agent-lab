"""Dataset pinning for the investment-research task set.

The finance benchmark's numbers are only as good as the pair (astock.db,
tasks_finance.jsonl). These tests pin the invariants that must hold for any
verdict produced from them - and, like the main set, refuse to let a quiet
rebuild move a published benchmark: the dataset digest below is pinned, so
rebuilding the tasks or the snapshot forces a conscious test edit, which is the
same tripwire `build_tasks.py` uses for the learning-platform set.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlagent.eval.runner import dataset_digest, load_tasks

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "astock.db"
TASKS = ROOT / "data" / "tasks_finance.jsonl"

# content digest of tasks_finance.jsonl as published. A rebuild that changes a
# single question changes this string, and the test fails on purpose: re-run the
# benchmark before publishing new numbers, then update the pin.
PINNED_DATASET_HASH = "967c8491"

CATEGORIES = {"metric_lookup", "ratio", "growth", "cross_statement", "screening",
              "topk", "market", "aggregation", "absence_period", "absence_company", "absence_date"}


def conn():
    return sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)


def test_database_exists_and_has_the_committed_shape():
    assert DB.exists(), "run python scripts/fin_fetch.py (then commit the artifact)"
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 28
    for t in ("balance_sheets", "income_statements", "cashflow_statements"):
        assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 392, t
    assert c.execute("SELECT COUNT(*) FROM daily_quotes").fetchone()[0] > 10000
    assert c.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0] == 28


def test_snapshot_is_one_coherent_trading_day():
    c = conn()
    days = c.execute("SELECT DISTINCT trade_date FROM market_snapshot").fetchall()
    assert len(days) == 1, f"snapshot mixed days: {days}"


def test_task_file_matches_its_pinned_digest():
    assert dataset_digest(TASKS) == PINNED_DATASET_HASH


def test_every_task_is_wellformed_and_categorised():
    tasks = load_tasks(TASKS)
    ids = {t["id"] for t in tasks}
    assert len(ids) == len(tasks)
    for t in tasks:
        assert t["db"] == "astock.db"
        assert t["category"] in CATEGORIES
        assert t["difficulty"] in ("easy", "medium", "hard")
        assert t["expect_absent"] == (t["category"].startswith("absence_"))
        assert isinstance(t["gold_row_count"], int)


def test_answerable_gold_executes_nonempty_nonnull():
    c = conn()
    tasks = load_tasks(TASKS)
    answerable = [t for t in tasks if not t["expect_absent"]]
    assert len(answerable) >= 55, len(answerable)
    for t in answerable:
        rows = c.execute(t["gold_sql"]).fetchall()
        assert rows, f"vacuous gold: {t['id']}"
        assert t["gold_row_count"] == len(rows), t["id"]
        if len(rows[0]) == 1:
            assert rows[0][0] is not None, f"NULL gold: {t['id']}"


def test_absent_gold_is_proof_of_absence():
    c = conn()
    tasks = load_tasks(TASKS)
    absent = [t for t in tasks if t["expect_absent"]]
    assert len(absent) >= 15, len(absent)
    for t in absent:
        rows = c.execute(t["gold_sql"]).fetchall()
        assert not rows, f"absence violated at build time: {t['id']}"


def test_absence_families_cover_all_three_kinds_of_missing():
    cats = {json.loads(l)["category"] for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()}
    assert {"absence_period", "absence_company", "absence_date"} <= cats


def test_absence_targets_are_really_outside_the_database():
    """The absence questions are only honest if their targets cannot exist:
    undisclosed periods, off-universe companies, non-trading dates."""
    c = conn()
    assert not c.execute("SELECT 1 FROM income_statements WHERE report_date IN "
                         "('2026-09-30','2026-12-31','2022-12-31')").fetchone()
    for nm in ("分众传媒", "顺丰控股", "海康威视", "中国中免"):
        assert not c.execute("SELECT 1 FROM companies WHERE short_name=?", (nm,)).fetchone()
    for day in ("2026-09-26", "2026-10-01", "2024-12-31"):
        assert not c.execute("SELECT 1 FROM daily_quotes WHERE trade_date=?", (day,)).fetchone()


def test_growth_questions_compare_the_same_period_type():
    """Cumulative statements: a YoY across mismatched period types would make the
    gold itself the kind of error the benchmark exists to catch."""
    tasks = [t for t in load_tasks(TASKS) if t["category"] == "growth"]
    assert tasks
    for t in tasks:
        sql = t["gold_sql"]
        cur, prev = sql.split("JOIN income_statements p")[1].split("report_date='")[1][:10], None
        # both dates must end in the same month-day
        import re
        dates = re.findall(r"report_date='(\d{4}-\d{2}-\d{2})'", sql)
        assert len(dates) == 2 and dates[0][5:] == dates[1][5:], t["id"]
