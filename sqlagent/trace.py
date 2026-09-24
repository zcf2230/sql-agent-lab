"""Per-run tracing.

Nothing here is optional decoration: every improvement claimed in the results
table has to be reconstructable from these JSONL files. If you cannot show the
exact prompt, tool argument and error for a run, you cannot claim you fixed it.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_FIELD_CHARS = 4000

# One process-wide lock, because the runner parallelises with threads (see
# `TraceRecorder.write`). Two concurrent writers with no shared lock can split a large
# record across OS writes, and the interleaved halves are not parseable JSON.
_APPEND_LOCK = threading.Lock()


def _shrink(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) > MAX_FIELD_CHARS:
        return text[:MAX_FIELD_CHARS] + f"...<+{len(text) - MAX_FIELD_CHARS} chars>"
    return text


@dataclass
class TraceRecorder:
    task_id: str
    config_hash: str
    provider: str
    started_at: float = field(default_factory=time.time)
    steps: list[dict] = field(default_factory=list)

    def llm_call(self, turn: int, content, tool_calls, prompt_tokens: int, completion_tokens: int, latency_ms: float) -> None:
        self.steps.append(
            {
                "i": len(self.steps),
                "kind": "llm",
                "turn": turn,
                "t_ms": round(latency_ms, 1),
                "content": _shrink(content) if content else None,
                "tool_calls": [{"name": c.name, "arguments": _shrink(c.arguments)} for c in tool_calls],
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        )

    def tool_call(self, name: str, arguments: dict, result: dict) -> None:
        self.steps.append(
            {
                "i": len(self.steps),
                "kind": "tool",
                "name": name,
                "arguments": _shrink(arguments),
                "ok": bool(result.get("ok")),
                "result": _shrink(result),
                "error_type": result.get("error_type"),
            }
        )

    def stats(self) -> dict:
        sql_attempts = [s for s in self.steps if s["kind"] == "tool" and s["name"] == "run_sql"]
        error_kinds = [s.get("error_type") for s in self.steps if s["kind"] == "tool" and not s["ok"]]
        return {
            "steps": sum(1 for s in self.steps if s["kind"] == "llm"),
            "tool_calls": sum(1 for s in self.steps if s["kind"] == "tool"),
            "sql_attempts": len(sql_attempts),
            "sql_errors": sum(1 for s in sql_attempts if not s["ok"]),
            "prompt_tokens": sum(s.get("prompt_tokens", 0) for s in self.steps if s["kind"] == "llm"),
            "completion_tokens": sum(s.get("completion_tokens", 0) for s in self.steps if s["kind"] == "llm"),
            "wall_ms": round((time.time() - self.started_at) * 1000, 1),
            "error_kinds": error_kinds,
            "tool_sequence": [s["name"] for s in self.steps if s["kind"] == "tool"],
        }

    def write(self, runs_dir: Path, tag: str) -> Path:
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{tag}.jsonl"
        record = {
            "task_id": self.task_id,
            "config_hash": self.config_hash,
            "provider": self.provider,
            "timestamp": self.started_at,
            "stats": self.stats(),
            "steps": self.steps,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        # The runner appends from several worker threads into one file. A record is a
        # few KB, TextIOWrapper hands that to the OS in chunks, and two interleaved
        # halves make a line that is not JSON. How often that happened in the published
        # files is not restated here on purpose - `python -m sqlagent.report` counts the
        # surviving damage every time it runs, and report section 9 prints it.
        with _APPEND_LOCK, path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return path
