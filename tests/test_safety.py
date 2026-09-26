"""Safety guard tests: everything here is an attempt to make the database do
something a read-only agent should never be able to do."""

from __future__ import annotations

import json

import pytest

from sqlagent.safety import SafetyViolation, guard_read_only

KNOWN = {"users", "orders", "courses"}

MUST_BLOCK = [
    "DROP TABLE users",
    "DELETE FROM users WHERE 1=1",
    "UPDATE users SET city = 'x'",
    "INSERT INTO users(id, name, email) VALUES (9999, 'x', 'x@y')",
    "CREATE TABLE pwned (id INTEGER)",
    # The two shapes whose class names used to be in the write-node list without existing
    # in sqlglot. They are blocked by the read-only root key, and now that the dead names
    # are gone this is the only thing proving the shapes were never actually uncovered.
    "ALTER TABLE users RENAME TO people",
    "COPY INTO users FROM '/tmp/x.csv'",
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


def test_the_write_path_is_actually_tested_not_just_asked_about():
    """The third review's §9-2: `guard_read_only` is a pure function of a string, so
    the write path could always be tested without a model. It previously wasn't - the
    only evidence was twelve hand-picked samples, and the one real hole in it was found
    by a reviewer rather than by any of those twelve.

    This runs the same corpus the script publishes. It is $0, deterministic, and covers
    two directions: writes must not pass, and legitimate reads must not be blocked.
    The second had never been measured at all, and is the likelier real defect -
    an over-eager guard fails benchmark questions silently.
    """
    import os
    import re
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "guard_corpus.py"
    # PYTHONUTF8 travels to the child: the corpus prints Chinese, and a parent running
    # under -X utf8 while the child writes the console codepage decodes to mojibake.
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1"})
    assert proc.returncode == 0, f"guard corpus failed:\n{proc.stdout}\n{proc.stderr}"
    out = proc.stdout
    assert "穿透率 0.00%" in out, out
    assert "误拒   0" in out, out
    assert "192 条" in out, "every gold query in the task set should be exercised"
    # The distinction is the point: fail-closed rejections must not be counted as the
    # allowlist working, so the report has to keep them in a separate bucket.
    assert "UNPARSEABLE" in out and "fail-closed" in out
    # And the measurement has to carry its instrument. Every verdict above is sqlglot's
    # AST classification (the allowlist compares `tree.key` and node types), so after a
    # parser upgrade the two zeros are still printed but mean something else. Assert that
    # a version is reported - not which version, which would just be a pinned number.
    assert re.search(r"sqlglot \d+\.\d+", out), "guard_corpus must name the parser it judged with"


# --- function calls: the shape-only allowlist was not enough ---------------------------

def test_an_unmodelled_function_is_refused_without_being_named_anywhere():
    """The property that makes this a whitelist rather than a denylist: a name nobody has
    ever heard of is refused because the parser could not model it, not because someone
    added it to a list. `sqlite_exec` does not exist in SQLite and appears nowhere in the
    source - if this test ever passes only because of an explicit blocklist, the design has
    quietly become the thing this file's opening paragraph says a denylist is."""
    from sqlagent.safety import ALLOWED_UNMODELLED, guard_read_only

    for name in ("sqlite_exec", "system", "popen", "writeblob", "future_fileio_call"):
        assert name not in ALLOWED_UNMODELLED
        with pytest.raises(SafetyViolation) as exc:
            guard_read_only(f"SELECT {name}('x') FROM users")
        assert "not on the allowlist" in str(exc.value)


def test_the_functions_the_filesystem_needed_are_the_reason_this_exists():
    from sqlagent.safety import guard_read_only

    for sql in ("SELECT writefile('/tmp/x', email) FROM users",
                "SELECT readfile('/etc/passwd') FROM users",
                "SELECT load_extension('vec0') FROM users",
                "SELECT eval('DROP TABLE users') FROM users"):
        with pytest.raises(SafetyViolation, match="allowlist"):
            guard_read_only(sql)


def test_the_allowlisted_names_are_exactly_what_the_real_corpora_need():
    """Two names, measured over 1,726 hand-written and generated gold queries: the
    self-built set uses no unmodelled function at all, BIRD uses `julianday` and
    `datetime`. Anything added to this set needs that kind of evidence, not a use case."""
    from sqlagent.safety import ALLOWED_UNMODELLED, guard_read_only

    assert set(ALLOWED_UNMODELLED) == {"julianday", "datetime"}, ALLOWED_UNMODELLED
    for sql in ("SELECT julianday('now') FROM users",
                "SELECT datetime(created_at) FROM users",
                "SELECT COUNT(*), AVG(rating), ROUND(MAX(price), 2), GROUP_CONCAT(name) FROM users",
                "SELECT CASE WHEN SUM(x) > 0 THEN 1 ELSE 0 END FROM users",
                "SELECT CAST(id AS TEXT), SUBSTR(name, 1, 3), LOWER(city), TRIM(email) FROM users"):
        guard_read_only(sql)


def test_every_gold_query_in_the_benchmark_still_survives_the_guard():
    """The failure mode of a tighter guard is not a breach, it is a silently unanswerable
    benchmark: a false-rejected gold scores 0 and reads as a model failure. This is the
    same measurement `scripts/guard_corpus.py` publishes, asserted directly so a regression
    names the query rather than a percentage."""
    from pathlib import Path
    from sqlagent.safety import guard_read_only

    root = Path(__file__).resolve().parent.parent
    rows = [json.loads(l) for l in (root / "data" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows, "the task set is missing; run `python -m sqlagent.data.build_tasks`"
    rejected = [r["id"] for r in rows if not _passes(guard_read_only, r["gold_sql"])]
    assert not rejected, f"{len(rejected)} gold queries now fail the guard: {rejected[:5]}"


def _passes(fn, sql: str) -> bool:
    try:
        fn(sql)
        return True
    except Exception:
        return False


def test_every_write_node_name_still_resolves_on_the_installed_sqlglot():
    """Fifth review, F6: `safety._KNOWN_MISSING` was computed and read by nobody - a check
    that reports a hole instead of closing one. Two of the names in the tuple (`AlterTable`,
    `CopyInto`) turned out not to exist in sqlglot at all, so `WRITE_NODES` had been built
    from 25 classes while the list advertised 27.

    `WRITE_NODES` is built with `getattr(exp, name, None)` and **silently drops** anything a
    future sqlglot renames: the guard would lose a write shape and every other test here
    would stay green. So the names are pinned to resolve, and the count is pinned so a
    rename cannot pass by trading one class for another. The assertion lives in a test
    rather than raising at import time on purpose - an older install should fail a check,
    not refuse to start."""
    from sqlglot import exp

    from sqlagent.safety import _WRITE_NODE_NAMES, WRITE_NODES

    unresolved = [n for n in _WRITE_NODE_NAMES if getattr(exp, n, None) is None]
    assert unresolved == [], f"sqlglot no longer models these write shapes: {unresolved}"
    assert len(WRITE_NODES) == len(_WRITE_NODE_NAMES) == 25
    for name, cls in zip(_WRITE_NODE_NAMES, WRITE_NODES):
        assert getattr(exp, name) is cls, f"{name} no longer maps to the class the guard uses"
