"""The two comparators this project now runs side by side, pinned by case.

`scripts/bird_judge.py` reports where our judge and the public-benchmark rule disagree.
That report is only worth anything if both sides are what we say they are, so each rule
is nailed down here with the smallest case that separates it from the others. The
public rule is deliberately the coarse one (set semantics), because the finding under
test is that it cannot see duplicate rows - and a claim about someone else's blind spot
had better not rest on a misreading of their comparator.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlagent.eval.scoring import Execution, score_execution  # noqa: E402

spec = importlib.util.spec_from_file_location("bird_judge", ROOT / "scripts" / "bird_judge.py")
bird = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bird)


def ex(columns, rows):
    return Execution(ok=True, columns=list(columns), rows=[list(r) for r in rows])


# ---- the public rule: set semantics -------------------------------------------------

def test_public_rule_cannot_see_duplicate_rows():
    """This is the blind spot the experiment is about, stated as a unit: dropping a
    DISTINCT adds rows, and a set comparison still calls it equal."""
    gold = [[1], [2]]
    assert bird.public_verdict(gold, gold + [[1]]) is True, \
        "set() deduplicates, so a duplicate-row bug is invisible to the public metric"


def test_public_rule_is_blind_to_row_order_but_not_to_column_order():
    assert bird.public_verdict([[1, 2], [3, 4]], [[3, 4], [1, 2]]) is True, "row order ignored"
    assert bird.public_verdict([[1, 2]], [[2, 1]]) is False, \
        "columns are compared positionally, so a permutation is a difference"


# ---- our rule: name-aligned, multiset, order per question --------------------------

def test_ours_accepts_the_column_permutation_the_public_rule_rejects():
    gold = ex(["course_id", "n"], [[10, 3]])
    pred = ex(["n", "course_id"], [[3, 10]])
    assert score_execution(gold, pred, require_order=False).correct is True
    assert bird.public_verdict(gold.rows, pred.rows) is False


def test_ours_rejects_the_duplicate_row_the_public_rule_accepts():
    gold = ex(["city"], [["Beijing"], ["Shanghai"]])
    pred = ex(["city"], [["Beijing"], ["Beijing"], ["Shanghai"]])
    assert score_execution(gold, pred, require_order=False).correct is False
    assert bird.public_verdict(gold.rows, pred.rows) is True


def test_ours_and_public_agree_on_the_happy_path_and_on_a_missing_row():
    gold = ex(["x"], [[1], [2]])
    assert score_execution(gold, gold, require_order=False).correct is True
    assert bird.public_verdict(gold.rows, gold.rows) is True
    missing = ex(["x"], [[1]])
    assert score_execution(gold, missing, require_order=False).correct is False
    assert bird.public_verdict(gold.rows, missing.rows) is False


def test_the_documented_public_rule_is_the_one_the_code_implements():
    """`PUBLIC_RULE` is quoted in the report and in the docs. If the function and the
    sentence ever disagree, the sentence has to fail here, not in a review."""
    assert bird.PUBLIC_RULE == "set(gold_rows) == set(pred_rows), order- and duplicate-blind"
    assert bird.public_verdict([[1], [1]], [[1]]) is True, "duplicates collapse"
    assert bird.public_verdict([[1]], [[2]]) is False


# --- the sampling rule that decides what "the agent scores 41.7% on BIRD" means --------


def _synthetic_dev():
    """Two tiers, three databases, question ids ordered by database the way BIRD's are."""
    rows = []
    qid = 0
    for tier in ("simple", "challenging"):
        for db_id in ("aaa", "bbb", "ccc"):
            for _ in range(5):
                qid += 1
                rows.append({"question_id": qid, "db_id": db_id, "difficulty": tier,
                             "question": "q", "SQL": "SELECT 1", "evidence": ""})
    return rows


def test_the_task_builder_rotates_across_databases_instead_of_taking_a_prefix():
    """Taking the first N ids per tier is what the judge experiment does, and it is fine
    there (40/tier reaches every database). At 4/tier it returned 12 questions out of
    `california_schools` alone - the same collapse §9-24 recorded for the template families.
    A small, paid sample has to spread or it measures one database's dialect."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "bird_tasks", Path(__file__).resolve().parent.parent / "scripts" / "bird_tasks.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)

    rows = _synthetic_dev()
    naive = [r for r in rows if r["difficulty"] == "simple"][:2]
    assert len({r["db_id"] for r in naive}) == 1, "the prefix rule is the one being replaced"

    picked = bt.pick_spread(rows, 2)
    assert len(picked) == 4, "two per tier"
    for tier in ("simple", "challenging"):
        dbs = {r["db_id"] for r in picked if r["difficulty"] == tier}
        assert dbs == {"aaa", "bbb"}, f"{tier} came back as {dbs}"
    assert picked == bt.pick_spread(rows, 2), "the rule must be deterministic, not shuffled"


def test_a_task_row_names_its_database_relative_to_the_root():
    """Absolute paths in a committed task file would embed the author's machine; the runner
    resolves this against `--db-root`."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "bird_tasks", Path(__file__).resolve().parent.parent / "scripts" / "bird_tasks.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)

    dev = Path("/tmp/bird-dev")
    task = bt.to_task({"question_id": 7, "db_id": "california_schools", "difficulty": "simple",
                       "question": "Which schools?", "evidence": "use DOC", "SQL": "SELECT 1"},
                      dev / "california_schools" / "california_schools.sqlite", dev)
    assert not Path.is_absolute(Path(task["db"])), task["db"]
    assert task["db"] == "california_schools/california_schools.sqlite"
    assert "External knowledge: use DOC" in task["question"], "BIRD's protocol gives the evidence"
    assert task["require_order"] is False
