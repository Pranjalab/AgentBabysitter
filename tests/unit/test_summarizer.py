"""Phase 6 — summarizer: Claude API + fallbacks + char budgets.

All tests mock the Anthropic SDK; no real API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cldx.agent import Agent
from cldx.summarizer import MODE_INSTRUCTIONS, _fallback, _truncate, summarize


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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = Agent.default()
    result = await summarize("prompt_summary", "Claude wants to install axios", agent)
    assert "[unsummarized" in result
    assert "axios" in result


async def test_summarize_falls_back_on_api_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    agent = Agent.default()

    with patch("cldx.summarizer._summarize_with_anthropic",
               side_effect=RuntimeError("boom")):
        result = await summarize("prompt_summary", "ctx", agent)
    assert "[unsummarized" in result
    assert "boom" in result


async def test_summarize_falls_back_for_ollama_until_implemented(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")  # irrelevant for ollama
    cfg = tmp_path / "a.yml"
    cfg.write_text(
        "name: Aria\npersona: x\nmodel: ollama:llama3.1:8b\n"
    )
    agent = Agent.load(cfg)
    result = await summarize("prompt_summary", "ctx", agent)
    assert "[unsummarized" in result
    assert "ollama" in result.lower()


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
