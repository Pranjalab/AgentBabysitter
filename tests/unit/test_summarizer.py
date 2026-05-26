"""Phase 6 — summarizer: dispatch across Anthropic / Bedrock / Gemini.

All tests mock the upstream SDKs; no real API calls.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cldx.agent import Agent
from cldx.summarizer import (
    MODE_INSTRUCTIONS,
    SummaryResult,
    _fallback,
    _truncate,
    summarize,
    summarize_with_status,
)


def _mock_anthropic_response(text: str):
    """Build a SDK-shaped response object."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


# --- truncation helper ----------------------------------------------------

def test_truncate_returns_short_inputs_unchanged():
    assert _truncate("hello", 100) == "hello"


def test_truncate_adds_ellipsis_when_over_limit():
    out = _truncate("hello world hello world", 10)
    assert len(out) <= 10
    assert out.endswith("…")


# --- summarize: fallback behaviors ---------------------------------------

async def test_summarize_falls_back_when_no_api_key(monkeypatch):
    """summarize() must return raw text (no marker) but flag fallback via status."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = Agent.default()
    status = await summarize_with_status(
        "prompt_summary", "Claude wants to install axios", agent,
    )
    assert status.summarized is False
    assert "API key" in status.fallback_reason
    assert "axios" in status.text
    assert "[unsummarized" not in status.text   # marker no longer leaks


async def test_summarize_returns_clean_text_on_failure(monkeypatch):
    """Plain summarize() returns just the raw text — no marker prefix."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = Agent.default()
    text = await summarize("prompt_summary", "ctx for telegram", agent)
    assert text == "ctx for telegram"
    assert "[unsummarized" not in text


async def test_summarize_falls_back_on_api_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    agent = Agent.default()

    with patch("cldx.summarizer._summarize_with_anthropic",
               side_effect=RuntimeError("boom")):
        status = await summarize_with_status("prompt_summary", "ctx", agent)
    assert status.summarized is False
    assert "boom" in status.fallback_reason
    assert status.text == "ctx"
    assert "[unsummarized" not in status.text


async def test_summarize_falls_back_for_ollama_until_implemented(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")  # irrelevant for ollama
    cfg = tmp_path / "a.yml"
    cfg.write_text(
        "name: Aria\npersona: x\nmodel: ollama:llama3.1:8b\n"
    )
    agent = Agent.load(cfg)
    status = await summarize_with_status("prompt_summary", "ctx", agent)
    assert status.summarized is False
    assert "ollama" in status.fallback_reason.lower()
    assert status.text == "ctx"


# --- summarize: real path (mocked SDK) ------------------------------------

async def test_summarize_calls_anthropic_with_persona_as_system(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    agent = Agent.default()

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_mock_anthropic_response("Claude wants ls — approve?")
    )

    with patch("anthropic.AsyncAnthropic", return_value=fake_client):
        result = await summarize("prompt_summary", "Claude wants to run ls", agent)

    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    # System should be a list of blocks (cache control)
    assert isinstance(call_kwargs["system"], list)
    assert any(agent.persona[:20] in b.get("text", "") for b in call_kwargs["system"])
    # Each system block should have cache_control set for prompt caching.
    assert all("cache_control" in b for b in call_kwargs["system"])
    # Model should match the agent's configured model.
    assert call_kwargs["model"] == agent.model
    assert result.startswith("Claude wants ls")


async def test_summarize_enforces_char_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    agent = Agent.default()
    long_summary = "x" * 1000

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_mock_anthropic_response(long_summary)
    )

    with patch("anthropic.AsyncAnthropic", return_value=fake_client):
        result = await summarize("prompt_summary", "ctx", agent)

    # prompt_summary limit is 200 chars per the default agent
    assert len(result) <= agent.limit_for("prompt_summary")


async def test_summarize_unknown_mode_raises():
    agent = Agent.default()
    with pytest.raises(ValueError):
        await summarize("not_a_mode", "ctx", agent)  # type: ignore[arg-type]


async def test_summarize_completion_uses_larger_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    agent = Agent.default()
    text = "y" * 1000

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_mock_anthropic_response(text)
    )

    with patch("anthropic.AsyncAnthropic", return_value=fake_client):
        result = await summarize("completion_summary", "ctx", agent)

    # completion_summary defaults to 500 chars
    assert len(result) <= agent.limit_for("completion_summary")
    assert len(result) > agent.limit_for("prompt_summary")  # bigger than prompt


def test_mode_instructions_cover_all_modes():
    """Every defined SummaryMode must have a corresponding instruction."""
    expected = {"prompt_summary", "escalation_summary", "completion_summary"}
    assert set(MODE_INSTRUCTIONS.keys()) == expected


# --- Bedrock backend -----------------------------------------------------


def _bedrock_agent(tmp_path):
    """Build an Agent pinned to a Bedrock model id."""
    import yaml
    cfg = tmp_path / "agent_name.yml"
    cfg.write_text(yaml.safe_dump({
        "name": "Aria",
        "persona": "be brief",
        "model": "bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "aws_region": "ap-south-1",
    }))
    return Agent.load(cfg)


