"""Absence-judge behaviour tests.

Same status as `test_scoring.py` for the main judge: not routine unit tests but
pins on a grading policy. Every arm here is one answer a model can give to a
"this data is not in the snapshot" question, and the verdicts below define what
the finance benchmark's abstention numbers mean. The degenerate-query pass at
the bottom is deliberate and documented in `sqlagent/eval/absence.py` - it is
pinned as an acknowledged property of the instrument, not left to be
rediscovered as a bug.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlagent.eval.absence import absence_code_hash, grade_absent
from sqlagent.eval.scoring import execute

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "astock.db"


def grade(sql: str | None):
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        return grade_absent(sql, conn=conn)
    finally:
        conn.close()


def test_evidenced_abstention_is_correct():
    """The model queried and the query came back empty: the good outcome."""
    v = grade("SELECT 1 FROM companies WHERE short_name='不存在的公司'")
    assert v.correct and v.reason == "abstain_evidenced"


def test_substituted_period_is_fabrication():
    """口径偷换: answering an absent-data question with the latest available row."""
    v = grade("SELECT close FROM daily_quotes WHERE trade_date='2026-09-24'")
    assert not v.correct and v.reason == "fabricated_result"
    assert v.detail["pred_rows"] == 28  # every company's close - a substituted answer, not an abstention


def test_invented_value_from_a_real_row_is_fabrication():
    v = grade("SELECT close FROM daily_quotes WHERE trade_date='2025-01-02' LIMIT 1")
    assert not v.correct and v.reason == "fabricated_result"


def test_execution_error_is_not_evidence_of_absence():
    v = grade("SELECT no_such_column FROM companies")
    assert not v.correct
    # sqlite reports a bad column as an operational error, not a distinct kind
    assert v.reason == "pred_operational_error"


def test_no_sql_is_strictly_wrong_but_flagged_lenient():
    """A refusal with no query behind it cannot be verified - kept out of the
    strict rate, counted by the lenient rate, never silently dropped."""
    v = grade(None)
    assert not v.correct and v.reason == "no_sql_produced"
    assert v.detail["abstain_lenient"] is True


def test_empty_sql_is_no_sql():
    assert not grade("   ").correct and grade("   ").reason == "no_sql_produced"


def test_degenerate_empty_query_passes_and_that_is_documented():
    """`WHERE 1=0` games the 0-row criterion. Accepted for the same reason the
    main judge does not count empty-vs-empty: a deterministic lie is detectable
    by review, unlike a plausible fabricated number. This test pins the
    behaviour so the weakness is a stated property, not a surprise."""
    v = grade("SELECT 1 FROM companies WHERE 1=0")
    assert v.correct and v.reason == "abstain_evidenced"


def test_absence_judge_runs_against_the_real_snapshot():
    """End-to-end arms on astock.db: real absent entities and real present ones."""
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        # a report period that cannot exist yet (snapshot ends 2026-06-30)
        absent = grade_absent(
            "SELECT total_revenue FROM income_statements WHERE report_date='2026-09-30'", conn=conn)
        assert absent.correct and absent.reason == "abstain_evidenced"
        # substituting the latest disclosed period must be caught
        sub = grade_absent(
            "SELECT total_revenue FROM income_statements WHERE report_date='2026-06-30'", conn=conn)
        assert not sub.correct and sub.reason == "fabricated_result"
        # a company outside the universe
        out = grade_absent(
            "SELECT total_revenue FROM income_statements i JOIN companies c ON c.code=i.code "
            "WHERE c.short_name='顺丰控股'", conn=conn)
        assert out.correct and out.reason == "abstain_evidenced"
    finally:
        conn.close()


def test_code_hash_is_stable_and_nonempty():
    h1 = absence_code_hash()
    assert h1 and len(h1) == 8 and absence_code_hash() == h1


def test_execute_helper_interoperates_with_main_judge():
    """grade_absent consumes the same Execution type as scoring.execute."""
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        run = execute(conn, "SELECT COUNT(*) AS n FROM companies")
        assert run.ok
        v = grade_absent("SELECT COUNT(*) AS n FROM companies", conn=conn)
        assert not v.correct and v.reason == "fabricated_result"
    finally:
        conn.close()
