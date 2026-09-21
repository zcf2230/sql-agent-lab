"""Credential storage tests.

The property being asserted is "a plaintext key does not persist on disk", not
just "the encryption call works" - the first is the reason the module exists.
"""

from __future__ import annotations

import os
import re

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
    """Guards the accident that actually happens: a stray key file that gets committed.

    Matches key *shape*, not the substring 'sk-': prose about the format lives in the
    README and in the .env template, and a scanner that flags documentation gets
    ignored within a week.
    """
    pattern = re.compile(r"sk-[A-Za-z0-9]{20,}")
    offenders = []
    for path in secrets.ROOT.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc", ".db", ".dpapi"}:
            continue
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(line) for line in text.splitlines()):
            offenders.append(str(path.relative_to(secrets.ROOT)))
    assert not offenders, f"possible plaintext credential(s): {offenders}"


def test_the_scanner_actually_fire():
    """A detector that is never proven to trigger is decoration.

    The planted value is concatenated at runtime so this file does not itself trip
    the worktree scan above.
    """
    pattern = re.compile(r"sk-[A-Za-z0-9]{20,}")
    planted = "SQLAGENT_API_KEY=" + "sk-" + "0123456789abcdef" * 2
    assert pattern.search(planted)
    assert not pattern.search("the key starts with sk- and is 35 chars")
    assert not pattern.search("SQLAGENT_API_KEY=PASTE_YOUR_NEW_KEY_HERE")
    assert not pattern.search("sk-your-key-here")
