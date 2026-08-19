"""Toolchain smoke test + the DaemonConfig on-disk contract.

Proves pytest runs in the venv and that ``DaemonConfig`` survives a JSON
round-trip (the serialization is the spec, PLAN.md 4.4). No I/O, no network.
"""

from __future__ import annotations

import json

from absd import __version__
from absd.config import ENGINE_CHOICES, DaemonConfig


def test_version_string() -> None:
    """The package exposes a version (toolchain/import sanity).

    Three numeric components, optionally followed by a pre-release suffix —
    `3.6.0` or `3.6.0-beta.1`. The suffix is load-bearing elsewhere: it is what
    stops a beta silently installing a release's daemon source.
    """
    import re

    assert isinstance(__version__, str)
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", __version__), __version__


def test_defaults_are_sane() -> None:
    """Out-of-box defaults match the values PLAN.md 4.1/4.2/4.5 describe."""
    cfg = DaemonConfig()
    assert cfg.engine in ENGINE_CHOICES
    assert cfg.engine == "auto"
    assert cfg.max_sessions >= 1
    assert cfg.poll_timeout_s == 50
    assert cfg.reclaim_backoff_max_s >= cfg.reclaim_grace_s


def test_round_trip_default() -> None:
    """A default config survives to_dict -> from_dict unchanged."""
    cfg = DaemonConfig()
    assert DaemonConfig.from_dict(cfg.to_dict()) == cfg


def test_round_trip_custom() -> None:
    """A fully-customized config survives a real JSON encode/decode cycle."""
    cfg = DaemonConfig(
        engine="tmux",
        workspace_root="~/work/repos",
        max_sessions=5,
        poll_timeout_s=40,
        poll_stagger_s=2.0,
        reclaim_grace_s=3.0,
        reclaim_backoff_max_s=45.0,
    )
    decoded = DaemonConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert decoded == cfg


def test_from_dict_ignores_unknown_keys() -> None:
    """Forward tolerance: unknown keys from a newer file don't crash (PLAN.md 4.4)."""
    payload = DaemonConfig().to_dict()
    payload["some_future_field"] = "ignored"
    cfg = DaemonConfig.from_dict(payload)
    assert cfg == DaemonConfig()


def test_from_dict_partial_uses_defaults() -> None:
    """A sparse file fills missing keys from defaults."""
    cfg = DaemonConfig.from_dict({"engine": "herdr"})
    assert cfg.engine == "herdr"
    assert cfg.max_sessions == DaemonConfig().max_sessions
