"""How much of the 89% -> 41.7% gap is the column policy itself? $0, no model.

§20 reported the gap and §22 proved one sentence of prompt closes 11.6pp of it, but the
repository's own "what I still do not know" list says the gap was never *decomposed*: the
failure table said 22 of 35 misses were `column_count_mismatch`, which is a label on the
judge's exit path, not a measurement of what relaxing that rule would buy.

So this runs the stored 60 answers through a third policy - **gold's columns must all be
present, extra predicted columns are dropped instead of failing the comparison** - and
reports where pass@1 lands. Everything else is the shipped judge: same normalisation, same
1e-4 tolerance, same multiset comparison, duplicates still wrong.

Read the number with its boundary: a lenient-column pass@1 is what the *same* answers score
under a policy this project deliberately rejected, because a JOIN that drags in the primary
key is a different answer from the one the question asked. It decomposes the gap. It does
not make 41.7% a different result.

    python scripts/bird_gap.py [--dev-dir ../.external/dev]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bird_judge import bird_databases, load_gold  # noqa: E402

from sqlagent import db  # noqa: E402
from sqlagent.eval.scoring import _column_key, _compare, execute, score_execution  # noqa: E402


def lenient_correct(gold, pred, strict: bool) -> tuple[bool, str]:
    """Same comparison, one relaxation: predicted may carry columns gold does not have.

    `strict` is passed in and short-circuits, because a relaxation that scores *lower* than
    the policy it relaxes is not a relaxation. The first version of this function matched
    gold columns to predicted ones by label only, and the shipped judge falls back to
    positional alignment when labels are uninformative (unnamed expression columns) - so 8
    answers the judge had accepted came out wrong here, and `lenient pass@1` printed 28.3%
    against a strict 41.7%. A number that is impossible is a bug, not a finding.

    Returns (verdict, how), where `how` names the route that worked: a label match, a
    positional prefix, or neither.
    """
    if strict:
        return True, "already correct with the column rule applied"
    if not pred.ok or not gold.ok:
        return False, "execution failed"
    k = len(gold.columns)
    if len(pred.columns) < k:
        return False, "fewer columns than gold - not an extra-column case"
    keys = [_column_key(c) for c in pred.columns]
    gkeys = [_column_key(c) for c in gold.columns]
    routes: list[tuple[str, list[int]]] = []
    src, used = [], set()
    for g in gkeys:
        hit = next((i for i, kk in enumerate(keys) if kk == g and i not in used), None)
        if hit is None:
            src = None
            break
        src.append(hit)
        used.add(hit)
    if src is not None:
        routes.append(("matched by column name", src))
    routes.append(("positional prefix", list(range(k))))
    for name, idx in routes:
        projected = [[row[i] for i in idx] for row in pred.rows]
        if _compare(gold.rows, projected, ordered=False):
            return True, name
    return False, "extra columns are not the (only) problem"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-dir", default=str(ROOT.parent / ".external" / "dev"))
    ap.add_argument("--answers", default=str(ROOT / "results" / "bird-agent.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "results" / "bird-gap.jsonl"))
    args = ap.parse_args()

    dev_dir = Path(args.dev_dir)
    if not dev_dir.is_dir():
        raise SystemExit(f"{dev_dir} not found - see scripts/bird_judge.py for the download")
    dbs = bird_databases(dev_dir)
    gold_by_qid = {str(r["question_id"]): r for r in load_gold(dev_dir)}
    rows = [json.loads(l) for l in Path(args.answers).read_text(encoding="utf-8").splitlines() if l.strip()]
    body = [r for r in rows if "id" in r]
    if not body:
        raise SystemExit(f"{args.answers} has no per-task rows")

    out_rows, flipped, both_wrong = [], 0, 0
    how: Counter[str] = Counter()
    reasons = Counter[str]()
    for r in body:
        g = gold_by_qid.get(str(r["id"]).replace("bird-q", ""))
        if not g:
            raise SystemExit(f"{r['id']}: no gold row in dev.json - the task ids drifted")
        path = dbs.get(str(g["db_id"]))
        if not path:
            raise SystemExit(f"{r['id']}: database {g['db_id']} missing")
        conn = db.connect_ro(str(path))
        try:
            gold = execute(conn, str(g["SQL"]))
            pred = execute(conn, r.get("final_sql") or "")
        finally:
            conn.close()
        strict_v = score_execution(gold, pred, require_order=bool(r.get("require_order")))
        strict = strict_v.correct
        lenient, route = lenient_correct(gold, pred, strict)
        how[route] += 1
        if lenient and not strict:
            flipped += 1
        if strict and not lenient:
            raise SystemExit(f"{r['id']}: a relaxation rejected an answer the strict policy "
                             "accepted - the comparison is not a superset, so no number "
                             "printed here means anything")
        if not lenient:
            both_wrong += 1
            reasons[str(strict_v.reason)] += 1
        out_rows.append({"id": r["id"], "db_id": g["db_id"], "difficulty": r.get("difficulty"),
                         "strict": strict, "lenient": lenient, "route": route,
                         "strict_reason": strict_v.reason,
                         "gold_columns": len(gold.columns), "pred_columns": len(pred.columns)})

    n = len(out_rows)
    mine = sum(1 for x in out_rows if x["strict"])
    off = sum(1 for x in out_rows if x["lenient"])
    summary = {"n": n, "strict_pass": mine, "lenient_pass": off,
               "strict_pass_at_1": round(mine / n, 4), "lenient_pass_at_1": round(off / n, 4),
               "closed_by_relaxing_columns": flipped, "still_wrong": both_wrong,
               "still_wrong_reasons": dict(reasons), "alignment_routes": dict(how)}
    Path(args.out).write_text(
        json.dumps({"_summary": summary}, ensure_ascii=False) + "\n"
        + "\n".join(json.dumps(x, ensure_ascii=False) for x in out_rows) + "\n",
        encoding="utf-8", newline="\n")

    print(f"{n} 条已存答案，两套列政策：")
    print(f"  本项目口径（列集合必须相等）  pass@1 = {mine / n:.1%}  ({mine}/{n})")
    print(f"  宽松列口径（允许多返回列）    pass@1 = {off / n:.1%}  ({off}/{n})")
    print(f"  放宽这一条能翻过来 {flipped} 题；仍有 {both_wrong} 题在两种口径下都错")
    print(f"  对齐路径 {dict(how)}")
    print(f"  剩余失败的原因分布 {dict(reasons)}")
    print(f"逐题记录 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
