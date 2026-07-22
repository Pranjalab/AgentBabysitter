"""``HerdrEngine`` — the enhanced session backend (PLAN.md 4.2, Step 1.2).

Runs each ABS session as a **named herdr session** (``abs-<profile>``) driven
entirely through herdr's CLI + JSON socket API (D3: arms-length, never
forked/vendored/copied). A headless ``herdr server`` per session owns a single
workspace at the project cwd; the launcher runs in that workspace's root pane.

This backend is an *enhancement* over :class:`~absd.engines.tmux.TmuxEngine`,
never a requirement (D4): every feature here matches TmuxEngine's semantics so
the same behavioural tests pass on both. herdr's extra powers (agent-status
events) belong to Phase 2, not this module.

Everything below is built from the verified Step 0.2 recipes
(``docs/v3/herdr-recipes.md``); herdr 0.7.5, socket protocol 17.

Design notes (portability — PLAN.md 4.4):
  - The wire behaviour is the spec. All herdr invocations use ``subprocess.run``
    with explicit argument lists (never ``shell=True``) and timeouts. The one
    long-lived process, ``herdr server``, is spawned detached (``start_new_session``)
    and never waited on.
  - Output parsing lives in module-level pure functions (``parse_session_list``,
    ``pane_id_from_create``, ``first_pane``, ``parse_process_info``,
    ``command_running``) so it is unit-testable without herdr installed.
  - Env is passed to the launched command via ``workspace create --env KEY=VALUE``
    (verified: the values reach the launched process's ``/proc/<pid>/environ``).
    See ``create_session`` for the rationale and visibility tradeoff.

Liveness contract (the thing the daemon relies on):
  ``is_alive(profile)`` is true iff the launched command is still running.
  **herdr keeps the pane and session alive after the command exits** (the pane's
  interactive shell reclaims the foreground — unlike tmux without remain-on-exit,
  where the session vanishes). So session/pane existence is NOT the liveness
  signal. The signal is herdr-native and empirically verified: the pane's
  ``foreground_process_group_id`` differs from its ``shell_pid`` exactly while a
  launched job holds the foreground; when the command exits, the idle shell
  reclaims the foreground and the two collapse to equal. See ``command_running``.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from absd.engines.base import EngineError, SessionInfo

#: Every ABS herdr session is named with this prefix + the profile name. herdr's
#: own always-present "default" session never carries it, so it is filtered out.
SESSION_PREFIX = "abs-"

#: The ABS pane. Each ABS session has exactly one workspace (``w1``) with one root
#: pane (``w1:p1``) — the recipe guarantees this. Discovered from the create
#: response and re-derived defensively from ``pane list`` for later operations.
_ROOT_PANE = "w1:p1"

#: Default seconds to wait on any single herdr CLI subprocess call.
_DEFAULT_TIMEOUT = 15.0

#: Default budget for the "wait until the freshly-spawned server accepts calls"
#: poll. Readiness is wall-clock (the socket file appears a beat before the server
#: answers), so we poll ``session list`` for ``running:true`` rather than sleep.
_READY_TIMEOUT = 15.0
_READY_INTERVAL = 0.05

#: Fallback herdr location when it is not on PATH (the pinned install path, D13).
_FALLBACK_BIN = str(Path.home() / ".local" / "bin" / "herdr")


class HerdrError(EngineError):
    """A herdr operation failed. Subclass of the shared
    :class:`~absd.engines.base.EngineError` so the parameterized suite catches
    one type for either backend, while herdr-specific handlers can still catch
    this narrower class."""


# --------------------------------------------------------------------------- #
# Pure parsing helpers — unit-testable without herdr installed
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HerdrSession:
    """One row of ``herdr session list --json``. Pure value type."""

    name: str
    running: bool
    socket_path: str | None = None


@dataclass(frozen=True)
class PaneRef:
    """A pane's id + cwd, from ``herdr pane list``. Pure value type."""

    pane_id: str
    cwd: str | None


@dataclass(frozen=True)
class ProcessInfo:
    """Parsed ``herdr pane process-info``. Pure value type.

    ``fg_pgid`` is the pane's foreground process-group id; ``shell_pid`` is the
    pane's interactive shell. A launched job holds the foreground iff
    ``fg_pgid != shell_pid`` (see :func:`command_running`).
    """

    pane_id: str | None
    shell_pid: int | None
    fg_pgid: int | None
    foreground_pids: tuple[int, ...] = ()


