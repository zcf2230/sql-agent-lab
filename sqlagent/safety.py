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
# `AlterTable` and `CopyInto` used to be in here and are not: sqlglot has no such classes,
# so both were silently dropped by the `getattr` below and the only thing they ever produced
# was a `_KNOWN_MISSING` list nobody read. The shapes they were meant to catch are real and
# still blocked - `ALTER TABLE ...` parses as `Alter`, `COPY INTO ...` as `Copy` - and
# `tests/test_safety.py` now carries those two statements as cases, which is where the
# coverage actually lives.
_WRITE_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "DropPartition", "Create", "Alter",
    "TruncateTable", "Merge", "Copy", "Grant", "Revoke",
    "Command", "Pragma", "Attach", "Detach", "Refresh", "Set", "Transaction",
    "Commit", "Rollback", "UsingData", "Returning", "Into", "Export",
)
WRITE_NODES: tuple[type, ...] = tuple(
    t for t in (getattr(exp, name, None) for name in _WRITE_NODE_NAMES)
    if isinstance(t, type) and issubclass(t, exp.Expression)
)

# Functions the parser can model are SQL's own vocabulary. Anything it cannot model comes
# back as `Anonymous` - an arbitrary name the *engine* resolves at run time - and that is
# the hole the shape-only allowlist left open: `SELECT writefile('/tmp/x', data) FROM t` is
# a plain SELECT over a disclosed table, so every existing check passed it, and the only
# thing that stopped it was this machine's SQLite build never having file I/O compiled in.
#
# The allowlist is closed rather than long, and it is closed on evidence: over 1,726 real
# gold queries (192 self-built + all 1,534 BIRD dev) the self-built set uses **no**
# unmodelled function at all, and BIRD uses exactly two. So refusing everything else costs
# zero legitimate answers on either corpus - measured by `scripts/guard_corpus.py` (the
# 192) and by `scripts/bird_judge.py` gold-vs-gold (the 1,534, which execute through this
# same guard).
ALLOWED_UNMODELLED = frozenset({"julianday", "datetime"})


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

    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
            if name not in ALLOWED_UNMODELLED:
                raise SafetyViolation(
                    f"function '{name}' is not on the allowlist: the parser could not model "
                    "it, so the engine resolves it at run time - which is how writefile, "
                    "readfile and load_extension reach the filesystem from a plain SELECT")

    tables = referenced_tables(tree)
    for name in tables:
        if name.startswith("sqlite_"):
            raise SafetyViolation(f"access to internal catalog table '{name}' is not allowed")
    if known_tables is not None:
        unknown = tables - {t.lower() for t in known_tables}
        if unknown:
            raise SafetyViolation(f"unknown table(s): {sorted(unknown)}")

    return tree.sql(dialect=DIALECT)
