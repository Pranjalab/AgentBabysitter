"""Setup wizards: Anthropic + Telegram interactive flows with mocked HTTP."""

from __future__ import annotations

import os
from io import StringIO

import pytest
from rich.console import Console

from cldx.secrets import env_file_path
from cldx.setup_wizard import (
    run_anthropic_setup,
    run_bedrock_setup,
    run_full_setup,
    run_gemini_setup,
    run_llm_setup,
    run_telegram_setup,
    show_config,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    for k in (
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
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
    # At least three "not set" markers — Anthropic / Telegram bot / chat
    assert out.count("not set") >= 3


# --- Bedrock wizard -------------------------------------------------------

def test_bedrock_wizard_happy_path(isolated_home, cap_console):
    console, _ = cap_console
    saved_token = "bedrock-api-key-" + "x" * 64
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([
            saved_token,           # bearer token
            "ap-south-1",          # region
            "",                    # use default model id
            "n",                   # skip test call
            "n",                   # don't update agent_name.yml
        ]),
    )
    assert ok is True
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == saved_token
    assert os.environ["AWS_REGION"] == "ap-south-1"
    # File should be on disk too.
    from cldx.secrets import env_file_path
    assert env_file_path("bedrock").exists()


def test_bedrock_wizard_rewrites_agent_yml_on_confirm(isolated_home, cap_console):
    console, _ = cap_console
    custom_model = "apac.anthropic.claude-haiku-4-5-20251001-v1:0"
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([
            "bedrock-api-key-" + "y" * 50,
            "ap-south-1",
            custom_model,
            "n",                  # skip test
            "y",                  # YES update agent_name.yml
        ]),
    )
    assert ok is True
    # agent_name.yml should now reflect the new model.
    import yaml
    agent_path = isolated_home / "config" / "agent_name.yml"
    data = yaml.safe_load(agent_path.read_text())
    assert data["model"] == f"bedrock:{custom_model}"
    assert data["aws_region"] == "ap-south-1"


def test_bedrock_wizard_skipped_with_empty_token(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([""]),
    )
    assert ok is False
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


def test_bedrock_wizard_rejects_obviously_short_token(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([
            "short",                                       # too short, retry
            "bedrock-api-key-" + "z" * 64,                 # accept
            "us-east-1",
            "",
            "n",
            "n",
        ]),
    )
    assert ok is True


def test_bedrock_wizard_runs_test_call_when_user_accepts(isolated_home, cap_console):
    console, _ = cap_console
    calls = []
    fake_test = lambda token, region, model_id: (
        calls.append((token, region, model_id)) or (True, f"got {model_id}")
    )[1:] and (True, f"got {model_id}")
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([
            "bedrock-api-key-" + "k" * 50,
            "us-east-1",
            "",                  # default model
            "y",                 # YES run test
            "n",                 # don't update agent yml
        ]),
        test_fn=fake_test,
    )
    assert ok is True
    assert calls and "claude-haiku" in calls[0][2]


# --- Gemini wizard --------------------------------------------------------

