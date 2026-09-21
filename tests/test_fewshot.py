"""Few-shot wiring tests.

`--fewshot-k` used to be inert: the value reached `config_hash` and the summary
line, but nothing ever put examples into the prompt. A run labelled "3-shot" was
byte-identical to the baseline, which is the worst kind of bug in a benchmark - it
does not crash, it just manufactures a result.

So the tests here assert the flag changes what the model is actually shown.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sqlagent.config import DATA_DIR, Settings
from sqlagent.fewshot import leakage_check, normalise, select_examples
from sqlagent.prompts import build_messages

TASKS = [json.loads(l) for l in (DATA_DIR / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
needs_tasks = pytest.mark.skipif(len(TASKS) < 10, reason="run `python -m sqlagent.data.build_tasks` first")


@needs_tasks
def test_examples_never_contain_the_question_being_answered():
    assert leakage_check(TASKS, k=4, sample=len(TASKS)) == []


@needs_tasks
def test_selection_is_deterministic_per_task():
    a = select_examples(TASKS, TASKS[0]["id"], 3, seed=7)
    b = select_examples(TASKS, TASKS[0]["id"], 3, seed=7)
    assert a == b
    assert len(a) == 6  # three question/assistant pairs


@needs_tasks
def test_k_zero_shows_no_examples():
    assert select_examples(TASKS, TASKS[0]["id"], 0) == []


@needs_tasks
def test_more_examples_actually_grow_the_prompt():
    """The regression that matters: k has to change what the model sees."""
    target = TASKS[1]
    small = build_messages(target["question"], fewshot=select_examples(TASKS, target["id"], 1))
    big = build_messages(target["question"], fewshot=select_examples(TASKS, target["id"], 4))
    assert len(big) == len(small) + 6
    assert len(json.dumps(big)) > len(json.dumps(small))
    # the demonstrations are everything except system and the final target question
    demonstrations = " ".join(m["content"] for m in big[1:-1])
    assert normalise(target["question"]) not in normalise(demonstrations)


@needs_tasks
def test_target_question_is_the_last_message():
    """Ordering invariant, and the reason it exists.

    Demonstrations used to be appended *after* the target question, so the final
    message the model saw was an assistant answer to an unrelated example. It
    answered that instead: pass@1 collapsed 89.6% -> 3.7%, and `final_sql` was
    verifiably another task's gold. Nothing crashed; the ablation just reported a
    false finding about few-shot learning.
    """
    target = TASKS[3]["question"]
    for k in (0, 1, 4):
        messages = build_messages(target, fewshot=select_examples(TASKS, TASKS[3]["id"], k))
        assert messages[-1]["role"] == "user"
        assert normalise(target) in normalise(messages[-1]["content"])
        assert messages[0]["role"] == "system"
        assert len(messages) == 2 + 2 * k


@needs_tasks
def test_examples_do_not_leak_the_target_gold():
    target = TASKS[3]
    shots = " ".join(m["content"] for m in select_examples(TASKS, target["id"], 4))
    assert normalise(target["gold_sql"]) not in normalise(shots)


@needs_tasks
@pytest.mark.skipif(not (DATA_DIR / "learning_platform.db").exists(), reason="needs the database")
def test_runner_passes_examples_through_to_the_agent():
    """End-to-end: `run_one` must report the examples it injected."""
    from sqlagent.eval.runner import run_one

    settings = Settings(provider="mock", fewshot_k=3, db_path=str(DATA_DIR / "learning_platform.db"))

    def factory(task):
        from sqlagent.llm import MockProvider
        return MockProvider(task["gold_sql"], task.get("gold_tables") or ["users"], "none", True)

    row = run_one(TASKS[2], settings, factory, TASKS)
    assert row["n_fewshot_msgs"] == 6, "fewshot_k reached the summary but not the prompt"

    zero = run_one(TASKS[2], Settings(provider="mock", db_path=str(DATA_DIR / "learning_platform.db")), factory, TASKS)
    assert zero["n_fewshot_msgs"] == 0
