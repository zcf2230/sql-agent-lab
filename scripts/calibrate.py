"""Grader calibration sweep.

A benchmark number is only worth as much as your willingness to trust the judge,
and "trust me, the comparison looks right" is not evidence. This script injects
known defects with the mock provider and asks a single question each time: did
the judge notice?

The expectation is *derived*, not hand-written. The runner reports whether the
injected SQL produced a materially different result (`result_changed`), so:

  * defect changed the answer      -> the judge must reject it
  * defect changed nothing         -> the judge must accept it (a defect nobody
                                      could see is not a judge miss)
  * presentation-only difference   -> accept, by policy

Two failure directions, which pull in opposite ways:
  false accept - the judge waved through a wrong answer  -> every score is inflated
  false reject - the judge failed a correct answer       -> you chase phantom bugs

Run:  python scripts/calibrate.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

# Modes and what they inject. `reflow` re-serialises the identical query, so it
# lands in "changed nothing" automatically and doubles as a control condition.
MODES = {
    "reflow": "same query, re-serialised",
    "reorder_cols": "columns presented in the opposite order",
    "float_round": "last measure rounded to 1 dp",
    "drop_distinct": "DISTINCT removed, rows may duplicate",
    "wrong_limit": "LIMIT 3 forced onto the result",
    "bad_column": "a column renamed to nonsense (exercises the repair loop)",
}

# Differences that are presentation only and therefore must still score correct.
PRESENTATION_ONLY = {"reorder_cols"}


# Fields that record *how this machine ran the query*, not what the judge decided.
# Left in, every re-run of this script rewrites all 192 rows of six tracked files
# (wall-clock jitter) plus a `cached` flag that flips once the result cache is warm,
# and a reviewer who follows HANDOFF §4.2 is left staring at 386-line diffs per file
# with no way to tell "your judge changed behaviour" from "a clock ticked".
# Stripping them makes the tracked artifact a record of judgement only.
VOLATILE_ROW_FIELDS = (("stats", "wall_ms"), ("cached",))


def _normalise(path: Path) -> None:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "_summary" not in obj:
            for field in VOLATILE_ROW_FIELDS:
                if len(field) == 1:
                    obj.pop(field[0], None)
                elif isinstance(obj.get(field[0]), dict):
                    obj[field[0]].pop(field[1], None)
        else:
            # `model` here is whatever the ambient .env happened to say, and no model
            # was called: this sweep drives the mock provider to exercise the *judge*.
            # Left alone it reads as "calibrated on deepseek-chat", and it made the
            # committed artifacts disagree with a re-run on a different default.
            obj["_summary"]["model"] = "mock (judge under test; no model called)"
        out.append(json.dumps(obj, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def run_mode(mode: str) -> dict:
    tag = f"calib-{mode}"
    cmd = [
        sys.executable, "-m", "sqlagent.eval.runner",
        "--provider", "mock", "--corruption", mode, "--tag", tag,
        "--workers", "4",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        raise SystemExit(f"runner failed for corruption={mode}")
    path = RESULTS / f"{tag}.jsonl"
    _normalise(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    return {"rows": [json.loads(l) for l in lines[1:] if l.strip()],
        "summary": json.loads(lines[0])["_summary"]}


def main() -> int:
    header = (
        f"{'corruption':<14} {'tested':>6} {'ok':>5} {'false-accept':>12} {'false-reject':>12} "
        f"{'rate':>6} {'no-op':>5}  what it injects"
    )
    print(header)
    print("-" * len(header))
    totals = {"tested": 0, "agree": 0, "fa": 0, "fr": 0}
    offenders: dict[str, list[str]] = {}

    for mode, what in MODES.items():
        data = run_mode(mode)
        # 0-row tasks are excluded from the headline metric, so exclude them here too
        scored = [r for r in data["rows"] if not r.get("trivial")]
        invisible = sum(1 for r in scored if r.get("result_changed") is None)
        tested = [r for r in scored if r.get("result_changed") is not None]

        rows_out = []
        false_accept = false_reject = 0
        for r in tested:
            should_pass = mode in PRESENTATION_ONLY or not r["result_changed"]
            if r["correct"] == should_pass:
                continue
            rows_out.append(f"{r['id']}({r['reason']})")
            if should_pass is False:
                false_accept += 1
            else:
                false_reject += 1

        agree = len(tested) - false_accept - false_reject
        totals["tested"] += len(tested)
        totals["agree"] += agree
        totals["fa"] += false_accept
        totals["fr"] += false_reject
        offenders[mode] = rows_out[:3]
        print(f"{mode:<14} {len(tested):>6} {agree:>5} {false_accept:>12} {false_reject:>12} "
              f"{(agree / len(tested) if tested else float('nan')):>5.1%} {invisible:>9}  {what[:26]}")
        if rows_out:
            print(f"{'':<14}   -> {', '.join(rows_out)}")

    print("-" * len(header))
    print(f"{'OVERALL':<14} {totals['tested']:>6} {totals['agree']:>5} {totals['fa']:>12} {totals['fr']:>12} "
          f"{totals['agree'] / max(1, totals['tested']):>5.1%}")
    print(
        "\n`no-op` = the defect could not be injected into that query shape at all (no\n"
        "DISTINCT to drop, nothing to reorder). Those rows are scored as 'must accept'\n"
        "rather than dropped - a judge cannot catch a defect that never reached the\n"
        "answer, and quietly excluding them would flatter the metric.\n"
        "false-accept is the dangerous column: every entry in it inflates a pass@1 you\n"
        "might later put on a CV. Open the id in results/ and decide whether the judge\n"
        "or the expectation is wrong before quoting any number.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
