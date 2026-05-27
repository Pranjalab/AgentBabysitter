"""Load and persist user secrets from ``~/.cldx/config/*.env``.

cldx keeps secrets out of the policy file and out of git. Each integration
gets its own ``.env`` file:

- ``anthropic.env``  →  ``ANTHROPIC_API_KEY``
- ``telegram.env``   →  ``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID``
- ``secrets.env``    →  any catch-all overrides

On startup, ``load_into_environ()`` reads every ``*.env`` file in the
config dir and exposes them through ``os.environ`` so the summarizer,
Telegram bridge, etc. find their keys without any plumbing.

``save_secret()`` writes back atomically, sets ``chmod 600``, and
preserves any other keys already in the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from cldx._paths import cldx_home


SECRET_NAMES = ("anthropic", "telegram", "secrets")


def secrets_dir() -> Path:
    return cldx_home() / "config"


def env_file_path(name: str) -> Path:
    return secrets_dir() / f"{name}.env"


# --- parsing -------------------------------------------------------------


def _parse_env_file(path: Path) -> dict[str, str]:
    """Tiny .env parser: KEY=value lines, # comments, optional quotes."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k.strip()] = v
    return out


# --- loading / saving ----------------------------------------------------


def load_into_environ(
    secrets_dir_override: Path | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Read every ``*.env`` under the config dir into ``os.environ``.

    Returns the merged dict of values loaded (handy for tests + introspection).
    By default, environment variables already set by the parent shell are
    *not* overwritten — pass ``overwrite=True`` if you want file values to win.
    """
    d = secrets_dir_override or secrets_dir()
    loaded: dict[str, str] = {}
    if not d.exists():
        return loaded
    for env_file in sorted(d.glob("*.env")):
        for k, v in _parse_env_file(env_file).items():
            loaded[k] = v
            if overwrite or k not in os.environ:
                os.environ[k] = v
    return loaded


def save_secret(name: str, key: str, value: str) -> Path:
    """Atomic-ish write of ``KEY=value`` into ``~/.cldx/config/<name>.env``.

    Preserves every other key already in the file. Sets ``chmod 600`` on the
    final file so the secret is user-readable only.
    """
    d = secrets_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = env_file_path(name)
    current = _parse_env_file(path)
    current[key] = value

    tmp = path.with_suffix(".env.tmp")
    lines = []
    for k, v in current.items():
        # Quote if the value would confuse the parser.
        if any(c in v for c in (" ", "#", '"', "'", "\t")):
            escaped = v.replace('"', '\\"')
            lines.append(f'{k}="{escaped}"')
        else:
            lines.append(f"{k}={v}")
    tmp.write_text("\n".join(lines) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass  # best-effort on platforms that don't support chmod
    tmp.replace(path)
    return path


def clear_secret(name: str, key: str) -> bool:
    """Remove ``key`` from ``~/.cldx/config/<name>.env``. Returns True if removed."""
    path = env_file_path(name)
    current = _parse_env_file(path)
    if key not in current:
        return False
    del current[key]
    if current:
        tmp = path.with_suffix(".env.tmp")
        tmp.write_text(
            "\n".join(f"{k}={v}" for k, v in current.items()) + "\n"
        )
        tmp.replace(path)
    else:
        path.unlink(missing_ok=True)
    return True


# --- display helpers -----------------------------------------------------


def mask_secret(value: str | None) -> str:
    """Render a secret for the screen: first 4 + last 4 chars, middle masked."""
    if not value:
        return "[red](not set)[/red]"
    if len(value) < 12:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


def have_anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def have_telegram_config() -> bool:
    return bool(
        os.environ.get("TELEGRAM_BOT_TOKEN")
        and os.environ.get("TELEGRAM_CHAT_ID")
    )
