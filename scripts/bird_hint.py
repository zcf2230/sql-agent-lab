"""Does the diagnosed failure cause actually cause it? Re-run BIRD with one prompt rule.

Section 20 diagnosed 22 of 35 BIRD failures as `column_count_mismatch` - the model answers
the question and then adds identifier columns nobody asked for. Diagnosis is cheap; the
claim worth having is causal. So this runs the *same* 60 questions with the *same* model and
the only difference being one extra sentence in the system prompt.

    python scripts/bird_hint.py --db-root ../.external/dev

`prompt_version` is part of `config_hash`, and that is what makes this honest rather than
free-looking: without overriding it, the run would hit the previous arm's cache and report
the old answers as the new experiment. Overriding it forces fresh calls (~$0.075) and leaves
the two arms distinguishable in `results/`.

Cost: one real run over 60 questions. Nothing here is measured without the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlagent import agent as agent_module  # noqa: E402
from sqlagent import prompts, stats  # noqa: E402
from sqlagent.config import settings_from_env  # noqa: E402
from sqlagent.eval.runner import load_tasks, run_one, summarise  # noqa: E402
from sqlagent.llm import OpenAICompatProvider  # noqa: E402

HINT = ("Return exactly the columns the question asks for, in the order it asks. "
        "Do not add identifier, key or helper columns that the question did not request.")
BASELINE_RUN = "bird-agent"
HINT_RUN = "bird-agent-hint"

_original_build = prompts.build_messages


def _patched_build_messages(question, fewshot=None, dialect_hint=""):
    return _original_build(question, fewshot=fewshot,
                           dialect_hint=(dialect_hint + " " + HINT).strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-root", required=True, help="extracted BIRD dev directory")
    ap.add_argument("--tasks", default=str(ROOT / "data" / "tasks_bird-60.jsonl"))
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        raise SystemExit(f"{tasks_path} missing - build it: "
                         "python scripts/bird_tasks.py --dev-dir <dev> --per-tier 20")
    tasks = load_tasks(tasks_path)

    settings = settings_from_env(provider="openai", model=args.model,
                                 prompt_version=f"{prompts.PROMPT_VERSION}+columns-hint")
    agent_module.build_messages = _patched_build_messages

    def factory(task):
        return OpenAICompatProvider(settings)

    print(f"arm: prompt_version={settings.prompt_version} hash={settings.config_hash()} "
          f"tasks={len(tasks)} (fresh calls: this key has no cache)")
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda t: run_one(t, settings, factory, tasks), tasks), 1):
            rows.append(row)
            if i % 20 == 0 or i == len(tasks):
                marks = "".join("." if r.get("trivial") else ("+" if r["correct"] else "-")
                                for r in rows[-20:])
                print(f"  {i:>4}/{len(tasks)}  {marks}")

    summary = summarise(rows, settings)
    out = ROOT / "results" / f"{HINT_RUN}.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"_summary": summary}, ensure_ascii=False) + "\n")
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    base = stats.run_summary(BASELINE_RUN)
    n = summary["n_graded"] or 1
    lo, hi = stats.wilson(round(summary["pass_at_1"] * n), n)
    print(f"\npass@1  {BASELINE_RUN}: {base['pass_at_1']:.1%}")
    print(f"pass@1  {HINT_RUN}: {summary['pass_at_1']:.1%}  Wilson 95% [{lo:.1%}, {hi:.1%}]")
    print(f"花费 ${summary['total_cost_usd']:.4f}   失败结构 {dict(Counter(r['reason'] for r in rows if not r['correct']))}")

    # Paired, because the two arms answered the same questions: what moved, per question.
    base_path = ROOT / "results" / f"{BASELINE_RUN}.jsonl"
    b = {}
    if base_path.exists():
        for line in base_path.read_text(encoding="utf-8").splitlines()[1:]:
            rec = json.loads(line)
            if rec.get("id"):
                b[rec["id"]] = bool(rec.get("correct"))
    fixed = [r["id"] for r in rows if r["correct"] and not b.get(r["id"], False)]
    broken = [r["id"] for r in rows if not r["correct"] and b.get(r["id"], False)]
    print(f"配对：修好 {len(fixed)} 题，弄坏 {len(broken)} 题 -> {out.name}")
    if b:
        _, _, p_value = stats.mcnemar_exact(b, {r["id"]: bool(r["correct"]) for r in rows})
        print(f"McNemar 精确双侧 p={p_value:.4f}（n={len(b)}，同一批题、只换一句 prompt）")
    else:
        print(f"没有 {BASELINE_RUN}.jsonl，配对检验跳过 - 单次观测不构成对比")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
