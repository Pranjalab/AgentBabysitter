"""Phase 6 — agent loader (agent_name.yml → Agent dataclass)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from abs.agent import Agent, AgentError, DEFAULT_LIMITS


def test_agent_default_factory():
    a = Agent.default()
    assert a.name == "Sentinel"
    assert a.model == "claude-haiku-4-5"
    assert a.api_key_env == "ANTHROPIC_API_KEY"
    assert a.limits == DEFAULT_LIMITS


def test_agent_loads_from_yaml(tmp_path):
    cfg = tmp_path / "agent_name.yml"
    cfg.write_text(yaml.safe_dump({
        "name": "Aria",
        "persona": "be brief",
        "model": "claude-sonnet-4-6",
        "api_key_env": "MY_KEY",
        "limits": {"prompt_summary": 150},
    }))
    a = Agent.load(cfg)
    assert a.name == "Aria"
    assert a.persona == "be brief"
    assert a.model == "claude-sonnet-4-6"
    assert a.api_key_env == "MY_KEY"
    assert a.limits["prompt_summary"] == 150
    # Missing limits keys fall back to defaults
    assert a.limits["escalation_summary"] == DEFAULT_LIMITS["escalation_summary"]


def test_agent_load_falls_back_to_bundled_default_when_missing(monkeypatch, tmp_path):
    """If no user agent_name.yml exists, load() returns the bundled default."""
    monkeypatch.setenv("ABS_HOME", str(tmp_path))
    a = Agent.load()
    # Bundled default has name=Sentinel.
    assert a.name == "Sentinel"


def test_agent_load_uses_user_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ABS_HOME", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_name.yml").write_text(yaml.safe_dump({
        "name": "Aria",
        "persona": "be quick",
        "model": "claude-haiku-4-5",
    }))
    a = Agent.load()
    assert a.name == "Aria"


def test_agent_rejects_unknown_model(tmp_path):
    cfg = tmp_path / "agent_name.yml"
    cfg.write_text(yaml.safe_dump({"name": "X", "model": "gpt-4"}))
    with pytest.raises(AgentError):
        Agent.load(cfg)


def test_agent_accepts_ollama_prefixed_model(tmp_path):
    cfg = tmp_path / "agent_name.yml"
    cfg.write_text(yaml.safe_dump({
        "name": "Aria",
        "persona": "x",
        "model": "ollama:llama3.1:8b",
    }))
    a = Agent.load(cfg)
    assert a.model == "ollama:llama3.1:8b"


def test_agent_limit_for_falls_back():
    a = Agent.default()
    assert a.limit_for("prompt_summary") == 200
    # Unknown mode -> fallback to 500 (DEFAULT_LIMITS escalation/completion default).
    assert a.limit_for("unknown_mode_xyz") == 500


def test_agent_load_raises_on_corrupt_yaml(tmp_path):
    cfg = tmp_path / "bad.yml"
    cfg.write_text(":\n  : :\n  - this is not valid")
    with pytest.raises(AgentError):
        Agent.load(cfg)
