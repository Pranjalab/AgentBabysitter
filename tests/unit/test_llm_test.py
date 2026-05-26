"""`cldx test llm` end-to-end smoke runner."""

from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console

from cldx.agent import Agent
from cldx.llm_test import SAMPLE_CONTEXTS, run_llm_test


@pytest.fixture
def cap_console():
    buf = StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=120), buf


async def test_llm_test_returns_zero_when_all_modes_succeed(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_summarize(mode, ctx, ag):
        return f"summary for {mode}"

    with patch("cldx.llm_test.summarize", side_effect=fake_summarize):
        rc = await run_llm_test(console=console, agent=agent)
    assert rc == 0
    out = buf.getvalue()
    assert "All 3 modes worked" in out or "All " in out
    for mode in SAMPLE_CONTEXTS:
        assert mode in out


async def test_llm_test_returns_one_when_fallback_detected(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_summarize(mode, ctx, ag):
        if mode == "prompt_summary":
            return "[unsummarized: no API key] truncated context..."
        return "real summary"

    with patch("cldx.llm_test.summarize", side_effect=fake_summarize):
        rc = await run_llm_test(console=console, agent=agent)
    assert rc == 1
    assert "fallback" in buf.getvalue().lower()


async def test_llm_test_returns_one_on_exception(cap_console):
    console, buf = cap_console
    agent = Agent.default()

    async def fake_summarize(mode, ctx, ag):
        raise RuntimeError("network down")

    with patch("cldx.llm_test.summarize", side_effect=fake_summarize):
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

    async def fake_summarize(mode, ctx, ag):
        return "ok"

    with patch("cldx.llm_test.summarize", side_effect=fake_summarize):
        await run_llm_test(console=console, agent=agent)
    out = buf.getvalue()
    assert "bedrock" in out
    assert "apac.anthropic.claude-haiku-4-5-20251001-v1:0" in out
    assert "ap-south-1" in out
