"""Few-shot example selection.

Two rules, and the second is the one that matters:

  1. examples are drawn deterministically from a seed, so an ablation row can be
     re-run and compared;
  2. **the task being answered is never in its own example list**, and neither is
     any task with the same normalised question. Echoing the gold SQL for the very
     question you are asking does not measure few-shot learning, it measures
     whether the model can copy. That is the single easiest way to publish a
     Text-to-SQL number that is a lie.

The demonstrations are question -> SQL pairs only, not full tool trajectories. A
trajectory demonstration is more faithful to the deployed loop and costs several
times the context; that trade is recorded in the README rather than hidden.
"""

from __future__ import annotations

import random
import re

_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _WS.sub(" ", text or "").strip().casefold()


def _as_turns(question: str, gold_sql: str) -> list[dict]:
    return [
        {"role": "user", "content": f"Question: {question}"},
        {"role": "assistant", "content": f"```sql\n{gold_sql}\n```"},
    ]


def select_examples(tasks: list[dict], task_id: str, k: int, seed: int = 0) -> list[dict]:
    """Return up to k demonstration turns, excluding the target task itself."""
    if k <= 0:
        return []
    target = next((t for t in tasks if t["id"] == task_id), None)
    target_text = normalise(target["question"]) if target else None

    pool = [
        t for t in tasks
        if t["id"] != task_id and normalise(t["question"]) != target_text and not t.get("trivial")
    ]
    if not pool:
        return []

    # Seed on the task id as well: every question then sees a stable, different
    # example set, so the ablation is not secretly measuring one lucky draw.
    rng = random.Random(f"{seed}:{task_id}")
    chosen = rng.sample(pool, min(k, len(pool)))
    out: list[dict] = []
    for t in chosen:
        out.extend(_as_turns(t["question"], t["gold_sql"]))
    return out


def leakage_check(tasks: list[dict], k: int, seed: int = 0, sample: int = 50) -> list[str]:
    """Assert no example set contains its own question. Cheap, and it guards the
    one mistake that would silently inflate every few-shot number."""
    problems: list[str] = []
    for t in tasks[:sample]:
        blob = normalise(" ".join(m["content"] for m in select_examples(tasks, t["id"], k, seed)))
        if normalise(t["question"]) in blob:
            problems.append(t["id"])
    return problems
