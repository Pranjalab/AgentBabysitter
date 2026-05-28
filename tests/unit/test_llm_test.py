"""`abs test llm` end-to-end smoke runner."""

from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console

from abs.agent import Agent
from abs.llm_test import SAMPLE_CONTEXTS, run_llm_test
from abs.summarizer import SummaryResult


@pytest.fixture
def cap_console():
    buf = StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=120), buf


async def test_llm_test_returns_zero_when_all_modes_succeed(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_status(mode, ctx, ag):
        return SummaryResult(text=f"summary for {mode}", summarized=True)

    with patch("abs.llm_test.summarize_with_status", side_effect=fake_status):
        rc = await run_llm_test(console=console, agent=agent)
    assert rc == 0
    out = buf.getvalue()
    assert "All " in out and "worked" in out
    for mode in SAMPLE_CONTEXTS:
        assert mode in out


async def test_llm_test_returns_one_when_fallback_detected(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_status(mode, ctx, ag):
        if mode == "prompt_summary":
            return SummaryResult(
                text="raw pane context (truncated)",
                summarized=False,
                fallback_reason="no API key",
            )
        return SummaryResult(text="real summary", summarized=True)

    with patch("abs.llm_test.summarize_with_status", side_effect=fake_status):
        rc = await run_llm_test(console=console, agent=agent)
    assert rc == 1
    out = buf.getvalue()
    assert "fallback" in out.lower()
    assert "no API key" in out
    # The fallback text shown must NOT have the [unsummarized] marker.
    assert "[unsummarized" not in out


async def test_llm_test_returns_one_on_exception(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_status(mode, ctx, ag):
        raise RuntimeError("network down")

    with patch("abs.llm_test.summarize_with_status", side_effect=fake_status):
        rc = await run_llm_test(console=console, agent=agent)
    assert rc == 1
    assert "network down" in buf.getvalue()


async def test_llm_test_header_shows_backend_and_model(cap_console):
    console, buf = cap_console
    import yaml
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
        yaml.safe_dump({
            "name": "Aria",
            "persona": "x",
            "model": "bedrock:apac.anthropic.claude-haiku-4-5-20251001-v1:0",
            "aws_region": "ap-south-1",
        }, f)
        path = f.name
    agent = Agent.load(path)

    async def fake_status(mode, ctx, ag):
        return SummaryResult(text="ok", summarized=True)

    with patch("abs.llm_test.summarize_with_status", side_effect=fake_status):
        await run_llm_test(console=console, agent=agent)
    out = buf.getvalue()
    assert "bedrock" in out
    assert "apac.anthropic.claude-haiku-4-5-20251001-v1:0" in out
    assert "ap-south-1" in out
