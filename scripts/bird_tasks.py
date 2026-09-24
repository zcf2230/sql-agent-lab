"""Turn BIRD dev into a task file the runner can answer, one database per question.

Why this is a separate step and not part of `bird_judge.py`: that script measures the
*judge* and never calls a model. This one only builds questions; spending money is the
runner's job, so the two stay independently checkable.

    python scripts/bird_tasks.py --dev-dir ../.external/dev/dev --per-tier 4
    python -m sqlagent.eval.runner --provider openai --model deepseek-chat \
        --tasks data/tasks_bird-12.jsonl --db-root ../.external/dev/dev --tag bird-agent

The task file is **not committed**: it carries BIRD's question text, evidence and gold
SQL, which are someone else's dataset. The result file stores ids, the model's own SQL
and the verdicts, so a published run cites question numbers rather than republishing them.

Cost: this script is free. The runner that follows it calls the model once per question.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from bird_judge import bird_databases, load_gold  # noqa: E402


def pick_spread(rows: list[dict], per_tier: int) -> list[dict]:
    """`per_tier` questions per difficulty tier, rotated across databases.

    "First N sorted by question_id" is the wrong rule for a small budget: BIRD orders its
    ids by database, so 4 per tier came back as 12 questions out of `california_schools` -
    the same mistake the restability sampler made when it filled 13 of 20 slots from one
    template family by file order (§9-24). Rotation is still deterministic, so the same
    questions come back on any machine, and it reports the composition it produced.
    """
    by_tier: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_tier[str(r.get("difficulty") or "unknown")][str(r["db_id"])].append(r)

    chosen = []
    for tier in sorted(by_tier):
        queues = {db: sorted(qs, key=lambda r: int(r["question_id"]))
                  for db, qs in sorted(by_tier[tier].items())}
        taken = 0
        while taken < per_tier and any(queues.values()):
            for db in list(queues):
                if not queues[db]:
                    del queues[db]
                    continue
                chosen.append(queues[db].pop(0))
                taken += 1
                if taken == per_tier:
                    break
    return chosen


def to_task(row: dict, db_file: Path, dev_dir: Path) -> dict:
    evidence = str(row.get("evidence") or "").strip()
    question = str(row["question"]).strip()
    if evidence:
        # BIRD's own protocol hands this hint to the model; leaving it out would make
        # the number incomparable with every published BIRD score.
        question = f"{question}\nExternal knowledge: {evidence}"
    return {
        "id": f"bird-q{row['question_id']}",
        "question": question,
        "gold_sql": str(row["SQL"]).strip(),
        # relative to --db-root, so the task file does not embed anyone's absolute path
        "db": str(db_file.relative_to(dev_dir)).replace("\\", "/"),
        "difficulty": str(row.get("difficulty") or "unknown"),
        "category": str(row["db_id"]),
        # BIRD annotates no "must the rows come back in this order" flag, and its own
        # checker compares sets, so order is not part of what this run measures.
        "require_order": False,
        "question_id": int(row["question_id"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-dir", required=True, help="extracted BIRD dev directory")
    ap.add_argument("--per-tier", type=int, default=4, help="questions per difficulty tier")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    dev_dir = Path(args.dev_dir).resolve()
    dbs = bird_databases(dev_dir)
    rows = pick_spread(load_gold(dev_dir), args.per_tier)
    tasks, skipped = [], []
    for r in rows:
        db_file = dbs.get(str(r["db_id"]))
        if db_file is None or not str(r.get("SQL") or "").strip():
            skipped.append(f"bird-q{r['question_id']} ({r['db_id']})")
            continue
        tasks.append(to_task(r, db_file, dev_dir))

    out = Path(args.out) if args.out else ROOT / "data" / f"tasks_bird-{len(tasks)}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    by_tier: dict[str, int] = {}
    per_tier_dbs: dict[str, set] = {}
    for t in tasks:
        by_tier[t["difficulty"]] = by_tier.get(t["difficulty"], 0) + 1
        per_tier_dbs.setdefault(t["difficulty"], set()).add(t["db"])
    print(f"wrote {out.name}: {len(tasks)} tasks over {len({t['db'] for t in tasks})} databases")
    # The composition is printed rather than trusted: a selection rule that quietly
    # collapses onto one database looks exactly like a spread one in the totals.
    for tier in sorted(by_tier):
        print(f"  {tier:<12} {by_tier[tier]} 题 / {len(per_tier_dbs[tier])} 个库")
    if skipped:
        print(f"  skipped (no database or no gold): {skipped}")
    print(f"run it with:  python -m sqlagent.eval.runner --provider openai "
          f"--model deepseek-chat --tasks {out.relative_to(ROOT)} --db-root {args.dev_dir} "
          f"--tag bird-agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
