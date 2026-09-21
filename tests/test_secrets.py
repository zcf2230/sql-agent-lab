"""Credential storage tests.

The property being asserted is "a plaintext key does not persist on disk", not
just "the encryption call works" - the first is the reason the module exists.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlagent.secrets", reason="DPAPI is Windows-only")
from sqlagent import secrets  # noqa: E402

CANARY = "sk-unit-test-value-not-a-real-key-000000"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_roundtrip_and_cipher_is_not_plaintext():
    blob = secrets.protect(CANARY.encode())
    assert secrets.unwrap(blob).decode() == CANARY
    assert CANARY.encode() not in blob


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_wrong_entropy_cannot_unwrap(monkeypatch):
    blob = secrets.protect(CANARY.encode())
    monkeypatch.setattr(secrets, "ENTROPY", b"some-other-entropy")
    with pytest.raises(OSError):
        secrets.unwrap(blob)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_no_plaintext_key_lingers_in_the_worktree():
    """Guards the accident that actually happens: a stray key file that gets committed."""
    offenders = []
    for path in secrets.ROOT.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc", ".db", ".dpapi"}:
            continue
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "sk-" in text and "your-key" not in text and "unit-test" not in text and "sk-" != text:
            offenders.append(str(path.relative_to(secrets.ROOT)))
    assert not offenders, f"possible plaintext credential(s): {offenders}"
