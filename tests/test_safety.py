"""Safety guard tests: everything here is an attempt to make the database do
something a read-only agent should never be able to do."""

from __future__ import annotations

import pytest

from sqlagent.safety import SafetyViolation, guard_read_only

KNOWN = {"users", "orders", "courses"}

MUST_BLOCK = [
    "DROP TABLE users",
    "DELETE FROM users WHERE 1=1",
    "UPDATE users SET city = 'x'",
    "INSERT INTO users(id, name, email) VALUES (9999, 'x', 'x@y')",
    "CREATE TABLE pwned (id INTEGER)",
    "SELECT 1; DROP TABLE users",
    "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM users INTO 'x.csv'",
    "ATTACH DATABASE 'x.db' AS y",
    "PRAGMA user_version = 1",
    "SELECT * FROM secret_table",
]


@pytest.mark.parametrize("sql", MUST_BLOCK)
def test_rejected(sql):
    with pytest.raises(SafetyViolation):
        guard_read_only(sql, known_tables=KNOWN)


MUST_ALLOW = [
    "SELECT * FROM users",
    "SELECT id FROM users UNION SELECT id FROM orders",
    "WITH t AS (SELECT id FROM users) SELECT * FROM t",
    "SELECT id, RANK() OVER (ORDER BY id) AS r FROM users",
    "SELECT DISTINCT city FROM users ORDER BY city LIMIT 10",
    "SELECT * FROM users u JOIN orders o ON o.user_id = u.id WHERE o.amount > 100",
]


@pytest.mark.parametrize("sql", MUST_ALLOW)
def test_allowed(sql):
    assert guard_read_only(sql, known_tables=KNOWN)


def test_cte_names_are_not_treated_as_real_tables():
    sql = "WITH t AS (SELECT id FROM users) SELECT * FROM t"
    assert guard_read_only(sql, known_tables=KNOWN)


def test_cte_wrapped_dml_is_rejected_at_any_depth():
    """The hole a reviewer found. These three all had a SELECT root and walked past
    the original check, which only inspected the root node plus INTO/RETURNING.

    Nothing harmful happened even when they passed: SQLite rejects DML-in-CTE and the
    connection is `mode=ro`. That is the second and third layer catching a gap in the
    layer that was advertised as *the* check - which is why the guard, not just the
    docs, had to change.
    """
    for sql in (
        "WITH d AS (DELETE FROM users) SELECT * FROM d",
        "WITH u AS (UPDATE courses SET price = 0 RETURNING id) SELECT * FROM u",
        "WITH i AS (INSERT INTO users(id, name, email) VALUES (1,'a','b')) SELECT * FROM i",
        "CREATE TABLE bak AS SELECT * FROM users",
        "SELECT id FROM users INTO OUTFILE '/tmp/x.csv'",
        "VACUUM",
        "GRANT SELECT ON users TO someone",
    ):
        with pytest.raises(SafetyViolation):
            guard_read_only(sql, known_tables=KNOWN)


def test_write_node_list_has_not_silently_rotted():
    """A guard built from class *names* fails quietly when the library renames them.

    `getattr(exp, name, None)` skips a missing name without error, so an upgrade
    could thin the defence to nothing while every test still passed. This asserts the
    list is still mostly resolvable and forces a conscious edit if it is not.
    """
    from sqlagent.safety import WRITE_NODES, _WRITE_NODE_NAMES

    assert len(WRITE_NODES) >= len(_WRITE_NODE_NAMES) - 4, (
        f"only {len(WRITE_NODES)}/{len(_WRITE_NODE_NAMES)} node names resolved; "
        "sqlglot likely renamed things - re-check the list, do not just widen this bound"
    )
    names = {n.__name__ for n in WRITE_NODES}
    for critical in ("Insert", "Update", "Delete", "Drop", "Create", "Returning", "Into"):
        assert critical in names, f"{critical} no longer covered - that is a live hole"


def test_allowlisted_reads_are_not_collateral_damage():
    """A guard that blocks everything is indistinguishable from a working guard."""
    for sql in (
        "SELECT a.amount - COALESCE(a.refund_amount, 0) AS d FROM orders a",
        "SELECT id, RANK() OVER (ORDER BY id) AS r FROM users",
        "WITH t AS (SELECT id FROM users) SELECT * FROM t",
        "SELECT category, COUNT(*) AS n FROM courses GROUP BY category ORDER BY n DESC",
    ):
        assert guard_read_only(sql, known_tables=KNOWN)


def test_return_canonical_sql_is_reparsable():
    canonical = guard_read_only("select *   from   USERS", known_tables=KNOWN)
    assert guard_read_only(canonical, known_tables=KNOWN)
