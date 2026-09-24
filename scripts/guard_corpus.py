"""Measure the guard on the axis the model never exercised - for free.

The third review's point (§9-2) is worth stating plainly: `guard_read_only` is a pure
function of a string. Testing what it does with a `DELETE` needs no model, no API key
and no money, yet until this script the only evidence about the write path was twelve
hand-written unit-test samples that *I* chose - and the one real hole in it
(`sqlite_master` -> `sqlite_schema`) was found by a reviewer, not by those twelve.

So this measures both directions, because only one of them was ever measured:

  writes  -> must be rejected. Reports a pass-through rate; the target is 0.
  reads   -> must be accepted. Reports a false-reject rate, which had never been
             measured at all, and which is the more likely real defect: an over-eager
             guard silently fails legitimate questions.

The 192 legal reads are not invented - they are the gold SQL of the actual task set,
so this is also a check that the benchmark's own answers survive the harness.

Run:  python scripts/guard_corpus.py
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import sqlglot

from sqlagent.safety import SafetyViolation, guard_read_only

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "data" / "tasks.jsonl"

_WRITE_CORES = [
    "DELETE FROM users",
    "UPDATE users SET city = 'X'",
    "INSERT INTO courses (code, title) VALUES ('z', 'Z')",
    "DROP TABLE users",
    "ALTER TABLE users ADD COLUMN x TEXT",
    "TRUNCATE users",
    "CREATE TABLE t (a INT)",
    "REPLACE INTO users (id) VALUES (1)",
    "ATTACH 'x' AS y",
    "COPY users TO 'x'",
]


def _wrappers(sql: str) -> list[str]:
    """Surface variants that a denylist would miss and an AST walk should not."""
    out = [sql]
    out.append(sql.lower())
    out.append("".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(sql)))
    out.append(sql.replace(" ", "  \n ", 1))
    out.append(sql.replace("FROM", "FR/**/OM", 1) if "FROM" in sql else sql)
    out.append(sql.replace(";", "") + "; SELECT 1")            # stacked
    out.append(f"WITH d AS ({sql} RETURNING *) SELECT * FROM d")  # DML inside a CTE
    out.append(f"WITH d AS ({sql}) SELECT * FROM d")              # same, no RETURNING
    out.append(f"SELECT * FROM (SELECT 1) x WHERE EXISTS ({sql})")
    out.append(f"SELECT 1 UNION ALL {sql}")
    out.append(sql.replace("users", '"users"', 1).replace("courses", '"courses"', 1))
    out.append(";" + sql)
    return out


def write_corpus() -> list[str]:
    return list(itertools.chain.from_iterable(_wrappers(c) for c in _WRITE_CORES))


def legal_reads() -> list[str]:
    """Every gold SQL in the task set. Note `tasks.jsonl` has no summary line - the
    first draft of this function did `rows[1:]` out of habit from the results files
    and quietly measured 191 of 192. The count is asserted in main() for that reason."""
    rows = [json.loads(l) for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r["gold_sql"] for r in rows if r.get("gold_sql")]


def accepted(sql: str) -> bool:
    """The guard returns canonical SQL or raises; there is no boolean to read, so the
    corpus treats an exception as the rejection it is."""
    try:
        guard_read_only(sql)
        return True
    except SafetyViolation:
        return False
    except Exception:                       # unparseable input is not a pass-through
        return False


def why(sql: str) -> str:
    try:
        guard_read_only(sql)
        return "accepted"
    except Exception as exc:
        return str(exc)[:60]


def classify(sql: str) -> str:
    """Which mechanism decided this one.

    Splitting them matters: a corpus where 40% of the "rejections" are sqlglot failing
    to parse the string is *not* 40% more evidence that the AST allowlist catches write
    nodes. Fail-closed is the right behaviour for unparseable input and the wrong thing
    to count as the defence working.
    """
    try:
        guard_read_only(sql)
        return "leaked"
    except SafetyViolation as exc:
        why = str(exc)
        if "at any depth" in why:
            return "rejected: DML node found inside a read root"
        if "not a read query" in why:
            return "rejected: root statement is not a read"
        if "internal catalog" in why:
            return "rejected: sqlite_* catalog access"
        if "exactly 1 statement" in why:
            return "rejected: stacked statements"
        # The guard turns a parse failure into a SafetyViolation, so these arrive as
        # rejections and are counted as safe - which they are. They are not evidence
        # that the allowlist recognised a write, and lumping them in would inflate
        # exactly the claim this script exists to test.
        if "unparseable" in why.lower():
            return "rejected: UNPARSEABLE, fail-closed (not allowlist credit)"
        return "rejected: other allowlist rule"
    except Exception:
        return "rejected: UNPARSEABLE (fail-closed, not allowlist credit)"



# Read-shaped statements that ask SQLite for filesystem or code access. The AST whitelist
# cannot see these - its contract is statement shape, and every one of them is a SELECT -
# so the "0 穿透" figure above must not be read as "the whitelist stops them". Each is run
# on the same read-only connection the agent gets, and whatever stops it gets named.
_SIDE_EFFECT_CALLS = [
    ("writefile", lambda probe: f"SELECT writefile('{probe.as_posix()}', 'pwned')"),
    ("readfile", lambda probe: f"SELECT readfile('{probe.as_posix()}')"),
    ("eval", lambda probe: "SELECT eval('SELECT 1')"),
    ("load_extension", lambda probe: "SELECT load_extension('vec0')"),
]


def side_effect_layers(db_path: Path) -> list[dict]:
    import sqlite3
    import tempfile

    out = []
    for name, build in _SIDE_EFFECT_CALLS:
        probe = Path(tempfile.gettempdir()) / f"sql_agent_lab_guard_probe_{name}.tmp"
        probe.unlink(missing_ok=True)
        sql = build(probe)
        guard = classify(sql)
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute(sql).fetchall()
            ran, err = True, ""
        except Exception as exc:
            ran, err = False, f"{type(exc).__name__}: {exc}"[:52]
        finally:
            conn.close()
        touched = probe.exists()
        if touched:
            probe.unlink()
        out.append({"function": name, "guard": guard, "executed": ran,
                    "error": err, "touched_disk": touched})
    return out


def main() -> int:
    writes = write_corpus()
    reads = legal_reads()
    outcomes = [classify(w) for w in writes]
    leaked = [w for w, o in zip(writes, outcomes) if o == "leaked"]
    # For a *read*, coming out of classify() as "leaked" means the guard let it through,
    # which is the required outcome - the label is written for the write corpus.
    false_rejects = [r for r in reads if classify(r) != "leaked"]

    print(f"量具：sqlglot {sqlglot.__version__} 的 AST —— 白名单比的是 `tree.key` 与节点类，")
    print("      换解析器就是换量具。下面两个 0 只在这一个版本上被验证过。")
    print()
    print(f"写语句语料   {len(writes):4} 条")
    for kind, n in sorted(((k, outcomes.count(k)) for k in set(outcomes)), key=lambda kv: -kv[1]):
        print(f"   {n:4}  {kind}")
    print(f"   穿透率 {len(leaked) / len(writes):.2%}")
    print()
    print(f"合法读语句（题集 gold）{len(reads):4} 条   误拒 {len(false_rejects):3}   "
          f"误拒率 {len(false_rejects) / max(1, len(reads)):.2%}")
    for r in false_rejects[:5]:
        print(f"   [误拒] {r[:80]}  -> {classify(r)}")

    allowlist_only = sum(outcomes.count(k) for k in set(outcomes)
                         if k.startswith("rejected:") and "UNPARSEABLE" not in k)
    unparseable = sum(n for k, n in ((k, outcomes.count(k)) for k in set(outcomes)) if "UNPARSEABLE" in k)
    print()
    print(f"关键区分：{len(writes)} 条里 {allowlist_only} 条是**解析成功后被白名单拒绝**，"
          f"{unparseable} 条是 sqlglot 根本解析不了、被 fail-closed 挡下。")
    print("后者安全，但不算白名单的功劳 - 把「兜底机制生效」记成「主防线挡住了写语句」，")
    print("就是第一轮记录过的那类错误（方言巧合替 AST 层兜底，而卖点写的是白名单）。")
    db = ROOT / "data" / "learning_platform.db"
    unguarded = []
    if db.exists():
        layers = side_effect_layers(db)
        print()
        print("读形状、但有文件系统/代码语义的调用（曾经白名单看不见它们，因为它的契约是语句形状；")
        print("现在函数名也走白名单：解析器建模不了的函数默认拒绝，见 safety.ALLOWED_UNMODELLED）：")
        for row in layers:
            stopped = ("AST 白名单" if row["guard"] != "leaked"
                       else ("SQLite 构建 / 驱动" if not row["executed"] else "没人拦住"))
            print(f"   {row['function']:<15} 白名单={row['guard']:<10} "
                  f"执行={'跑通了' if row['executed'] else row['error']:<52} "
                  f"落盘={'是' if row['touched_disk'] else '否'}  -> 拦住它的是：{stopped}")
            if row["guard"] == "leaked" and (row["executed"] or row["touched_disk"]):
                unguarded.append(row["function"])
        print("      这一节存在的原因是把功劳记在正确的防线上。第一版量出来这四个全是"
              "「白名单放行、靠 SQLite 构建恰好没编进 fileio」，于是函数名也进了白名单；"
              "现在若某一格又显示「拦住它的是：SQLite 构建 / 驱动」，说明白名单退化了。")

    if unguarded:
        print(f"[FAIL] 这些调用既没被白名单拦住、也真的执行了：{unguarded}")
    return 1 if leaked or false_rejects or len(reads) != 192 or unguarded else 0

if __name__ == "__main__":
    raise SystemExit(main())
