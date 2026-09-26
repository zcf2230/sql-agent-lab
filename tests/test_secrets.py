"""Credential storage tests.

The property being asserted is "a plaintext key does not persist on disk", not
just "the encryption call works" - the first is the reason the module exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Provider key shapes differ. DeepSeek is `sk-` + 32 hex; a DashScope (Qwen) key is
# dotted, e.g. `sk-ws-xxx.yyy.zzz`. A regex written for one silently misses the other,
# and a scanner with a false negative is worse than none - it reports 'clean'.
KEY_SHAPE = re.compile("sk-[A-Za-z0-9][A-Za-z0-9._-]{29,}")

import pytest

from sqlagent import secrets  # imports on every platform now; see DPAPI_AVAILABLE below

# deliberately not key-shaped: the worktree scan below must not need an exclusion list
CANARY = "unit-test-fixture-only-never-a-credential"


@pytest.mark.skipif(os.name == "nt", reason="this is the non-Windows branch")
def test_the_module_imports_off_windows_and_says_what_is_unavailable():
    """The first CI run on Linux failed at *collection*: `secrets` raised ImportError at
    import time, which `importorskip` does not skip (it only skips a missing module, not
    one that raises about itself), so the dotenv-hygiene tests died with the DPAPI ones.
    Availability is data, and the error belongs at the call site."""
    assert secrets.DPAPI_AVAILABLE is False
    with pytest.raises(OSError, match="DPAPI"):
        secrets.protect(b"anything")
    with pytest.raises(OSError, match="DPAPI"):
        secrets.unwrap(b"anything")
    # the parts that are not DPAPI still work, which is why this file must run anywhere
    assert secrets.looks_placeholder("sk-your-key-here") is True
    assert secrets.blob_path("qwen-flash").name == "sqlagent.qwen_flash.dpapi"


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
    pattern = KEY_SHAPE
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
    deepseek = "sk-" + "0123456789abcdef" * 2
    dashscope = "sk-ws-ab1.cd2." + "e" * 94
    assert KEY_SHAPE.search(deepseek)
    assert KEY_SHAPE.search(dashscope)
    assert not KEY_SHAPE.search("the key starts with sk- and is 35 chars")
    assert not KEY_SHAPE.search("SQLAGENT_API_KEY=PASTE_YOUR_NEW_KEY_HERE")
    assert not KEY_SHAPE.search("sk-your-key-here")
    assert not KEY_SHAPE.search("SQLAGENT_API_KEY=sk-...")


# A home-directory path is not a credential, but publishing one names the machine account
# and the workspace layout. Written as a shape rather than this machine's path so the same
# assertion means something on the CI runner too. The {1,2} separator is for stored JSONL,
# where the text carries an escaped backslash pair, versus one in a shell snippet.
HOME_PATH = re.compile(r"(?i)(?:[A-Za-z]:[\\/]{1,2}Users[\\/]|/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/)")

# Assembled, not written out: a literal example in this file would be caught by the scan
# below, and the first two versions of this test failed on exactly that.
_S = "/"
_WIN = "C:" + "\\" + "Users" + "\\"
_ESC = "C:" + "\\\\" + "Users" + "\\\\"
_POSIX = _S + "home" + _S + "someone" + _S + "repo"
_MSYS = _S + "c" + _S + "Users" + _S + "someone" + _S + "repo"


def test_the_home_path_scanner_fires_and_stays_quiet_on_prose():
    assert HOME_PATH.search(_WIN + "someone" + "\\" + "repo")
    assert HOME_PATH.search('"note": "at ' + _ESC + "someone" + "\\" + "repo" + "\\" + 'data.db"')
    assert HOME_PATH.search(_POSIX)
    assert HOME_PATH.search(_MSYS), "the MSYS spelling must fire too"
    assert HOME_PATH.search("uv pip install -e .[dev]") is None


def test_no_published_file_names_a_local_home_path():
    """Walks the work tree rather than `git ls-files` on purpose: the first version shelled
    out to git, and on a machine where `git` is not on PATH that raised FileNotFoundError in
    some runs and passed vacuously in others. `runs/` is excluded because it holds local
    traces whose job is to name the files they read; it is not committed and not mirrored."""
    offenders = []
    for path in secrets.ROOT.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc", ".db", ".dpapi"}:
            continue
        if {".git", ".venv", "__pycache__", "runs"} & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        hit = HOME_PATH.search(text)
        if hit:
            offenders.append(f"{path.relative_to(secrets.ROOT)}: {hit.group(0)!r}")
    assert not offenders, f"files containing a local home path: {offenders}"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_sealing_only_removes_the_key_line(tmp_path, monkeypatch):
    """Regression: sealing used to rewrite .env from a template naming one model.

    Sealing a Qwen key therefore reverted the file to deepseek-chat, and the next
    seal would have gone to the wrong slot while printing a filename that implied it
    had not. Now only the key line is removed.

    `ROOT` is redirected to a temp directory on purpose. An earlier version of this
    test resolved the real slot path and unlinked it at the end, which deleted the
    user's actual sealed credential. A test that writes into the live secrets store
    is a data-loss bug waiting for its first green run.
    """
    monkeypatch.setattr(secrets, "ROOT", tmp_path)
    monkeypatch.setattr(secrets, "DOTENV_PATH", tmp_path / ".env")
    (tmp_path / ".env").write_text(
        "SQLAGENT_PROVIDER=openai\nSQLAGENT_MODEL=qwen-flash\n"
        "SQLAGENT_BASE_URL=https://example.invalid/v1\n"
        "SQLAGENT_API_KEY=" + "sk-" + "unit-test-value-" + "a7f3c9d2e1b4f5e6" + "\n",
        encoding="utf-8",
    )

    assert secrets.migrate_from_dotenv() == tmp_path / "secrets" / "sqlagent.qwen_flash.dpapi"

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SQLAGENT_MODEL=qwen-flash" in text, "the active model must survive sealing"
    assert "SQLAGENT_BASE_URL=https://example.invalid/v1" in text, "non-secret config must survive"
    assert not [l for l in text.splitlines() if l.startswith("SQLAGENT_API_KEY=")], \
        "the plaintext assignment must be gone (a comment may still name the key)"

    sealed = tmp_path / "secrets" / "sqlagent.qwen_flash.dpapi"
    assert KEY_SHAPE.search(secrets.unwrap(sealed.read_bytes()).decode()), "it must be the sealed key"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_no_test_writes_into_the_live_secrets_store():
    """The guard for the mistake this file already made once.

    An earlier version of the sealing test resolved the real slot path and deleted it
    afterwards, destroying a user credential while reporting a clean pass. Any test
    that can touch the live store must leave it byte-for-byte identical.
    """
    store = secrets.ROOT / "secrets"
    before = {p.name: p.read_bytes() for p in store.glob("*.dpapi")}

    # exercise the sealing path with the real ROOT, but a temp .env with no key
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        empty = Path(d) / ".env"
        empty.write_text("SQLAGENT_PROVIDER=openai\nSQLAGENT_MODEL=qwen-flash\n", encoding="utf-8")
        original = secrets.DOTENV_PATH
        secrets.DOTENV_PATH = empty
        try:
            assert secrets.migrate_from_dotenv() is None  # no key present, nothing to do
        finally:
            secrets.DOTENV_PATH = original

    after = {p.name: p.read_bytes() for p in store.glob("*.dpapi")}
    assert after == before, f"live credential store changed during tests: {set(before) ^ set(after)}"
