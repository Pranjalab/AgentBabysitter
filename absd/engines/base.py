"""The ``Engine`` protocol — the session-backend contract (PLAN.md 4.2).

An engine creates and manages persistent, attachable Claude Code sessions on
behalf of the daemon. The daemon never talks to a multiplexer directly; it goes
through this narrow interface so that tmux and herdr are interchangeable (D4)
and nothing herdr-specific leaks into the daemon (Step 1.1 critique gate).

Only the protocol and its value type live here. Concrete backends (TmuxEngine,
HerdrEngine) are separate modules added in Phase 1 — this is the Step 0.1
skeleton, intentionally implementation-free.

Contract notes (the spec a future port re-implements, PLAN.md 4.4):
  - Sessions are named ``abs-<profile>``; one session per profile.
  - ``create_session`` is ALWAYS headless — it never attaches a client
    (attaching is a separate terminal action, ``abs attach``).
  - The command an engine runs is the existing launcher, not a Python
    reimplementation: ``bash <SCRIPT_PATH> --profile <p> --daemon-start
    [--away]`` in the project cwd (PLAN.md 4.2). This reuses all v2 behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class EngineError(RuntimeError):
    """A session-engine operation failed (bad backend exit, missing binary, a
    duplicate-session request, etc.).

    Shared failure type for the contract, so callers (and the parameterized test
    suite) can catch one exception regardless of backend. Backends may raise
    subclasses (``TmuxEngineError``/``HerdrError``) for backend-specific catches;
    all of them are ``EngineError``. Living here — not in a concrete backend —
    keeps it part of the protocol, not owned by tmux (Step 1.2). This is the only
    logic in an otherwise implementation-free module; the ``Engine`` Protocol and
    ``SessionInfo`` are unchanged.
    """


@dataclass
class SessionInfo:
    """A snapshot of one engine-managed session, as returned by ``list_sessions``.

    Stable value type (PLAN.md 4.4). ``alive`` reflects the engine's own
    liveness view at the time of the call.
    """

    profile: str
    name: str  # the engine session name, e.g. "abs-work"
    alive: bool
    cwd: str | None = None
    pid: int | None = None


@dataclass
class SessionHandle:
    """The identity of the pane a session was launched into (returned by
    ``create_session``, Step 2.2c precise-liveness fix).

    ``pane_id`` is the engine's own pane identifier for the launched command
    (herdr ``"w1:p1"``; ``None`` for tmux, whose single-pane session is already an
    unambiguous liveness target). ``pid`` is the launched job's pid where the
    engine can report it at create time (best-effort; may be ``None`` before the
    command foregrounds). The daemon records these and targets liveness at THIS
    pane — so an attach that spawns a second pane can never be mistaken for the
    real session (the incident that killed a live claude)."""

    pane_id: str | None = None
    pid: int | None = None


@runtime_checkable
class Engine(Protocol):
    """Backend that runs persistent, detach/reattach-capable sessions.

    Matches PLAN.md 4.2. Every method is a no-logic signature here; backends
    implement them. Methods raise/return per their docstrings once implemented.
    """

    #: Human-readable backend identifier, e.g. "tmux" or "herdr".
    name: str

    def available(self) -> bool:
        """True if this backend's binary/runtime is usable on this machine."""
        ...

    def create_session(
        self,
        profile: str,
        cwd: Path,
        command: list[str],
        env: dict[str, str],
    ) -> SessionHandle:
        """Create a headless session ``abs-<profile>`` running ``command`` in
        ``cwd`` with ``env`` overlaid. Never attaches a client. Returns a
        :class:`SessionHandle` identifying the launched pane (Step 2.2c)."""
        ...

    def is_alive(self, profile: str, pane_id: str | None = None) -> bool:
        """True if the session for ``profile`` is running. When ``pane_id`` is
        given, target THAT specific pane (the one recorded at create) rather than
        any pane — so an attach-spawned pane is never mistaken for the launched
        command. ``pane_id=None`` keeps the whole-session view (list_sessions,
        legacy callers)."""
        ...

    def kill(self, profile: str) -> None:
        """Terminate the session for ``profile`` and clean up its resources."""
        ...

    def attach_command(self, profile: str) -> str:
        """Return the shell command a human runs to attach — printed by
        ``abs attach <profile>``."""
        ...

    def list_sessions(self) -> list[SessionInfo]:
        """Return one ``SessionInfo`` per ABS-owned session this backend knows."""
        ...
