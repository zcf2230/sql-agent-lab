"""Run the investment-research (投研取数) task set against mock or a live model.

A BIRD-style side driver rather than an extension of `eval.runner`: the main
runner grades every task with `scoring.grade`, and its result cache is keyed by
a config_hash that covers the main judge's files. The finance set needs the
absence judge (`sqlagent/eval/absence.py`) for `expect_absent` tasks, so it runs
here with its own cache root (`runs/cache_fin`) - sharing (config_hash, task_id)
keys with the main cache would let one judge serve the other's stale verdicts.

Usage:
  uv run python scripts/fin_eval.py --provider mock           # $0 pipeline check
  uv run python scripts/fin_eval.py --model deepseek-chat     # live, costs money
  uv run python scripts/fin_eval.py --model deepseek-chat --check-baseline results/fin-baseline.jsonl --strict-gate
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlagent.agent import SqlAgent  # noqa: E402
from sqlagent.config import DATA_DIR, RUNS_DIR, Settings, settings_from_env  # noqa: E402
from sqlagent.eval.absence import absence_code_hash, grade_absent  # noqa: E402
from sqlagent.eval.runner import dataset_digest, load_tasks  # noqa: E402
from sqlagent.eval.scoring import grade  # noqa: E402
from sqlagent.fewshot import select_examples  # noqa: E402
from sqlagent.llm import MockProvider, OpenAICompatProvider  # noqa: E402
from sqlagent.tools import Toolbox  # noqa: E402

CACHE_DIR = ROOT / "runs" / "cache_fin"
RESULTS_DIR = ROOT / "results"

_local = threading.local()
_local = threading.local()


def _db_of(task: dict, settings: Settings) -> str:
    rel = task.get("db") or "astock.db"
    root = Path(settings.db_root) if settings.db_root else DATA_DIR
    path = (root / rel).resolve()
    if not path.exists():
        raise FileNotFoundError(f"task {task['id']}: no database at {path}")
    return str(path)


def _conn(settings: Settings, db_path: str) -> sqlite3.Connection:
    key = f"fin_{abs(hash(db_path))}"
    existing = getattr(_local, key, None)
    if existing is None:
        existing = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        setattr(_local, key, existing)
    return existing


def run_one(task: dict, settings: Settings, provider_factory, all_tasks: list[dict]) -> dict:
    cache_file = CACHE_DIR / settings.config_hash() / f"{task['id']}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        cached["cached"] = True
        return cached
    conn = _conn(settings, _db_of(task, settings))
    provider = provider_factory(task)
    agent = SqlAgent(settings, Toolbox(conn, settings))
    shots = select_examples(all_tasks, task["id"], settings.fewshot_k, settings.seed)
    result = agent.run(task["question"], task["id"], provider, fewshot=shots)
    if task.get("expect_absent"):
        verdict = grade_absent(result.final_sql, conn=conn)
    else:
        verdict = grade(conn, task["gold_sql"], result.final_sql, require_order=task.get("require_order", False))
    row = {
        "id": task["id"],
        "difficulty": task.get("difficulty", "unknown"),
        "category": task.get("category", "unknown"),
        "expect_absent": bool(task.get("expect_absent")),
        "final_sql": result.final_sql,
        "answer_text": (result.answer_text or "")[:400],
        "stop_reason": result.stop_reason,
        "cost_usd": result.cost_usd,
        "stats": result.stats,
        "safety_blocks": result.safety_blocks,
        "correct": verdict.correct,
        "trivial": verdict.trivial,
        "reason": verdict.reason,
        "require_order": task.get("require_order", False),
        "n_fewshot_msgs": len(shots),
        "detail": verdict.detail,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    return row


def summarise(rows: list[dict], settings: Settings) -> dict:
    answerable = [r for r in rows if not r["expect_absent"] and not r.get("trivial")]
    absent = [r for r in rows if r["expect_absent"]]
    a_correct = [r for r in answerable if r["correct"]]
    ab_evidenced = [r for r in absent if r["reason"] == "abstain_evidenced"]
    ab_nosql = [r for r in absent if r["reason"] == "no_sql_produced"]
    ab_strict = len(ab_evidenced)
    ab_lenient = ab_strict + len(ab_nosql)
    reasons = Counter(r["reason"] for r in rows if not r["correct"])
    exceptions = reasons.get("harness_exception", 0)
    invalid = exceptions > max(2, 0.02 * len(rows))
    total_cost = sum(r.get("cost_usd", 0.0) for r in rows)
    return {
        "fin_code_hash": absence_code_hash(),
        "dataset_hash": settings.dataset_hash,
        "config_hash": settings.config_hash(),
        "provider": settings.provider,
        "model": settings.model,
        "fewshot_k": settings.fewshot_k,
        "self_repair": settings.self_repair,
        "sqlite_version": sqlite3.sqlite_version,
        "n_tasks": len(rows),
        "n_answerable": len(answerable),
        "n_absent": len(absent),
        "valid": not invalid,
        # headline: answerable accuracy is the direct analogue of pass@1
        "pass_at_1": None if invalid or not answerable else round(len(a_correct) / len(answerable), 4),
        # absence dimension: strict = evidenced abstentions only; lenient adds
        # unverified refusals (see absence.py for why they are kept apart)
        "absent_strict_rate": None if invalid or not absent else round(ab_strict / len(absent), 4),
        "absent_lenient_rate": None if invalid or not absent else round(ab_lenient / len(absent), 4),
        "n_abstain_evidenced": len(ab_evidenced),
        "n_abstain_nosql": len(ab_nosql),
        "n_fabricated": sum(1 for r in absent if r["reason"] == "fabricated_result"),
        "failure_taxonomy": dict(reasons.most_common()),
        "avg_llm_steps": round(statistics.fmean([r["stats"].get("steps", 0) for r in rows if r.get("stats")]), 2),
        "protocol_adherence": round(
            sum(1 for r in rows if "run_sql" in r.get("stats", {}).get("tool_sequence", [])) / max(1, len(rows)), 4),
        "total_cost_usd": round(total_cost, 5),
        "harness_exceptions": exceptions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the investment-research benchmark.")
    ap.add_argument("--provider", choices=["mock", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--corruption", default=None, help="mock only")
    ap.add_argument("--fewshot-k", type=int, default=None)
    ap.add_argument("--no-self-repair", action="store_true")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tasks", type=Path, default=DATA_DIR / "tasks_finance.jsonl")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--check-baseline", type=Path, default=None)
    ap.add_argument("--strict-gate", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    overrides: dict = {"provider": args.provider, "model": args.model, "corruption": args.corruption,
                       "dataset_hash": dataset_digest(args.tasks)}
    if args.no_self_repair:
        overrides["self_repair"] = False
    if args.fewshot_k is not None:
        overrides["fewshot_k"] = args.fewshot_k
    if args.max_steps:
        overrides["max_steps"] = args.max_steps
    settings = settings_from_env(**overrides)

    if args.no_cache:
        import shutil
        shutil.rmtree(CACHE_DIR / settings.config_hash(), ignore_errors=True)

    tasks = load_tasks(args.tasks)[args.offset:(args.offset + args.limit) or None]
    if not tasks:
        raise SystemExit(f"no tasks loaded from {args.tasks} - build: python -m sqlagent.data.build_astock_tasks")

    def factory(task):
        if settings.provider == "mock":
            return MockProvider(gold_sql=task["gold_sql"],
                                tables=task.get("gold_tables") or [],
                                corruption=settings.corruption,
                                self_repair=settings.self_repair)
        return OpenAICompatProvider(settings)

    print(f"running {len(tasks)} finance tasks | provider={settings.provider} model={settings.model} "
          f"hash={settings.config_hash()} fin_code={settings.dataset_hash}")

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda t: run_one(t, settings, factory, tasks), tasks), 1):
            rows.append(row)
            if i % 25 == 0 or i == len(tasks):
                marks = "".join("+" if r["correct"] else "-" for r in rows[-25:])
                print(f"  {i:>4}/{len(tasks)}  {marks}")

    summary = summarise(rows, settings)
    tag = args.tag or f"fin-{settings.tag()}"
    out = RESULTS_DIR / f"{tag}.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"_summary": summary}, ensure_ascii=False) + "\n")
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary.get("valid", True):
        print(f"\n[X] RUN INVALID - harness exceptions {summary['harness_exceptions']}/{summary['n_tasks']}")
        return 3

    if args.check_baseline:
        baseline = {json.loads(l)["id"]: json.loads(l)
                    for l in args.check_baseline.read_text(encoding="utf-8").splitlines() if l.strip()}
        current = {r["id"]: r for r in rows}
        reg = [i for i, b in baseline.items() if b.get("correct") and not current.get(i, {}).get("correct")]
        print(f"\nregressions: {len(reg)}  newly-fixed: "
              f"{sum(1 for i in current if current[i].get('correct') and not baseline.get(i, {}).get('correct'))}")
        if reg:
            print("  BROKE:", sorted(reg)[:25])
            if args.strict_gate:
                return 1
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
