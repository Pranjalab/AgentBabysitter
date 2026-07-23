"""Daemon configuration — the on-disk ``~/.abs/daemon/config.json`` shape.

Per PLAN.md 4.3, the daemon reads ``config.json`` (engine, workspace root, poll
timings, max concurrent sessions). Per PLAN.md 4.4, the on-disk state formats
are part of the spec a future port must re-implement, so the serialization here
is real and stable — but *loading from disk* (path discovery, umask 0600
enforcement, defaults-on-missing) is left as a stub for Step 1.3, where it can
be tested against a temp ``~/.abs``.

Design notes:
  - Plain ``dataclass`` + ``json`` only (stdlib-first, PLAN.md 4.4).
  - ``from_dict`` is forward-tolerant: unknown keys are ignored so a newer
    on-disk file never crashes an older daemon (and vice versa).
  - ``workspace_root`` is stored as written (may contain ``~``); expansion and
    the D6 path-jail check belong to whoever *uses* it, not to the config type.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

# Allowed values for the engine selector (PLAN.md 4.2 / D4). "auto" resolves to
# herdr-if-available-and-spike-verified, else tmux.
ENGINE_CHOICES = ("auto", "herdr", "tmux")

_MODE = 0o600


class ConfigError(ValueError):
    """``config.json`` is present but invalid (bad engine, out-of-range number).

    Raised by :func:`load` so the daemon refuses to start on a corrupt config
    rather than silently polling with surprising timings.
    """


@dataclass
class DaemonConfig:
    """In-memory representation of ``~/.abs/daemon/config.json``.

    Fields map 1:1 to the JSON object. Defaults are the intended out-of-box
    behavior described across PLAN.md 4.1/4.2/4.5.
    """

    # Session engine selection (D4). One of ENGINE_CHOICES.
    engine: str = "auto"

    # D6 path jail: the ONLY directory under which Telegram-initiated new folders
    # may be created, and whose direct children are offered as start targets.
    # Stored verbatim (may contain "~"); expansion happens at use sites.
    workspace_root: str = "~/Projects"

    # G5 concurrency cap: max simultaneous live sessions the daemon will launch.
    max_sessions: int = 3

    # getUpdates long-poll timeout, seconds (PLAN.md 4.5).
    poll_timeout_s: int = 50

    # Per-profile poller start stagger, seconds — avoids a thundering herd of
    # simultaneous long-polls at boot (PLAN.md 4.5 / R10).
    poll_stagger_s: float = 1.5

    # RECLAIM grace delay before the first post-session probe (PLAN.md 4.1).
    reclaim_grace_s: float = 5.0

    # Upper bound on the exponential 409 backoff during RECLAIM (PLAN.md 4.1).
    reclaim_backoff_max_s: float = 60.0

    # ABS START flow inactivity timeout (PLAN.md Step 1.5): a half-finished flow
    # (project/mode not yet chosen) expires after this many seconds.
    flow_timeout_s: float = 300.0

    # HANDOFF startup grace (PLAN.md Step 1.5 / 4.1): after the daemon launches a
    # session, how long to wait for it to come alive (engine/pid) before treating
    # a never-alive launch as a failed start and reclaiming. Guards the launch
    # window so a session that is still booting is not mistaken for a dead one.
    session_start_grace_s: float = 30.0

    # Log rotation (Step 1.8): daemon.log and events.jsonl rotate at this size,
    # keeping this many .1/.2/.3 generations.
    log_max_bytes: int = 5 * 1024 * 1024
    log_keep: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable mapping for this config."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DaemonConfig":
        """Build a config from a parsed JSON object, ignoring unknown keys.

        Forward/backward tolerant on purpose (PLAN.md 4.4): an out-of-range or
        malformed *value* is not validated here — validation is a separate
        concern for the loader (Step 1.3). This only maps known keys.
        """
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def validate(cfg: DaemonConfig) -> DaemonConfig:
    """Validate a config's values; return it unchanged, or raise ``ConfigError``.

    Rules (PLAN.md 4.1/4.2/4.5):
      - ``engine`` must be one of :data:`ENGINE_CHOICES`.
      - ``max_sessions`` >= 1.
      - ``poll_timeout_s`` in [0, 300] (0 = short poll; Telegram caps ~50).
      - ``poll_stagger_s`` >= 0.
      - ``reclaim_grace_s`` >= 0.
      - ``reclaim_backoff_max_s`` >= ``reclaim_grace_s`` (the cap must not sit
        below the initial grace, or backoff maths is nonsense).
      - ``flow_timeout_s`` >= 0 and ``session_start_grace_s`` >= 0 (Step 1.5).
    """
    if cfg.engine not in ENGINE_CHOICES:
        raise ConfigError(
            f"engine must be one of {ENGINE_CHOICES}, got {cfg.engine!r}"
        )
    if not isinstance(cfg.max_sessions, int) or cfg.max_sessions < 1:
        raise ConfigError(f"max_sessions must be an int >= 1, got {cfg.max_sessions!r}")
    if not (0 <= cfg.poll_timeout_s <= 300):
        raise ConfigError(f"poll_timeout_s must be in [0, 300], got {cfg.poll_timeout_s!r}")
    if cfg.poll_stagger_s < 0:
        raise ConfigError(f"poll_stagger_s must be >= 0, got {cfg.poll_stagger_s!r}")
    if cfg.reclaim_grace_s < 0:
        raise ConfigError(f"reclaim_grace_s must be >= 0, got {cfg.reclaim_grace_s!r}")
    if cfg.reclaim_backoff_max_s < cfg.reclaim_grace_s:
        raise ConfigError(
            "reclaim_backoff_max_s must be >= reclaim_grace_s "
            f"({cfg.reclaim_backoff_max_s!r} < {cfg.reclaim_grace_s!r})"
        )
    if cfg.flow_timeout_s < 0:
        raise ConfigError(f"flow_timeout_s must be >= 0, got {cfg.flow_timeout_s!r}")
    if cfg.session_start_grace_s < 0:
        raise ConfigError(
            f"session_start_grace_s must be >= 0, got {cfg.session_start_grace_s!r}"
        )
    if not isinstance(cfg.log_max_bytes, int) or cfg.log_max_bytes < 1024:
        raise ConfigError(f"log_max_bytes must be an int >= 1024, got {cfg.log_max_bytes!r}")
    if not isinstance(cfg.log_keep, int) or cfg.log_keep < 1:
        raise ConfigError(f"log_keep must be an int >= 1, got {cfg.log_keep!r}")
    return cfg


def load(path: Path) -> DaemonConfig:
    """Load and validate ``config.json`` from ``path``.

    - **Missing file** → defaults (a fresh install with no config yet is valid).
    - **Present** → parse JSON, coerce via :meth:`DaemonConfig.from_dict`
      (unknown keys ignored, PLAN.md 4.4), validate values (:func:`validate`),
      and enforce 0600 perms on the file (pool/config are local user data,
      PLAN.md 5.5). A file that is not a JSON object, or whose values are
      out of range, raises :class:`ConfigError`.
    """
    path = Path(path)
    if not path.exists():
        return DaemonConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ConfigError(f"{path}: unreadable/invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level JSON must be an object, got {type(raw).__name__}")
    cfg = validate(DaemonConfig.from_dict(raw))
    # Config carries no secrets, but the daemon tree is 0600/0700 throughout
    # (PLAN.md 4.3); tighten a loosely-created file rather than trust its mode.
    try:
        os.chmod(path, _MODE)
    except OSError:
        pass
    return cfg


def save(path: Path, cfg: DaemonConfig) -> None:
    """Write ``cfg`` to ``path`` as pretty JSON, 0600 (used by ``abs`` tooling)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
    try:
        os.write(fd, (json.dumps(cfg.to_dict(), indent=2) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    os.chmod(path, _MODE)
