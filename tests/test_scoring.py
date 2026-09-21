"""Grader behaviour tests.

These are not routine unit tests. Every number the project reports is produced by
`score_execution`, so each test pins one decision that would otherwise be able to
silently inflate or deflate the score.
"""

from __future__ import annotations

from sqlagent.eval.scoring import Execution, Verdict, score_execution


def ex(*rows, columns=None):
    return Execution(ok=True, columns=columns or [f"c{i}" for i in range(len(rows[0]))], rows=[list(r) for r in rows])


def check(gold, pred, require_order=False) -> Verdict:
    return score_execution(gold, pred, require_order=require_order)


def test_identical_result_is_correct():
    assert check(ex((1, "a"), (2, "b")), ex((1, "a"), (2, "b"))).correct


def test_column_order_is_ignored_when_names_allow_alignment():
    gold = ex((10.0, "Chen"), (20.0, "Li"), columns=["total", "name"])
    pred = ex(("Chen", 10.0), ("Li", 20.0), columns=["name", "total"])
    v = check(gold, pred)
    assert v.correct, v.detail


def test_row_order_enforced_only_when_the_question_asks_for_it():
    gold = ex((1, "a"), (2, "b"))
    pred = ex((2, "b"), (1, "a"))
    assert check(gold, pred, require_order=False).correct
    assert not check(gold, pred, require_order=True).correct
    assert check(gold, pred, require_order=True).reason == "order_wrong"


def test_float_noise_within_tolerance_is_correct():
    gold = ex((3.14159,))
    pred = ex((3.1416,))
    assert check(gold, pred).correct


def test_float_noise_beyond_tolerance_is_wrong():
    assert not check(ex((3.14159,)), ex((3.14,))).correct


def test_numeric_text_and_real_are_interchangeable():
    assert check(ex((3.5,)), ex(("3.50",))).correct


def test_null_is_not_zero():
    assert not check(ex((None,)), ex((0,))).correct
    assert not check(ex((0,)), ex((None,))).correct


def test_duplicate_rows_are_a_real_difference():
    """The single most common Text-to-SQL error: a join that fans out rows."""
    gold = ex(("a",), ("b",))
    pred = ex(("a",), ("a",), ("b",))
    v = check(gold, pred)
    assert not v.correct and v.reason == "row_count_mismatch"


def test_missing_row_is_caught_even_when_values_match():
    assert not check(ex(("a",), ("b",)), ex(("a",))).correct


def test_string_case_differences_are_accepted():
    assert check(ex(("Beijing",)), ex(("BEIJING",))).correct


def test_whitespace_only_difference_is_accepted():
    assert check(ex(("Shanghai",)), ex(("  Shanghai ",))).correct


def test_date_with_and_without_time_component_are_equal():
    assert check(ex(("2025-03-04",)), ex(("2025-03-04 00:00:00",))).correct


def test_empty_vs_empty_is_flagged_trivial_and_not_credited():
    gold, pred = Execution(ok=True, columns=["n"], rows=[]), Execution(ok=True, columns=["n"], rows=[])
    v = score_execution(gold, pred)
    assert v.trivial and not v.correct and v.reason == "match_trivial"


def test_wrong_column_count_is_reported():
    v = check(ex((1, 2)), ex((1,)))
    assert not v.correct and v.reason == "column_count_mismatch"


def test_broken_gold_is_blamed_on_the_benchmark_not_the_model():
    v = score_execution(Execution(ok=False, error="no such table: x", error_type="database_error"), ex((1,)))
    assert not v.correct and v.reason == "gold_broken"


def test_failing_prediction_reports_its_error_kind():
    v = score_execution(ex((1,)), Execution(ok=False, error="no such column: foo", error_type="operational_error"))
    assert not v.correct and v.reason == "pred_operational_error"
