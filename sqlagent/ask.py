"""Ask the agent one question and watch it work, step by step.

Everything else in this package is batch machinery - runner, sweeps, reports - which made
the repo awkward to describe: a project that advertises "a product, not a script" had no
way to type a question into it. This is that way, and it is deliberately thin: it builds
the same `SqlAgent` over the same `Toolbox` the benchmark uses, so what you see here is the
measured system, not a demo reimplementation that can drift from it.

    python -m sqlagent.ask "How many users came from the ads channel?"
    python -m sqlagent.ask "..." --provider mock        # $0, answers with the gold of a task
    python -m sqlagent.ask "..." --quiet                # final SQL only, for piping

Cost: one real run per question (~¥0.01 with the measured per-task price), so it prints the
token and cost accounting rather than pretending the call is free.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .agent import SqlAgent
from .config import DATA_DIR, Settings, settings_from_env
from .fewshot import select_examples
from .llm import MockProvider, OpenAICompatProvider
from .tools import Toolbox

ROOT = Path(__file__).resolve().parent.parent


def match_task(question: str, tasks_path: Path | None = None) -> dict | None:
    """The benchmark task with this exact question, if there is one.

    Used only to offer the gold SQL and a verdict afterwards. Matching on the question text
    rather than an id because a person typing a question does not have an id - and if the
    text is not an exact match, the honest answer is "this is not a benchmark question",
    since a paraphrase can have a different correct answer.
    """
    path = tasks_path or DATA_DIR / "tasks.jsonl"
    if not path.exists():
        return None
    want = question.strip()
    for line in path.read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t.get("question", "").strip() == want:
            return t
    return None


def run_question(question: str, settings: Settings, verbose: bool = True) -> dict:
    conn = sqlite3.connect(f"file:{Path(settings.db_path).as_posix()}?mode=ro", uri=True)
    box = Toolbox(conn, settings)
    agent = SqlAgent(settings, box)
    provider = (MockProvider("SELECT COUNT(*) AS n FROM users", ["users"], "none", settings.self_repair)
                if settings.provider == "mock" else OpenAICompatProvider(settings))
    result = agent.run(question, "ask", provider)
    out = {"question": question, "final_sql": result.final_sql, "answer": result.answer_text,
           "stop_reason": result.stop_reason, "cost_usd": result.cost_usd,
           "stats": result.stats, "safety_blocks": result.safety_blocks,
           # what the model actually asked the database for, in order: the guard's
           # refusals are the interesting part of a single-question demo
           "sql_attempts": [{"sql": sql, "ok": bool(payload.get("ok")),
                             "error": payload.get("error") or payload.get("error_type") or ""}
                            for sql, payload in box.attempts]}
    conn.close()
    if verbose:
        for i, step in enumerate(out["sql_attempts"], 1):
            flag = "执行成功" if step["ok"] else "被拒绝"
            print(f"  {i:>2}. [{flag}] {' '.join(step['sql'].split())[:96]}")
            if step["error"]:
                print(f"      -> {step['error'][:150]}")
        if not out["sql_attempts"]:
            print("  （一次 SQL 都没执行：这道题的答案来自模型散文，不是数据库）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask the Text-to-SQL agent one question.")
    ap.add_argument("question")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--fewshot-k", type=int, default=None)
    ap.add_argument("--quiet", action="store_true", help="print only the final SQL")
    args = ap.parse_args()

    settings = settings_from_env(provider=args.provider, model=args.model,
                                 fewshot_k=args.fewshot_k)
    if settings.provider != "mock" and not settings.api_key:
        print("[X] 没有可用凭据：.env 的 SQLAGENT_API_KEY 或该模型的 DPAPI 槽位。"
              " 想零花费看流程：--provider mock", file=sys.stderr)
        return 2

    out = run_question(args.question, settings, verbose=not args.quiet)
    if args.quiet:
        print(out["final_sql"] or "")
        return 0

    print()
    print(f"最终 SQL: {out['final_sql'] or '（没有产出可执行查询）'}")
    if out["answer"]:
        print(f"模型回答: {out['answer'][:400]}")
    print(f"停止原因: {out['stop_reason']}   LLM 步数 {out['stats'].get('steps')}   "
          f"SQL 尝试 {out['stats'].get('sql_attempts')}   "
          f"护栏拦截 {out['safety_blocks']} 次   花费 ${out['cost_usd']:.5f}")

    task = match_task(args.question)
    if task:
        from .eval.scoring import grade  # local import: only benchmark questions reach this

        conn = sqlite3.connect(f"file:{Path(settings.db_path).as_posix()}?mode=ro", uri=True)
        verdict = grade(conn, task["gold_sql"], out["final_sql"],
                        require_order=task.get("require_order", False))
        conn.close()
        print(f"\n这道题在基准里（{task['id']}）。gold SQL:\n  {task['gold_sql']}")
        print(f"判分: {'正确' if verdict.correct else '不正确'} - {verdict.reason}"
              + ("  (空集对空集，不计分)" if verdict.trivial else ""))
    else:
        print("\n（这道题不在基准里，所以没有 gold 可比 - 上面展示的是系统真实怎么走的。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
