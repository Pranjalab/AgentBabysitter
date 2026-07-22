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

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

# Allowed values for the engine selector (PLAN.md 4.2 / D4). "auto" resolves to
# herdr-if-available-and-spike-verified, else tmux.
ENGINE_CHOICES = ("auto", "herdr", "tmux")


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


def load(path: Path) -> DaemonConfig:
    """Load and validate a ``config.json`` from disk. (Step 1.3.)

    Will: read the file if present (defaults if absent), parse JSON, coerce via
    ``DaemonConfig.from_dict``, validate ``engine`` against ``ENGINE_CHOICES``
    and numeric ranges, and enforce 0600 perms. Not implemented yet.
    """
    raise NotImplementedError("config loading lands in Step 1.3")
