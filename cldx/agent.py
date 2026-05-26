"""Load and validate the agent persona from agent_name.yml.

Looks up the user's override at ``~/.cldx/config/agent_name.yml`` first,
falls back to the bundled default ``cldx/defaults/agent_name.yml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from cldx._paths import cldx_home


DEFAULT_LIMITS = {
    "prompt_summary": 200,
    "escalation_summary": 500,
    "completion_summary": 500,
}

KNOWN_MODELS = {
    # Anthropic Claude (default)
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    # Future Ollama models will be prefixed with "ollama:"
}


class AgentError(RuntimeError):
    pass


@dataclass
class Agent:
    name: str = "Sentinel"
    persona: str = "You are a terse technical assistant."
    model: str = "claude-haiku-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))

    # --- factories ---

    @classmethod
    def default(cls) -> "Agent":
        return cls()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Agent":
        """Load an agent definition.

        Precedence (when ``path`` is None):
            1. ``~/.cldx/config/agent_name.yml`` if it exists
            2. The bundled default
        """
        if path is not None:
            return cls._from_yaml(Path(path))

        user_path = cldx_home() / "config" / "agent_name.yml"
        if user_path.exists():
            return cls._from_yaml(user_path)

        bundled = Path(str(files("cldx") / "defaults" / "agent_name.yml"))
        if bundled.exists():
            return cls._from_yaml(bundled)

        return cls.default()

    @classmethod
    def _from_yaml(cls, path: Path) -> "Agent":
        try:
            with path.open("r", encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as e:
            raise AgentError(f"failed to read {path}: {e}") from e

        limits = dict(DEFAULT_LIMITS)
        limits.update(data.get("limits", {}) or {})

        model = data.get("model", "claude-haiku-4-5")
        cls._validate_model(model)

        return cls(
            name=data.get("name") or "Sentinel",
            persona=(data.get("persona") or "").strip() or cls().persona,
            model=model,
            api_key_env=data.get("api_key_env", "ANTHROPIC_API_KEY"),
            limits=limits,
        )

    # --- validation ---

    @staticmethod
    def _validate_model(model: str) -> None:
        if model.startswith("ollama:"):
            return  # future: defer validation to the Ollama adapter
        if model not in KNOWN_MODELS:
            raise AgentError(
                f"unknown model {model!r}. Known: {sorted(KNOWN_MODELS)} "
                "or 'ollama:<model>'"
            )

    # --- introspection ---

    def limit_for(self, mode: str) -> int:
        """Char budget for a summary mode. Falls back to a sensible default."""
        return int(self.limits.get(mode, DEFAULT_LIMITS.get(mode, 500)))
