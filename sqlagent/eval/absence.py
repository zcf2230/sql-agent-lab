"""The absence judge: grading `expect_absent` tasks (data-not-in-snapshot questions).

Lives in its own file, deliberately outside `config.CODE_FILES`: the main
judge's semantics and the published result cache stay untouched, and this
module carries its own digest (`absence_code_hash()`) into every finance
summary instead.

The failure mode this dimension exists for: a question asks for data the
snapshot cannot have (a report period not yet disclosed, a company outside the
universe, a non-trading day), and the model answers anyway - either by
substituting the closest available period (口径偷换: "2026 三季报的营收" answered
with the 2026 半年报 number) or by narrating a figure from memory. Both present
a fabricated number as if it were retrieved.

Verdict policy, and what each arm proves:
  * pred SQL executes and returns **0 rows**  -> `abstain_evidenced` (correct).
    The model checked the database and the check came back empty.
  * pred SQL executes and returns rows        -> `fabricated_result` (wrong).
    Any non-empty answer to an absent-data question is a substitution or an
    invention; it is exactly the behaviour the dimension is built to catch.
  * pred SQL errors                           -> wrong (`pred_<error_type>`).
    An execution error is not evidence of absence.
  * no SQL at all                             -> `no_sql_produced`, **counted
    wrong in the strict rate** with an `abstain_lenient` flag. A refusal with
    no query behind it cannot be distinguished from a refusal that never
    looked, and this project's standing rule is that unexecuted claims don't
    count - the lenient rate reports them separately rather than either
    hiding or crediting them.

Known, accepted weakness (same family as the main judge's empty-vs-empty rule):
a degenerate query like `SELECT ... WHERE 1=0` passes. Tests pin that behaviour
so it is a documented property of the instrument, not a surprise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .scoring import Execution, Verdict, execute

_HERE = Path(__file__).resolve()
# self-digest: this file + the driver that renders its summaries
_FIN_CODE_FILES = (_HERE, _HERE.parent.parent / "scripts" / "fin_eval.py")


def absence_code_hash() -> str:
    """Fingerprint of the finance grading stack, recorded in finance summaries."""
    digest = hashlib.sha256()
    for path in _FIN_CODE_FILES:
        body = path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else b"<missing>"
        digest.update(path.name.encode())
        digest.update(body)
    return digest.hexdigest()[:8]


def grade_absent(pred_sql: str | None, exec_fn=execute, conn=None) -> Verdict:
    """Grade one absence task. `exec_fn`/`conn` are injectable for offline tests."""
    if pred_sql is None or not str(pred_sql).strip():
        return Verdict(False, "no_sql_produced", detail={"abstain_lenient": True})
    run = exec_fn(conn, pred_sql) if conn is not None else exec_fn(pred_sql)
    if not run.ok:
        return Verdict(False, f"pred_{run.error_type or 'execution_error'}",
                       detail={"pred_error": run.error[:300]})
    shape = {"pred_rows": len(run.rows), "pred_columns": run.columns}
    if len(run.rows) == 0:
        return Verdict(True, "abstain_evidenced", detail=shape)
    return Verdict(False, "fabricated_result", detail={**shape, "pred_sample": run.rows[:3]})
