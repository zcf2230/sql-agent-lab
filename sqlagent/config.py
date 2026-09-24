"""Central configuration.

Everything that can change an experiment result lives here, and `config_hash()`
fingerprints it so the eval runner can (a) cache results per configuration and
(b) refuse to compare two runs that were configured differently.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"

# USD per 1M tokens. Unknown models cost 0, which keeps mock/offline runs free.
#
# PROVENANCE, because these numbers were wrong once already. The original
# `deepseek-chat` row (0.27 / 1.10) is a stale list price. Back-solving the first
# 192-task benchmark - 9,764,872 prompt + 523,713 completion tokens across ten
# runs, against the ~CNY 10 actually debited in the DeepSeek console - puts the
# effective rate near CNY 1 / 2 per 1M tokens, i.e. the row below. That is a
# *derived* figure from one account in one month, not a published price list, and
# it silently absorbs DeepSeek's prefix-cache discount, which this benchmark hits
# heavily because a ReAct loop resends the same system prompt and tool schema on
# every one of its ~4.7 calls.
#
# So: treat every cost number this project prints as an estimate. If you change
# provider or the price list moves, update this table - and prefer the console.
PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.28, 0.42),
    # CALIBRATED BLENDED RATE, not a price list. Two Qwen runs consumed 1,897,639
    # tokens (97.3% of them input) against CNY 1 debited in the DashScope console,
    # which gives CNY 0.527 / 1M tokens overall. The same figure is entered for
    # input and output because a single billing observation cannot separate them.
    # Valid wherever the mix stays input-dominated; a run that shifts the ratio
    # (long answers, few tool calls) will be wrong by roughly that shift.
    "qwen-flash": (0.0732, 0.0732),
    # UNVERIFIED - carried over from the original guess and never checked against a
    # bill. No GLM row at all: a (0.0, 0.0) entry reads as "this model is free",
    # which is the same mistake as an understated price, just in the flattering
    # direction. Omit it and the report says 单价未录入 instead.
    "qwen-plus": (0.11, 0.28),
    "gpt-4o-mini": (0.15, 0.60),
}

USD_PER_CNY = 7.2  # rough mid-2026 rate, only used for the CNY readout


# Where each priced model actually lives. Same reason `PRICING` is keyed by model:
# a run's endpoint must follow the model that was asked for. Before this table,
# `.env`'s SQLAGENT_BASE_URL decided the endpoint and `--model` only decided the
# label, so a run tagged `deepseek-chat` while .env still pointed at DashScope sent
# `model=deepseek-chat` to Qwen's URL and got 404 on all 80 tasks. The runner caught
# it (`harness_exceptions`, pass@1 withheld), which is the only reason this was a
# wasted minute rather than a published number. Unknown models fall back to .env.
MODEL_BASE_URLS: dict[str, str] = {
    "deepseek-chat": "https://api.deepseek.com/v1",
    "deepseek-reasoner": "https://api.deepseek.com/v1",
    "qwen-flash": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "qwen-plus": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

PROMPT_VERSION = "v1"  # bump whenever you edit the system prompt -> invalidates cache

# Files whose behaviour determines a verdict. Editing any of them - including a
# comment - changes the digest and invalidates the result cache.
CODE_FILES = ("agent.py", "tools.py", "prompts.py", "fewshot.py", "llm.py", "safety.py", "db.py", "eval/scoring.py")


def code_hash() -> str:
    """Self-invalidating fingerprint of the harness source.

    A manually bumped version constant is the same footgun in a different hat: the
    person editing the judge is the person who has to remember that the judge
    changed. Hashing the files makes forgetting impossible.

    Newlines are normalised first. This reads *working-tree* bytes while git stores
    LF, so a clone with different line endings would otherwise produce a different
    digest and orphan the whole result cache for no behavioural reason.
    """
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in CODE_FILES:
        path = here / name
        body = path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else b"<missing>"
        digest.update(name.encode())
        digest.update(body)
    return digest.hexdigest()[:8]


def load_env(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments). Avoids a dependency."""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"  # "mock" | "openai"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    request_timeout_s: float = 60.0
    max_retries: int = 3

    # --- agent loop ---
    max_steps: int = 8
    self_repair: bool = True
    fewshot_k: int = 0

    # --- database / execution ---
    db_path: str = str(DATA_DIR / "learning_platform.db")
    # Where a task's own `db` field is resolved from, for benchmarks whose questions
    # live in many databases (BIRD). Deliberately *not* part of `config_hash`: the
    # per-task `db` value is inside the task file, which the dataset digest already
    # covers, so adding a machine-local absolute path to the hash would only make the
    # key unshareable. The resolved root is recorded in each run's `_summary` instead.
    db_root: str = ""
    max_result_rows: int = 200
    query_timeout_s: float = 5.0

    # --- experiments ---
    seed: int = 20260921
    prompt_version: str = PROMPT_VERSION
    corruption: str = "none"  # mock provider only; see MockProvider docstring
    dataset_hash: str = ""  # digest of the task file, filled in by the runner

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        if self.provider == "mock":
            return 0.0  # never let a synthetic run print a plausible-looking cost
        pin, pout = PRICING.get(self.model, (0.0, 0.0))
        return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000

    def config_hash(self) -> str:
        """Stable fingerprint of everything that affects a score.

        The dataset digest is part of this on purpose. Task ids are stable across
        regeneration (`filter_projection-006` stays `filter_projection-006`), so a
        cache keyed only on the model config would keep serving last week's verdict
        for a question and gold you have since rewritten.
        """
        relevant = {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_steps": self.max_steps,
            "self_repair": self.self_repair,
            "fewshot_k": self.fewshot_k,
            "prompt_version": self.prompt_version,
            "seed": self.seed,
            "corruption": self.corruption,
            "dataset": self.dataset_hash,
            "code": code_hash(),
        }
        blob = json.dumps(relevant, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def tag(self) -> str:
        bits = [self.provider, self.model.replace("-", "_")]
        if self.provider == "mock":
            bits.append(f"corr-{self.corruption}")
        bits.append(f"fs{self.fewshot_k}")
        if not self.self_repair:
            bits.append("noselfrepair")
        return "__".join(bits)


def resolve_api_key(model: str = "") -> str:
    """Environment (which .env feeds into), then this model's DPAPI slot.

    Never log the result. Placeholder values are skipped rather than returned -
    otherwise a half-filled `.env` template would outrank a working sealed key and
    every request would 401 with no clue why.
    """
    candidate = os.environ.get("SQLAGENT_API_KEY", "")
    if candidate and not candidate.lower().startswith(("sk-your-key", "paste_", "placeholder")):
        return candidate
    try:
        from . import secrets  # Windows-only; lazy so the package imports elsewhere
        return secrets.load(model or None) or ""
    except (ImportError, OSError):
        return ""


def settings_from_env(**overrides) -> Settings:
    load_env()
    kwargs: dict = {}
    # The model is resolved first because it decides two things that must agree with
    # it: which credential slot to open, and which endpoint to send to. `--model` used
    # to be applied last, so both were taken from whatever provider .env pointed at.
    model = overrides.get("model") or os.environ.get("SQLAGENT_MODEL") or ""
    if model:
        kwargs["model"] = model
    if key := resolve_api_key(model):
        kwargs["api_key"] = key
    env_base = os.environ.get("SQLAGENT_BASE_URL", "")
    base = MODEL_BASE_URLS.get(model) or env_base
    if base:
        if env_base and model in MODEL_BASE_URLS and env_base != base:
            print(f"[config] {model} 的端点以 MODEL_BASE_URLS 为准（{base}），"
                  f"忽略 .env 里的 SQLAGENT_BASE_URL={env_base}", file=sys.stderr)
        kwargs["base_url"] = base
    if os.environ.get("SQLAGENT_PROVIDER"):
        kwargs["provider"] = os.environ["SQLAGENT_PROVIDER"]
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**kwargs)
