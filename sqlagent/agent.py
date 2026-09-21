"""The ReAct loop.

One turn = one model call, optionally followed by tool execution whose results are
appended as `role=tool` messages. The loop ends when the model replies without
asking for a tool, or when the step budget runs out.

`final_sql` is taken from the last *successfully executed* query, not from the
model's prose. If a model narrates a query it never ran, that counts as a failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import RUNS_DIR, Settings
from .prompts import build_messages
from .tools import Toolbox
from .trace import TraceRecorder

_SQL_FENCE = re.compile(r"```sql\s*(.+?)```", re.S | re.I)


@dataclass
class AgentResult:
    task_id: str
    final_sql: str | None
    answer_text: str | None
    stop_reason: str  # final_answer | max_steps | no_sql_executed
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    stats: dict = field(default_factory=dict)
    executed_row_count: int | None = None
    safety_blocks: int = 0


def serialise_tool_calls(calls) -> list[dict]:
    return [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
        for c in calls
    ]


class SqlAgent:
    def __init__(self, settings: Settings, toolbox: Toolbox):
        self.s = settings
        self.tb = toolbox

    def run(self, question: str, task_id: str, provider, fewshot: list[dict] | None = None) -> AgentResult:
        messages = build_messages(question, fewshot=fewshot)
        trace = TraceRecorder(task_id=task_id, config_hash=self.s.config_hash(), provider=provider.name)

        last_ok_sql: str | None = None
        last_ok_rows: int | None = None
        safety_blocks = 0
        stop_reason = "max_steps"

        for turn in range(1, self.s.max_steps + 1):
            completion = provider.complete(messages, self.tb.schemas)
            trace.llm_call(
                turn,
                completion.content,
                completion.tool_calls,
                completion.prompt_tokens,
                completion.completion_tokens,
                completion.latency_ms,
            )

            if not completion.tool_calls:
                stop_reason = "final_answer"
                messages.append({"role": "assistant", "content": completion.content or ""})
                break

            messages.append({"role": "assistant", "content": completion.content, "tool_calls": serialise_tool_calls(completion.tool_calls)})

            for call in completion.tool_calls:
                result = self.tb.call(call.name, call.arguments)
                trace.tool_call(call.name, call.arguments, result)
                if result.get("error_type") == "rejected_by_safety_guard":
                    safety_blocks += 1
                if call.name == "run_sql" and result.get("ok"):
                    last_ok_sql = str(call.arguments.get("sql"))
                    last_ok_rows = int(result.get("row_count") or 0)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        final_sql = last_ok_sql or self._sql_from_prose(messages[-1].get("content", ""))
        if last_ok_sql is None and final_sql is not None:
            stop_reason = "no_sql_executed"

        cost = self.s.cost_usd(trace.stats()["prompt_tokens"], trace.stats()["completion_tokens"])
        trace.write(RUNS_DIR, self.s.tag())

        return AgentResult(
            task_id=task_id,
            final_sql=final_sql,
            answer_text=next((m.get("content") for m in reversed(messages) if m["role"] == "assistant"), None),
            stop_reason=stop_reason,
            prompt_tokens=trace.stats()["prompt_tokens"],
            completion_tokens=trace.stats()["completion_tokens"],
            cost_usd=cost,
            stats=trace.stats(),
            executed_row_count=last_ok_rows,
            safety_blocks=safety_blocks,
        )

    @staticmethod
    def _sql_from_prose(text: str | None) -> str | None:
        if not text:
            return None
        match = _SQL_FENCE.search(text)
        return match.group(1).strip() if match else None
