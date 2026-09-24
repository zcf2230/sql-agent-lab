"""The judge.

Every number on a resume descends from this file, so its failure modes are
documented explicitly and pinned by tests/test_scoring.py.

Matching policy (execution accuracy, Spider-style, with three deliberate
departures):
  * column order is ignored when a permutation of it matches - a model that
    returns (name, total) for a gold (total, name) answered the question.
  * ordering is enforced only when the gold SQL has ORDER BY. Enforcing it
    otherwise punishes semantically identical answers.
  * a prediction that returns zero rows where gold returns zero rows is NOT
    counted silently. It is reported as `trivial` and excluded from the
    headline metric, because an empty-vs-empty match proves nothing.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import permutations

from .. import db
from ..safety import SafetyViolation, guard_read_only

ABS_TOL = 1e-4
REL_TOL = 1e-6
_MAX_PERM_COLUMNS = 6


@dataclass
class Verdict:
    correct: bool
    reason: str
    trivial: bool = False
    detail: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {"correct": self.correct, "reason": self.reason, "trivial": self.trivial, **self.detail}


@dataclass
class Execution:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    error: str = ""
    error_type: str = ""


def execute(conn: sqlite3.Connection, sql: str, max_rows: int = 5000, timeout_s: float = 8.0) -> Execution:
    if not sql or not sql.strip():
        return Execution(ok=False, error="empty sql", error_type="empty_sql")
    try:
        guard_read_only(sql)
    except SafetyViolation as exc:
        return Execution(ok=False, error=str(exc), error_type="unsafe_sql")
    try:
        result = db.run_select(conn, sql, max_rows=max_rows, timeout_s=timeout_s)
    except db.QueryError as exc:
        return Execution(ok=False, error=exc.message, error_type=exc.kind)
    return Execution(ok=True, columns=result.columns, rows=result.canonical())


def _normalise(value):
    """Fold the representational differences SQLite leaves us with."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").strip()
        except Exception:
            return repr(value)
    if isinstance(value, str):
        text = value.strip()
        # our data stores timestamps both as 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DD'
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(\.0+)?$", text)
        if m:
            return m.group(1)
        return text
    return value