def _loads(output: str) -> dict:
    """Parse one JSON line; return ``{}`` on any malformed/empty input.

    herdr emits one JSON object per line. Degrading to ``{}`` (rather than
    raising) lets a transient hiccup read as "nothing there" instead of a crash,
    matching TmuxEngine's tolerant parsing.
    """
    text = output.strip()
    if not text:
        return {}
    # herdr replies are single-line, but be defensive if extra lines appear.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def parse_session_list(output: str) -> list[HerdrSession]:
    """Parse ``herdr session list --json`` stdout. Pure function.

    Shape: ``{"sessions":[{"name","running","socket_path",...}, ...]}``. Rows
    missing a name are skipped; ``running`` is coerced to bool.
    """
    obj = _loads(output)
    sessions: list[HerdrSession] = []
    for row in obj.get("sessions", []) or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        sessions.append(
            HerdrSession(
                name=name,
                running=bool(row.get("running")),
                socket_path=row.get("socket_path"),
            )
        )
    return sessions


def session_running(output: str, name: str) -> bool:
    """True iff ``name`` appears with ``running:true`` in a session-list dump."""
    return any(s.name == name and s.running for s in parse_session_list(output))


def pane_id_from_create(output: str) -> str:
    """Pull ``result.root_pane.pane_id`` from a ``workspace create`` reply.

    Raises :class:`HerdrError` if the shape is unexpected — session creation must
    fail loudly, not silently return a bogus pane id.
    """
    obj = _loads(output)
    try:
        pane_id = obj["result"]["root_pane"]["pane_id"]
    except (KeyError, TypeError):
        pane_id = None
    if not isinstance(pane_id, str) or not pane_id:
        raise HerdrError(
            f"could not parse pane id from workspace-create reply: {output!r}"
        )
    return pane_id


def first_pane(output: str) -> PaneRef | None:
    """First pane from ``herdr pane list`` (``result.panes[0]``), or None.

    Pure. ABS sessions have exactly one pane, so "first" is "the" pane.
    """
    obj = _loads(output)
    panes = None
    result = obj.get("result")
    if isinstance(result, dict):
        panes = result.get("panes")
    if not isinstance(panes, list) or not panes:
        return None
    p = panes[0]
    if not isinstance(p, dict):
        return None
    pane_id = p.get("pane_id")
    if not isinstance(pane_id, str) or not pane_id:
        return None
    cwd = p.get("cwd")
    return PaneRef(pane_id=pane_id, cwd=cwd if isinstance(cwd, str) and cwd else None)


def parse_process_info(output: str) -> ProcessInfo:
    """Parse ``herdr pane process-info`` into a :class:`ProcessInfo`. Pure.

    Shape: ``{"result":{"process_info":{"pane_id","shell_pid",
    "foreground_process_group_id","foreground_processes":[{"pid",...}]}}}``.
    Missing fields degrade to ``None``/empty rather than raising.
    """
    obj = _loads(output)
    pi = {}
    result = obj.get("result")
    if isinstance(result, dict):
        maybe = result.get("process_info")
        if isinstance(maybe, dict):
            pi = maybe

    def _int_or_none(v: object) -> int | None:
        return v if isinstance(v, int) else None

    fg_pids: list[int] = []
    for proc in pi.get("foreground_processes", []) or []:
        if isinstance(proc, dict) and isinstance(proc.get("pid"), int):
            fg_pids.append(proc["pid"])

    return ProcessInfo(
        pane_id=pi.get("pane_id") if isinstance(pi.get("pane_id"), str) else None,
        shell_pid=_int_or_none(pi.get("shell_pid")),
        fg_pgid=_int_or_none(pi.get("foreground_process_group_id")),
        foreground_pids=tuple(fg_pids),
    )


def command_running(info: ProcessInfo) -> bool:
    """True iff a launched job holds the pane's foreground. Pure.

    Empirically: while a command runs via ``pane run`` it becomes the pane's
    foreground process group, so ``fg_pgid != shell_pid``. When it exits, the
    interactive shell reclaims the foreground and ``fg_pgid == shell_pid``. A
    ``None`` fg_pgid (no info / server gone) reads as not-running.
    """
    if info.fg_pgid is None or info.shell_pid is None:
        return False
    return info.fg_pgid != info.shell_pid


def launched_pid(info: ProcessInfo) -> int | None:
    """The launched command's pid — the foreground process-group leader — while a
    command runs, else None. Mirrors TmuxEngine's ``pane_pid`` (which equals the
    launched command's ``$$``)."""
    return info.fg_pgid if command_running(info) else None


# --------------------------------------------------------------------------- #
# HerdrEngine
# --------------------------------------------------------------------------- #


