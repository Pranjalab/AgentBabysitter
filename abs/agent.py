"""Load and validate the agent persona from agent_name.yml.

Looks up the user's override at ``~/.abs/config/agent_name.yml`` first,
falls back to the bundled default ``abs/defaults/agent_name.yml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from abs._paths import abs_home


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
    # Other backends use prefixes:
    #   bedrock:<modelId>     e.g. bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0
    #   gemini:<modelId>      e.g. gemini:gemini-2.0-flash
    #   ollama:<model:tag>    e.g. ollama:llama3.1:8b
    #   none:raw              skip LLM entirely; raw pane goes to Telegram
}

# A backend is the upstream service that actually runs the LLM call.
KNOWN_BACKENDS = ("anthropic", "bedrock", "gemini", "ollama", "none")


class AgentError(RuntimeError):
    pass


@dataclass
class Agent:
    name: str = "Sentinel"
    persona: str = "You are a terse technical assistant."
    model: str = "claude-haiku-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    # Backend-specific extras (only some are read by some backends)
    aws_region: str = "us-east-1"          # Bedrock

    # --- Backend dispatch -----------------------------------------------

    @property
    def backend(self) -> str:
        """Which upstream provider this agent's model runs on.

        ``none`` is a sentinel backend that skips the LLM step entirely —
        used when the user wants raw pane content forwarded to Telegram
        without an upstream summarisation call.
        """
        if self.model.startswith("none:") or self.model == "none":
            return "none"
        if self.model.startswith("bedrock:"):
            return "bedrock"
        if self.model.startswith("gemini:"):
            return "gemini"
        if self.model.startswith("ollama:"):
            return "ollama"
        return "anthropic"

    @property
    def bare_model_id(self) -> str:
        """Strip the backend prefix from `model` (e.g. `gemini:foo` → `foo`)."""
        for prefix in ("none:", "bedrock:", "gemini:", "ollama:"):
            if self.model.startswith(prefix):
                return self.model[len(prefix):]
        return self.model

    # --- factories ---

    @classmethod
    def default(cls) -> "Agent":
        return cls()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Agent":
        """Load an agent definition.

        Precedence (when ``path`` is None):
            1. ``~/.abs/config/agent_name.yml`` if it exists
            2. The bundled default
        """
        if path is not None:
            return cls._from_yaml(Path(path))

        user_path = abs_home() / "config" / "agent_name.yml"
        if user_path.exists():
            return cls._from_yaml(user_path)

        bundled = Path(str(files("abs") / "defaults" / "agent_name.yml"))
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
            aws_region=data.get("aws_region", "us-east-1"),
        )

    # --- validation ---

    @staticmethod
    def _validate_model(model: str) -> None:
        # `none:<anything>` and the bare `none` both disable the LLM.
        if model == "none" or model.startswith("none:"):
            return
        # Prefixed models are validated by their respective backend adapters;
        # we only enforce that the prefix is one we know how to dispatch.
        if model.startswith(("bedrock:", "gemini:", "ollama:")):
            # The part after the prefix must not be empty.
            _, _, rest = model.partition(":")
            if not rest.strip():
                raise AgentError(f"backend model id is empty: {model!r}")
            return
        if model not in KNOWN_MODELS:
            raise AgentError(
                f"unknown model {model!r}. Known: {sorted(KNOWN_MODELS)} "
                "or use a backend prefix: bedrock:<id> / gemini:<id> / "
                "ollama:<id> / none:raw"
            )

    # --- introspection ---

    def limit_for(self, mode: str) -> int:
        """Char budget for a summary mode. Falls back to a sensible default."""
        return int(self.limits.get(mode, DEFAULT_LIMITS.get(mode, 500)))
