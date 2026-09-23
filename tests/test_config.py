"""Cache-key tests.

The result cache is what makes ablation cheap, and a cache that returns a stale
verdict is worse than no cache: the number looks plausible. These pin the three
properties the key has to have.
"""

from __future__ import annotations

from pathlib import Path

from sqlagent.config import Settings, code_hash



def test_changing_the_model_changes_the_key():
    assert Settings(model="deepseek-chat").config_hash() != Settings(model="qwen-plus").config_hash()


def test_changing_the_dataset_changes_the_key():
    a = Settings(dataset_hash="aaaaaaaa")
    b = Settings(dataset_hash="bbbbbbbb")
    assert a.config_hash() != b.config_hash(), "task ids are stable across rebuilds"


def test_code_digest_ignores_line_endings():
    """A clone must not orphan the cache over CRLF versus LF."""
    here = Path(__file__).resolve().parent.parent / "sqlagent"
    before = code_hash()
    touched = here / "prompts.py"
    original = touched.read_bytes()
    touched.write_bytes(original.replace(b"\n", b"\r\n"))
    try:
        assert code_hash() == before, "digest is sensitive to newline style"
    finally:
        touched.write_bytes(original)
    assert code_hash() == before


def test_every_listed_source_file_actually_exists():
    from sqlagent.config import CODE_FILES

    here = Path(__file__).resolve().parent.parent / "sqlagent"
    missing = [f for f in CODE_FILES if not (here / f).exists()]
    assert not missing, f"CODE_FILES names files that are gone: {missing}"


def test_settings_fingerprint_is_stable_within_a_process():
    assert Settings().config_hash() == Settings().config_hash()


def test_the_requested_model_decides_the_endpoint(monkeypatch):
    """`--model X` must not send X to whatever endpoint `.env` last pointed at.

    A restability run tagged `deepseek-chat` reached DashScope's URL because
    SQLAGENT_BASE_URL outranked the model name, so all 80 tasks got 404. The runner
    withheld the score, which is the only reason this became a test instead of a
    published number that was never produced by the model it named.
    """
    from sqlagent.config import MODEL_BASE_URLS, settings_from_env

    monkeypatch.setenv("SQLAGENT_PROVIDER", "openai")
    monkeypatch.setenv("SQLAGENT_MODEL", "qwen-flash")
    monkeypatch.setenv("SQLAGENT_BASE_URL", "https://example.invalid/v1")
    for model, url in MODEL_BASE_URLS.items():
        s = settings_from_env(model=model)
        assert s.base_url == url, f"{model} resolved against the .env endpoint"
        assert s.model == model

    # a model this table does not know keeps the explicit endpoint - the table must
    # not become a second place that has to be edited before a new provider works.
    assert settings_from_env(model="not-in-the-table").base_url == "https://example.invalid/v1"
