"""SQL safety guard.

The model is untrusted input. Before anything reaches the connection we require
exactly one statement, whose root is a read query, whose tables all exist in the
schema we disclosed, and which never touches `sqlite_*` internals.

Note the shape of the check: we *allowlist* SELECT/UNION/INTERSECT/EXCEPT rather
than denylisting DROP/DELETE/UPDATE. A denylist only has to be missed once.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DIALECT = "sqlite"
_READ_ROOT_KEYS = {"select", "union", "intersect", "except", "values"}

# Nodes that mark a statement as doing something other than reading, at *any*
# depth. The root-key allowlist alone was not enough: `WITH d AS (DELETE FROM
# users) SELECT * FROM d` has a SELECT root and walked straight through, as did the
# UPDATE and INSERT forms. Nothing harmful happened - the connection is `mode=ro`
# and SQLite rejects DML-in-CTE at parse time - but that is defence in depth
# covering a hole in the layer that advertised itself as the check.
_WRITE_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "DropPartition", "Create", "Alter",
    "AlterTable", "TruncateTable", "Merge", "Copy", "CopyInto", "Grant", "Revoke",
    "Command", "Pragma", "Attach", "Detach", "Refresh", "Set", "Transaction",
    "Commit", "Rollback", "UsingData", "Returning", "Into", "Export",
)
WRITE_NODES: tuple[type, ...] = tuple(
    t for t in (getattr(exp, name, None) for name in _WRITE_NODE_NAMES)
    if isinstance(t, type) and issubclass(t, exp.Expression)
)
_KNOWN_MISSING = [n for n in _WRITE_NODE_NAMES if getattr(exp, n, None) is None]


class SafetyViolation(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_one(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except Exception as exc:  # sqlglot raises many concrete parser errors
        raise SafetyViolation(f"unparseable SQL: {exc}") from exc
    if len(statements) != 1:
        raise SafetyViolation(f"expected exactly 1 statement, got {len(statements)} (stacked statements are rejected)")
    return statements[0]


def referenced_tables(tree: exp.Expression) -> set[str]:
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out = set()
    for t in tree.find_all(exp.Table):
        name = (t.name or "").lower()
        if not name or name in cte_names:
            continue
        out.add(name)
    return out


def guard_read_only(sql: str, known_tables: set[str] | None = None) -> str:
    """Return the canonicalised SQL. Raises SafetyViolation otherwise."""
    tree = parse_one(sql)

    if tree.key not in _READ_ROOT_KEYS:
        raise SafetyViolation(f"statement type '{tree.key}' is not a read query")

    for node in tree.walk():
        bad = next((n for n in WRITE_NODES if isinstance(node, n)), None)
        if bad is not None:
            raise SafetyViolation(
                f"read queries may not contain {bad.__name__.upper()} at any depth "
                f"(found inside a '{tree.key}' root)"
            )

    tables = referenced_tables(tree)
    for name in tables:
        if name.startswith("sqlite_"):
            raise SafetyViolation(f"access to internal catalog table '{name}' is not allowed")
    if known_tables is not None:
        unknown = tables - {t.lower() for t in known_tables}
        if unknown:
            raise SafetyViolation(f"unknown table(s): {sorted(unknown)}")

    return tree.sql(dialect=DIALECT)
