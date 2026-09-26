"""Re-judge stored finance answers under two column policies ($0, offline).

The first live run (results/fin-deepseek-chat.jsonl) came back 17.65% strict with
47/68 answerable failures being `column_count_mismatch`: the model systematically
returns context columns (`short_name, report_date, value, value_yi`) where the
gold is a single answer column. Same disease the main benchmark measured on BIRD
(§26), but at 69% of failures it dominates the headline, so the strict number
alone reads as "the model cannot do this" when part of the truth is "the model
answers with a research-friendly table instead of a naked number".

This script is the fin analogue of `scripts/bird_gap.py`: it re-executes the
stored `final_sql` against the same committed snapshot and reports BOTH policies,
with the strict verdict re-computed first as a self-check (it must reproduce the
stored verdicts bit-for-bit, or the stored file and this script disagree and
nothing else in the output can be trusted).

Policies:
  strict  - the main judge, unchanged: column sets must match exactly.
  relaxed - a column SUBSET of the prediction aligns with gold and matches under
            the same judge. Implemented by projecting the prediction onto each
            candidate subset and calling `score_execution` itself, so the relaxed
            arm inherits exactly the same value tolerance, row multiset rules and
            require_order handling - no second comparator to drift.

For absence tasks the mechanical judge is re-computed and each non-strict case is
printed with its stored answer text: the strict policy counts an evidence-shaped
row (history dump, verification counts, min/max bounds) as fabricated even when
the prose explicitly declines to answer. The triage of those rows is a human read
recorded in docs/HANDOFF.md §27 - the script's job is to lay the evidence out.

$0 by construction: no model is called, everything is re-execution of stored SQL.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlagent.eval.absence import grade_absent  # noqa: E402
from sqlagent.eval.scoring import execute, score_execution  # noqa: E402

DB = ROOT / "data" / "astock.db"
RESULTS = ROOT / "results"
OUT = RESULTS / "fin-gap.jsonl"
_MAX_EXTRA_SUBSETS = 16  # pred with more columns than that is not worth enumerating


def relaxed_verdict(conn, gold_sql: str, pred_sql: str | None, require_order: bool):
    """Column-subset tolerance on top of the unchanged judge."""
    if pred_sql is None:
        return None
    gold = execute(conn, gold_sql)
    pred = execute(conn, pred_sql)
    if not pred.ok or not gold.ok:
        return None
    k = len(gold.columns)
    n = len(pred.columns)
    if n == k:
        return None  # strict already covers this shape
    if n < k or n > _MAX_EXTRA_SUBSETS + k:
        return None
    best = None
    for subset in combinations(range(n), k):
        projected = Execution_of(pred, subset)
        v = score_execution(gold, projected, require_order=require_order)
        if v.correct:
            best = v
            break
    return best


def Execution_of(pred, subset):
    from sqlagent.eval.scoring import Execution

    cols = [pred.columns[i] for i in subset]
    rows = [[row[i] for i in subset] for row in pred.rows]
    return Execution(ok=True, columns=cols, rows=rows)


def main() -> int:
    result_files = sorted(RESULTS.glob("fin-*.jsonl"))
    result_files = [p for p in result_files if "gap" not in p.name and "mock" not in p.name]
    if not result_files:
        raise SystemExit("no live finance result file found")
    src = result_files[-1]
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary, tasks = rows[0]["_summary"], rows[1:]
    model = f"{summary['provider']}/{summary['model']}"
    print(f"re-judging {len(tasks)} stored answers from {src.name} ({model})\n")

    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    out_rows, strict_mismatch = [], 0
    for t in tasks:
        gold_sql = next((json.loads(l)["gold_sql"] for l in
                         (ROOT / "data" / "tasks_finance.jsonl").read_text(encoding="utf-8").splitlines()
                         if json.loads(l)["id"] == t["id"]), None)
        rec = {"id": t["id"], "category": t["category"], "expect_absent": t["expect_absent"],
               "stored_reason": t["reason"], "stored_correct": t["correct"]}
        if t["expect_absent"]:
            v = grade_absent(t.get("final_sql"), conn=conn)
            rec["strict_correct"], rec["strict_reason"] = v.correct, v.reason
            rec["answer_text"] = (t.get("answer_text") or "")[:300]
        else:
            v = score_execution(execute(conn, gold_sql), execute(conn, t["final_sql"]),
                                require_order=t.get("require_order", False))
            rec["strict_correct"], rec["strict_reason"] = v.correct, v.reason
            if v.reason != t["reason"]:
                strict_mismatch += 1
            rv = relaxed_verdict(conn, gold_sql, t["final_sql"], t.get("require_order", False))
            rec["relaxed_correct"] = bool(rv.correct) if rv else False
            rec["relaxed_matched"] = bool(rv.correct) if rv else None
        out_rows.append(rec)

    answerable = [r for r in out_rows if not r["expect_absent"]]
    absent = [r for r in out_rows if r["expect_absent"]]
    strict_ok = [r for r in answerable if r["strict_correct"]]
    relaxed_ok = [r for r in answerable if r["strict_correct"] or r["relaxed_correct"]]
    print(f"self-check: strict re-computation disagrees with stored verdicts on {strict_mismatch} tasks")
    print(f"answerable n={len(answerable)}  strict={len(strict_ok)/len(answerable):.4f}  "
          f"relaxed={len(relaxed_ok)/len(answerable):.4f}")
    by_cat = {}
    for r in answerable:
        d = by_cat.setdefault(r["category"], {"n": 0, "strict": 0, "relaxed": 0})
        d["n"] += 1
        d["strict"] += r["strict_correct"]
        d["relaxed"] += r["strict_correct"] or r["relaxed_correct"]
    print(f"{'category':<18} {'n':>3} {'strict':>7} {'relaxed':>8}")
    for cat, d in sorted(by_cat.items()):
        print(f"{cat:<18} {d['n']:>3} {d['strict']/d['n']:>7.2f} {d['relaxed']/d['n']:>8.2f}")
    print(f"\nabsence n={len(absent)}  strict={sum(r['strict_correct'] for r in absent)}/{len(absent)}"
          f"  reasons={dict(Counter(r['strict_reason'] for r in absent))}")
    print("non-strict absence cases (answer text laid out for human triage):")
    for r in absent:
        if not r["strict_correct"]:
            print(f"  --- {r['id']} [{r['strict_reason']}]")
            print(f"      {r['answer_text'][:220]}")

    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"_summary": {"source": src.name, "model": model,
                                          "answerable_strict": len(strict_ok) / len(answerable),
                                          "answerable_relaxed": len(relaxed_ok) / len(answerable),
                                          "strict_selfcheck_mismatches": strict_mismatch}},
                            ensure_ascii=False) + "\n")
        for r in out_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
