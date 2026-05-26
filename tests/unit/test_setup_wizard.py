"""Setup wizards: Anthropic + Telegram interactive flows with mocked HTTP."""

from __future__ import annotations

import os
from io import StringIO

import pytest
from rich.console import Console

from cldx.secrets import env_file_path
from cldx.setup_wizard import (
    run_anthropic_setup,
    run_full_setup,
    run_telegram_setup,
    show_config,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


@pytest.fixture
def cap_console():
    buf = StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=120), buf


def _scripted(answers: list[str]):
    """Return an input_fn that yields successive answers from `answers`."""
    it = iter(answers)
    return lambda _prompt: next(it)


# --- Anthropic wizard ----------------------------------------------------

def test_anthropic_wizard_accepts_valid_key(isolated_home, cap_console):
    console, _ = cap_console
    saved_key = "sk-ant-test123456789012345"
    ok = run_anthropic_setup(
        console=console,
        input_fn=_scripted([saved_key, "n"]),  # paste key, decline API test
        test_key_fn=lambda _k: (True, "skipped"),
    )
    assert ok is True
    assert os.environ["ANTHROPIC_API_KEY"] == saved_key
    assert env_file_path("anthropic").exists()


def test_anthropic_wizard_rejects_bad_key_format(isolated_home, cap_console):
    console, buf = cap_console
    ok = run_anthropic_setup(
        console=console,
        input_fn=_scripted(["this is not a key", "sk-ant-validkey1234567890", "n"]),
        test_key_fn=lambda _k: (True, "ok"),
    )
    assert ok is True
    assert "doesn't look like" in buf.getvalue().lower()


def test_anthropic_wizard_skipped_with_empty_input(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_anthropic_setup(
        console=console,
        input_fn=_scripted([""]),
        test_key_fn=lambda _k: (True, "ok"),
    )
    assert ok is False
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_anthropic_wizard_keeps_existing_when_user_declines_replace(
    isolated_home, cap_console, monkeypatch
):
    console, _ = cap_console
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-existing000000000")
    ok = run_anthropic_setup(
        console=console,
        input_fn=_scripted(["n"]),  # decline to replace
        test_key_fn=lambda _k: (True, "ok"),
    )
    assert ok is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-existing000000000"


def test_anthropic_wizard_runs_test_when_user_accepts(isolated_home, cap_console):
    console, buf = cap_console
    called = []
    ok = run_anthropic_setup(
        console=console,
        input_fn=_scripted(["sk-ant-test123456789012", "y"]),
        test_key_fn=lambda k: (called.append(k) or True, "looks good")[1:] and (True, "looks good"),
    )
    assert ok is True
    assert called == ["sk-ant-test123456789012"]


# --- Telegram wizard -----------------------------------------------------

def _fake_http_for(responses: dict[str, dict]):
    """Build an http_fn that returns canned responses based on URL substring."""
    def http_fn(url, _data=None, _timeout=10):
        for key, value in responses.items():
            if key in url:
                return value
        raise ValueError(f"unexpected URL in test: {url}")
    return http_fn


def test_telegram_wizard_happy_path(isolated_home, cap_console):
    console, _ = cap_console
    http = _fake_http_for({
        "getMe": {"ok": True, "result": {"username": "test_bot", "id": 1}},
        "getUpdates": {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 9876}, "text": "hi"}},
        ]},
        "sendMessage": {"ok": True, "result": {"message_id": 1}},
    })
    ok = run_telegram_setup(
        console=console,
        input_fn=_scripted([
            "12345678:AAEgFakeTokenForTests",  # bot token
            "",                                # ack press-enter for chat discovery
            "y",                               # send test message
        ]),
        http_fn=http,
    )
    assert ok is True
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "12345678:AAEgFakeTokenForTests"
    assert os.environ["TELEGRAM_CHAT_ID"] == "9876"


def test_telegram_wizard_rejects_invalid_token_format(isolated_home, cap_console):
    console, _ = cap_console
    http = _fake_http_for({
        "getMe": {"ok": True, "result": {"username": "ok_bot"}},
        "getUpdates": {"ok": True, "result": [
            {"message": {"chat": {"id": 100}, "text": "hi"}},
        ]},
        "sendMessage": {"ok": True, "result": {}},
    })
    ok = run_telegram_setup(
        console=console,
        input_fn=_scripted([
            "bad",                              # too short → retry
            "12345678:AAEgFakeTokenForTests",
            "",
            "n",                                # don't send test message
        ]),
        http_fn=http,
    )
    assert ok is True


def test_telegram_wizard_fails_when_getMe_returns_not_ok(isolated_home, cap_console):
    console, _ = cap_console
    http = _fake_http_for({
        "getMe": {"ok": False, "description": "Unauthorized"},
    })
    ok = run_telegram_setup(
        console=console,
        input_fn=_scripted(["12345678:invalidtoken1234567"]),
        http_fn=http,
    )
    assert ok is False


def test_telegram_wizard_prompts_manually_when_chat_id_not_discovered(
    isolated_home, cap_console
):
    console, _ = cap_console
    http = _fake_http_for({
        "getMe": {"ok": True, "result": {"username": "ok_bot"}},
        "getUpdates": {"ok": True, "result": []},  # no recent messages
        "sendMessage": {"ok": True, "result": {}},
    })
    ok = run_telegram_setup(
        console=console,
        input_fn=_scripted([
            "12345678:AAEgFakeTokenForTests",
            "",            # press-enter
            "not numeric", # retry chat id
            "42",          # valid chat id
            "n",           # decline test message
        ]),
        http_fn=http,
    )
    assert ok is True
    assert os.environ["TELEGRAM_CHAT_ID"] == "42"


def test_telegram_wizard_aborts_when_chat_id_empty_and_no_updates(
    isolated_home, cap_console
):
    console, _ = cap_console
    http = _fake_http_for({
        "getMe": {"ok": True, "result": {"username": "ok_bot"}},
        "getUpdates": {"ok": True, "result": []},
    })
    ok = run_telegram_setup(
        console=console,
        input_fn=_scripted([
            "12345678:AAEgFakeTokenForTests",
            "",
            "",  # empty chat id → abort
        ]),
        http_fn=http,
    )
    assert ok is False
    # Token should still have been saved so the user doesn't lose it.
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "12345678:AAEgFakeTokenForTests"


# --- show_config ---------------------------------------------------------

def test_show_config_masks_secrets(isolated_home, monkeypatch, cap_console):
    console, buf = cap_console
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecretvalue000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345678:AAEgSecretBotToken")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234567890")

    show_config(console=console)
    out = buf.getvalue()
    # Sensitive values masked
    assert "supersecret" not in out
    assert "SecretBot" not in out
    # But chat_id is fine to show in full
    assert "1234567890" in out
    # Headers / labels appear
    assert "ANTHROPIC_API_KEY" in out
    assert "TELEGRAM_BOT_TOKEN" in out


def test_show_config_indicates_unset_secrets(isolated_home, cap_console):
    console, buf = cap_console
    show_config(console=console)
    out = buf.getvalue()
    # Three "not set" markers, one per secret
    assert out.count("not set") >= 3
