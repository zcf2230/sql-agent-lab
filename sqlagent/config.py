"""Central configuration.

Everything that can change an experiment result lives here, and `config_hash()`
fingerprints it so the eval runner can (a) cache results per configuration and
(b) refuse to compare two runs that were configured differently.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"

# USD per 1M tokens. Unknown models cost 0, which keeps mock/offline runs free.
PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.84, 2.00),
    "qwen-plus": (0.11, 0.28),
    "glm-4-flash": (0.0, 0.0),
    "gpt-4o-mini": (0.15, 0.60),
}

PROMPT_VERSION = "v1"  # bump whenever you edit the system prompt -> invalidates cache


def load_env(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments). Avoids a dependency."""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
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
    max_result_rows: int = 200
    query_timeout_s: float = 5.0

    # --- experiments ---
    seed: int = 20260921
    prompt_version: str = PROMPT_VERSION
    corruption: str = "none"  # mock provider only; see MockProvider docstring

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        if self.provider == "mock":
            return 0.0  # never let a synthetic run print a plausible-looking cost
        pin, pout = PRICING.get(self.model, (0.0, 0.0))
        return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000

    def config_hash(self) -> str:
        """Stable fingerprint of everything that affects a score."""
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


def settings_from_env(**overrides) -> Settings:
    load_env()
    kwargs: dict = {}
    if os.environ.get("SQLAGENT_API_KEY"):
        kwargs["api_key"] = os.environ["SQLAGENT_API_KEY"]
    if os.environ.get("SQLAGENT_MODEL"):
        kwargs["model"] = os.environ["SQLAGENT_MODEL"]
    if os.environ.get("SQLAGENT_BASE_URL"):
        kwargs["base_url"] = os.environ["SQLAGENT_BASE_URL"]
    if os.environ.get("SQLAGENT_PROVIDER"):
        kwargs["provider"] = os.environ["SQLAGENT_PROVIDER"]
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**kwargs)
