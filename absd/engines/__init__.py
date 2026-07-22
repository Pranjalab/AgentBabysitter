"""Session-engine adapter package.

Defines the ``Engine`` protocol (``base``) that persistent-session backends
implement. Concrete backends — ``TmuxEngine`` (reference, Step 1.1) and
``HerdrEngine`` (Step 1.2, gated on the Step 0.2 spike) — are NOT part of the
Step 0.1 skeleton and will be added as their own modules here.

ABS must be fully functional without herdr (D4): every feature ships and passes
its tests on tmux alone. herdr is an arms-length enhancement, driven only via
its CLI/socket — never forked, vendored, or copied (D3).
"""

from __future__ import annotations

from absd.engines.base import Engine, SessionInfo
from absd.engines.tmux import TmuxEngine

__all__ = ["Engine", "SessionInfo", "TmuxEngine", "get_engine"]


def get_engine(name: str) -> Engine:
    """Return an :class:`Engine` for the configured backend name (PLAN.md 4.2).

    Accepted names:
      - ``"tmux"`` — the reference backend (always available where tmux is).
      - ``"auto"`` — pick the best available backend. Today that is always tmux.

    ``auto`` is where HerdrEngine selection lands in Step 1.2: prefer herdr when
    its binary is present AND the Step 0.2 recipe verifies, else fall back to tmux
    (D4 — ABS is fully functional without herdr). The seam is explicit and single-
    line so 1.2 slots in without reshaping callers.
    """
    if name == "tmux":
        return TmuxEngine()
    if name == "auto":
        # TODO(Step 1.2): if HerdrEngine().available() and the pinned recipe
        # verifies, return HerdrEngine(); until then auto == tmux (D4 fallback).
        return TmuxEngine()
    raise ValueError(f"unknown engine: {name!r} (expected 'tmux' or 'auto')")
