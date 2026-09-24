"""Trace files are the evidence layer, so a torn record is a lost claim.

`runs/*.jsonl` is appended by the runner's worker threads (`--workers`, default 4).
Before the append held a process-wide lock, a several-KB record could reach the OS in
pieces and interleave with another thread's pieces. That is not hypothetical: roughly
one line in every few thousand of the published trace files is a fragment that is not
JSON, and the report's per-task replay silently had no row to show for those tasks.

So the fix is the lock in `TraceRecorder.write`, and the readers count what they skip
instead of swallowing it - see `report.load_traces` and section 9 of the generated report.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlagent.trace import TraceRecorder


def _heavy_trace(i: int) -> TraceRecorder:
    """One record large enough that a single `write()` is not one OS write."""
    rec = TraceRecorder(task_id=f"task-{i}", config_hash="cfg", provider="mock")
    for turn in range(12):
        rec.llm_call(turn=turn, content="x" * 4000, tool_calls=[],
                     prompt_tokens=100, completion_tokens=50, latency_ms=1.0)
    return rec


def test_parallel_appends_never_tear_a_record(tmp_path: Path):
    n = 24
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: _heavy_trace(i).write(tmp_path, "concurrent"), range(n)))

    path = tmp_path / "concurrent.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n, f"{len(lines)} lines for {n} records: something interleaved"
    seen = set()
    for line in lines:
        rec = json.loads(line)          # a torn line raises here, which is the bug
        seen.add(rec["task_id"])
        assert len(rec["steps"]) == 12, "a record parsed but arrived truncated"
    assert seen == {f"task-{i}" for i in range(n)}


def test_a_torn_trace_file_is_counted_not_silently_dropped(tmp_path: Path, monkeypatch):
    """The reader half of the same defect: skipping an unparseable line without saying
    so turns missing evidence into a page that shows no gap where it used to be."""
    from sqlagent import report

    runs = tmp_path / "runs"
    runs.mkdir()
    good = {"task_id": "t1", "config_hash": "c", "provider": "mock", "timestamp": 0,
            "stats": {}, "steps": []}
    (runs / "abl2-baseline.jsonl").write_text(
        json.dumps(good) + "\n" + ', "result": "{\\"ok\\": true' + "\n", encoding="utf-8")
    monkeypatch.setattr(report, "RUNS", runs)

    traces, malformed = report.load_traces()
    assert list(traces["abl2-baseline"]) == ["t1"]
    assert malformed == {"abl2-baseline": 1}, "the torn line was dropped without a count"
    note = report.trace_integrity_note(malformed)
    assert "少了 1 条" in note and "abl2-baseline 少 1 条" in note
    assert "不是没跑过" in note, "the note must say this is missing evidence, not a low score"
    assert "分数不受影响" in note, "and must bound what the hole touches"
    assert report.trace_integrity_note({}) == "", "clean files must not print a warning"
