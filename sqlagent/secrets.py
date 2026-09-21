"""Credential storage backed by Windows DPAPI.

The API key is never kept in plaintext. It is encrypted with `CryptProtectData`
at **CurrentUser** scope plus a project-specific entropy string, which means the
blob can only be unwrapped by:

  * a process running as *this* Windows user, **and**
  * one that supplies the same entropy

Copying `secrets/*.dpapi` to another machine or another account yields nothing.

Two honest limits, so nobody over-trusts this:
  * it is not protection against a process already running as you - that process
    can call the same API with the same entropy;
  * it is not protection against someone who has your Windows login password,
    since the master key is derived from it.

Anything better needs a real secret manager, which is out of scope for a laptop
benchmark. The reason this file exists at all: a key in a plaintext `.env` gets
committed by accident, pasted into a screenshot, or shared with "here, run it for
me". An opaque blob does not.

Never pass the key as a command-line argument - argv is logged by shells, process
monitors and crash reporters. It is read from the environment or from a file only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = ROOT / ".env"
LEGACY_BLOB = ROOT / "secrets" / "sqlagent.dpapi"


def dotenv_value(key: str) -> str:
    """Read one non-secret field out of .env without importing config (no import cycle)."""
    if not DOTENV_PATH.exists():
        return ""
    prefix = key + "="
    for line in DOTENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def blob_path(model: str | None = None) -> Path:
    """One blob slot per model.

    A second provider is the whole point of a cross-model comparison, and the first
    implementation kept a single `sqlagent.dpapi` - sealing the Qwen key would have
    silently destroyed the DeepSeek one. The slot is named after SQLAGENT_MODEL so no
    new configuration key is needed to say which credential you mean.
    """
    name = (model or dotenv_value("SQLAGENT_MODEL") or "default").strip().lower().replace("-", "_")
    return ROOT / "secrets" / f"sqlagent.{name}.dpapi"

# Changing this string invalidates every stored blob (deliberate, not a bug).
ENTROPY = b"sql-agent-lab:credential:v1"

if os.name != "nt":  # pragma: no cover - the benchmark runs on Windows
    raise ImportError("sqlagent.secrets uses Windows DPAPI and is unavailable on this platform")

import ctypes
from ctypes import wintypes

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_bytes(blob: _DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(blob.pbData)


def protect(plaintext: bytes) -> bytes:
    entropy = _DATA_BLOB(len(ENTROPY), ctypes.create_string_buffer(ENTROPY, len(ENTROPY)))
    src = _DATA_BLOB(len(plaintext), ctypes.create_string_buffer(plaintext, len(plaintext)))
    dst = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(src), "sql-agent-lab", ctypes.byref(entropy), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst),
    )
    if not ok:
        raise OSError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    return _to_bytes(dst)


def unwrap(blob: bytes) -> bytes:
    entropy = _DATA_BLOB(len(ENTROPY), ctypes.create_string_buffer(ENTROPY, len(ENTROPY)))
    src = _DATA_BLOB(len(blob), ctypes.create_string_buffer(blob, len(blob)))
    dst = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(src), None, ctypes.byref(entropy), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(dst),
    )
    if not ok:
        raise OSError(
            f"CryptUnprotectData failed: {ctypes.get_last_error()}. "
            "A blob can only be unwrapped by the Windows user (and entropy) that sealed it."
        )
    return _to_bytes(dst)


def store(key: str) -> Path:
    key = key.strip()
    if not key:
        raise ValueError("refusing to store an empty key")
    path = blob_path()
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(protect(key.encode("utf-8")))
    path.chmod(0o600)
    return path


def load(model: str | None = None) -> str | None:
    path = blob_path(model)
    if not path.exists():
        # one-time migration: the original single-slot blob belongs to deepseek-chat
        if model in (None, "deepseek-chat") and LEGACY_BLOB.exists() and path != LEGACY_BLOB:
            return unwrap(LEGACY_BLOB.read_bytes()).decode("utf-8")
        return None
    return unwrap(path.read_bytes()).decode("utf-8")


def looks_placeholder(key: str) -> bool:
    """Reject values that are not really keys.

    The failure being defended against is ordinary: a template line is left in
    place, the placeholder gets sealed, every later request 401s, and the obvious
    conclusion is that the API is broken.
    """
    low = key.strip().lower()
    if len(low) < 20 or not low.startswith("sk-"):
        return True
    return any(marker in low for marker in ("your-key", "paste", "placeholder", "xxxx", "000000"))


def migrate_from_dotenv(delete_source: bool = True) -> bool:
    """Move a plaintext .env key into this model's blob, then destroy the plaintext."""
    if not DOTENV_PATH.exists():
        return False
    key = None
    # utf-8-sig: Notepad can prepend a BOM, which would otherwise glue itself onto
    # the first key name and make that line silently unparseable
    for line in DOTENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("SQLAGENT_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        print("no SQLAGENT_API_KEY line found in .env")
        return False
    if looks_placeholder(key):
        raise SystemExit(
            "That does not look like a real key (need sk-..., at least 20 chars, and not a\n"
            "placeholder). Replace the value after 'SQLAGENT_API_KEY=' in .env and run again."
        )
    store(key)
    if delete_source:
        DOTENV_PATH.write_text(
            "# key sealed into this model's slot under secrets/ (Windows DPAPI: CurrentUser + entropy)\n"
            "# To reseed: add  SQLAGENT_API_KEY=sk-...  below, close this file's editor,\n"
            "# then run:  python -m sqlagent.secrets   (it wipes the key back out of this file)\n"
            "# Never put the key on the command line - shells and crash reporters record it.\n"
            "SQLAGENT_PROVIDER=openai\n"
            "SQLAGENT_MODEL=deepseek-chat\n"
            "SQLAGENT_BASE_URL=https://api.deepseek.com/v1\n",
            encoding="utf-8",
        )
    return True


def main() -> int:
    if len(sys.argv) > 2:
        # argv is recorded in shell history and process logs; never accept the key here
        print("Refusing to take a key as an argument. Use: SQLAGENT_API_KEY=... python -m sqlagent.secrets")
        return 2
    if migrate_from_dotenv():
        print(f"sealed into {blob_path().relative_to(ROOT)}; plaintext .env overwritten")
        return 0
    from_env = os.environ.get("SQLAGENT_API_KEY")
    if from_env:
        print(f"sealed into {blob_path().relative_to(ROOT)}")
        store(from_env)
        os.environ.pop("SQLAGENT_API_KEY")
        return 0
    print("nothing to do: .env holds no key and SQLAGENT_API_KEY is unset")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
