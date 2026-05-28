"""Filesystem layout helpers.

Runtime state lives under `~/.abs/` (or `$ABS_HOME` if set).
Default config ships inside the package at `abs/defaults/policy.yml`
and is used as a fallback when the user hasn't customized one yet.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


def abs_home() -> Path:
    """User-state root. Override with `$ABS_HOME`. Default: `~/.abs`."""
    override = os.environ.get("ABS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".abs"


def user_config_path(name: str) -> Path:
    """Path to a file in the user's config dir (does not have to exist yet)."""
    return abs_home() / "config" / name


def bundled_default(name: str) -> Path:
    """Path to a default file shipped inside the installed package."""
    return Path(str(files("abs") / "defaults" / name))


def resolve_policy_path(cli_override: str | os.PathLike | None = None) -> Path:
    """Resolve which `policy.yml` to use.

    Precedence:
        1. Explicit `--policy <path>` from the CLI.
        2. User config at `~/.abs/config/policy.yml`, if it exists.
        3. The bundled default in the installed package.
    """
    if cli_override:
        return Path(cli_override).expanduser()
    user = user_config_path("policy.yml")
    if user.exists():
        return user
    return bundled_default("policy.yml")
