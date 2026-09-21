"""Read-only SQLite access layer.

Two rules enforced here rather than trusted to the model:
  1. the connection is opened in URI `mode=ro`, so a stray write fails at the
     SQLite layer even if the AST guard is bypassed;
  2. every query runs under a progress-handler deadline and a hard row cap, so a
     cartesian blowup cannot hang the eval runner or eat the whole context.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


class QueryError(Exception):
    """A database-side failure whose message is fed back to the model verbatim."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def as_tool_error(self) -> dict:
        return {"ok": False, "error_type": self.kind, "error": self.message}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: float
    ok: bool = True

    def as_tool_payload(self, preview_rows: int = 20) -> dict:
        return {
            "ok": True,
            "columns": self.columns,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "preview": self.rows[:preview_rows],
        }

    def canonical(self) -> list[list]:
        return [list(r) for r in self.rows]


def connect_ro(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path} (run `python -m sqlagent.data.build_db` first)")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 2000")
    return conn


def list_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def describe(conn: sqlite3.Connection, table: str) -> dict:
    """Schema + row count for one table. `table` is validated, never interpolated blindly."""
    if table not in set(list_tables(conn)):
        raise QueryError("unknown_table", f"table '{table}' does not exist")
    cols = [
        {"name": r[1], "type": r[2], "notnull": bool(r[3])}
        for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return {"table": table, "row_count": n, "columns": cols}


def sample_values(conn: sqlite3.Connection, table: str, column: str, limit: int = 8) -> dict:
    """Real stored values. Models guess 'paid' when the column actually holds 'PAID'."""
    if table not in set(list_tables(conn)):
        raise QueryError("unknown_table", f"table '{table}' does not exist")
    known = {c["name"] for c in describe(conn, table)["columns"]}
    if column not in known:
        raise QueryError("unknown_column", f"column '{column}' not in {table}")
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL '
        f"ORDER BY 1 LIMIT ?",
        (limit,),
    ).fetchall()
    nulls = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL').fetchone()[0]
    return {"table": table, "column": column, "distinct_sample": [r[0] for r in rows], "null_count": nulls}


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 200, timeout_s: float = 5.0) -> QueryResult:
    start = time.perf_counter()
    deadline = start + timeout_s

    def _interrupt() -> int:
        return 1 if time.perf_counter() > deadline else 0

    conn.set_progress_handler(_interrupt, 2000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [list(r) for r in fetched[:max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        kind = "timeout" if "interrupted" in msg.lower() else "operational_error"
        raise QueryError(kind, msg) from exc
    except sqlite3.Error as exc:
        raise QueryError("database_error", str(exc)) from exc
    finally:
        conn.set_progress_handler(None, 0)
