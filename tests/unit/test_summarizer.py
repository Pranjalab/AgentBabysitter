"""Phase 6 — summarizer.py: Claude API summaries with agent persona.

Uses prompt caching for the persona system prompt + static instructions.
Three modes: prompt_summary, escalation_summary, completion_summary.
Each is hard-capped at the agent's configured char budget.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6


@pytest.mark.skip(reason="Phase 6 — summarizer not implemented yet")
async def test_summarize_prompt_under_200_chars():
    """prompt_summary output must respect the 200-char budget."""


@pytest.mark.skip(reason="Phase 6")
async def test_summarize_completion_under_500_chars():
    """completion_summary output must respect the 500-char budget."""


@pytest.mark.skip(reason="Phase 6")
async def test_summarize_uses_agent_persona_as_system_prompt():
    """The agent's persona string must be passed as the system message."""


@pytest.mark.skip(reason="Phase 6")
async def test_summarize_uses_prompt_caching():
    """The persona + static instructions block should be cache_control'd."""


@pytest.mark.skip(reason="Phase 6")
async def test_summarize_falls_back_naively_on_api_failure():
    """If the Anthropic API call fails, return a truncated raw context tagged `[unsummarized]`."""


@pytest.mark.skip(reason="Phase 6")
async def test_summarize_respects_model_selection():
    """Agent.model='ollama:llama3.1:8b' routes to local backend, not Anthropic."""
