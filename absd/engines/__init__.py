"""Session-engine adapter package.

Defines the ``Engine`` protocol (``base``) that persistent-session backends
implement, plus the concrete backends: ``TmuxEngine`` (reference, Step 1.1) and
``HerdrEngine`` (enhanced, Step 1.2).

ABS must be fully functional without herdr (D4): every feature ships and passes
its tests on tmux alone. herdr is an arms-length enhancement, driven only via
its CLI/socket — never forked, vendored, or copied (D3).
"""

from __future__ import annotations

from absd.engines.base import Engine, EngineError, SessionInfo
from absd.engines.herdr import HerdrEngine
from absd.engines.tmux import TmuxEngine

__all__ = [
    "Engine",
    "EngineError",
    "SessionInfo",
    "TmuxEngine",
    "HerdrEngine",
    "get_engine",
]


def get_engine(name: str) -> Engine:
    """Return an :class:`Engine` for the configured backend name (PLAN.md 4.2).

    Accepted names:
      - ``"tmux"`` — the reference backend (always available where tmux is).
      - ``"herdr"`` — the enhanced backend (requires the herdr binary).
      - ``"auto"`` — prefer herdr when its binary is present AND runnable, else
        fall back to tmux (D4 — ABS is fully functional without herdr).

    Kept deliberately dumb and single-line per branch so callers never reshape
    and it stays trivially testable (``available()`` is monkeypatched in tests).
    """
    if name == "tmux":
        return TmuxEngine()
    if name == "herdr":
        return HerdrEngine()
    if name == "auto":
        herdr = HerdrEngine()
        return herdr if herdr.available() else TmuxEngine()
    raise ValueError(
        f"unknown engine: {name!r} (expected 'tmux', 'herdr', or 'auto')"
    )
