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


def test_return_canonical_sql_is_reparsable():
    canonical = guard_read_only("select *   from   USERS", known_tables=KNOWN)
    assert guard_read_only(canonical, known_tables=KNOWN)
