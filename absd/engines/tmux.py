"""``TmuxEngine`` — the reference session backend (PLAN.md 4.2, Step 1.1).

Runs each ABS session inside tmux on an **isolated socket** (``tmux -L abs``)
with a tuned config (``assets/abs.tmux.conf``). Session name is ``abs-<profile>``.
Because the socket and config are ABS's own, this never touches a tmux server the
user started themselves.

This is the reference implementation of the ``Engine`` protocol: every ABS
feature must work on tmux alone (D4). HerdrEngine (Step 1.2) is an arms-length
enhancement, never a requirement.

Design notes (portability — PLAN.md 4.4):
  - The wire behaviour is the spec. All tmux invocations use ``subprocess.run``
    with explicit argument lists (never ``shell=True``) and timeouts.
  - Output parsing lives in module-level pure functions (``parse_pane_records``,
    ``sessions_from_records``) so it is unit-testable without tmux installed.
  - Env is passed to the launched command via tmux ``new-session -e`` flags
    (tmux >= 3.2). See ``create_session`` for the rationale.

Liveness contract (the thing the daemon relies on):
  ``is_alive(profile)`` is true iff the launched command is still running. tmux
  is run WITHOUT ``remain-on-exit`` (see the conf), so when the command exits the
  single-pane session is destroyed and ``has-session`` fails — the signal we key
  on. A defensive ``pane_dead`` check is also applied so the contract holds even
  if a future conf change enabled remain-on-exit.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from absd.engines.base import EngineError, SessionHandle, SessionInfo

#: Every ABS tmux session is named with this prefix + the profile name.
SESSION_PREFIX = "abs-"

#: Default isolated socket name. Overridable per-instance (tests use a random one).
DEFAULT_SOCKET = "abs"

#: Field separator for tmux -F format strings. Tab never appears in the fields we
#: request (session names are ``abs-<profile>``, paths have no tabs, ints).
_FS = "\t"

#: -F template for enumerating panes across all sessions on the socket.
_PANE_FORMAT = _FS.join(
    ["#{session_name}", "#{pane_current_path}", "#{pane_pid}", "#{pane_dead}"]
)

#: Default seconds to wait on any single tmux subprocess call. tmux commands are
#: local and near-instant; a timeout only guards against a wedged server.
_DEFAULT_TIMEOUT = 10.0


#: ``EngineError`` was promoted to :mod:`absd.engines.base` in Step 1.2 so both
#: backends share one failure type (the parameterized suite catches it for either
#: engine). Re-exported here so the historical import path
#: (``from absd.engines.tmux import EngineError``) keeps working unchanged.
__all__ = [
    "EngineError",
    "PaneRecord",
    "TmuxEngine",
    "parse_pane_records",
    "sessions_from_records",
    "DEFAULT_SOCKET",
    "SESSION_PREFIX",
]


def _default_conf_path() -> Path:
    """Absolute path to the shipped ``assets/abs.tmux.conf``.

    ``absd/engines/tmux.py`` -> parents[2] is the repo root, where ``assets/``
    lives alongside ``absd/``.
    """
    return Path(__file__).resolve().parents[2] / "assets" / "abs.tmux.conf"


@dataclass(frozen=True)
class PaneRecord:
    """One parsed row of ``list-panes -a -F`` output. Pure value type."""

    session: str
    cwd: str
    pid: int | None
    dead: bool


def parse_pane_records(output: str) -> list[PaneRecord]:
    """Parse ``tmux list-panes -a -F <_PANE_FORMAT>`` stdout into records.

    Pure function — unit-testable without tmux. Blank lines and malformed rows
    (too few fields) are skipped rather than raising, so a transient tmux hiccup
    degrades to "no panes" instead of a crash.
    """
    records: list[PaneRecord] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(_FS)
        if len(parts) < 4:
            continue
        session, cwd, pid_s, dead_s = parts[0], parts[1], parts[2], parts[3]
        try:
            pid: int | None = int(pid_s)
        except ValueError:
            pid = None
        records.append(
            PaneRecord(session=session, cwd=cwd, pid=pid, dead=(dead_s == "1"))
        )
    return records


def sessions_from_records(records: list[PaneRecord]) -> list[SessionInfo]:
    """Fold pane records into one ``SessionInfo`` per ABS session.

    Pure function. Only ``abs-*`` sessions are reported (other sessions on the
    socket, if any, are ignored). ``alive`` is true if any pane of the session is
    not dead; ``cwd``/``pid`` come from the first pane seen for that session.
    Sorted by profile for stable output.
    """
    by_session: dict[str, list[PaneRecord]] = {}
    for r in records:
        if not r.session.startswith(SESSION_PREFIX):
            continue
        by_session.setdefault(r.session, []).append(r)

    infos: list[SessionInfo] = []
    for name, panes in by_session.items():
        first = panes[0]
        infos.append(
            SessionInfo(
                profile=name[len(SESSION_PREFIX):],
                name=name,
                alive=any(not p.dead for p in panes),
                cwd=first.cwd or None,
                pid=first.pid,
            )
        )
    infos.sort(key=lambda s: s.profile)
    return infos


class TmuxEngine:
    """tmux-backed :class:`~absd.engines.base.Engine` on an isolated socket."""

    name = "tmux"

    def __init__(
        self,
        socket_name: str = DEFAULT_SOCKET,
        config_path: Path | None = None,
        tmux_bin: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        #: Isolated socket (``-L``). Parameterised so tests use a throwaway name
        #: and never touch the user's default tmux server.
        self.socket_name = socket_name
        #: Tuned config (``-f``). Loaded when the server starts; passing it on
        #: every call is harmless (ignored once the server is up).
        self.config_path = Path(config_path) if config_path else _default_conf_path()
        self._tmux = tmux_bin or shutil.which("tmux") or "tmux"
        self._timeout = timeout

    # -- naming ---------------------------------------------------------------

    @staticmethod
    def _session_name(profile: str) -> str:
        return f"{SESSION_PREFIX}{profile}"

    # -- subprocess plumbing --------------------------------------------------

    def _base_args(self) -> list[str]:
        return [self._tmux, "-L", self.socket_name, "-f", str(self.config_path)]

    def _run(
        self, args: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run one tmux command on our socket. Never uses a shell."""
        try:
            proc = subprocess.run(
                self._base_args() + args,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise EngineError(f"tmux binary not found: {self._tmux!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError(
                f"tmux command timed out after {self._timeout}s: {' '.join(args)}"
            ) from exc
        if check and proc.returncode != 0:
            raise EngineError(
                f"tmux {' '.join(args)} failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        return proc

    def _has_session(self, name: str) -> bool:
        # has-session exits 0 if it exists, non-zero (with an error on stderr —
        # "can't find session" or "no server running") otherwise. Both are "no".
        proc = self._run(["has-session", "-t", f"={name}"], check=False)
        return proc.returncode == 0

    def _pane_records(self) -> list[PaneRecord]:
        # If no server is running, list-panes exits non-zero — that just means no
        # sessions, so swallow it and return empty.
        proc = self._run(["list-panes", "-a", "-F", _PANE_FORMAT], check=False)
        if proc.returncode != 0:
            return []
        return parse_pane_records(proc.stdout)

    # -- Engine protocol ------------------------------------------------------

    def available(self) -> bool:
        """True if the tmux binary is present and runnable (``tmux -V`` exits 0)."""
        if not self._tmux:
            return False
        try:
            proc = subprocess.run(
                [self._tmux, "-V"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return proc.returncode == 0

    def create_session(
        self,
        profile: str,
        cwd: Path,
        command: list[str],
        env: dict[str, str],
    ) -> SessionHandle:
        """Create the headless session ``abs-<profile>`` running ``command`` in
        ``cwd`` with ``env`` overlaid. Never attaches a client. Returns a
        :class:`SessionHandle` with the launched pane's pid.

        Raises :class:`EngineError` if a session of that name already exists — the
        daemon relies on this being loud, not a silent no-op.

        Env mechanism: tmux ``new-session -e KEY=VALUE`` (tmux >= 3.2, verified on
        3.4). Chosen over an ``env KEY=VALUE cmd`` prefix because (a) the values
        reach the command's process without appearing in the pane's visible
        command line / ``ps`` / pane title, and (b) it is scoped to this session's
        environment, not the socket-wide global environment. The command is passed
        as an explicit argv after ``--`` so tmux execs it directly (no shell
        re-parsing of the arguments).
        """
        name = self._session_name(profile)
        # Deterministic, explained failure before we even ask tmux — tmux's own
        # "duplicate session" error is also fatal, but this gives a clear message.
        if self._has_session(name):
            raise EngineError(f"session {name!r} already exists")

        args = ["new-session", "-d", "-s", name, "-c", str(cwd)]
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        args.append("--")
        args += list(command)
        self._run(args)
        # Record the launched pane's pid. tmux is single-pane per ABS session and
        # destroys the session when the command exits (remain-on-exit off), so
        # pane_id targeting is unnecessary here — the pid is for the daemon's
        # clobber cross-check (Step 2.2c). Attach to tmux does NOT spawn panes.
        pid = None
        for r in self._pane_records():
            if r.session == name:
                pid = r.pid
                break
        return SessionHandle(pane_id=None, pid=pid)

    def is_alive(self, profile: str, pane_id: str | None = None) -> bool:
        """True iff the session exists AND its command is still running.

        Primary signal: ``has-session`` (with remain-on-exit off, a dead command
        means the session is gone). Defensive: even if the session exists, treat
        it as not-alive if every pane is marked dead (guards against a future conf
        that enabled remain-on-exit). ``pane_id`` is accepted for protocol parity
        (the daemon passes the recorded pane) but tmux needs no pane targeting: a
        tmux ``abs-*`` session is single-pane and attach never adds panes, so the
        whole-session view is already precise (unlike herdr — see HerdrEngine)."""
        name = self._session_name(profile)
        if not self._has_session(name):
            return False
        panes = [r for r in self._pane_records() if r.session == name]
        if not panes:
            # Raced with teardown between the two calls: the command exited.
            return False
        return any(not p.dead for p in panes)

    def kill(self, profile: str) -> None:
        """Terminate the session for ``profile``. Idempotent: a no-op (no raise)
        if the session is already gone."""
        name = self._session_name(profile)
        if not self._has_session(name):
            return
        self._run(["kill-session", "-t", f"={name}"], check=False)

    def attach_command(self, profile: str) -> str:
        """The exact shell command a human runs to attach (printed by
        ``abs attach``). Note: no ``-f`` — the config only matters at server
        start; attach just connects to the running server."""
        return shlex.join(self.attach_argv(profile))

    def attach_argv(self, profile: str) -> list[str]:
        """argv form of :meth:`attach_command`, for ``os.execvp`` in the CLI.

        Uses the literal ``tmux`` token (not the resolved ``self._tmux`` path) so
        the printed command is the stable, copy-pasteable ``tmux -L <socket>
        attach -t abs-<profile>`` regardless of where the binary lives; ``execvp``
        resolves it on PATH."""
        return [
            "tmux",
            "-L",
            self.socket_name,
            "attach",
            "-t",
            self._session_name(profile),
        ]

    def list_sessions(self) -> list[SessionInfo]:
        """One ``SessionInfo`` per ``abs-*`` session on our socket."""
        return sessions_from_records(self._pane_records())
