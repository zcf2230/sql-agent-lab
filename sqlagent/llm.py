"""Provider abstraction.

`OpenAICompatProvider` talks to any /chat/completions endpoint (DeepSeek, Qwen,
GLM, OpenAI). `MockProvider` is a scripted oracle with deliberately injected
defects: it exists so the whole loop, tracing, scoring and caching machinery can
be exercised and *tested* without spending money or trusting a model.

Mock scores are NOT model capability. They calibrate the grader.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx
import sqlglot

from .config import Settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Completion:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion: ...


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough chars/4 estimate, used when a provider omits `usage`."""
    return max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        self.s = settings
        if not settings.api_key:
            raise RuntimeError(
                "provider='openai' has no key. Seal one: put SQLAGENT_API_KEY=sk-... in .env "
                "and run `python -m sqlagent.secrets`. Do not pass it on the command line."
            )
        self._url = settings.base_url.rstrip("/") + "/chat/completions"
        self._client = httpx.Client(timeout=settings.request_timeout_s)

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        body = {
            "model": self.s.model,
            "messages": messages,
            "temperature": self.s.temperature,
            "max_tokens": self.s.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        last_exc: Exception | None = None
        for attempt in range(self.s.max_retries):
            try:
                start = time.perf_counter()
                resp = self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self.s.api_key}"},
                    json=body,
                )
                # 429/5xx are transient; a 400 from a bad request body is not.
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()
                return self._parse(data, (time.perf_counter() - start) * 1000)
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                # exponential backoff, plus Retry-After when the server offers one
                delay = min(30.0, (2**attempt) * 1.5)
                retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                time.sleep(delay)
        raise RuntimeError(f"provider failed after {self.s.max_retries} attempts: {last_exc}")

    def _parse(self, data: dict, latency_ms: float) -> Completion:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError:
                # models do emit truncated JSON; surfacing it beats crashing the run
                args = {"_unparseable": raw}
            calls.append(ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=tc["function"]["name"], arguments=args))
        usage = data.get("usage") or {}
        return Completion(
            content=msg.get("content"),
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or _estimate_tokens([])),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
            model=data.get("model", self.s.model),
        )


# --------------------------------------------------------------------------
# Mock provider
# --------------------------------------------------------------------------

# Each mode is chosen to answer a different question about the harness:
#   none          -> happy path
#   reflow        -> semantically identical rewrite. MUST still score correct.
#   reorder_cols  -> column order differs, values equal. MUST still score correct.
#   float_round   -> 3.14159 vs 3.14. MUST score correct under tolerance.
#   drop_distinct -> extra duplicate rows. MUST score WRONG.
#   wrong_limit   -> right rows, wrong count. MUST score WRONG.
#   bad_column    -> SQL errors. Exercises the self-repair loop.
CORRUPTION_MODES = ("none", "reflow", "reorder_cols", "float_round", "drop_distinct", "wrong_limit", "bad_column")


def corrupt(sql: str, mode: str) -> tuple[str, bool]:
    """Return (sql, applied).

    `applied` matters: some defects cannot be injected into a given query (you
    cannot drop a DISTINCT that is not there). A calibration table that counted
    those as "the grader missed it" would be lying, so the runner filters on it.
    """
    if mode not in CORRUPTION_MODES:
        raise ValueError(f"unknown corruption mode {mode!r}; choose from {', '.join(CORRUPTION_MODES)}")
    if mode == "none":
        return sql, False
    tree = sqlglot.parse_one(sql, dialect="sqlite")
    if mode == "reflow":
        out = tree.sql(dialect="sqlite")
        return out, out != sql
    selects = [s for s in tree.find_all(sqlglot.exp.Select)]
    if not selects:
        return sql, False
    sel = selects[0]
    if mode == "reorder_cols":
        if len(sel.expressions) < 2:
            return sql, False
        sel.set("expressions", list(reversed(sel.expressions)))
    elif mode == "float_round":
        exprs = list(sel.expressions)
        if not exprs:
            return sql, False
        last = exprs[-1]
        # unwrap the alias first: ROUND(COUNT(*) AS n, 1) is not SQL
        inner = last.unalias() if isinstance(last, sqlglot.exp.Alias) else last
        alias = last.alias_or_name
        wrapped_sql = f"SELECT ROUND({inner.sql(dialect='sqlite')}, 1)" + (f" AS {alias}" if alias else "")
        wrapped = sqlglot.parse_one(wrapped_sql, dialect="sqlite")
        sel.set("expressions", exprs[:-1] + list(wrapped.expressions))
    elif mode == "drop_distinct":
        if sel.args.get("distinct") is None:
            return sql, False
        sel.set("distinct", None)
    elif mode == "wrong_limit":
        tree.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(3)))
    elif mode == "bad_column":
        col = next(iter(tree.find_all(sqlglot.exp.Column)), None)
        if col is None:
            return sql, False
        col.set("this", sqlglot.exp.column(col.name + "_typo"))
    out = tree.sql(dialect="sqlite")
    return out, out != sql


def _tool_ok(message: dict) -> bool:
    """Parse the tool message instead of substring-searching it.

    `content` is already a JSON string, so a '"ok": true' probe fails once
    json.dumps escapes the inner quotes - which is how an earlier version of this
    mock never noticed success and ran until the step budget expired.
    """
    try:
        return bool(json.loads(message.get("content") or "{}").get("ok"))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


class MockProvider:
    """Answers with (optionally corrupted) gold SQL, driving the tool loop honestly.

    `table_probe` and `value_probe` feed the agent real schema/values so the
    happy path produces a realistic multi-step trace. On a DB error it repairs
    itself only when `self_repair` is enabled, which is what lets the ablation
    table attribute a delta to the repair loop.
    """

    name = "mock"

    def __init__(self, gold_sql: str, tables: list[str], corruption: str, self_repair: bool):
        self.gold = gold_sql
        self.tables = tables
        self.corruption = corruption
        self.self_repair = self_repair
        self.turn = 0
        self.submitted, self.applied = corrupt(gold_sql, corruption)

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.turn += 1
        pt = _estimate_tokens(messages)
        last = messages[-1] if messages else {}

        if last.get("role") != "tool":
            return self._tool(ToolCall("c1", "get_schema", {"tables": self.tables}), pt)

        if last.get("name") != "run_sql":
            # the schema probe came back; now submit the (possibly corrupted) query
            return self._run_sql(self.submitted, pt)

        if _tool_ok(last):
            return self._final(self.submitted, pt)
        if not self.self_repair:
            return self._note("I could not resolve the database error and stopped.", pt)
        return self._run_sql(self.gold, pt)  # repaired

    def _tool(self, call: ToolCall, pt: int) -> Completion:
        return Completion(content=None, tool_calls=[call], prompt_tokens=pt, completion_tokens=12, model="mock")

    def _final(self, sql: str, pt: int) -> Completion:
        body = f"```sql\n{sql}\n```\nThat answers the question."
        return Completion(content=body, tool_calls=[], prompt_tokens=pt, completion_tokens=len(body) // 4, model="mock")

    def _note(self, text: str, pt: int) -> Completion:
        return Completion(content=text, tool_calls=[], prompt_tokens=pt, completion_tokens=len(text) // 4, model="mock")

    def _run_sql(self, sql: str, pt: int) -> Completion:
        self.submitted = sql
        return self._tool(ToolCall(f"c{self.turn}", "run_sql", {"sql": sql}), pt)
