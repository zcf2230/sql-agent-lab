"""Grader behaviour tests.

These are not routine unit tests. Every number the project reports is produced by
`score_execution`, so each test pins one decision that would otherwise be able to
silently inflate or deflate the score.
"""

from __future__ import annotations

from sqlagent.eval.scoring import Execution, Verdict, result_differs, score_execution


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


# --- two inconsistencies found by putting the judge on BIRD's real databases -----------
# (`scripts/bird_judge.py`; question ids are BIRD dev ids so the case can be re-pulled)


def test_bird_q94_a_column_label_with_different_whitespace_still_aligns():
    """BIRD dev q94 (`financial`): re-serialising an unnamed expression column turns
    `( SELECT MAX(A11) - MIN(A11) FROM district )` into `(SELECT …)`. Exact label
    matching failed, alignment silently degraded to positional, and a column order the
    policy says to accept was judged a wrong answer."""
    gold = Execution(ok=True, columns=["account_id", "( SELECT MAX(A11) - MIN(A11) FROM district )"],
                     rows=[[6, 4431]])
    pred = Execution(ok=True, columns=["(SELECT MAX(A11) - MIN(A11) FROM district)", "account_id"],
                     rows=[[4431, 6]])
    v = score_execution(gold, pred, require_order=False)
    assert v.correct, f"label spelling noise defeated the alignment: {v.reason}"
    assert not result_differs(gold, pred)


def test_bird_q1_column_names_are_not_part_of_a_changed_result():
    """BIRD dev q1 (`california_schools`): the injected rewrite re-quoted
    `` `Free Meal Count (Ages 5-17)` `` into the SQL-standard double-quote form. Every
    row was identical, yet `result_differs` called it a changed result because it
    compared column labels the judge does not compare. That column of the calibration
    table answers "did the defect reach the answer", so it must use the judge's own
    notion of the same answer - one definition, not two."""
    gold = Execution(ok=True, columns=["`Free Meal Count` / `Enrollment`"], rows=[[0.043478260869565216]])
    pred = Execution(ok=True, columns=['"Free Meal Count" / "Enrollment"'], rows=[[0.043478260869565216]])
    assert score_execution(gold, pred, require_order=False).correct
    assert not result_differs(gold, pred), "a name-only difference is not a material difference"


def test_bird_q62_integer_one_and_float_one_are_the_same_answer():
    """ROUND(COUNT(*), 1) returns 1.0 where the gold returns the integer 1. The judge
    already agreed via the shared value normalisation; the recorder of
    `result_changed` disagreed, because its own key path differed from the judge's."""
    gold = Execution(ok=True, columns=["COUNT(T2.School)"], rows=[[1]])
    pred = Execution(ok=True, columns=["ROUND(COUNT(T2.School), 1)"], rows=[[1.0]])
    assert score_execution(gold, pred, require_order=False).correct
    assert not result_differs(gold, pred)


def test_bird_q77_identifier_quotes_in_a_label_are_also_spelling_noise():
    """The second half of the same defect, found two questions later: the expression
    column is labelled ``T1.`FRPM Count (Ages 5-17)` * 100 / …`` and re-serialises to
    the double-quoted form. Whitespace normalisation alone left the labels unequal."""
    gold = Execution(ok=True, rows=[["White Oak Elementary", 3.755868544600939]],
                     columns=["School", "T1.`FRPM Count (Ages 5-17)` * 100 / T1.`Enrollment (Ages 5-17)`"])
    pred = Execution(ok=True, rows=[[3.755868544600939, "White Oak Elementary"]],
                     columns=['T1."FRPM Count (Ages 5-17)" * 100 / T1."Enrollment (Ages 5-17)"', "School"])
    v = score_execution(gold, pred, require_order=False)
    assert v.correct, f"quote style defeated the alignment: {v.reason}"
    assert not result_differs(gold, pred)