def test_gemini_wizard_happy_path(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_gemini_setup(
        console=console,
        input_fn=_scripted([
            "AIzaSyDummyTestKey1234567890",     # API key
            "",                                  # use default model
            "n",                                 # skip test
            "n",                                 # don't update agent_name.yml
        ]),
    )
    assert ok is True
    assert os.environ["GEMINI_API_KEY"] == "AIzaSyDummyTestKey1234567890"


def test_gemini_wizard_rewrites_agent_yml_on_confirm(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_gemini_setup(
        console=console,
        input_fn=_scripted([
            "AIzaSyDummyTestKey1234567890",
            "gemini-1.5-flash",
            "n",
            "y",                                # update agent_name.yml
        ]),
    )
    assert ok is True
    import yaml
    data = yaml.safe_load((isolated_home / "config" / "agent_name.yml").read_text())
    assert data["model"] == "gemini:gemini-1.5-flash"


def test_gemini_wizard_rejects_short_key(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_gemini_setup(
        console=console,
        input_fn=_scripted([
            "short",                            # retry
            "AIzaSyDummyTestKey1234567890",
            "",
            "n",
            "n",
        ]),
    )
    assert ok is True


def test_gemini_wizard_skipped_with_empty_key(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_gemini_setup(console=console, input_fn=_scripted([""]))
    assert ok is False


# --- LLM picker ----------------------------------------------------------

def test_llm_picker_routes_to_anthropic(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_llm_setup(
        console=console,
        input_fn=_scripted([
            "1",                                # pick anthropic
            "sk-ant-validkey1234567890",
            "n",                                # skip API test
        ]),
    )
    assert ok is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-validkey1234567890"


def test_llm_picker_routes_to_bedrock(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_llm_setup(
        console=console,
        input_fn=_scripted([
            "2",                                # pick bedrock
            "bedrock-api-key-" + "a" * 50,
            "ap-south-1",
            "",                                 # default model
            "n",                                # skip test
            "n",                                # don't update agent yml
        ]),
    )
    assert ok is True
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"].startswith("bedrock-api-key-")


def test_llm_picker_routes_to_gemini(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_llm_setup(
        console=console,
        input_fn=_scripted([
            "3",                                # pick gemini
            "AIzaSyDummyTestKey1234567890",
            "",                                 # default model
            "n",                                # skip test
            "n",                                # don't update agent yml
        ]),
    )
    assert ok is True


def test_llm_picker_skip(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_llm_setup(console=console, input_fn=_scripted(["4"]))
    assert ok is False


def test_llm_picker_rejects_invalid_choice(isolated_home, cap_console):
    console, _ = cap_console
    ok = run_llm_setup(
        console=console,
        input_fn=_scripted(["wat", "9", "4"]),  # garbage → 9 → finally skip
    )
    assert ok is False


# --- Paste-tolerant input -------------------------------------------------

def test_bedrock_wizard_accepts_multi_kilobyte_token(isolated_home, cap_console):
    """The token field must accept ~3KB pastes without truncation.

    macOS canonical mode caps `input()` at 1024 bytes; the wizard's default
    input function uses prompt_toolkit to dodge that. Test code injects its
    own input_fn so this test just verifies the value round-trips through
    save_secret + load.
    """
    console, _ = cap_console
    long_token = "bedrock-api-key-" + ("A" * 3072)  # well above the 1KB cap
    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([long_token, "us-east-1", "", "n", "n"]),
    )
    assert ok is True
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == long_token
    # Verify the file on disk also holds the full value.
    from cldx.secrets import _parse_env_file, env_file_path
    saved = _parse_env_file(env_file_path("bedrock"))
    assert saved["AWS_BEARER_TOKEN_BEDROCK"] == long_token


def test_bedrock_default_model_is_region_aware():
    """Cross-region inference profile prefix must match the AWS region."""
    from cldx.setup_wizard import _bedrock_default_model_for_region

    assert _bedrock_default_model_for_region("us-east-1").startswith("us.")
    assert _bedrock_default_model_for_region("us-west-2").startswith("us.")
    assert _bedrock_default_model_for_region("eu-west-1").startswith("eu.")
    assert _bedrock_default_model_for_region("eu-central-1").startswith("eu.")
    assert _bedrock_default_model_for_region("ap-south-1").startswith("apac.")
    assert _bedrock_default_model_for_region("ap-northeast-1").startswith("apac.")
    # Unknown region falls back to US.
    assert _bedrock_default_model_for_region("antarctica-1").startswith("us.")


def test_bedrock_wizard_uses_apac_prefix_in_ap_south_1(isolated_home, cap_console):
    """Pasting ap-south-1 as the region must surface an apac.* default."""
    console, _ = cap_console
    seen_default: list[str] = []

    def script(prompt):
        if "model" in prompt.lower():
            # The default appears in the bracketed default in the prompt text.
            seen_default.append(prompt)
        return {
            0: "bedrock-api-key-" + "x" * 64,
            1: "ap-south-1",
            2: "",         # accept default model
            3: "n",        # skip test
            4: "n",        # don't update agent
        }.get(script.idx, "")

    script.idx = -1

    def wrap(prompt):
        script.idx += 1
        return script(prompt)

    run_bedrock_setup(console=console, input_fn=wrap)
    assert seen_default, "model-id prompt should have been shown"
    assert "apac." in seen_default[0]


def test_bedrock_test_failure_lists_available_when_validation_error(
    isolated_home, cap_console, monkeypatch
):
    """On a ValidationException, the wizard should query Bedrock for
    available models and offer the user a numbered pick list."""
    console, buf = cap_console

    available = [
        "apac.anthropic.claude-haiku-4-5-20251001-v1:0",
        "apac.anthropic.claude-sonnet-4-6-20251001-v1:0",
    ]
    monkeypatch.setattr(
        "cldx.setup_wizard._bedrock_list_available_models",
        lambda region, max_results=8: available,
    )

    # First test call fails with a validation error, second succeeds.
    calls: list[tuple[str, str, str]] = []

    def fake_test(token, region, model_id):
        calls.append((token, region, model_id))
        if len(calls) == 1:
            return False, (
                "An error occurred (ValidationException) when calling "
                "the InvokeModel operation: The provided model identifier "
                "is invalid."
            )
        return True, f"got {model_id}"

    ok = run_bedrock_setup(
        console=console,
        input_fn=_scripted([
            "bedrock-api-key-" + "y" * 64,    # token
            "ap-south-1",                      # region
            "us.anthropic.bad-id-v1:0",        # wrong model id (user typed)
            "y",                                # run test
            "1",                                # pick alternative #1 from list
            "n",                                # don't update agent yml
        ]),
        test_fn=fake_test,
    )
    assert ok is True
    # Two test attempts: first the bad ID, then the picked alternative.
    assert len(calls) == 2
    assert calls[1][2] == available[0]
    # The pick list must have been printed.
    out = buf.getvalue()
    assert "Models available in ap-south-1" in out
    assert available[0] in out


def test_paste_friendly_input_falls_back_when_stdin_not_a_tty(monkeypatch, isolated_home):
    """Pipe-mode (no TTY) must fall through to plain input()."""
    from io import StringIO
    from cldx.setup_wizard import paste_friendly_input
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys, "stdin", StringIO("piped value\n"))
    monkeypatch.setattr("builtins.input", lambda _prompt: "piped value")

    assert paste_friendly_input("? ") == "piped value"