def _values_equal(a, b) -> bool:
    a, b = _normalise(a), _normalise(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    if isinstance(a, float) != isinstance(b, float):
        # '3.50' stored as text vs 3.5 stored as real
        try:
            return math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)
        except (TypeError, ValueError):
            return str(a) == str(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b or a.casefold() == b.casefold()
    return a == b


def _rows_equal(gold: list, pred: list) -> bool:
    if len(gold) != len(pred):
        return False
    return all(_values_equal(x, y) for x, y in zip(gold, pred))


def _compare(gold_rows: list[list], pred_rows: list[list], ordered: bool) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if ordered:
        return all(_rows_equal(g, p) for g, p in zip(gold_rows, pred_rows))

    def key(row):
        # a total order that puts equal-under-tolerance values adjacent
        out = []
        for v in row:
            n = _normalise(v)
            if n is None:
                out.append((0, 0.0, ""))
            elif isinstance(n, float):
                out.append((1, n, ""))
            else:
                out.append((2, 0.0, str(n)))
        return tuple(out)

    g = sorted(gold_rows, key=key)
    p = sorted(pred_rows, key=key)
    return all(_rows_equal(x, y) for x, y in zip(g, p))


def _column_key(name: str) -> str:
    """A column label with its spelling noise removed.

    BIRD dev q94 taught us about whitespace: an unnamed expression column is labelled by
    its own text, so re-serialising yields `( SELECT … )` versus `(SELECT …)`. q77 and
    q85 taught us about identifier quotes: `` T1.`FRPM Count (Ages 5-17)` * 100 / … ``
    versus the SQL-standard double-quoted form of the same expression. Exact label
    matching then fails to align the columns, alignment silently degrades to positional,
    and a column order the policy says to accept gets judged as a wrong answer.

    Only the label is normalised, never the values: this decides whether two projections
    are the same projection, and a judge that cannot tell that is stricter than it
    claims to be.
    """
    return re.sub(r"[`\"\[\]\s]", "", str(_normalise(name))).casefold()


def _align_columns(gold_cols: list[str], pred_cols: list[str]) -> list[int] | None:
    """Return `src` where src[j] is the pred column index matching gold column j."""
    if len(gold_cols) != len(pred_cols):
        return None
    n = len(gold_cols)
    if n == 0:
        return []
    if n > _MAX_PERM_COLUMNS:
        return list(range(n))  # too many to search; fall back to positional
    g = [_column_key(c) for c in gold_cols]
    p = [_column_key(c) for c in pred_cols]
    for cand in permutations(range(n)):
        if all(p[cand[j]] == g[j] for j in range(n)):
            return list(cand)
    return list(range(n))  # names uninformative (e.g. both unnamed expressions)


def _reorder(rows: list[list], src: list[int]) -> list[list]:
    return [[row[i] for i in src] for row in rows]


def score_execution(gold: Execution, pred: Execution, require_order: bool = False) -> Verdict:
    """`require_order` comes from the *question*, never from sniffing ORDER BY.

    Gold queries carry ORDER BY for display reasons on most items; enforcing that
    would penalise a correct answer to a question that asked for no order. And a
    top-k with tied sort keys has no single correct sequence, so ordering is only
    enforced where the task set says the question demanded it.
    """
    if not pred.ok:
        return Verdict(False, f"pred_{pred.error_type or 'execution_error'}", detail={"pred_error": pred.error[:300]})
    if not gold.ok:
        # a broken gold item is our fault, not the model's - surface it loudly
        return Verdict(False, "gold_broken", detail={"gold_error": gold.error[:300]})

    perm = _align_columns(gold.columns, pred.columns)
    # always reported: a "defect" that leaves the result untouched is not evidence
    # about the grader, and the calibration sweep needs to be able to tell
    shape = {"gold_rows": len(gold.rows), "pred_rows": len(pred.rows), "gold_columns": gold.columns, "pred_columns": pred.columns}
    if perm is None:
        return Verdict(False, "column_count_mismatch", detail=dict(shape))
    pred_rows = _reorder(pred.rows, perm) if len(gold.columns) > 1 else pred.rows

    if len(gold.rows) != len(pred_rows):
        return Verdict(False, "row_count_mismatch", detail=dict(shape))

    ordered = require_order
    trivial = len(gold.rows) == 0

    if _compare(gold.rows, pred_rows, ordered=ordered):
        return Verdict(
            not trivial,  # empty-vs-empty is excluded from the headline metric
            "match_trivial" if trivial else "match",
            trivial=trivial,
            detail={**shape, "ordered": ordered},
        )

    # right rows, wrong sequence: only possible when the question demanded an order
    if ordered and _compare(gold.rows, pred_rows, ordered=False):
        return Verdict(False, "order_wrong", detail={**shape, "gold_sample": gold.rows[:3], "pred_sample": pred_rows[:3]})

    return Verdict(False, "value_mismatch", detail={**shape, "gold_sample": gold.rows[:3], "pred_sample": pred_rows[:3]})


def _material_key(row: list) -> tuple:
    """A row signature where presentation is erased but meaning is not.

    57 and 57.0 are the same answer; a duplicated row or a different value is not.
    Floats are quantised to the grader's own tolerance, otherwise this comparator
    would be stricter than the thing it audits and every 1e-5 rounding residue
    would be reported as a judge miss. Stricter than the grader on strings: no case
    or whitespace folding, no date normalisation.
    """
    out = []
    for v in row:
        if v is None:
            out.append(("n",))
        elif isinstance(v, (bool, int, float)):
            out.append(("f", round(float(v), 4)))
        else:
            try:
                out.append(("f", round(float(v), 4)))
            except (TypeError, ValueError):
                out.append(("s", str(v)))
    return tuple(out)


def result_differs(a: Execution, b: Execution) -> bool:
    """True when the two executions return materially different answers.

    This used to compare `sorted(a.columns) != sorted(b.columns)` as well, which made it
    *stricter than the judge it exists to audit*: the judge aligns columns by name and
    ignores spelling noise in labels, while this function called `` `x` / `y` `` versus
    ``"x" / "y"`` a changed result even when every row was identical. That column of the
    calibration table is read as "did the defect reach the answer", so a definition
    drift here turns a real no-op into a reported miss - and it was invisible on
    home-made data, where the projection is always a named column. Found by running the
    judge against BIRD's databases (`scripts/bird_judge.py`).

    One definition now: align exactly the way the judge aligns, then compare multisets.
    """
    if a.ok != b.ok:
        return True
    if not a.ok:
        return False
    if len(a.columns) != len(b.columns):
        return True
    src = _align_columns(a.columns, b.columns) or list(range(len(a.columns)))
    if len(a.rows) != len(b.rows):
        return True
    aligned = [[row[i] for i in src] for row in b.rows]
    return sorted(_material_key(r) for r in a.rows) != sorted(_material_key(r) for r in aligned)


def grade(conn: sqlite3.Connection, gold_sql: str, pred_sql: str | None, require_order: bool = False) -> Verdict:
    if pred_sql is None:
        return Verdict(False, "no_sql_produced")
    gold = execute(conn, gold_sql)
    pred = execute(conn, pred_sql)
    return score_execution(gold, pred, require_order=require_order)