async def test_summarize_dispatches_to_bedrock(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-api-key-test")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    agent = _bedrock_agent(tmp_path)

    # Build a fake boto3 module the summarizer can import.
    fake_body = MagicMock()
    fake_body.read.return_value = json.dumps({
        "content": [{"type": "text", "text": "ok via bedrock"}],
    }).encode()
    fake_client = MagicMock()
    fake_client.invoke_model.return_value = {"body": fake_body}
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    result = await summarize("prompt_summary", "claude wants to ls", agent)
    assert "ok via bedrock" in result
    fake_boto3.client.assert_called_with("bedrock-runtime", region_name="ap-south-1")
    # Verify the body had the right shape.
    kw = fake_client.invoke_model.call_args.kwargs
    assert kw["modelId"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    body = json.loads(kw["body"])
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["messages"][0]["content"] == "claude wants to ls"
    assert "Mode: prompt_summary" in body["system"]


async def test_summarize_bedrock_falls_back_when_no_creds(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    agent = _bedrock_agent(tmp_path)

    status = await summarize_with_status("prompt_summary", "ctx", agent)
    assert status.summarized is False
    assert "AWS" in status.fallback_reason or "credentials" in status.fallback_reason.lower()
    assert status.text == "ctx"
    assert "[unsummarized" not in status.text


async def test_summarize_bedrock_falls_back_when_boto3_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bedrock-api-key-test")
    # Force the boto3 import to fail by stubbing it as None.
    monkeypatch.setitem(sys.modules, "boto3", None)
    agent = _bedrock_agent(tmp_path)

    status = await summarize_with_status("prompt_summary", "ctx", agent)
    assert status.summarized is False
    assert "boto3" in status.fallback_reason
    assert status.text == "ctx"
    assert "[unsummarized" not in status.text


# --- Gemini backend ------------------------------------------------------


def _gemini_agent(tmp_path):
    import yaml
    cfg = tmp_path / "agent_name.yml"
    cfg.write_text(yaml.safe_dump({
        "name": "Aria",
        "persona": "be brief",
        "model": "gemini:gemini-2.0-flash",
    }))
    return Agent.load(cfg)


async def test_summarize_dispatches_to_gemini(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    agent = _gemini_agent(tmp_path)

    fake_response = MagicMock()
    fake_response.text = "ok via gemini"
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client
    fake_types = MagicMock()
    fake_types.GenerateContentConfig = MagicMock()

    # Build a fake google module tree so `from google import genai` works.
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)

    result = await summarize("prompt_summary", "claude wants to ls", agent)
    assert "ok via gemini" in result
    fake_genai.Client.assert_called_with(api_key="fake-gemini-key")
    fake_client.aio.models.generate_content.assert_awaited_once()
    kw = fake_client.aio.models.generate_content.await_args.kwargs
    assert kw["model"] == "gemini-2.0-flash"
    assert kw["contents"] == "claude wants to ls"


async def test_summarize_gemini_falls_back_when_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    agent = _gemini_agent(tmp_path)

    status = await summarize_with_status("prompt_summary", "ctx", agent)
    assert status.summarized is False
    assert "Gemini" in status.fallback_reason or "GEMINI" in status.fallback_reason
    assert status.text == "ctx"
    assert "[unsummarized" not in status.text


async def test_summarize_gemini_accepts_google_api_key_var(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-var")
    agent = _gemini_agent(tmp_path)

    fake_response = MagicMock()
    fake_response.text = "ok"
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = AsyncMock(return_value=fake_response)
    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", MagicMock())

    result = await summarize("prompt_summary", "ctx", agent)
    assert "ok" in result
    fake_genai.Client.assert_called_with(api_key="from-google-var")


# --- Agent.backend property ----------------------------------------------


def test_agent_backend_for_anthropic_default():
    assert Agent().backend == "anthropic"


def test_agent_backend_for_bedrock_prefix(tmp_path):
    agent = _bedrock_agent(tmp_path)
    assert agent.backend == "bedrock"
    assert agent.bare_model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_agent_backend_for_gemini_prefix(tmp_path):
    agent = _gemini_agent(tmp_path)
    assert agent.backend == "gemini"
    assert agent.bare_model_id == "gemini-2.0-flash"


def test_agent_backend_for_ollama_prefix(tmp_path):
    import yaml
    cfg = tmp_path / "a.yml"
    cfg.write_text(yaml.safe_dump({"name": "x", "model": "ollama:llama3.1:8b"}))
    agent = Agent.load(cfg)
    assert agent.backend == "ollama"


def test_agent_validates_prefixed_model_must_have_id():
    """A prefix with empty body must raise."""
    from cldx.agent import AgentError
    import yaml
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
        yaml.safe_dump({"name": "x", "model": "bedrock:"}, f)
        path = f.name
    with pytest.raises(AgentError):
        Agent.load(path)
