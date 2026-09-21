"""Eval runner: parallel execution, result caching, metrics and a regression gate.

Why a cache: you will run this benchmark hundreds of times. Caching the *scored
verdict* per (config_hash, task_id) turns an ablation sweep from minutes into
seconds and, more importantly, makes re-runs bit-identical.

Why a regression gate: prompting is reflex programming. Without `--check-baseline`
an edit that fixes question 41 and silently breaks questions 12 and 19 looks like
progress.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..agent import SqlAgent
from ..config import DATA_DIR, PRICING, RUNS_DIR, Settings, settings_from_env
from ..fewshot import select_examples
from ..llm import MockProvider, OpenAICompatProvider
from ..safety import SafetyViolation, parse_one, referenced_tables
from ..tools import Toolbox
from .scoring import execute, grade, result_differs

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = ROOT / "runs" / "cache"
RESULTS_DIR = ROOT / "results"

_local = threading.local()


def load_tasks(path: Path) -> list[dict]:
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for t in tasks:
        for key in ("id", "question", "gold_sql"):
            if key not in t:
                raise KeyError(f"task missing '{key}': {t}")
    return tasks


def gold_tables(gold_sql: str) -> list[str]:
    try:
        return sorted(referenced_tables(parse_one(gold_sql)))
    except SafetyViolation:
        return []


def _conn(settings: Settings) -> sqlite3.Connection:
    """One connection per thread: sqlite3 objects are not shareable across threads."""
    key = f"conn_{settings.config_hash()}"
    existing = getattr(_local, key, None)
    if existing is None:
        existing = sqlite3.connect(f"file:{(Path(settings.db_path)).as_posix()}?mode=ro", uri=True)
        _local.__dict__[key] = existing
    return existing


def run_one(task: dict, settings: Settings, provider_factory, all_tasks: list[dict] | None = None) -> dict:
    cache_file = CACHE_DIR / settings.config_hash() / f"{task['id']}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        cached["cached"] = True
        return cached

    conn = _conn(settings)
    try:
        # inside the try on purpose: one odd gold query must not kill a 200-task sweep
        provider = provider_factory(task)
        agent = SqlAgent(settings, Toolbox(conn, settings))
        # examples exclude the task being answered; passing None keeps k inert
        shots = select_examples(all_tasks, task["id"], settings.fewshot_k, settings.seed)
        result = agent.run(task["question"], task["id"], provider, fewshot=shots)
    except Exception as exc:  # a harness bug shows up as a failed row, not a dead run
        return {
            "id": task["id"],
            "error": f"{type(exc).__name__}: {exc}",
            "correct": False,
            "reason": "harness_exception",
            "trivial": False,
            "stats": {},
        }
    verdict = grade(conn, task["gold_sql"], result.final_sql, require_order=task.get("require_order", False))
    changed = None
    if settings.provider == "mock" and getattr(provider, "applied", False):
        # did the injected defect actually change the answer?
        injected = execute(conn, provider.submitted)
        changed = result_differs(execute(conn, task["gold_sql"]), injected)
    row = {
        "id": task["id"],
        "difficulty": task.get("difficulty", "unknown"),
        "category": task.get("category", "unknown"),
        "final_sql": result.final_sql,
        "stop_reason": result.stop_reason,
        "cost_usd": result.cost_usd,
        "stats": result.stats,
        "safety_blocks": result.safety_blocks,
        "correct": verdict.correct,
        "trivial": verdict.trivial,
        "reason": verdict.reason,
        "require_order": task.get("require_order", False),
        "corruption_applied": getattr(provider, "applied", None),
        "n_fewshot_msgs": len(shots),
        "result_changed": changed,
        "detail": verdict.detail,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    return row


def summarise(rows: list[dict], settings: Settings) -> dict:
    graded = [r for r in rows if not r.get("trivial")]
    correct = [r for r in graded if r["correct"]]
    reasons = Counter(r["reason"] for r in rows if not r["correct"])
    steps = [r["stats"].get("steps", 0) for r in rows if r.get("stats")]
    attempts = [r["stats"].get("sql_attempts", 0) for r in rows if r.get("stats")]
    errored = [r for r in rows if r.get("stats", {}).get("sql_errors", 0) > 0]
    recovered = [r for r in errored if r["correct"]]
    total_cost = sum(r.get("cost_usd", 0.0) for r in rows)
    exceptions = reasons.get("harness_exception", 0)

    # A run where the harness itself failed is not a low score. Qwen's first run came
    # back as pass@1 = 0.0000 with 192/192 exceptions, which reads as "weak model" to
    # anyone skimming the table - so the verdict is refused here rather than printed.
    invalid = exceptions > max(2, 0.02 * len(rows))

    out = {
        "config_hash": settings.config_hash(),
        "provider": settings.provider,
        "model": settings.model,
        "corruption": settings.corruption,
        "fewshot_k": settings.fewshot_k,
        "self_repair": settings.self_repair,
        "n_tasks": len(rows),
        "n_graded": len(graded),
        "n_trivial": len(rows) - len(graded),
        "n_corruption_applied": sum(1 for r in rows if r.get("corruption_applied")),
        "n_result_changed": sum(1 for r in rows if r.get("result_changed")),
        "valid": not invalid,
        "pass_at_1": 0.0 if invalid else (round(len(correct) / len(graded), 4) if graded else 0.0),
        "by_difficulty": {
            d: round(
                sum(1 for r in graded if r["correct"] and r.get("difficulty") == d)
                / max(1, sum(1 for r in graded if r.get("difficulty") == d)),
                4,
            )
            for d in sorted({r.get("difficulty", "unknown") for r in rows})
        },
        "failure_taxonomy": dict(reasons.most_common()),
        "avg_llm_steps": round(statistics.fmean(steps), 2) if steps else 0,
        "avg_sql_attempts": round(statistics.fmean(attempts), 2) if attempts else 0,
        "self_repair_opportunities": len(errored),
        "self_repair_recovery_rate": round(len(recovered) / len(errored), 4) if errored else None,
        "total_safety_blocks": sum(r.get("safety_blocks", 0) for r in rows),
        "total_cost_usd": round(total_cost, 5),
        "cost_per_solved_usd": round(total_cost / len(correct), 6) if correct else None,
        # How often the agent actually used the loop. Qwen's 3-shot run answered
        # 147/192 questions without ever executing a query, because the
        # demonstrations themselves contain no tool calls - a prompt-format effect
        # that pass@1 alone reports as an accuracy improvement.
        "protocol_adherence": round(
            sum(1 for r in rows if "run_sql" in r.get("stats", {}).get("tool_sequence", []))
            / max(1, len(rows)), 4),
        "n_no_sql_executed": sum(1 for r in rows if r.get("stop_reason") == "no_sql_executed"),
        "n_zero_tool_calls": sum(1 for r in rows if not r.get("stats", {}).get("tool_sequence")),
        # an unpriced model must never look like a free one
        "model_priced": settings.model in PRICING,
        "harness_exceptions": exceptions,
        "gold_broken": reasons.get("gold_broken", 0),
    }
    if invalid:
        out["invalid_reason"] = (
            f"{exceptions}/{len(rows)} tasks aborted inside the harness; pass@1 is withheld "
            f"because a measurement failure must not be readable as a model score"
        )
    return out


def regression_check(rows: list[dict], baseline_path: Path, strict: bool) -> list[str]:
    """Report tasks that flipped, and refuse to let silent regressions ship."""
    if not baseline_path.exists():
        return [f"no baseline at {baseline_path.name}; this run becomes the reference"]
    baseline = {json.loads(l)["id"]: json.loads(l) for l in baseline_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    current = {r["id"]: r for r in rows}
    regressions = [i for i, b in baseline.items() if b.get("correct") and not current.get(i, {}).get("correct")]
    fixes = [i for i in current if current[i].get("correct") and not baseline.get(i, {}).get("correct")]
    lines = [f"regressions: {len(regressions)}  newly-fixed: {len(fixes)}"]
    if regressions:
        lines.append(f"  BROKE: {sorted(regressions)[:25]}")
        lines.append(f"  detail -> {json.dumps({i: current[i]['reason'] for i in sorted(regressions)[:8]})}")
    if strict and regressions:
        lines.append("GATE FAILED (exit 1): fix these before claiming an improvement")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Text-to-SQL benchmark.")
    ap.add_argument("--provider", choices=["mock", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--corruption", default=None, help="mock only: none|reflow|reorder_cols|float_round|drop_distinct|wrong_limit|bad_column")
    ap.add_argument("--fewshot-k", type=int, default=None)
    ap.add_argument("--no-self-repair", action="store_true")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tasks", type=Path, default=DATA_DIR / "tasks.jsonl")
    ap.add_argument("--tag", default=None, help="name for the results jsonl")
    ap.add_argument("--check-baseline", type=Path, default=None)
    ap.add_argument("--strict-gate", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    overrides = {"provider": args.provider, "model": args.model, "corruption": args.corruption}
    if args.tasks.exists():
        overrides["dataset_hash"] = hashlib.sha256(args.tasks.read_bytes()).hexdigest()[:8]
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

    tasks = load_tasks(args.tasks)[args.offset : (args.offset + args.limit) or None]
    if not tasks:
        raise SystemExit(f"no tasks loaded from {args.tasks} - build them first: python -m sqlagent.data.build_tasks")

    def factory(task):
        if settings.provider == "mock":
            return MockProvider(
                gold_sql=task["gold_sql"],
                tables=task.get("gold_tables") or gold_tables(task["gold_sql"]),
                corruption=settings.corruption,
                self_repair=settings.self_repair,
            )
        return OpenAICompatProvider(settings)

    print(f"running {len(tasks)} tasks | provider={settings.provider} model={settings.model} "
          f"corruption={settings.corruption} self_repair={settings.self_repair} hash={settings.config_hash()}")

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda t: run_one(t, settings, factory, tasks), tasks), 1):
            rows.append(row)
            if i % 25 == 0 or i == len(tasks):
                marks = "".join("." if r.get("trivial") else ("+" if r["correct"] else "-") for r in rows[-25:])
                print(f"  {i:>4}/{len(tasks)}  {marks}")

    summary = summarise(rows, settings)
    tag = args.tag or settings.tag()
    out = results_dir / f"{tag}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_summary": summary}, ensure_ascii=False) + "\n")
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary.get("valid", True):
        print()
        print(f"[X] RUN INVALID - {summary['invalid_reason']}")
        print(f"    first error: {next((r.get('error','') for r in rows if r.get('error')), '')[:200]}")
        return 3
    if summary["provider"] == "mock":
        print("\n[!] provider=mock: pass@1 above measures the GRADER and the harness, not any model.")
        print("    Read `failure_taxonomy` from a corruption sweep instead (see scripts/calibrate_grader.sh).")
    print(f"\nwrote {out.relative_to(ROOT)}")

    if args.check_baseline:
        for line in regression_check(rows, args.check_baseline, args.strict_gate):
            print(line)
        if args.strict_gate and any("BROKE" in line for line in regression_check(rows, args.check_baseline, False)):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
