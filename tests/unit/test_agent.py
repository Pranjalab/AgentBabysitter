"""Phase 6 — agent.py: agent_name.yml loader + persona definition."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6


@pytest.mark.skip(reason="Phase 6 — agent loader not implemented yet")
def test_agent_loads_name_and_persona(tmp_path):
    """agent.load() reads name, persona, model, api_key_env, limits."""


@pytest.mark.skip(reason="Phase 6")
def test_agent_defaults_when_file_missing(tmp_path):
    """Missing agent_name.yml falls back to a built-in default Agent."""


@pytest.mark.skip(reason="Phase 6")
def test_agent_validates_model_field():
    """Unknown model string raises a clear error."""


@pytest.mark.skip(reason="Phase 6")
def test_agent_limit_keys_have_defaults():
    """If `limits.prompt_summary` is missing, default to 200."""