class HerdrEngine:
    """herdr-backed :class:`~absd.engines.base.Engine`.

    Stateless like TmuxEngine: every method re-derives what it needs from herdr's
    own state. ``session_prefix`` is the herdr-side analogue of TmuxEngine's
    isolated socket — it namespaces this engine's sessions so tests can run on a
    throwaway prefix (``abs-test-<random>-``) and ``list_sessions`` reports only
    this engine's sessions, never another run's or a real ABS session's.
    """

    name = "herdr"

    def __init__(
        self,
        herdr_bin: str | None = None,
        session_prefix: str = SESSION_PREFIX,
        timeout: float = _DEFAULT_TIMEOUT,
        ready_timeout: float = _READY_TIMEOUT,
    ) -> None:
        #: Resolve herdr on PATH, else the pinned install path (D13).
        self._herdr = herdr_bin or shutil.which("herdr") or _FALLBACK_BIN
        #: Namespaces this engine's sessions (default ``abs-``; tests override).
        self._session_prefix = session_prefix
        self._timeout = timeout
        self._ready_timeout = ready_timeout

    # -- naming ---------------------------------------------------------------

    def _session_name(self, profile: str) -> str:
        return f"{self._session_prefix}{profile}"

    def _profile_of(self, session_name: str) -> str:
        return session_name[len(self._session_prefix):]

    # -- subprocess plumbing --------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        session: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one herdr CLI command. Never uses a shell.

        ``session`` sets ``HERDR_SESSION`` for session-scoped commands (the
        selector for the per-session socket). ``check`` raises
        :class:`HerdrError` on non-zero exit.
        """
        env = dict(os.environ)
        if session is not None:
            env["HERDR_SESSION"] = session
        try:
            proc = subprocess.run(
                [self._herdr, *args],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
            )
        except FileNotFoundError as exc:
            raise HerdrError(f"herdr binary not found: {self._herdr!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HerdrError(
                f"herdr command timed out after {self._timeout}s: {' '.join(args)}"
            ) from exc
        if check and proc.returncode != 0:
            raise HerdrError(
                f"herdr {' '.join(args)} failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        return proc

    def _session_list_raw(self) -> str:
        # session list is a client-side enumeration; it answers with no server up.
        proc = self._run(["session", "list", "--json"], check=False)
        return proc.stdout if proc.returncode == 0 else ""

    def _is_running(self, name: str) -> bool:
        return session_running(self._session_list_raw(), name)

    def _pane_of(self, session: str) -> PaneRef | None:
        """The session's single pane (id + cwd), or None if unavailable."""
        proc = self._run(["pane", "list"], session=session, check=False)
        if proc.returncode != 0:
            return None
        return first_pane(proc.stdout)

    def _process_info(self, session: str, pane_id: str) -> ProcessInfo:
        proc = self._run(
            ["pane", "process-info", "--pane", pane_id],
            session=session,
            check=False,
        )
        if proc.returncode != 0:
            return ProcessInfo(pane_id=None, shell_pid=None, fg_pgid=None)
        return parse_process_info(proc.stdout)

    def _wait_for_ready(self, name: str) -> None:
        """Block until the freshly-spawned server for ``name`` accepts calls.

        Bounded wall-clock poll (readiness is not signalled — the socket file
        appears a beat before the server answers). Raises :class:`HerdrError` if
        the server never comes up within the budget.
        """
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self._is_running(name):
                return
            time.sleep(_READY_INTERVAL)
        if self._is_running(name):
            return
        raise HerdrError(
            f"herdr server for session {name!r} did not become ready within "
            f"{self._ready_timeout}s"
        )

    # -- Engine protocol ------------------------------------------------------

    def available(self) -> bool:
        """True if the herdr binary is present and runnable (``--version`` exits 0)."""
        if not self._herdr:
            return False
        try:
            proc = subprocess.run(
                [self._herdr, "--version"],
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
    ) -> None:
        """Create the headless session ``abs-<profile>`` running ``command`` in
        ``cwd`` with ``env`` overlaid. Never attaches a client.

        Raises :class:`HerdrError` if a session of that name is already running —
        the daemon relies on this being loud, not a silent no-op (matches
        TmuxEngine's contract).

        Sequence (from the verified recipes): spawn a detached headless
        ``herdr server`` for the named session → poll until it accepts calls →
        ``workspace create --cwd --env … --no-focus`` (one ``--env KEY=VALUE`` per
        item) → parse the root pane id → ``pane run`` the command as a single
        shell-quoted string typed into the pane's shell.

        Env mechanism: ``workspace create --env KEY=VALUE``. Verified: the values
        reach the launched process's environment (``/proc/<pid>/environ``) and do
        **not** appear on the pane's command line / ``ps`` / pane title (better
        than an ``env KEY=VALUE cmd`` prefix). Visibility tradeoff (same as tmux's
        ``-e``): the values are readable by any same-user process via
        ``/proc/<pid>/environ``, so this is fine for the non-secret launcher vars
        ABS passes but **secrets must never be passed this way** (PLAN.md 5.5).
        """
        name = self._session_name(profile)
        if self._is_running(name):
            raise HerdrError(f"session {name!r} already running")

        # 1. Spawn the headless server, fully detached so it outlives us. herdr
        #    writes its own server log under the session dir; we discard fds.
        try:
            subprocess.Popen(  # noqa: S603 - explicit argv, no shell
                [self._herdr, "server"],
                env={**os.environ, "HERDR_SESSION": name},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(cwd),
            )
        except (OSError, FileNotFoundError) as exc:
            raise HerdrError(f"could not start herdr server: {exc}") from exc

        # 2. Wait until it accepts calls (bounded wall-clock poll).
        self._wait_for_ready(name)

        # 3. Create the single workspace at cwd, injecting env, staying headless.
        wargs = [
            "workspace",
            "create",
            "--cwd",
            str(cwd),
            "--label",
            name,
            "--no-focus",
        ]
        for key, value in env.items():
            wargs += ["--env", f"{key}={value}"]
        create = self._run(wargs, session=name)
        pane_id = pane_id_from_create(create.stdout)

        # 4. Launch the command in the pane. Pass it as one shell-quoted string;
        #    herdr types it + Enter into the pane's shell, which re-parses it.
        self._run(
            ["pane", "run", pane_id, shlex.join(command)],
            session=name,
        )

    def is_alive(self, profile: str) -> bool:
        """True iff the launched command is still running.

        See the module liveness note: herdr keeps the pane/session after the
        command exits, so existence is not the signal. Alive iff the session is
        running AND a launched job holds the pane's foreground
        (``fg_pgid != shell_pid``).
        """
        name = self._session_name(profile)
        if not self._is_running(name):
            return False
        pane = self._pane_of(name)
        if pane is None:
            # Raced with teardown, or the server is not answering: treat as gone.
            return False
        return command_running(self._process_info(name, pane.pane_id))

    def kill(self, profile: str) -> None:
        """Terminate the session for ``profile`` and clean up. Idempotent: a no-op
        (no raise) if the session is already gone.

        Full teardown (recipe order): ``pane close`` (kills the pane's whole
        process group — the launcher and any children), ``session stop`` (stops
        the per-session server; leaves no herdr process), ``session delete``
        (removes residual session state). All best-effort so a partially-gone
        session still ends fully torn down. (``session stop`` alone was verified
        to reap pane children too; ``pane close`` first is belt-and-suspenders.)
        """
        name = self._session_name(profile)
        if not self._is_running(name):
            # Not running; still clear any residual saved state, then stop.
            self._run(["session", "delete", name], check=False)
            return
        pane = self._pane_of(name)
        if pane is not None:
            self._run(["pane", "close", pane.pane_id], session=name, check=False)
        self._run(["session", "stop", name], session=name, check=False)
        self._run(["session", "delete", name], check=False)

    def attach_command(self, profile: str) -> str:
        """The exact shell command a human runs to attach (printed by
        ``abs attach``). Per the recipes; detach with ``ctrl+b q`` (the session +
        pane keep running). Uses the literal ``herdr`` token so the printed string
        is stable and copy-pasteable regardless of where the binary lives."""
        return shlex.join(self.attach_argv(profile))

    def attach_argv(self, profile: str) -> list[str]:
        """argv form of :meth:`attach_command` (for ``os.execvp`` in a CLI).

        ``herdr session attach <name>`` takes the session name positionally, so no
        ``HERDR_SESSION`` env is needed. NOTE: full herdr attach requires a real
        TTY — running it with stdin redirected panics (recipes §5); the CLI must
        only exec this when attached to a terminal."""
        return ["herdr", "session", "attach", self._session_name(profile)]

    def list_sessions(self) -> list[SessionInfo]:
        """One ``SessionInfo`` per ``<prefix>*`` herdr session this engine owns.

        Filtered by ``session_prefix`` (this engine's namespace) — herdr's
        ``session list`` is global, so we must scope it, the way TmuxEngine scopes
        to its socket. For a running session, cwd/pid/alive come from its pane's
        process-info; a listed-but-not-running session (rare — ``kill`` deletes)
        reports ``alive=False`` with no cwd/pid. Sorted by profile.
        """
        infos: list[SessionInfo] = []
        for sess in parse_session_list(self._session_list_raw()):
            if not sess.name.startswith(self._session_prefix):
                continue
            profile = self._profile_of(sess.name)
            if not sess.running:
                infos.append(
                    SessionInfo(profile=profile, name=sess.name, alive=False)
                )
                continue
            pane = self._pane_of(sess.name)
            if pane is None:
                infos.append(
                    SessionInfo(profile=profile, name=sess.name, alive=False)
                )
                continue
            info = self._process_info(sess.name, pane.pane_id)
            infos.append(
                SessionInfo(
                    profile=profile,
                    name=sess.name,
                    alive=command_running(info),
                    cwd=pane.cwd,
                    pid=launched_pid(info),
                )
            )
        infos.sort(key=lambda s: s.profile)
        return infos
