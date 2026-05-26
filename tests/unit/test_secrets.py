"""Secrets loader: env file parsing, atomic write, masking."""

from __future__ import annotations

import os
import stat

import pytest

from cldx.secrets import (
    _parse_env_file,
    clear_secret,
    env_file_path,
    have_anthropic_key,
    have_telegram_config,
    load_into_environ,
    mask_secret,
    save_secret,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    # Wipe any secret env vars the test shell already had so we test
    # cldx's behavior, not the parent environment.
    for k in (
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


# --- env file parsing ----------------------------------------------------

def test_parse_env_file_handles_comments_blanks_quotes(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        "# a comment\n"
        "\n"
        "FOO=bar\n"
        '   BAZ="quoted value"  \n'
        "QUUX='single-quoted'\n"
        "BARE=42\n"
        "  \n"
    )
    result = _parse_env_file(p)
    assert result == {
        "FOO": "bar",
        "BAZ": "quoted value",
        "QUUX": "single-quoted",
        "BARE": "42",
    }


def test_parse_env_file_returns_empty_when_missing(tmp_path):
    assert _parse_env_file(tmp_path / "does-not-exist.env") == {}


# --- save_secret ---------------------------------------------------------

def test_save_secret_creates_file_and_writes_key(isolated_home):
    path = save_secret("anthropic", "ANTHROPIC_API_KEY", "sk-ant-test")
    assert path.exists()
    parsed = _parse_env_file(path)
    assert parsed == {"ANTHROPIC_API_KEY": "sk-ant-test"}


def test_save_secret_preserves_existing_keys(isolated_home):
    save_secret("telegram", "TELEGRAM_BOT_TOKEN", "tkn-1")
    save_secret("telegram", "TELEGRAM_CHAT_ID", "12345")
    parsed = _parse_env_file(env_file_path("telegram"))
    assert parsed == {"TELEGRAM_BOT_TOKEN": "tkn-1", "TELEGRAM_CHAT_ID": "12345"}


def test_save_secret_updates_existing_key(isolated_home):
    save_secret("anthropic", "ANTHROPIC_API_KEY", "old")
    save_secret("anthropic", "ANTHROPIC_API_KEY", "new")
    parsed = _parse_env_file(env_file_path("anthropic"))
    assert parsed["ANTHROPIC_API_KEY"] == "new"


def test_save_secret_quotes_values_with_spaces(isolated_home):
    save_secret("custom", "MSG", "hello world with #hash")
    raw = env_file_path("custom").read_text()
    assert 'MSG="hello world' in raw


def test_save_secret_sets_user_only_permissions(isolated_home):
    """Best-effort: secret files should be mode 600 on POSIX."""
    path = save_secret("anthropic", "ANTHROPIC_API_KEY", "x")
    mode = stat.S_IMODE(path.stat().st_mode)
    # Other users must not be able to read.
    assert mode & 0o077 == 0, f"mode {oct(mode)} leaks to group/other"


# --- clear_secret --------------------------------------------------------

def test_clear_secret_removes_key(isolated_home):
    save_secret("telegram", "TELEGRAM_BOT_TOKEN", "x")
    save_secret("telegram", "TELEGRAM_CHAT_ID", "y")
    removed = clear_secret("telegram", "TELEGRAM_BOT_TOKEN")
    assert removed is True
    assert _parse_env_file(env_file_path("telegram")) == {"TELEGRAM_CHAT_ID": "y"}


def test_clear_secret_deletes_file_when_last_key_removed(isolated_home):
    save_secret("anthropic", "ANTHROPIC_API_KEY", "x")
    clear_secret("anthropic", "ANTHROPIC_API_KEY")
    assert not env_file_path("anthropic").exists()


def test_clear_secret_returns_false_when_missing(isolated_home):
    assert clear_secret("anthropic", "MISSING") is False


# --- load_into_environ ---------------------------------------------------

def test_load_into_environ_populates_os_environ(isolated_home, monkeypatch):
    save_secret("anthropic", "ANTHROPIC_API_KEY", "sk-ant-loaded")
    save_secret("telegram", "TELEGRAM_BOT_TOKEN", "tkn")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    loaded = load_into_environ()
    assert loaded["ANTHROPIC_API_KEY"] == "sk-ant-loaded"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-loaded"
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "tkn"


def test_load_into_environ_does_not_overwrite_by_default(isolated_home, monkeypatch):
    save_secret("anthropic", "ANTHROPIC_API_KEY", "from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    load_into_environ()
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_load_into_environ_can_overwrite(isolated_home, monkeypatch):
    save_secret("anthropic", "ANTHROPIC_API_KEY", "from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    load_into_environ(overwrite=True)
    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"


def test_load_into_environ_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLDX_HOME", str(tmp_path / "does-not-exist"))
    assert load_into_environ() == {}


# --- masking + presence helpers ------------------------------------------

def test_mask_secret_masks_long_strings():
    masked = mask_secret("sk-ant-abcdef1234567890")
    assert masked.startswith("sk-a")
    assert masked.endswith("7890")
    assert "…" in masked
    assert "abcd" not in masked


def test_mask_secret_handles_short_and_empty():
    assert "not set" in mask_secret(None)
    assert "not set" in mask_secret("")
    assert mask_secret("short") == "***"


def test_have_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert have_anthropic_key() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert have_anthropic_key() is True


def test_have_telegram_config_requires_both(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert have_telegram_config() is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    assert have_telegram_config() is False  # still missing chat_id
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    assert have_telegram_config() is True
