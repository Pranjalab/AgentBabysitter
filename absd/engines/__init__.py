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

__all__ = ["Engine", "SessionInfo"]
