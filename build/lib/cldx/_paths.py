"""Filesystem layout helpers.

Runtime state lives under `~/.cldx/` (or `$CLDX_HOME` if set).
Default config ships inside the package at `cldx/defaults/policy.yml`
and is used as a fallback when the user hasn't customized one yet.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


def cldx_home() -> Path:
    """User-state root. Override with `$CLDX_HOME`. Default: `~/.cldx`."""
    override = os.environ.get("CLDX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".cldx"


def user_config_path(name: str) -> Path:
    """Path to a file in the user's config dir (does not have to exist yet)."""
    return cldx_home() / "config" / name


def bundled_default(name: str) -> Path:
    """Path to a default file shipped inside the installed package."""
    return Path(str(files("cldx") / "defaults" / name))


def resolve_policy_path(cli_override: str | os.PathLike | None = None) -> Path:
    """Resolve which `policy.yml` to use.

    Precedence:
        1. Explicit `--policy <path>` from the CLI.
        2. User config at `~/.cldx/config/policy.yml`, if it exists.
        3. The bundled default in the installed package.
    """
    if cli_override:
        return Path(cli_override).expanduser()
    user = user_config_path("policy.yml")
    if user.exists():
        return user
    return bundled_default("policy.yml")
