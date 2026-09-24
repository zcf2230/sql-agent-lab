"""Re-judge every stored answer with the current judge and report what moved.

Why this exists rather than "just re-run the benchmark": the judge is a pure function of
(gold SQL, predicted SQL, database), so re-deriving a verdict costs a local query
execution and zero API calls, while re-running the agent would cost ~¥1.2 per pass and
would also re-draw the model's sampling - conflating "did my scoring change?" with "did
the model answer differently?". This script answers only the first question.

Run it whenever `eval/scoring.py` or `safety.py` changes - both are on the judge's path.
A verdict that moves here moves every number computed from it, so the published tables
have to be regenerated from these files, not hand-corrected. It exits non-zero when
anything moved, which is what lets CI treat "0 verdict changes" as an enforced property
instead of a measurement that was true once.

Usage:  python scripts/rejudge.py [--runs results]
Cost:   0. No model is called; the databases are opened read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlagent import db  # noqa: E402
from sqlagent.eval.scoring import grade  # noqa: E402

EXCLUDE_PREFIX = ("calib-", "bird-")


def load_tasks(path: Path | None = None) -> dict[str, dict]:
    rows = (path or ROOT / "data" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    return {json.loads(l)["id"]: json.loads(l) for l in rows if l.strip()}


def task_set_for(path: Path, main_tasks: dict[str, dict]) -> tuple[dict[str, dict], str]:
    """Which question set this run file should be graded against, and why.

    The restability runs re-ask questions from their own sets, not the main 192; those
    sets are tracked next to the results, so they are in scope too - against the task file
    that was actually used, keyed by the same suffix.
    """
    if path.name.startswith("restability-"):
        pick = path.name[len("restability-deepseek-chat-"):-len(".jsonl")]
        sibling = ROOT / "data" / f"tasks_restability-{pick}.jsonl"
        if not sibling.exists():
            return {}, f"no task set {sibling.name}"
        return load_tasks(sibling), ""
    return main_tasks, ""


def rejudge_run(path: Path, conn) -> dict:
    """Re-grade one run file. Never compares a run against a question set it did not use."""
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary = rows[0].get("_summary") or {}
    body = [r for r in rows[1:] if "id" in r and "correct" in r]
    report = {"run": path.name[:-len(".jsonl")], "rows": len(body), "checked": 0,
              "flips": Counter(), "drift": 0, "missing": 0, "out_of_scope": ""}
    if not body:
        report["out_of_scope"] = "no per-task rows"
        return report

    active, why = task_set_for(path, load_tasks())
    if why:
        report["out_of_scope"] = why
        return report
    # A run graded against a different question set cannot say anything about the judge.
    # The 208-question set predates the current 192; treating its verdict differences as
    # "your judge changed behaviour" is the false conclusion this scoping exists to stop -
    # the first version of this script nearly published exactly that.
    if summary.get("n_tasks") and summary["n_tasks"] != len(active):
        report["out_of_scope"] = (f"run has {summary['n_tasks']} tasks, "
                                  f"current set has {len(active)}")
        return report

    for r in body:
        task = active.get(r["id"])
        if task is None:
            report["missing"] += 1
            continue
        # A stored row carries the `require_order` it was judged under. If the task set has
        # since changed that flag (or the gold), a verdict difference is dataset drift, not
        # a judge change - and lumping the two together once produced a "your published
        # numbers moved" alarm over 208-row runs that predate the 192-question set entirely.
        if bool(r.get("require_order")) != bool(task.get("require_order")):
            report["drift"] += 1
            continue
        verdict = grade(conn, task["gold_sql"], r.get("final_sql"),
                        require_order=bool(task.get("require_order")))
        # Gold SQL text is not stored in the row, but its shape is: a task whose gold
        # returns a different row count or column set than the one recorded when the
        # run happened has been rebuilt underneath us (the 208 -> 192 question rebuild
        # changed value pools, hence gold text, hence verdicts - with no help from
        # `require_order`). Count that as dataset drift, not as judge behaviour.
        old_cols = (r.get("detail") or {}).get("gold_columns")
        if old_cols is not None and verdict.detail.get("gold_columns") is not None \
                and sorted(old_cols) != sorted(verdict.detail["gold_columns"]):
            report["drift"] += 1
            continue
        report["checked"] += 1
        if verdict.correct != bool(r["correct"]):
            report["flips"]["wrong->right" if verdict.correct else "right->wrong"] += 1
    return report


def rejudge(runs_dir: Path) -> list[dict]:
    conn = db.connect_ro(ROOT / "data" / "learning_platform.db")
    try:
        return [rejudge_run(p, conn) for p in sorted(runs_dir.glob("*.jsonl"))
                if not p.name.startswith(EXCLUDE_PREFIX)]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "results"))
    args = ap.parse_args()

    reports = rejudge(Path(args.runs))
    print(f"{'run':<34}{'rows':>6}{'rejudged':>10}{'flips':>7}  direction")
    total_flips = total_checked = runs_in = runs_out = 0
    for rep in reports:
        flips = rep["flips"]
        in_scope = sum(flips.values())
        if rep["out_of_scope"]:
            runs_out += 1
            print(f"{rep['run']:<40}{rep['rows']:>5}{'-':>9}{'-':>7}  "
                  f"out of scope: {rep['out_of_scope']}")
            continue
        runs_in += 1
        total_checked += rep["checked"]
        total_flips += in_scope
        note = "no change" if not flips else ", ".join(f"{k}={v}" for k, v in sorted(flips.items()))
        if rep["drift"]:
            note += f" [dataset drift, excluded: {rep['drift']}]"
        if rep["missing"]:
            note += f" [id gone from current task set: {rep['missing']}]"
        delta = flips["wrong->right"] - flips["right->wrong"]
        print(f"{rep['run']:<40}{rep['rows']:>5}{rep['checked']:>9}{in_scope:>7}  {note}"
              + (f"  net {100*delta/rep['checked']:+.2f}pp" if rep["checked"] and flips else ""))

    print(f"\n覆盖：{runs_in} 次运行在范围内，{runs_out} 次 out of scope；"
          f"{total_checked} 条已存答案用当前判分器重判")
    print(f"total in-scope verdict changes (judge behaviour only): {total_flips}")
    if total_flips:
        print("=> 已发表数字受影响：重跑 `scripts/calibrate.py`、`scripts/significance.py` 与 "
              "`python -m sqlagent.report`，并同步 README/HANDOFF 里被这些文件支撑的每个数。")
        return 1
    print("=> 判分器改动不影响任何已发表判定：每一条可比答案重判后结论一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
