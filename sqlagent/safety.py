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
        if isinstance(node, (exp.Into, exp.Returning)):
            raise SafetyViolation(f"read queries may not contain {node.key.upper()}")

    tables = referenced_tables(tree)
    for name in tables:
        if name.startswith("sqlite_"):
            raise SafetyViolation(f"access to internal catalog table '{name}' is not allowed")
    if known_tables is not None:
        unknown = tables - {t.lower() for t in known_tables}
        if unknown:
            raise SafetyViolation(f"unknown table(s): {sorted(unknown)}")

    return tree.sql(dialect=DIALECT)
