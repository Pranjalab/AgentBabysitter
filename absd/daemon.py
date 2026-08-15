"""The per-profile poller — IDLE_POLLING from PLAN.md 4.1.

One :class:`Poller` runs per profile as an independent asyncio task (Step 1.3
focuses on a single profile; Step 1.4 adds the multi-profile stagger/tests, but
the loop-over-profiles structure already lives in :mod:`absd.__main__`).

What a poller does, per PLAN.md 4.1 / 5.1–5.3 / D9–D14:

  * **Yields when it must not poll.** Before every cycle it checks
    :meth:`Profile.should_poll` — a live ``session.pid``, ``ABS BLOCK``, or
    ``ABS OFF`` all suspend polling (it never even calls ``getUpdates``). This
    is the 4.1 terminal-launch yield: a session started at the desk writes
    ``session.pid`` before ``exec``, and the poller sees it and steps aside.
  * **Long-polls** ``getUpdates(timeout=poll_timeout_s)``. On HTTP 409 (the
    in-session plugin owns the token) it backs off exponentially, capped at
    ``reclaim_backoff_max_s`` — it does not treat 409 as a crash.
  * **Allowlist first (5.1 / D10).** An update whose sender is not on the
    profile allowlist is dropped: no reply (that would leak bot liveness), no
    pool entry — but the offset still advances past it.
  * **Fixed grammar (D9 / 5.2).** Exact-match, case-insensitive, whole-message
    commands only: ``ABS STATUS`` and ``ABS POOL`` are answered from local
    state; everything else — including unknown ``ABS``-prefixed text, near
    misses, and callback queries — is pooled and acknowledged. No free-text
    interpretation; the daemon has no LLM.
  * **Never loses, never duplicates (D14 / R2).** Within a batch it pools every
    non-command message and *persists the pool before advancing the offset*.
    A crash in that window re-delivers the batch on restart; dedupe on
    ``update_id`` keeps the redelivery from doubling the pool. The seam where a
    crash is simulated in tests is :attr:`Poller.on_batch_persisted`.

Pure rendering/parsing helpers are module-level so they unit-test without I/O
(PLAN.md 4.4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from absd import __version__
from absd import flow as flow_mod
from absd.agentstatus import NOTICE_BLOCKED, NOTICE_DONE, StatusWatcher
from absd.config import DaemonConfig, restricted_backoff
from absd.engines.base import Engine, EngineError, SessionHandle
from absd.events import (
    EVENT_COMMAND,
    EVENT_ENGINE_KILL,
    EVENT_ERROR,
    EVENT_HANDOFF,
    EVENT_MENU_SET,
    EVENT_MESSAGE_POOLED,
    EVENT_POLLER_STATE,
    EVENT_RECLAIM_DONE,
    EVENT_RESTRICTED_LOGIN_NEEDED,
    EVENT_RESTRICTED_RELAUNCH,
    EVENT_SESSION_BLOCKED,
    EVENT_SESSION_DONE,
    EVENT_SESSION_END,
    EVENT_SESSION_START,
    EVENT_STALE_HANDOFF,
    END_EXITED,
    END_FAILED_START,
    END_FOREIGN_TAKEOVER_CLEARED,
    END_SANDBOX_CHANNEL_DOWN,
    EventLog,
)
from absd.flow import ProjectOption
from absd.pool import Pool, PooledMessage, utc_now_iso
from absd.profiles import Profile
from absd.recents import Recents, RecentEntry
from absd.registry import Registry
from absd.telegram import Conflict409, TelegramClient, TelegramError

log = logging.getLogger("absd.poller")

# Exact ack a pooled message earns (PLAN.md Step 1.3 — verbatim, do not reword).
POOL_ACK = "🗂 No session running — message saved to pool ({n}). Send ABS START to begin."
# ABS START is not wired until Step 1.5; it still pools, with a distinct note.
# Retained for backward compatibility; the flow (Step 1.5) intercepts ABS START
# before it can pool, so this template is now used only if the flow is disabled.
START_ACK = (
    "🗂 ABS START isn't wired up yet — that lands in a later update. "
    "Your message is saved to the pool ({n}) for when a session begins."
)

# ---- ABS START flow / handoff / reclaim messages (Step 1.5) ------------------

# A stray inline-keyboard tap with no active flow (e.g. an expired menu). Answer
# the callback so the phone UI stops spinning; never pool raw callback data.
STALE_MENU_ANSWER = "That menu has expired — send ABS START to begin again."
# A half-finished flow that timed out (PLAN.md Step 1.5 flow timeout).
FLOW_EXPIRED_MSG = "⌛ ABS START timed out. Send ABS START again to begin."
# ABS START while a session is already live for this profile (attach, don't flow).
ALREADY_LIVE_MSG = (
    "▶️ A session is already running for this profile. Attach at the terminal:\n"
    "  abs attach {profile}"
)
# At the configured max_sessions cap (across all profiles).
AT_CAP_MSG = (
    "🛑 At the session limit ({cap}). End a running session before starting another."
)
# No project options at all (no registry, no workspace root).
NO_PROJECTS_MSG = (
    "No projects to start in. From the terminal, register one:\n"
    "  abs project add <dir>\n"
    "or set a workspace root:\n"
    "  abs config workspace-root <dir>"
)
# The HANDOFF confirmation (Step 1.5 / 2.2d). The attach hint is the SAFE wrapper
# `abs attach <profile>` — NOT the raw engine command. `abs attach` now resolves
# the owning engine (bug 2 fix) and re-checks liveness before exec, so it can't
# resurrect a stopped session; a raw `herdr session attach` in the confirmation is
# exactly what a user copy-pasted to revive a session and kill a live one.
HANDOFF_CONFIRM = (
    "🚀 Started {label} ({mode}).\n"
    "Send it a task here, or attach at the terminal:\n"
    "  abs attach {profile}"
)
# Sent AFTER reclaim completes (PLAN.md 4.1 — only after the token is free again).
SESSION_ENDED_MSG = "⏹ Session ended. I'm listening again — send ABS START to begin."
# Step 2.1 (G8, herdr-only): the session stopped to ask something and nobody knows.
# Says where it is stuck and both ways to answer — the terminal, or right here (the
# session owns the bot while it is live, so a reply in this chat reaches it).
BLOCKED_MSG = (
    "⏸ {label} is waiting for input or approval ({mins}).\n"
    "Answer here, or attach at the terminal:\n"
    "  abs attach {profile}"
)
# The opt-in counterpart (config `done_notify`), off by default.
DONE_MSG = "✅ {label} finished its turn."
# Sent when a create collides with a stale leftover engine session and the daemon
# self-heals (kills it + retries) — states what happened and what it's doing.
HANDOFF_STALE_RECOVER_MSG = (
    "♻️ Found a leftover session for this profile — cleaning it up and retrying…"
)
# HANDOFF failed to launch the engine session — actionable next step (live-demo
# finding: the old "(err). Nothing is running." was contradictory + unhelpful).
HANDOFF_FAILED_MSG = (
    "⚠️ Couldn't start the session: {err}\n"
    "Nothing is running now. Send ABS START to try again, or check "
    "`abs daemon logs` at the terminal."
)

# Login detection (Step 1.6). Pre-launch: credentials file absent/empty.
LOGIN_MISSING_MSG = (
    "⚠ Claude Code is not logged in on this machine. Please run `claude` in a "
    "terminal and complete login, then try ABS START again."
)
# Sandbox pre-launch: the HOST being logged in says nothing about the box. The
# copied credentials are a frozen snapshot that expires while the host's keep
# refreshing, so the box can start, poll Telegram, receive the message — and
# answer nothing at all. Naming the fix beats leaving the operator to discover
# it by attaching.
SANDBOX_LOGIN_MISSING_MSG = (
    "⚠ Claude Code is not logged in inside sandbox '{name}'. A session there "
    "would receive your messages and never reply.\n\n"
    "Log in once from a terminal:\n"
    "`abs sandbox login {name}`\n\n"
    "Then try ABS START again."
)
# Post-launch: the session never came alive (failed_start) — likely a login issue.
FAILED_START_MSG = (
    "⚠ Session ended immediately — possible login issue. Run `claude` in a "
    "terminal to check, then ABS START again."
)

#: A sandbox session started but its in-box Telegram channel never took over the
#: bot (live-test finding: the copied plugin metadata pointed at host paths, so the
#: marketplace failed to load in the box and `--channels` silently started nothing).
#: Without this the daemon sat in SESSION_LIVE, not polling, and every message the
#: operator sent vanished. Now we reclaim and say so.
SANDBOX_CHANNEL_DOWN_MSG = (
    "⚠ The sandbox session started but its Telegram channel never came up, so it "
    "couldn't receive messages — I've taken the bot back.\n\n"
    "Check the box from the terminal:  abs attach <profile>   (or: docker exec -it "
    "absd-sbx-<name> claude plugin list — the telegram plugin must be ✔ enabled)."
)

#: Matches the in-box Telegram plugin process (``bun run --cwd …/claude-plugins-
#: official/telegram/<ver> … start``) via ``pgrep -f`` inside the container.
#: Version-agnostic on purpose, so a plugin upgrade doesn't silently break the check.
IN_BOX_POLLER_PATTERN = "claude-plugins-official/telegram"

# Kill-ladder-while-idle acks (Step 1.7 / D11).
OFF_ACK = "📴 Inbound off. Re-enable from the terminal: abs on"
BLOCK_ACK = "🔒 Blocked. Re-establish it deliberately from the terminal: abs setup"
CLEAR_POOL_ACK = "🗑 Pool cleared ({n} message(s) removed)."

# Reboot notification (Step 1.8): a session that did not survive a machine/daemon
# restart. Sent once per boot, per profile; suppressed for blocked/off profiles.
REBOOT_NOTICE = (
    "🔄 Machine or daemon restarted; session {label} did not survive. "
    "Pool intact ({n}). ABS START to relaunch."
)

# A resume tap whose recorded folder has since been deleted (Step 2.2 edge case).
RECENT_GONE_MSG = "📁 That folder no longer exists — I've removed it from recents."

# ---- restricted assistant keep-alive (Stage 3) -------------------------------

# A restricted bot's user tried a session-control command (ABS START/EXIT/OFF/BLOCK):
# session + profile control is operator-only, done from the terminal. NOT the coding
# refusal (that happens inside the box via the injected prompt) — this is the daemon
# refusing to hand a restricted bot's user the session controls.
RESTRICTED_CONTROL_REFUSED = (
    "🔒 This is a restricted assistant — starting, stopping and configuring the "
    "session are operator-only, done from the terminal. I can't do that from here."
)
# A message to a restricted bot while its in-box assistant is DOWN (relaunching or
# waiting on login). Not pooled — a restricted profile has no ABS START to flush a
# pool with; the assistant handles new messages once it's back up.
RESTRICTED_OFFLINE_MSG = (
    "💤 The assistant is offline right now — the operator has been asked to bring it "
    "back. Please try again shortly."
)
# Sent to the operator's chat when the restricted session can't be kept alive because
# the box isn't logged in (initial, or expired). Once per down-transition (no spam).
RESTRICTED_LOGIN_NEEDED_MSG = (
    "🔐 Restricted assistant '{name}' needs login: run `abs restricted login {name}` "
    "at the terminal, then it will come back on its own."
)

# Telegram "/" menus (Step 2.2 pulled forward). The daemon registers the IDLE menu
# while polling and the SESSION menu at handoff, so the "/" list always matches
# what the bot can currently do. Debounced (only re-set when the menu changes).
MENU_IDLE = [
    {"command": "abs_start", "description": "Start a session"},
    {"command": "abs_status", "description": "Daemon + pool status"},
    {"command": "abs_pool", "description": "Show pooled messages"},
]
MENU_SESSION = [
    {"command": "abs_exit", "description": "End the session"},
    {"command": "usage", "description": "Usage report"},
]

# Poller session-state machine (PLAN.md 4.1). IDLE covers both real IDLE_POLLING
# and the terminal-launch yield (which stays IDLE and yields via should_poll);
# SESSION_LIVE / RECLAIM are the daemon-initiated handoff states.
STATE_IDLE = "idle"
STATE_SESSION_LIVE = "session-live"
STATE_RECLAIM = "reclaim"
# Restricted keep-alive (Stage 3): the in-sandbox session is not running and the
# daemon is (re)launching it or waiting on login. Distinct from IDLE — a keep-alive
# profile never idle-polls for a normal ABS START flow.
STATE_KEEP_ALIVE_DOWN = "keep-alive-down"

# How long to nap between engine-liveness checks while SESSION_LIVE (PLAN.md 4.1
# "every few seconds"). Short so a finished session reclaims promptly.
SESSION_WATCH_S = 3.0

# Backoff schedule for a getUpdates 409 (plugin owns the token): 2s, 4s, 8s…
# capped at cfg.reclaim_backoff_max_s (PLAN.md 4.1).
BACKOFF_INITIAL_S = 2.0
# How long to nap before re-checking should_poll() while yielding (session live
# / blocked / off). Kept short so a session ending resumes polling promptly.
YIELD_RECHECK_S = 2.0
# Pool preview caps for ABS POOL (D9 small surface): show at most N, truncate
# each line so a long message can't blow up a Telegram reply.
POOL_PREVIEW_MAX = 10
POOL_PREVIEW_TRUNC = 80

# A sleep function the loop calls — injected in tests so no real time passes.
SleepFn = Callable[[float], Awaitable[None]]


# ---- pure command grammar (D9) ------------------------------------------------


def normalize_command(text: str | None) -> str:
    """Normalize a message for exact command matching: strip surrounding
    whitespace and uppercase. Internal whitespace is preserved on purpose, so
    ``"ABS  STATUS"`` (double space) is NOT ``"ABS STATUS"`` (a near miss pools,
    per the Step 1.3 spec)."""
    if not text:
        return ""
    return text.strip().upper()


# The three "/" menu aliases (Step 2.2 pulled forward). Case-insensitive, exact
# whole-message, with the optional ``@botname`` suffix Telegram appends in groups
# stripped. This ONLY adds these three fixed aliases — the D9 grammar is otherwise
# unchanged; everything else still pools.
_SLASH_ALIASES = {
    "/ABS_START": "ABS START",
    "/ABS_STATUS": "ABS STATUS",
    "/ABS_POOL": "ABS POOL",
}


def canonical_command(text: str | None) -> str:
    """Normalized command with ``/abs_*`` slash aliases resolved to their ABS
    phrase. A slash alias must be a single token (``/abs_start`` or
    ``/abs_start@mybot``) — ``/abs_start extra`` is NOT a command (it pools, D9)."""
    norm = normalize_command(text)
    if norm.startswith("/"):
        tokens = norm.split()
        if len(tokens) == 1:
            base = tokens[0].split("@", 1)[0]  # strip @botname
            alias = _SLASH_ALIASES.get(base)
            if alias is not None:
                return alias
    return norm


def is_status(text: str | None) -> bool:
    return canonical_command(text) == "ABS STATUS"


def is_pool_cmd(text: str | None) -> bool:
    return canonical_command(text) == "ABS POOL"


def is_start(text: str | None) -> bool:
    return canonical_command(text) == "ABS START"


# Kill-ladder-while-idle commands (Step 1.7 / D11). Exact whole-message,
# case-insensitive; NO slash aliases / menu entries (destructive — deliberate
# text-only friction).
def is_off(text: str | None) -> bool:
    return normalize_command(text) == "ABS OFF"


def is_block(text: str | None) -> bool:
    return normalize_command(text) == "ABS BLOCK"


def is_clear_pool(text: str | None) -> bool:
    return normalize_command(text) == "ABS CLEAR POOL"


# ---- pure reply rendering -----------------------------------------------------


def render_status(profile_name: str, session_pid: int | None, pool_count: int) -> str:
    """The ``ABS STATUS`` reply: session liveness, pool depth, daemon version."""
    if session_pid is not None:
        session_line = f"Session: live (pid {session_pid})"
    else:
        session_line = "Session: idle — no session running"
    return (
        f"ABS STATUS — {profile_name}\n"
        f"{session_line}\n"
        f"Pool: {pool_count} message(s)\n"
        f"Daemon: absd {__version__}"
    )


def _truncate(text: str, limit: int = POOL_PREVIEW_TRUNC) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_pool(messages: list[PooledMessage]) -> str:
    """The ``ABS POOL`` reply: a numbered, truncated preview capped at
    :data:`POOL_PREVIEW_MAX` lines."""
    total = len(messages)
    if total == 0:
        return "🗂 Pool is empty."
    shown = messages[:POOL_PREVIEW_MAX]
    lines = [f"🗂 Pool ({total}):"]
    for i, m in enumerate(shown, start=1):
        lines.append(f"{i}. {_truncate(m.text)}")
    if total > len(shown):
        lines.append(f"… showing {len(shown)} of {total}. Send ABS START to act on them.")
    return "\n".join(lines)


# ---- daemon status rendering (Step 1.4 `abs daemon status`) -------------------


def _parse_iso(ts: str | None) -> "datetime | None":
    from datetime import datetime, timezone

    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fmt_age(then: str | None, now: "datetime | None" = None) -> str:
    """Human 'age' of a timestamp, e.g. ``12s`` / ``3m`` / ``never``."""
    from datetime import datetime, timezone

    dt = _parse_iso(then)
    if dt is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 90:
        return f"{secs}s"
    mins = secs // 60
    if mins < 90:
        return f"{mins}m"
    return f"{mins // 60}h"


def _sent_message_id(result: dict[str, Any] | None) -> int | None:
    """The ``message_id`` from a sendMessage result, or None. Pure and tolerant —
    a missing id only costs an in-place edit (the flow falls back to a new
    message), so it must never raise."""
    if not isinstance(result, dict):
        return None
    mid = result.get("message_id")
    return mid if isinstance(mid, int) else None


def _fmt_duration(secs: float) -> str:
    """A duration as prose for a phone notification: ``waiting 25s`` → ``25s``.

    Same buckets as :func:`_fmt_age` but taking seconds directly (the blocked
    notification knows its own elapsed time and has no timestamp to diff).
    """
    n = int(max(0.0, secs))
    if n < 90:
        return f"{n}s"
    mins = n // 60
    if mins < 90:
        return f"{mins}m"
    return f"{mins // 60}h"


def read_status_files(daemon_dir: Path) -> list[dict[str, Any]]:
    """Read every ``status-<profile>.json`` under ``daemon_dir`` (sorted by
    profile). Malformed files are skipped, never fatal (PLAN.md 4.4)."""
    import json

    daemon_dir = Path(daemon_dir)
    out: list[dict[str, Any]] = []
    if not daemon_dir.is_dir():
        return out
    for path in sorted(daemon_dir.glob("status-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def render_daemon_status(records: list[dict[str, Any]], now: "datetime | None" = None) -> str:
    """Render the per-profile status block for ``abs daemon status``.

    One line per profile: name, state, pool depth, and last-poll age. Reads only
    the daemon's persisted status files — it never itself touches Telegram."""
    if not records:
        return "  (no per-profile poller status yet)"
    lines: list[str] = []
    for rec in records:
        name = str(rec.get("profile", "?"))
        state = str(rec.get("state", "?"))
        pool_n = rec.get("pool_count", 0)
        age = _fmt_age(rec.get("last_poll_at"), now=now)
        extra = ""
        if state == "yielding-to-session" and rec.get("session_pid"):
            extra = f" (pid {rec['session_pid']})"
        lines.append(
            f"  {name}: {state}{extra}  pool={pool_n}  last-poll {age} ago"
        )
    return "\n".join(lines)


# ---- update extraction --------------------------------------------------------


@dataclass
class Extracted:
    """The fields the poller needs from one update, regardless of its kind."""

    update_id: int
    from_id: int | None
    chat_id: int | None
    text: str  # message text, or callback data, or "" for a non-text message
    callback_query_id: str | None = None


def extract(update: dict[str, Any]) -> Extracted | None:
    """Pull the relevant fields out of a message or callback_query update.

    Returns ``None`` only when there is no ``update_id`` (malformed) — such an
    update cannot advance the offset safely, so it is ignored.
    """
    uid = update.get("update_id")
    if not isinstance(uid, int):
        return None
    if "message" in update and isinstance(update["message"], dict):
        msg = update["message"]
        frm = msg.get("from") or {}
        chat = msg.get("chat") or {}
        text = msg.get("text")
        return Extracted(
            update_id=uid,
            from_id=frm.get("id"),
            chat_id=chat.get("id"),
            text=text if isinstance(text, str) else "",
        )
    if "callback_query" in update and isinstance(update["callback_query"], dict):
        cbq = update["callback_query"]
        frm = cbq.get("from") or {}
        cmsg = cbq.get("message") or {}
        chat = cmsg.get("chat") or {}
        data = cbq.get("data")
        return Extracted(
            update_id=uid,
            from_id=frm.get("id"),
            chat_id=chat.get("id"),
            text=data if isinstance(data, str) else "",
            callback_query_id=cbq.get("id"),
        )
    # Some other update kind (edited_message, my_chat_member, …): no actionable
    # content, but it still must advance the offset. Represent it with no sender
    # so the allowlist check drops it (no reply, no pool) and the offset moves.
    return Extracted(update_id=uid, from_id=None, chat_id=None, text="")


# ---- ABS START flow state (Step 1.5) -----------------------------------------


@dataclass
class Flow:
    """In-memory, per-profile ABS START conversation state (PLAN.md Step 1.5).

    Not persisted: a half-finished flow that outlives ``flow_timeout_s`` is
    dropped (a new ABS START restarts from scratch). ``started_at`` is a
    monotonic timestamp so the timeout is immune to wall-clock jumps.
    """

    chat_id: int
    step: str  # "recents" | "project" | "folder" | "mode" | "pool"
    options: list[ProjectOption]
    started_at: float
    chosen_path: str | None = None
    label: str = ""
    #: Recents offered on the "recents" step (list of RecentEntry). Empty otherwise.
    recents: list[Any] = field(default_factory=list)
    #: The launch decision awaiting pool selection (set when entering the "pool"
    #: step; finalized into ``_handoff_request`` once send/skip is chosen).
    pending: "HandoffRequest | None" = None
    #: The unforwarded pooled messages offered on the "pool" step.
    pool_msgs: list[Any] = field(default_factory=list)
    #: 1-based indices ticked on the pool step's multi-select keyboard (Step 2.2).
    pool_selected: set[int] = field(default_factory=set)
    #: The message carrying the pool keyboard, so a toggle edits it in place
    #: instead of sending a new screen per tap.
    pool_message_id: int | None = None
    #: The sandboxes offered on the "sandbox" step (list of (name, state)).
    sandboxes: list[Any] = field(default_factory=list)
    #: Chosen sandbox name (3.2) — the session runs INSIDE this container.
    sandbox: str | None = None


@dataclass
class HandoffRequest:
    """A completed flow's decision, handed to :meth:`Poller._do_handoff`."""

    chat_id: int
    project_path: str
    label: str
    away: bool
    #: True for a "▶ Resume" launch — appends ``--continue`` so claude resumes the
    #: previous conversation in that path (Step 2.2 resume-first).
    resume: bool = False
    #: Pooled messages joined as claude's initial prompt (Step 1.7 forwarding), or
    #: None. Delivered as one argv element via the launcher.
    initial_prompt: str | None = None
    #: update_ids of the forwarded pooled messages — marked forwarded_at ONLY after
    #: create_session succeeds (D14: skip/failed launch keeps them unforwarded).
    forward_ids: list[int] = field(default_factory=list)
    #: Sandbox name (3.2) — when set, the session runs INSIDE this container (the
    #: engine pane is a ``docker exec``); liveness is pane-only across the boundary.
    sandbox: str | None = None
    #: Model override (Stage 3) — passed to the in-container launcher as ``--model``
    #: (e.g. ``"haiku"`` for the restricted assistant). None → the account default.
    model: str | None = None
    #: Restricted marker (Stage 3) — the in-container launcher injects the bundled
    #: restricted system prompt (no-code refusal + no session control) via
    #: ``--restricted``. Only meaningful for a sandbox target.
    restricted: bool = False


# ---- offset persistence -------------------------------------------------------


def _pid_is_alive(pid: int | None) -> bool:
    """True if ``pid`` names a live process (``os.kill(pid, 0)``). A pid owned by
    another user (PermissionError) counts as alive; anything else is dead."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Poller:
    """Per-profile state machine (PLAN.md 4.1): IDLE_POLLING, plus the Step 1.5
    HANDOFF → SESSION_LIVE → RECLAIM path for daemon-initiated sessions."""

    def __init__(
        self,
        profile: Profile,
        client: TelegramClient,
        cfg: DaemonConfig,
        state_dir: Path,
        engine: Engine | None = None,
        script_path: str | None = None,
        session_count: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
        events: EventLog | None = None,
        creds_path: Path | None = None,
        sandbox_mgr: Any | None = None,
    ) -> None:
        self.profile = profile
        self.client = client
        self.cfg = cfg
        #: Shared structured event log (observability). None → emit is a no-op.
        self.events = events
        #: Claude Code credentials file — presence+size checked before HANDOFF
        #: (Step 1.6 login detection; contents NEVER read). Defaults to
        #: ``$HOME/.claude/.credentials.json``; injectable for tests.
        self.creds_path = creds_path or (
            Path(os.environ.get("HOME") or Path.home()) / ".claude" / ".credentials.json"
        )
        self.pool = Pool(profile.pool_path)
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / f"poller-{profile.name}.json"
        #: Session engine (tmux/herdr) used to launch/kill/watch a handoff session.
        #: None until wired (unit tests that never hand off leave it None).
        self.engine = engine
        #: Absolute path to abs.sh — the launcher the engine runs (PLAN.md 4.2).
        self.script_path = script_path
        #: Returns the current count of live sessions across ALL profiles, for the
        #: max_sessions cap (G5). Defaults to "just this engine's alive sessions".
        self._session_count_fn = session_count
        #: Injected monotonic clock (tests compress the flow timeout / grace).
        self._clock = clock
        #: The project registry + workspace-root config are re-read on demand so
        #: a terminal `abs project add` shows up without a daemon restart.
        self.registry = Registry(self.state_dir / "registry.json")
        #: Recent launches (resume-first ABS START, Step 2.2). Shared file, keyed
        #: by profile; re-read on demand so a terminal launch shows up live.
        self.recents = Recents(self.state_dir / "recents.json")
        #: Last "/" menu kind set for this bot ("idle"/"session"/None), persisted
        #: so the daemon doesn't re-call set_my_commands every cycle (debounce).
        self._menu_path = self.state_dir / f"menu-{profile.name}.json"
        self._menu_kind: str | None = self._load_menu_kind()

        #: ABS START conversation state; None when no flow is in progress.
        self.flow: Flow | None = None
        #: Set by the mode step; consumed by poll_once → _do_handoff.
        self._handoff_request: HandoffRequest | None = None
        #: Coarse session-state (STATE_*); drives run()'s dispatch.
        self.session_state = STATE_IDLE
        #: Handoff bookkeeping (set at HANDOFF, used by SESSION_LIVE / RECLAIM).
        self._handoff_chat_id: int | None = None
        self._handoff_at: float = 0.0
        self._session_seen_alive = False
        #: The pane the daemon launched into (Step 2.2c precise liveness) — engine
        #: liveness is checked at THIS pane, never "first pane". None for tmux.
        self._session_pane_id: str | None = None
        #: The RECORDED launcher pid (our claude), for the pid liveness signal and
        #: clobber detection — NOT the shared session.pid file, which a terminal
        #: launch can overwrite (the incident that killed a live session).
        self._launched_pid: int | None = None
        #: One-shot guard so the "session.pid clobbered by a foreign session" warning
        #: is logged once per takeover, not every watch cycle.
        self._foreign_warned = False
        #: Observability bookkeeping: wall-clock session start (for lived_s), the
        #: session_end reason decided at the RECLAIM transition, and the 409 backoff
        #: count during the current reclaim probe.
        self._session_started_at: float = 0.0
        self._session_end_reason: str = END_EXITED
        self._reclaim_409s: int = 0
        #: One-shot boot recovery guard (Step 1.8) — recovery + reboot notice run
        #: exactly once per process boot, never on an in-process supervisor restart.
        self._booted = False
        #: Sandbox manager (3.2) for sandbox-target sessions; None → no sandbox
        #: targets offered. Injectable (a fake in tests).
        self.sandbox_mgr = sandbox_mgr
        #: Whether the CURRENT sandbox session's in-box Telegram channel has been
        #: observed running (checked once, at the end of the start grace window).
        self._sandbox_channel_seen: bool = False
        #: The sandbox name of the CURRENT live session (3.2), or None for a normal
        #: host session. When set, liveness is pane-only (the container-namespace pid
        #: is invisible to the host) and the FIX-C host-pid clobber check is skipped.
        self._session_sandbox: str | None = None
        #: Restricted keep-alive bookkeeping (Stage 3). ``_restricted_launched_at`` is
        #: when the current in-sandbox session was launched (fast-death detection);
        #: ``_restricted_seen_alive`` flips true once it survives the start grace (a
        #: healthy session, clears the failure streak). ``_relaunch_failures`` counts
        #: CONSECUTIVE fast deaths (cap → stop + login-needed); ``_relaunch_attempt``
        #: drives the backoff (reset on a healthy session). ``_login_notified`` makes
        #: the login-needed message once-per-down-transition. ``_creds_seen`` tracks
        #: the box's login state so a completed `abs restricted login` resets the caps.
        self._restricted_launched_at: float = 0.0
        self._restricted_seen_alive = False
        self._relaunch_failures = 0
        self._relaunch_attempt = 0
        self._login_notified = False
        self._creds_seen: bool | None = None
        #: Blocked/done notification state (Step 2.1). The watcher is pure and
        #: reusable; it is reset at every session end so an episode can never leak
        #: from one session into the next. ``_session_label`` is the human name of
        #: the live session (project/sandbox), used in the notification text.
        self._status_watcher = StatusWatcher(
            debounce_s=cfg.blocked_debounce_s,
            notify_blocked=cfg.blocked_notify,
            notify_done=cfg.done_notify,
        )
        self._session_label: str | None = None
        #: When the stale-handoff sweep last ran (Step 2.3); None = never, which
        #: arms the timer rather than sweeping — boot recovery has just run, and a
        #: second opinion on the same state one cycle later adds nothing.
        self._last_stale_check: float | None = None
        #: Offset value committed by the single HANDOFF commit (test bookkeeping).
        self.handoff_committed_offset: int | None = None
        #: Per-profile status file (Step 1.4) — rewritten atomically each cycle so
        #: ``abs daemon status`` can show a per-profile line without touching the
        #: running daemon. Distinct from the offset file above.
        self.status_path = self.state_dir / f"status-{profile.name}.json"
        #: ISO-8601 timestamp of the last *successful* getUpdates (drives the
        #: "last-poll age" in status). None until the first poll returns.
        self.last_poll_at: str | None = None
        # Next offset to request; None means "unfiltered first poll". Loaded from
        # disk so a restart resumes where it committed (crash-safety).
        self.offset: int | None = self._load_offset()
        #: Test seam — awaited (if set) right after the pool is persisted and
        #: BEFORE the offset advances. A test sets this to raise, simulating a
        #: crash in exactly that window (PLAN.md 1.3 crash-safety check).
        self.on_batch_persisted: Callable[[], Awaitable[None]] | None = None
        self._stop = asyncio.Event()

    # ---- offset state ----------------------------------------------------

    def _load_offset(self) -> int | None:
        import json

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        val = data.get("offset") if isinstance(data, dict) else None
        return val if isinstance(val, int) else None

    def _save_offset(self, offset: int) -> None:
        import json
        import os

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps({"offset": offset}) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.state_path))
        os.chmod(self.state_path, 0o600)

    # ---- per-profile status file (Step 1.4) ------------------------------

    def write_status(self) -> None:
        """Write this profile's status snapshot atomically (0600) for
        ``abs daemon status`` (PLAN.md Step 1.4). Never raises: a status-write
        failure must never disturb the poll loop, so all errors are swallowed
        (the file is a convenience, not a source of truth)."""
        import json
        import os

        state = self.profile.state_label()
        # Restricted keep-alive profiles aren't idle-polling — reflect the keep-alive
        # state so `abs daemon status` reads truthfully (not a misleading "polling").
        if self.profile.keep_alive():
            state = (
                "restricted-live"
                if self.session_state == STATE_SESSION_LIVE
                else "restricted-down"
            )
        record = {
            "profile": self.profile.name,
            "state": state,
            "pool_count": self.pool.pending_count(),
            "session_pid": self.profile.live_session_pid(),
            "last_poll_at": self.last_poll_at,
            "updated_at": utc_now_iso(),
        }
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.status_path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps(record) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(str(tmp), str(self.status_path))
            os.chmod(self.status_path, 0o600)
        except OSError:
            log.debug("poller[%s] status write failed", self.profile.name)

    # ---- one cycle -------------------------------------------------------

    async def poll_once(self) -> int:
        """Run one poll cycle. Returns the number of updates processed.

        Raises :class:`Conflict409` up to the caller (the run-loop handles the
        backoff). Does nothing and returns ``-1`` when the profile must yield
        (live session / blocked / off) — the caller distinguishes that from an
        empty poll to decide how long to sleep.
        """
        # SESSION_LIVE / RECLAIM are driven by run() (or watch_once/reclaim called
        # directly in tests) — poll_once is the IDLE_POLLING primitive only.
        if self.session_state != STATE_IDLE:
            return -1

        # A half-finished ABS START flow that timed out is dropped here, once per
        # cycle, with a notice (Step 1.5 flow timeout).
        await self._expire_flow_if_needed()

        reason = self.profile.yield_reason()
        if reason is not None:
            log.debug("poller[%s] yielding: %s", self.profile.name, reason)
            return -1

        updates = await self.client.get_updates(
            offset=self.offset, timeout=self.cfg.poll_timeout_s
        )
        # A returned getUpdates (empty or not) is a completed poll — stamp it so
        # "last-poll age" in status reflects reality.
        self.last_poll_at = utc_now_iso()
        if not updates:
            return 0

        await self._process_batch(updates)
        # A flow that completed in this batch (mode chosen) requested a HANDOFF.
        # Run it now, after the batch — this is the "leave the poll loop" step of
        # 4.1: it commits the offset once and flips us to SESSION_LIVE.
        if self._handoff_request is not None:
            await self._do_handoff()
        return len(updates)

    async def _process_batch(self, updates: list[dict[str, Any]]) -> None:
        """Handle a batch: pool + reply, then advance the offset LAST.

        Ordering is the crash-safety contract (D14 / R2): every pool append is
        durable before :meth:`_save_offset` runs, and the ``on_batch_persisted``
        seam sits between the two so a test can crash there.
        """
        max_uid = self.offset - 1 if self.offset is not None else -1
        # Dedupe set is read once per batch; redelivered ids already in the pool
        # are not appended again (crash re-delivery safety).
        pooled_ids = self.pool.existing_update_ids()

        for update in updates:
            ex = extract(update)
            if ex is None:
                continue
            if ex.update_id > max_uid:
                max_uid = ex.update_id
            await self._handle(ex, pooled_ids)

        # --- pool is now fully persisted (each _handle append fsync'd) ---------
        if self.on_batch_persisted is not None:
            # Test seam: simulate a crash AFTER pooling, BEFORE offset advance.
            await self.on_batch_persisted()

        # --- only now do we commit the offset ---------------------------------
        if max_uid >= 0:
            new_offset = max_uid + 1
            self.offset = new_offset
            self._save_offset(new_offset)

    async def _handle(self, ex: Extracted, pooled_ids: set[int]) -> None:
        """Process a single update: allowlist → command → pool. (5.1 → D9 → G3.)"""
        # 5.1: allowlist FIRST. Unknown sender → local debug log, no reply, no
        # pool. Offset still advances (handled by the batch max_uid).
        if ex.from_id is None or not self.profile.is_allowed(ex.from_id):
            log.debug(
                "poller[%s] dropping update %s from non-allowlisted sender",
                self.profile.name,
                ex.update_id,
            )
            return

        # ABS START (Step 1.5): begins/restarts the flow. Highest priority so a
        # user can always restart a stuck flow by re-sending it.
        if is_start(ex.text):
            self._emit(EVENT_COMMAND, name="ABS START")
            await self._begin_flow(ex)
            return

        # An in-progress flow owns the conversation: route this update to it
        # (callback tap or numbered/folder-name text). A stray callback with no
        # active flow (expired menu) is answered but never pooled.
        if self.flow is not None:
            await self._advance_flow(ex)
            return
        if ex.callback_query_id is not None:
            try:
                await self.client.answer_callback_query(
                    ex.callback_query_id, text=STALE_MENU_ANSWER
                )
            except Exception:
                log.debug("poller[%s] stale-callback answer failed", self.profile.name)
            return

        # D9 read commands: answered from local state, never pooled.
        if is_status(ex.text):
            self._emit(EVENT_COMMAND, name="ABS STATUS")
            await self._reply_status(ex)
            return
        if is_pool_cmd(ex.text):
            self._emit(EVENT_COMMAND, name="ABS POOL")
            await self._reply_pool(ex)
            return

        # Kill-ladder-while-idle (Step 1.7 / D11): OFF / BLOCK stop this poller by
        # writing the SAME state files the terminal uses (recovery is terminal-only);
        # CLEAR POOL empties the pool. All three ack and emit a command event.
        if is_off(ex.text):
            self._emit(EVENT_COMMAND, name="ABS OFF")
            self.profile.set_off()
            await self._reply(ex, OFF_ACK)
            return
        if is_block(ex.text):
            self._emit(EVENT_COMMAND, name="ABS BLOCK")
            self.profile.set_blocked()
            await self._reply(ex, BLOCK_ACK)
            return
        if is_clear_pool(ex.text):
            self._emit(EVENT_COMMAND, name="ABS CLEAR POOL")
            n = self.pool.pending_count()
            self.pool.clear()
            await self._reply(ex, CLEAR_POOL_ACK.format(n=n))
            return

        # Everything else pools (D14). Dedupe on update_id (crash re-delivery).
        if ex.update_id in pooled_ids:
            log.debug(
                "poller[%s] update %s already pooled — skipping duplicate",
                self.profile.name,
                ex.update_id,
            )
            # Still ack? No: a redelivery means we already acked before the
            # crash; re-acking would double-message. Stay silent.
            return

        msg = PooledMessage(
            update_id=ex.update_id,
            from_id=ex.from_id,
            text=ex.text,
            received_at=utc_now_iso(),
        )
        count = self.pool.append(msg)
        pooled_ids.add(ex.update_id)
        # Metadata only — the update_id, NEVER the text (content stays in the pool).
        self._emit(EVENT_MESSAGE_POOLED, update_id=ex.update_id)

        template = START_ACK if is_start(ex.text) else POOL_ACK
        await self._reply(ex, template.format(n=count))

    # ---- outbound replies ------------------------------------------------

    async def _reply(self, ex: Extracted, text: str) -> None:
        """Send a text reply to the update's chat; answer a callback if present."""
        if ex.callback_query_id is not None:
            try:
                await self.client.answer_callback_query(ex.callback_query_id)
            except Exception:  # answering is best-effort; never fail the cycle
                log.debug("poller[%s] answer_callback_query failed", self.profile.name)
        if ex.chat_id is None:
            return
        await self.client.send_message(ex.chat_id, text)

    async def _reply_status(self, ex: Extracted) -> None:
        pid = self.profile.live_session_pid()
        await self._reply(ex, render_status(self.profile.name, pid, self.pool.pending_count()))

    async def _reply_pool(self, ex: Extracted) -> None:
        await self._reply(ex, render_pool(self.pool.unforwarded()))

    # ---- observability (structured event log) ----------------------------

    def _emit(self, event: str, *, level: str = "info", **fields: Any) -> None:
        """Emit a structured event for this profile (never raises)."""
        if self.events is None:
            return
        try:
            self.events.emit(event, profile=self.profile.name, level=level, **fields)
        except Exception:
            log.debug("poller[%s] event emit failed", self.profile.name)

    def _set_state(self, new: str) -> None:
        """Transition the session-state, emitting a ``poller_state`` event on an
        actual change (from_state → state)."""
        old = self.session_state
        if old == new:
            self.session_state = new
            return
        self.session_state = new
        self._emit(EVENT_POLLER_STATE, state=new, from_state=old)

    # ---- ABS START flow (Step 1.5) ---------------------------------------

    async def _answer_if_callback(self, ex: Extracted) -> None:
        """Answer a callback query so the phone UI stops spinning (best-effort)."""
        if ex.callback_query_id is None:
            return
        try:
            await self.client.answer_callback_query(ex.callback_query_id)
        except Exception:
            log.debug("poller[%s] answer_callback_query failed", self.profile.name)

    def _script_path(self) -> str:
        """Absolute path to abs.sh (the launcher the engine runs, PLAN.md 4.2).
        Defaults to the abs.sh beside the ``absd`` package (repo root)."""
        if self.script_path:
            return self.script_path
        return str(Path(__file__).resolve().parents[1] / "abs.sh")

    def _registered(self) -> list[tuple[str, str]]:
        """Registered projects as ``(path, label)`` tuples (re-read on demand so a
        terminal ``abs project add`` shows up with no daemon restart)."""
        return [(e.path, e.label) for e in self.registry.read()]

    def _workspace_root(self) -> Path | None:
        """The configured workspace root (D6), expanded — or ``None`` if unset or
        not an existing directory. Read fresh from ``config.json`` on demand so a
        terminal ``abs config workspace-root`` takes effect without a restart."""
        raw = self.cfg.workspace_root
        cfg_path = self.state_dir / "config.json"
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("workspace_root"), str):
                raw = data["workspace_root"]
        except (OSError, ValueError):
            pass
        if not raw:
            return None
        root = Path(raw).expanduser()
        return root if root.is_dir() else None

    def _session_count(self) -> int:
        """Live sessions across ALL profiles (for the max_sessions cap, G5)."""
        if self._session_count_fn is not None:
            return self._session_count_fn()
        if self.engine is not None:
            try:
                return sum(1 for s in self.engine.list_sessions() if s.alive)
            except EngineError:
                return 0
        return 0

    def _at_session_cap(self) -> bool:
        return self._session_count() >= self.cfg.max_sessions

    def _flow_expired(self) -> bool:
        return (
            self.flow is not None
            and (self._clock() - self.flow.started_at) > self.cfg.flow_timeout_s
        )

    async def _expire_flow_if_needed(self) -> None:
        """Drop a half-finished flow that outlived ``flow_timeout_s`` and tell the
        user (PLAN.md Step 1.5). Called once per poll cycle."""
        if not self._flow_expired():
            return
        assert self.flow is not None
        chat_id = self.flow.chat_id
        self.flow = None
        try:
            await self.client.send_message(chat_id, FLOW_EXPIRED_MSG)
        except TelegramError:
            log.debug("poller[%s] flow-expiry notice failed", self.profile.name)

    def _recents_for_flow(self) -> list[RecentEntry]:
        """The recents to offer on the resume screen (most-recent-first, capped to
        what the keyboard shows). Re-read on demand so a terminal launch appears."""
        return self.recents.list(self.profile.name)[: flow_mod.RECENTS_SHOWN]

    async def _begin_flow(self, ex: Extracted) -> None:
        """Start (or restart) the ABS START flow. Resume-first (Step 2.2): if there
        are recent launches, offer them (one-tap resume) + "🆕 New session"; with no
        recents, go straight to the project picker (Step 1.5 behavior).

        Refuses up front (no flow started) when a session is already live for this
        profile (attach hint) or at the max_sessions cap."""
        chat_id = ex.chat_id
        await self._answer_if_callback(ex)

        if self.profile.live_session_pid() is not None or self.session_state != STATE_IDLE:
            self.flow = None
            if chat_id is not None:
                await self.client.send_message(
                    chat_id, ALREADY_LIVE_MSG.format(profile=self.profile.name)
                )
            return
        if self._at_session_cap():
            self.flow = None
            if chat_id is not None:
                await self.client.send_message(
                    chat_id, AT_CAP_MSG.format(cap=self.cfg.max_sessions)
                )
            return

        recents = self._recents_for_flow()
        if recents:
            if chat_id is None:
                self.flow = None
                return
            await self.client.send_message(
                chat_id,
                flow_mod.render_recents_menu(recents),
                reply_markup=flow_mod.build_recents_keyboard(recents),
            )
            self.flow = Flow(
                chat_id=chat_id,
                step="recents",
                options=[],
                started_at=self._clock(),
                recents=recents,
            )
            return

        # No recents → the Step 1.5 project picker unchanged.
        await self._send_project_step(chat_id)

    async def _send_project_step(self, chat_id: int | None) -> None:
        """Send the project keyboard and enter the "project" step (or explain that
        there is nothing to start in). Shared by _begin_flow and "🆕 New session"."""
        options = flow_mod.enumerate_project_options(
            self._registered(), self._workspace_root(),
            with_sandbox=(self.sandbox_mgr is not None),
        )
        if not options:
            self.flow = None
            if chat_id is not None:
                await self.client.send_message(chat_id, NO_PROJECTS_MSG)
            return
        if chat_id is None:
            self.flow = None
            return
        await self.client.send_message(
            chat_id,
            flow_mod.render_project_menu(options),
            reply_markup=flow_mod.build_project_keyboard(options),
        )
        self.flow = Flow(
            chat_id=chat_id,
            step="project",
            options=options,
            started_at=self._clock(),
        )

    async def _send_mode_step(self, flow: Flow) -> None:
        flow.step = "mode"
        await self.client.send_message(
            flow.chat_id,
            flow_mod.MODE_MENU_TEXT,
            reply_markup=flow_mod.build_mode_keyboard(),
        )

    async def _send_sandbox_step(self, flow: Flow) -> None:
        """Sandbox sub-picker (3.2): existing sandboxes (name + state) + New."""
        sandboxes: list[Any] = []
        if self.sandbox_mgr is not None:
            try:
                sandboxes = [(s.name, s.state) for s in self.sandbox_mgr.list()]
            except Exception:
                sandboxes = []
        flow.sandboxes = sandboxes
        flow.step = "sandbox"
        await self.client.send_message(
            flow.chat_id,
            flow_mod.render_sandbox_menu(sandboxes),
            reply_markup=flow_mod.build_sandbox_keyboard(sandboxes),
        )

    async def _choose_sandbox_target(self, flow: Flow, name: str) -> None:
        """A sandbox was picked/created — the session will run inside it; the pane
        cwd is the sandbox's dedicated host folder. Then the mode step."""
        flow.sandbox = name
        wd = None
        if self.sandbox_mgr is not None:
            try:
                wd = self.sandbox_mgr.host_workdir(name)
            except Exception:
                wd = None
        flow.chosen_path = wd or ""
        flow.label = f"🏖 {name}"
        await self._send_mode_step(flow)

    def _credentials_present(self) -> bool:
        """Login precheck (Step 1.6): the credentials file exists and is non-empty.
        PRESENCE + SIZE ONLY via ``stat`` — the file is NEVER opened for read, so
        no credential content is ever read or logged."""
        try:
            return self.creds_path.stat().st_size > 0
        except OSError:
            return False

    async def _finalize_or_pool(
        self, flow: Flow, project_path: str, label: str, away: bool, resume: bool
    ) -> None:
        """Finalize a launch decision — but if the pool has unforwarded messages,
        first run the pool-selection step (Step 1.7 CORRECTION 1: selection is a
        FLOW step BEFORE handoff, since after handoff the plugin owns the token and
        the user's replies would go to the session, not the daemon)."""
        decision = HandoffRequest(
            chat_id=flow.chat_id, project_path=project_path, label=label,
            away=away, resume=resume, sandbox=flow.sandbox,
        )
        unforwarded = self.pool.unforwarded()
        if unforwarded:
            flow.pending = decision
            flow.pool_msgs = unforwarded
            flow.pool_selected = set()
            flow.step = "pool"
            texts = [m.text for m in unforwarded]
            sent = await self.client.send_message(
                flow.chat_id,
                flow_mod.render_pool_selection(texts),
                reply_markup=flow_mod.build_pool_keyboard(texts),
            )
            # Remember the screen so toggles edit it rather than stacking new ones.
            flow.pool_message_id = _sent_message_id(sent)
            return
        self._handoff_request = decision
        self.flow = None

    async def _repaint_pool(self, flow: Flow) -> None:
        """Redraw the pool screen after a tick (Step 2.2 multi-select).

        Edits the existing message so a burst of taps leaves one screen, not one
        per tap. If the edit fails — the message was deleted, or it is too old for
        Telegram to edit — fall back to sending a fresh screen and adopt that one,
        so the keyboard the user is looking at is always the live one.
        """
        texts = [m.text for m in flow.pool_msgs]
        body = flow_mod.render_pool_selection(texts, flow.pool_selected)
        kb = flow_mod.build_pool_keyboard(texts, flow.pool_selected)
        if flow.pool_message_id is not None:
            try:
                await self.client.edit_message_text(
                    flow.chat_id, flow.pool_message_id, body, reply_markup=kb
                )
                return
            except TelegramError:
                log.debug(
                    "poller[%s] pool screen edit failed — resending",
                    self.profile.name,
                )
        try:
            sent = await self.client.send_message(flow.chat_id, body, reply_markup=kb)
            flow.pool_message_id = _sent_message_id(sent)
        except TelegramError:
            # The tick is still recorded; the next action will repaint or decide.
            log.warning("poller[%s] could not repaint the pool screen", self.profile.name)

    async def _advance_flow(self, ex: Extracted) -> None:
        """Drive one step of the active flow from an update (callback or text)."""
        flow = self.flow
        assert flow is not None
        # For a callback update, extract() put the callback data in ex.text; a
        # plain message has no callback id and the number/name is ex.text.
        data = ex.text if ex.callback_query_id is not None else None
        text = ex.text

        if flow.step == "recents":
            choice = flow_mod.choose_recent(data, text, len(flow.recents))
            await self._answer_if_callback(ex)
            if choice is None:
                await self.client.send_message(
                    flow.chat_id,
                    "Tap a Resume button or 🆕 New session (or reply with a number).",
                )
                return
            if choice == "new":
                await self._send_project_step(flow.chat_id)  # fresh launch, no --continue
                return
            entry = flow.recents[choice]
            # The recorded folder may have been deleted since (edge case): drop it
            # and re-show the (updated) recents, or fall to the picker if empty.
            if not Path(entry.path).is_dir():
                self.recents.remove(self.profile.name, entry.path)
                await self.client.send_message(flow.chat_id, RECENT_GONE_MSG)
                remaining = self._recents_for_flow()
                if remaining:
                    flow.recents = remaining
                    await self.client.send_message(
                        flow.chat_id,
                        flow_mod.render_recents_menu(remaining),
                        reply_markup=flow_mod.build_recents_keyboard(remaining),
                    )
                else:
                    await self._send_project_step(flow.chat_id)
                return
            # A resume is a launch too — re-check the cap at tap time.
            if self._at_session_cap():
                self.flow = None
                await self.client.send_message(
                    flow.chat_id, AT_CAP_MSG.format(cap=self.cfg.max_sessions)
                )
                return
            # One-tap resume: skip project + mode, use the recorded mode, --continue.
            # Pool-selection may still run before the actual handoff.
            await self._finalize_or_pool(
                flow, entry.path, entry.label, away=(entry.mode == "away"), resume=True
            )
            return

        if flow.step == "project":
            opt = flow_mod.choose_project(data, text, flow.options)
            await self._answer_if_callback(ex)
            if opt is None:
                await self.client.send_message(
                    flow.chat_id,
                    "Pick a project by tapping a button or replying with its number.",
                )
                return
            if opt.kind == "newfolder":
                flow.step = "folder"
                await self.client.send_message(flow.chat_id, flow_mod.NEW_FOLDER_PROMPT)
                return
            if opt.kind == "sandbox":
                await self._send_sandbox_step(flow)  # 3.2 — the sandbox sub-picker
                return
            flow.chosen_path = opt.path
            flow.label = opt.label
            await self._send_mode_step(flow)
            return

        if flow.step == "sandbox":
            choice = flow_mod.choose_sandbox(data, text, len(flow.sandboxes))
            await self._answer_if_callback(ex)
            if choice is None:
                await self.client.send_message(
                    flow.chat_id, "Pick a sandbox by tapping a button or its number."
                )
                return
            if choice == "new":
                flow.step = "sandbox_name"
                await self.client.send_message(flow.chat_id, flow_mod.NEW_SANDBOX_PROMPT)
                return
            name = flow.sandboxes[choice][0]
            await self._choose_sandbox_target(flow, name)
            return

        if flow.step == "sandbox_name":
            if ex.callback_query_id is not None:
                await self._answer_if_callback(ex)
                await self.client.send_message(flow.chat_id, flow_mod.NEW_SANDBOX_PROMPT)
                return
            valid, verr = flow_mod.validate_folder_name(text)
            if not valid:
                await self.client.send_message(
                    flow.chat_id, f"❌ {verr}\n\n{flow_mod.NEW_SANDBOX_PROMPT}"
                )
                return
            name = text.strip()
            try:
                if not self.sandbox_mgr.image_present():
                    self.flow = None
                    await self.client.send_message(
                        flow.chat_id,
                        "⚠️ The sandbox image isn't built. From the terminal: abs sandbox build",
                    )
                    return
                self.sandbox_mgr.create(name)
            except Exception as exc:
                self.flow = None
                await self.client.send_message(
                    flow.chat_id, f"⚠️ Couldn't create sandbox {name!r}: {exc}"
                )
                return
            await self._choose_sandbox_target(flow, name)
            return

        if flow.step == "folder":
            if ex.callback_query_id is not None:
                await self._answer_if_callback(ex)
                await self.client.send_message(flow.chat_id, flow_mod.NEW_FOLDER_PROMPT)
                return
            ok, err = flow_mod.validate_folder_name(text)
            if not ok:
                await self.client.send_message(
                    flow.chat_id, f"❌ {err}\n\n{flow_mod.NEW_FOLDER_PROMPT}"
                )
                return
            root = self._workspace_root()
            if root is None:
                self.flow = None
                await self.client.send_message(flow.chat_id, NO_PROJECTS_MSG)
                return
            target = flow_mod.safe_join_under_root(root, text.strip())
            if target is None:
                # Regex passed but the structural jail refused it (D6). Never happens
                # for a valid single segment; this is the belt-and-suspenders guard.
                await self.client.send_message(
                    flow.chat_id, "❌ That folder name isn't allowed here."
                )
                return
            try:
                target.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                self.flow = None
                await self.client.send_message(
                    flow.chat_id, f"⚠️ Couldn't create the folder: {exc}"
                )
                return
            flow.chosen_path = str(target)
            flow.label = target.name
            await self._send_mode_step(flow)
            return

        if flow.step == "mode":
            mode = flow_mod.choose_mode(data, text)
            await self._answer_if_callback(ex)
            if mode is None:
                await self.client.send_message(
                    flow.chat_id,
                    flow_mod.MODE_MENU_TEXT,
                    reply_markup=flow_mod.build_mode_keyboard(),
                )
                return
            # Mode chosen — finalize (or run the pool-selection step first). The
            # actual HANDOFF runs in poll_once right after this batch (PLAN.md 4.1).
            await self._finalize_or_pool(
                flow,
                flow.chosen_path or "",
                flow.label or Path(flow.chosen_path or "").name,
                away=(mode == flow_mod.MODE_AWAY),
                resume=False,
            )
            return

        if flow.step == "pool":
            choice = flow_mod.parse_pool_choice(data, text, len(flow.pool_msgs))
            await self._answer_if_callback(ex)
            if choice is None:
                await self.client.send_message(
                    flow.chat_id,
                    "Reply `send all`, `send 1,3`, or `skip` (or tap a button).",
                )
                return

            # A tick/untick is not a decision — repaint the same screen and wait.
            if isinstance(choice, tuple) and choice[0] == "toggle":
                idx = choice[1]
                if idx in flow.pool_selected:
                    flow.pool_selected.discard(idx)
                else:
                    flow.pool_selected.add(idx)
                await self._repaint_pool(flow)
                return

            decision = flow.pending
            assert decision is not None
            if choice == "skip":
                selected: list[Any] = []
            elif choice == "all":
                selected = list(flow.pool_msgs)
            elif choice == "send":
                # The ticked set. "Send" with nothing ticked cannot reach here —
                # the button reads "Send all" and carries the `all` callback — but
                # a stale keyboard could, and an empty send must mean skip, not
                # silently everything.
                selected = [
                    flow.pool_msgs[i - 1]
                    for i in sorted(flow.pool_selected)
                    if 1 <= i <= len(flow.pool_msgs)
                ]
            else:  # list of 1-based indices
                selected = [
                    flow.pool_msgs[i - 1]
                    for i in choice
                    if 1 <= i <= len(flow.pool_msgs)
                ]
            if selected:
                decision.initial_prompt = flow_mod.build_offline_prompt(
                    [m.text for m in selected]
                )
                decision.forward_ids = [m.update_id for m in selected]
            self._handoff_request = decision
            self.flow = None
            return

    # ---- HANDOFF marker (PLAN.md 4.1 / 4.3) ------------------------------

    @property
    def handoff_marker_path(self) -> Path:
        return self.profile.profile_dir / "daemon-handoff.json"

    def _write_handoff_marker(
        self,
        project: str,
        mode: str,
        chat_id: int | None,
        pane_id: str | None = None,
        launcher_pid: int | None = None,
        sandbox: str | None = None,
    ) -> None:
        """Write ``daemon-handoff.json`` atomically (0600). Records timestamp,
        project, mode (PLAN.md 4.1), the chat id (so RECLAIM knows where to send the
        'session ended' note), the launched ``pane_id`` + ``launcher_pid`` for
        precise liveness (Step 2.2c), and — for a sandbox target (3.2) — the
        ``sandbox`` name so recovery uses PANE-ONLY liveness for it (the
        container-namespace pid is invisible to the host daemon)."""
        record = {
            "timestamp": utc_now_iso(),
            "project": project,
            "mode": mode,
            "chat_id": chat_id,
            "pane_id": pane_id,
            "launcher_pid": launcher_pid,
            "sandbox": sandbox,
        }
        try:
            self.profile.profile_dir.mkdir(parents=True, exist_ok=True)
            path = self.handoff_marker_path
            tmp = path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps(record) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(str(tmp), str(path))
            os.chmod(path, 0o600)
        except OSError:
            log.warning("poller[%s] could not write handoff marker", self.profile.name)

    def _read_handoff_marker(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.handoff_marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _clear_handoff_marker(self) -> None:
        try:
            self.handoff_marker_path.unlink()
        except OSError:
            pass

    async def check_stale_handoff(self) -> None:
        """Boot/startup guard (2.3 preview): a handoff marker with no live session
        is stale — the session it referred to is gone. Clear it and warn. (No
        'session ended' ping on startup: it could be an old marker, and a spurious
        phone message on every daemon boot would be worse than silence.)"""
        marker = self._read_handoff_marker()
        if marker is None:
            return
        if self.profile.live_session_pid() is not None:
            return
        log.warning(
            "poller[%s] stale handoff marker (no live session) — clearing",
            self.profile.name,
        )
        self._clear_handoff_marker()

    def _marker_is_stale(self, marker: dict[str, Any]) -> bool:
        """True when ``marker`` describes a session that is provably gone (2.3).

        BOTH liveness signals must be silent — the recorded engine pane and a live
        session pid — and the marker must be older than ``stale_handoff_after_s``,
        which is validated to be at least the launch grace so a session that is
        still booting is never swept.
        """
        if self._marker_age_s(marker) < self.cfg.stale_handoff_after_s:
            return False
        if self.profile.live_session_pid() is not None:
            return False
        pane_id = marker.get("pane_id")
        if isinstance(pane_id, str) and pane_id and self.engine is not None:
            try:
                if self.engine.is_alive(self.profile.name, pane_id=pane_id):
                    return False
            except EngineError:
                # Cannot tell → assume alive. A sweep that guesses would kill work.
                return False
        return True

    async def sweep_stale_handoff(self) -> bool:
        """Periodic stale-handoff reclaim (Step 2.3). Returns True if it swept.

        A handoff marker outlives its session when the daemon never observed the
        death — the machine slept, the session was hard-killed while the daemon was
        blocked, a reclaim was interrupted between killing the engine and clearing
        the marker. Boot recovery catches that at startup; this catches it in a
        daemon that has been up for weeks, so the profile cannot sit holding a
        marker for a session that no longer exists.

        Deliberately conservative — it is the one path here that can end a session
        the operator might still be using, so it acts only when the marker is old
        AND both liveness signals are silent AND we are not in SESSION_LIVE (where
        ``watch_once`` already owns the decision, with more context than this has).
        """
        if self.session_state == STATE_SESSION_LIVE:
            return False
        now = self._clock()
        if self._last_stale_check is None:
            self._last_stale_check = now
            return False
        if (now - self._last_stale_check) < self.cfg.stale_handoff_check_s:
            return False
        self._last_stale_check = now
        marker = self._read_handoff_marker()
        if marker is None or not self._marker_is_stale(marker):
            return False
        age = int(self._marker_age_s(marker))
        log.warning(
            "poller[%s] stale handoff marker (%ss old, no live session) — reclaiming",
            self.profile.name,
            age,
        )
        self._emit(EVENT_STALE_HANDOFF, level="warning", age_s=age)
        # Kill any engine leftover before dropping the marker: a herdr pane whose
        # command exited still holds the session name, and the NEXT ABS START would
        # collide with it ("already running").
        self._kill_engine_session()
        self._clear_handoff_marker()
        self._reset_session_fields()
        return True

    # ---- HANDOFF (PLAN.md 4.1, exact ordering) ---------------------------

    async def _do_handoff(self) -> None:
        """Execute HANDOFF for a completed flow, in the 4.1 order:

        (1) we have already left the poll loop (this runs after the batch, and the
            state flips to SESSION_LIVE so poll_once won't poll again); (2) one
            final ``getUpdates(timeout=0, offset=last+1)`` — the single offset
            commit; (3) write the handoff marker; (4) engine.create_session; (5)
            confirmation with the attach hint; (6) → SESSION_LIVE.
        """
        req = self._handoff_request
        self._handoff_request = None
        if req is None:
            return

        # (1a) Login precheck (Step 1.6), immediately before HANDOFF: the Claude
        # Code credentials file must exist and be non-empty (presence only — never
        # read). Missing → do NOT launch; tell the user to log in. The flow is
        # already complete, so we just abort cleanly (nothing started, nothing to
        # mark forwarded — D14 keeps the pool).
        if not self._credentials_present():
            log.warning("poller[%s] login precheck failed — not launching", self.profile.name)
            self._emit(EVENT_ERROR, level="error", where="login_precheck")
            if req.chat_id is not None:
                await self._safe_send(req.chat_id, LOGIN_MISSING_MSG)
            return

        # (1b) Same check, but for the BOX — the host credentials above say
        # nothing about what is inside a sandbox. `claude auth status` in the
        # container is the real answer; a present, non-empty credentials file is
        # not, because the copy expires while the host's keeps refreshing. This
        # is the bug that let a sandbox session read messages and answer none.
        # Only a definite False blocks: `login_ok` returns None when the probe
        # itself could not run, and an unrunnable probe must never stop a
        # session that would have worked.
        if req.sandbox is not None and self.sandbox_mgr is not None:
            probe = getattr(self.sandbox_mgr, "login_ok", None)
            if probe is not None and probe(req.sandbox) is False:
                log.warning(
                    "poller[%s] sandbox %s is not logged in — not launching",
                    self.profile.name, req.sandbox,
                )
                self._emit(EVENT_ERROR, level="error", where="sandbox_login_precheck")
                if req.chat_id is not None:
                    await self._safe_send(
                        req.chat_id, SANDBOX_LOGIN_MISSING_MSG.format(name=req.sandbox)
                    )
                return

        # (2) single final offset commit.
        if self.offset is not None:
            try:
                await self.client.get_updates(offset=self.offset, timeout=0)
            except Conflict409:
                # The plugin may already be registering; the offset is committed
                # locally and the plugin resumes from it. Not fatal to the handoff.
                pass
            except TelegramError:
                log.warning("poller[%s] handoff offset commit failed", self.profile.name)
            self.handoff_committed_offset = self.offset

        mode = "away" if req.away else "normal"
        # 3.2: a sandbox target runs the session inside the container — track it so
        # liveness is pane-only across the boundary (set BEFORE launch so watch/
        # recovery see it immediately).
        self._session_sandbox = req.sandbox
        # Fresh session → re-arm the in-box channel check (latches once observed).
        self._sandbox_channel_seen = False
        # (3) handoff marker (pre-launch, for crash-safety — pane recorded below).
        self._write_handoff_marker(
            req.project_path, mode, req.chat_id, sandbox=req.sandbox
        )
        self._emit(
            EVENT_HANDOFF,
            project=req.project_path,
            mode=mode,
            engine=(self.engine.name if self.engine is not None else None),
            resume=req.resume,
        )

        # (4) launch through the engine (never reimplement the launcher — 4.2),
        # self-healing over a stale leftover engine session (live-demo finding:
        # a herdr session survives claude's exit, so the next create collided).
        handle = await self._launch_session(req)
        if handle is None:
            self._set_state(STATE_IDLE)
            return

        # Record the launched pane (Step 2.2c precise liveness) + persist it in the
        # marker, so death is judged at THIS pane / THIS pid — never a resurrected
        # attach pane or a clobbered session.pid.
        self._session_pane_id = handle.pane_id
        self._launched_pid = handle.pid
        self._foreign_warned = False
        self._write_handoff_marker(
            req.project_path, mode, req.chat_id,
            pane_id=handle.pane_id, launcher_pid=handle.pid, sandbox=req.sandbox,
        )
        self._emit(EVENT_SESSION_START, pane_id=handle.pane_id, pid=handle.pid)

        # Mark forwarded pooled messages as delivered — ONLY now that the session
        # was created with them as its initial prompt (D14: a skip or a failed
        # launch never reaches here, so those stay unforwarded, kept in the pool).
        if req.forward_ids:
            self.pool.mark_forwarded(req.forward_ids)

        # (5) confirmation. The attach hint is the SAFE wrapper `abs attach` — it
        # resolves the owning engine and re-checks liveness before attaching, so it
        # cannot resurrect a stopped session (unlike a raw engine attach command,
        # which is what killed a session in the second incident — Step 2.2d).
        if req.chat_id is not None:
            await self._safe_send(
                req.chat_id,
                HANDOFF_CONFIRM.format(
                    label=req.label, mode=mode, profile=self.profile.name
                ),
            )

        # (6) SESSION_LIVE. Record for resume-first ABS START, and swap the "/"
        # menu to the in-session set now the session owns the bot (Step 2.2).
        self._record_recent(req)
        self._handoff_chat_id = req.chat_id
        # Step 2.1: a fresh episode per session, and the name the blocked ping uses.
        self._status_watcher.reset()
        self._session_label = req.label
        self._handoff_at = self._clock()
        self._session_started_at = self._handoff_at
        self._session_seen_alive = False
        self._session_end_reason = END_EXITED
        self._set_state(STATE_SESSION_LIVE)
        await self._ensure_menu("session")
        log.info(
            "poller[%s] HANDOFF complete — session launching in %s",
            self.profile.name,
            req.project_path,
        )

    async def _safe_send(self, chat_id: int, text: str) -> None:
        """send_message that never lets a Telegram hiccup abort a handoff/reclaim."""
        try:
            await self.client.send_message(chat_id, text)
        except TelegramError:
            log.debug("poller[%s] send failed", self.profile.name)

    def _create_session(self, req: HandoffRequest) -> "SessionHandle":
        """Build the launcher argv/env and create the engine session (4.2),
        returning its :class:`SessionHandle` (the launched pane, Step 2.2c).

        For a SANDBOX target (3.2) the pane command is ``docker exec -it
        absd-sbx-<name> absd-session <profile> [flags]`` — claude runs INSIDE the
        box (the plugin polls from there), and the docker-exec CLIENT is the host
        process the engine watches. Otherwise the pane runs the host abs.sh launcher.
        """
        if self.engine is None:
            raise EngineError("no session engine configured")
        if req.sandbox is not None:
            if self.sandbox_mgr is None:
                raise EngineError("no sandbox manager configured")
            self.sandbox_mgr.ensure_running(req.sandbox)  # container must be up
            # v4: sync ABS + this profile's pairing into the box, so the in-box
            # launcher can run the session THROUGH abs.sh (status bar, Bash guard,
            # ABS remote controls, a session.pid `abs exit` can signal). Must come
            # after ensure_running — it is all `docker exec`, which needs a running
            # container. Best-effort inside; a pre-v4 box just keeps the old launch.
            prepare = getattr(self.sandbox_mgr, "prepare_session", None)
            if prepare is not None:
                prepare(req.sandbox, self.profile.name)
            launcher = flow_mod.build_sandbox_launcher_argv(
                self.profile.name, req.away,
                resume=req.resume, initial_prompt=req.initial_prompt,
                model=req.model, restricted=req.restricted,
            )
            argv = self.sandbox_mgr.session_exec_argv(req.sandbox, launcher)
            # cwd is the sandbox's dedicated host folder (falls back to the marker
            # project path); env is unused inside the box (absd-session sets its own).
            cwd = Path(req.project_path) if req.project_path else Path.home()
            return self.engine.create_session(self.profile.name, cwd, argv, {})
        argv = flow_mod.build_launcher_argv(
            self._script_path(), self.profile.name, req.away,
            resume=req.resume, initial_prompt=req.initial_prompt,
        )
        env = {"ABS_HOME": str(self.profile.abs_home)}
        return self.engine.create_session(
            self.profile.name, Path(req.project_path), argv, env
        )

    def _record_recent(self, req: HandoffRequest) -> None:
        """Record a successful launch for resume-first ABS START (Step 2.2).
        Never raises — a recents-write failure must not disturb a live handoff."""
        try:
            self.recents.record(
                self.profile.name,
                req.project_path,
                req.label,
                "away" if req.away else "normal",
            )
        except Exception:
            log.debug("poller[%s] recents record failed", self.profile.name)

    async def _launch_session(self, req: HandoffRequest) -> "SessionHandle | None":
        """Create the session, self-healing over a stale leftover (live-demo bug).

        Returns the :class:`SessionHandle` on success, ``None`` on failure. On a
        create failure:
          * if a **genuinely live** session exists for this profile (live
            ``session.pid``) — do NOT clobber it: clear the marker, point the user
            at it with the attach hint, and abort (no retry).
          * otherwise the collision is a **stale engine leftover** (e.g. a herdr
            session whose pane shell outlived claude): tell the user, ``kill`` the
            stale session, and retry the create exactly ONCE. Final failure clears
            the marker and reports an actionable message.
        """
        try:
            return self._create_session(req)
        except Exception as exc:  # EngineError or anything the engine surfaces
            if self.profile.live_session_pid() is not None:
                log.warning(
                    "poller[%s] handoff aborted — a live session already exists",
                    self.profile.name,
                )
                self._clear_handoff_marker()
                if req.chat_id is not None:
                    await self._safe_send(
                        req.chat_id, ALREADY_LIVE_MSG.format(profile=self.profile.name)
                    )
                return None
            log.warning(
                "poller[%s] create failed (%s); no live session — cleaning stale "
                "engine session and retrying once",
                self.profile.name,
                exc,
            )
            if req.chat_id is not None:
                await self._safe_send(req.chat_id, HANDOFF_STALE_RECOVER_MSG)
            self._kill_engine_session()
            try:
                handle = self._create_session(req)
                log.info("poller[%s] handoff recovered after stale cleanup", self.profile.name)
                return handle
            except Exception as exc2:
                log.error(
                    "poller[%s] handoff launch failed after self-heal: %s",
                    self.profile.name,
                    exc2,
                )
                self._emit(
                    EVENT_ERROR, level="error", where="handoff_launch", message=str(exc2)
                )
                self._clear_handoff_marker()
                if req.chat_id is not None:
                    await self._safe_send(
                        req.chat_id, HANDOFF_FAILED_MSG.format(err=exc2)
                    )
                return None

    def _start_grace(self) -> float:
        """How long a just-launched session gets to come alive.

        A sandbox launch gets its own, larger window: it has to `docker cp` ABS
        into the box, run abs.sh, boot claude and start the Telegram plugin
        before the channel exists. The 30s host default was set when an in-box
        session was bare `claude`, and against a v4 box it declared healthy
        launches dead — then reclaiming one that kept running inside the
        container left an orphan poller stealing the operator's messages.
        """
        if self._session_sandbox is not None:
            return self.cfg.sandbox_start_grace_s
        return self.cfg.session_start_grace_s

    def _kill_engine_session(self) -> None:
        """Tear down this profile's engine session (best-effort; never raises).

        Used by RECLAIM (a dead session's engine artefacts must not linger — a
        herdr session survives its command's exit) and by handoff self-heal."""
        if self.engine is None:
            return
        try:
            self.engine.kill(self.profile.name)
            self._emit(EVENT_ENGINE_KILL, ok=True)
        except Exception as exc:
            log.warning(
                "poller[%s] engine kill failed: %s", self.profile.name, exc
            )
            self._emit(EVENT_ENGINE_KILL, ok=False, level="warning")
        # For a SANDBOX session the engine kill only reached the host-side
        # `docker exec` client. The claude it launched inside the container
        # survives that, and so does its Telegram plugin — which keeps polling
        # the bot. One getUpdates consumer per token means that orphan then
        # splits every update with whatever starts next, and half the operator's
        # messages disappear with nothing erroring. Reap it here, at the single
        # choke point RECLAIM and handoff self-heal both go through.
        self._kill_sandbox_session()

    def _kill_sandbox_session(self) -> None:
        """Reap the in-container half of a sandbox session. Best-effort."""
        box = self._session_sandbox
        if box is None or self.sandbox_mgr is None:
            return
        killer = getattr(self.sandbox_mgr, "kill_session", None)
        if killer is None:
            return
        try:
            ok = killer(box, self.profile.name)
        except Exception as exc:
            log.warning(
                "poller[%s] in-box session kill failed for %s: %s",
                self.profile.name, box, exc,
            )
            return
        if not ok:
            # Worth a warning rather than silence: a survivor here is exactly
            # what corrupts the next session's message delivery.
            log.warning(
                "poller[%s] in-box session in %s may have survived the kill",
                self.profile.name, box,
            )

    # ---- Telegram "/" menu (Step 2.2 pulled forward) ---------------------

    def _load_menu_kind(self) -> str | None:
        try:
            data = json.loads(self._menu_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        kind = data.get("kind") if isinstance(data, dict) else None
        return kind if kind in ("idle", "session") else None

    def _persist_menu_kind(self, kind: str) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._menu_path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps({"kind": kind}) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(str(tmp), str(self._menu_path))
            os.chmod(self._menu_path, 0o600)
        except OSError:
            log.debug("poller[%s] menu-kind persist failed", self.profile.name)

    async def _ensure_menu(self, kind: str) -> None:
        """Register the ``/`` menu for ``kind`` ("idle"/"session") via
        set_my_commands, but ONLY when it differs from the last one set (debounce —
        never hammer the API every cycle). The last kind is persisted so a restart
        doesn't re-register needlessly."""
        if self._menu_kind == kind:
            return
        commands = MENU_IDLE if kind == "idle" else MENU_SESSION
        try:
            await self.client.set_my_commands(commands)
        except TelegramError:
            log.debug("poller[%s] set_my_commands failed", self.profile.name)
            return
        self._menu_kind = kind
        self._persist_menu_kind(kind)
        self._emit(EVENT_MENU_SET, kind=kind)

    # ---- SESSION_LIVE / RECLAIM (PLAN.md 4.1) ----------------------------

    def _engine_pane_alive(self) -> bool:
        """Engine liveness for OUR launched session — targeted at the RECORDED
        pane (Step 2.2c), never "any pane". An attach-spawned second pane is
        therefore never mistaken for the session."""
        if self.engine is None:
            return False
        try:
            return self.engine.is_alive(self.profile.name, pane_id=self._session_pane_id)
        except EngineError:
            return False

    def _foreign_takeover(self) -> bool:
        """True when a TERMINAL launch overwrote ``session.pid`` with a *different,
        live* pid — a foreign session took over the bot (the incident's root cause).

        The daemon must yield to it (like a boot-detected terminal session) and must
        NOT reclaim/kill: our own recorded pane may still be running the original
        session, and the foreign pid is someone else's live process. Compares the
        on-disk ``session.pid`` against the pid we recorded at launch.

        3.2: for a SANDBOX session there is no host ``session.pid`` to clobber (the
        launcher runs in the container namespace), and a terminal ``abs`` cannot take
        over a container session's bot the same way — so this is always False, and
        liveness falls to the pane signal only."""
        if self._session_sandbox is not None:
            return False
        if self._launched_pid is None:
            return False
        disk = self.profile.session_pid_on_disk()
        if disk is None or disk == self._launched_pid:
            return False
        return _pid_is_alive(disk)

    def _sandbox_channel_up(self) -> bool:
        """True if the in-box Telegram plugin process is running inside the sandbox.

        The pane signal only proves the HOST-side ``docker exec`` client is alive; it
        says nothing about whether the session inside the box actually took over the
        bot. This is the missing half — ``pgrep -f`` in the container for the plugin
        process (:data:`IN_BOX_POLLER_PATTERN`).

        Fails OPEN (returns True) when it cannot tell — no sandbox manager, or a
        docker error. A probe we can't run must never be grounds for killing a
        session the operator may be using."""
        if self._session_sandbox is None or self.sandbox_mgr is None:
            return True
        try:
            return bool(
                self.sandbox_mgr.process_alive(self._session_sandbox, IN_BOX_POLLER_PATTERN)
            )
        except Exception:  # noqa: BLE001 - a probe failure must not kill a session
            return True

    def _sandbox_channel_failed(self) -> bool:
        """True when a SANDBOX session has run past the start-grace window without its
        in-box Telegram channel ever coming up — the silent black hole (see
        :data:`SANDBOX_CHANNEL_DOWN_MSG`). Checked once: as soon as the channel is
        seen, ``_sandbox_channel_seen`` latches and this stays False for the session.

        Only meaningful for a sandbox session; always False otherwise."""
        if self._session_sandbox is None or self._sandbox_channel_seen:
            return False
        if (self._clock() - self._handoff_at) <= self._start_grace():
            return False  # still inside the boot window — give the box time
        if self._sandbox_channel_up():
            self._sandbox_channel_seen = True
            return False
        return True

    def _session_dead(self) -> bool:
        """Reconcile BOTH liveness signals (PLAN.md 4.1 + Step 2.2c): the session is
        dead only when neither our RECORDED launcher pid is alive NOR the engine
        reports our RECORDED pane alive.

        3.2: a SANDBOX session's pid lives in the CONTAINER namespace — the host
        daemon cannot ``kill -0`` it — so its liveness is the ENGINE PANE ONLY (the
        host-side ``docker exec`` client: when claude exits inside the box, that
        client exits and the pane command ends). Host session.pid is meaningless here.

        Otherwise this uses the pid we *launched* (``_launched_pid``), not the shared
        ``session.pid`` file — a terminal launch can clobber that file, and trusting
        it made the daemon reclaim (kill) a live session."""
        if self._session_sandbox is not None:
            return not self._engine_pane_alive()
        if self._foreign_takeover():
            return False
        # PID signal: the recorded launcher pid; fall back to the disk pid only if we
        # never recorded one (e.g. a pre-2.2c marker with no launcher_pid).
        if self._launched_pid is not None:
            pid_alive = _pid_is_alive(self._launched_pid)
        else:
            pid_alive = self.profile.live_session_pid() is not None
        return not pid_alive and not self._engine_pane_alive()

    def _agent_status(self) -> str | None:
        """Sample the engine's agent status for OUR pane, or None if unavailable.

        Feature-detected (Step 2.1 / D4): a backend without ``agent_status`` — tmux —
        returns None forever and the feature is simply absent. Every failure mode
        (no engine, engine error, herdr not answering) also reads None, which
        :mod:`absd.agentstatus` maps to ``unknown`` — never to a positive status,
        so a probe hiccup can neither raise nor cancel a notification.
        """
        if self.engine is None:
            return None
        probe = getattr(self.engine, "agent_status", None)
        if probe is None:
            return None
        try:
            return probe(self.profile.name, self._session_pane_id)
        except (EngineError, OSError):
            return None

    async def _check_agent_status(self) -> None:
        """Sample agent status once and send whatever notice it warrants (2.1, G8).

        Called on each SESSION_LIVE watch tick, only while the session is actually
        alive. Cheap enough to sit on the 3s tick (one ``pane list``), and skipped
        entirely when both notifications are off, so the default-off ``done_notify``
        plus a ``blocked_notify off`` costs nothing at all.
        """
        if not (self.cfg.blocked_notify or self.cfg.done_notify):
            return
        notice = self._status_watcher.feed(self._agent_status(), self._clock())
        if notice is None:
            return
        label = self._session_label or self.profile.name
        if notice.kind == NOTICE_BLOCKED:
            mins = _fmt_duration(notice.blocked_for_s)
            log.info(
                "poller[%s] agent blocked for %s — notifying", self.profile.name, mins
            )
            self._emit(EVENT_SESSION_BLOCKED, blocked_for_s=int(notice.blocked_for_s))
            text = BLOCKED_MSG.format(label=label, mins=mins, profile=self.profile.name)
        elif notice.kind == NOTICE_DONE:
            self._emit(EVENT_SESSION_DONE)
            text = DONE_MSG.format(label=label)
        else:  # pragma: no cover - the vocabulary is closed
            return
        if self._handoff_chat_id is not None:
            await self._safe_send(self._handoff_chat_id, text)

    async def watch_once(self) -> bool:
        """One SESSION_LIVE liveness check. Returns True while the session is
        (still) alive or starting; on death transitions to RECLAIM and returns
        False.

        A launch that never comes alive within ``session_start_grace_s`` (crash,
        not-logged-in — full login detection is Step 1.6) is treated as a failed
        start and reclaimed, so the daemon can never wedge in SESSION_LIVE."""
        # FIX C: a foreign terminal session clobbered session.pid → yield to it, do
        # NOT reclaim/kill. We stay SESSION_LIVE and keep watching; reclaim only when
        # BOTH the foreign pid AND our recorded pane are dead (checked below once the
        # foreign pid clears).
        if self._foreign_takeover():
            if not self._foreign_warned:
                log.warning(
                    "poller[%s] session.pid clobbered by a foreign (terminal) session "
                    "(pid %s) — yielding, NOT reclaiming our recorded session",
                    self.profile.name,
                    self.profile.session_pid_on_disk(),
                )
                self._foreign_warned = True
                # A foreign takeover means the original session's clean end is no
                # longer ours to observe — record the reason for the eventual end.
                self._session_end_reason = END_FOREIGN_TAKEOVER_CLEARED
            self._session_seen_alive = True
            return True

        # Capture the launcher pid (for the clobber cross-check) the first time our
        # recorded pane is alive, from the session.pid abs.sh wrote — if the engine
        # couldn't report it at create time (herdr, before the command foregrounds).
        # Skipped for a sandbox session: there is no host session.pid to capture (the
        # pid lives in the container namespace), and liveness is pane-only anyway.
        if (
            self._session_sandbox is None
            and self._launched_pid is None
            and self._engine_pane_alive()
        ):
            disk = self.profile.session_pid_on_disk()
            if disk is not None:
                self._launched_pid = disk

        # A sandbox session can be "alive" by the pane signal while being DEAF: the
        # process runs but its in-box Telegram channel never took over the bot. The
        # daemon has already stopped polling, so every message would be silently
        # dropped. Reclaim deliberately (polling resumes) and tell the operator.
        if self._sandbox_channel_failed():
            log.warning(
                "poller[%s] sandbox session's in-box Telegram channel never came up "
                "within %.0fs — reclaiming so polling resumes",
                self.profile.name,
                self._start_grace(),
            )
            self._session_end_reason = END_SANDBOX_CHANNEL_DOWN
            self._set_state(STATE_RECLAIM)
            return False

        if not self._session_dead():
            self._session_seen_alive = True
            # Step 2.1: only while confirmably alive. Not during the start grace
            # (nothing is running to be blocked) and not under a foreign takeover
            # (that session is the terminal's, and its pane is not ours to read).
            await self._check_agent_status()
            return True
        if self._session_seen_alive:
            log.info("poller[%s] session ended — entering RECLAIM", self.profile.name)
            self._set_state(STATE_RECLAIM)
            return False
        if (self._clock() - self._handoff_at) > self._start_grace():
            log.warning(
                "poller[%s] launched session never came alive within %.0fs — reclaiming",
                self.profile.name,
                self._start_grace(),
            )
            self._session_end_reason = END_FAILED_START
            self._set_state(STATE_RECLAIM)
            return False
        return True  # still starting up

    async def reclaim(self, sleep: SleepFn = asyncio.sleep) -> None:
        """RECLAIM (PLAN.md 4.1): tear down the dead session's engine artefacts →
        grace delay → probe ``getUpdates(timeout=0)`` → on 409 back off (2,4,8…
        capped) → on success clear the marker, send the 'session ended' note, and
        resume IDLE_POLLING. The probe does NOT advance the offset, so any messages
        that arrived after the session died are re-fetched and pooled by the next
        IDLE poll (D14 — never lost).

        The engine ``kill`` up front is the live-demo fix: a herdr session's pane
        shell survives claude's exit, so without it the session stays "running" and
        the NEXT handoff fails with "already running". Killing here (before polling
        resumes; errors tolerated) guarantees the next ABS START starts clean."""
        # session_end: emit before the probe (the session is already gone) with the
        # reason decided in watch_once + how long it lived (metadata only).
        lived_s = 0
        if self._session_started_at:
            lived_s = max(0, int(self._clock() - self._session_started_at))
        self._emit(EVENT_SESSION_END, reason=self._session_end_reason, lived_s=lived_s)

        self._kill_engine_session()
        await sleep(self.cfg.reclaim_grace_s)
        backoff: float | None = None
        self._reclaim_409s = 0
        while not self._stop.is_set():
            try:
                await self.client.get_updates(offset=self.offset, timeout=0)
            except Conflict409:
                self._reclaim_409s += 1
                backoff = (
                    BACKOFF_INITIAL_S
                    if backoff is None
                    else min(backoff * 2, self.cfg.reclaim_backoff_max_s)
                )
                log.debug(
                    "poller[%s] reclaim 409 — backoff %.1fs", self.profile.name, backoff
                )
                await sleep(backoff)
                continue
            except TelegramError:
                # Transient network error: nap and retry (never abandon reclaim).
                await sleep(YIELD_RECHECK_S)
                continue
            break  # probe succeeded → the token is free again

        if self._stop.is_set():
            return
        self._clear_handoff_marker()
        if self._handoff_chat_id is not None:
            # A session that never came alive (failed_start) most likely hit a login
            # issue (Step 1.6b) — say so instead of the generic "session ended".
            if self._session_end_reason == END_FAILED_START:
                note = FAILED_START_MSG
            elif self._session_end_reason == END_SANDBOX_CHANNEL_DOWN:
                # Ran fine but was deaf — a login hint would be misleading here.
                note = SANDBOX_CHANNEL_DOWN_MSG
            else:
                note = SESSION_ENDED_MSG
            try:
                await self.client.send_message(self._handoff_chat_id, note)
            except TelegramError:
                pass
        self._emit(EVENT_RECLAIM_DONE, backoff_409s=self._reclaim_409s)
        self._reset_session_fields()
        # Back to idle-polling — restore the idle "/" menu (Step 2.2).
        await self._ensure_menu("idle")
        log.info("poller[%s] RECLAIM complete — polling resumes", self.profile.name)

    def _reset_session_fields(self) -> None:
        """Clear all SESSION_LIVE/RECLAIM bookkeeping and return to IDLE."""
        self._session_seen_alive = False
        self._handoff_chat_id = None
        self._session_pane_id = None
        self._launched_pid = None
        self._foreign_warned = False
        self._session_started_at = 0.0
        self._session_end_reason = END_EXITED
        # Step 2.1: an unfinished blocked episode dies with its session — carrying it
        # into the next one would ping about a block that no longer exists.
        self._status_watcher.reset()
        self._session_label = None
        self._session_sandbox = None  # 3.2: sandbox session ended (container survives)
        self._set_state(STATE_IDLE)

    # ---- boot recovery + reboot notification (Step 1.8) ------------------

    def _marker_age_s(self, marker: dict[str, Any]) -> float:
        """Seconds since the marker's timestamp (wall-clock), or 0 if unparseable —
        used to derive lived_s across a restart (closes the obs residual)."""
        from datetime import datetime, timezone

        ts = marker.get("timestamp")
        if not isinstance(ts, str):
            return 0.0
        try:
            then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 0.0
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())

    async def _send_reboot_notice(self, chat_id: int | None, label: str) -> None:
        """Send the reboot notice (once), suppressed for blocked/off profiles — and
        for keep-alive (restricted) profiles, which the daemon auto-relaunches (a
        'did not survive; ABS START to relaunch' note would be wrong and unactionable
        for a bot whose user cannot start sessions)."""
        if chat_id is None:
            return
        if self.profile.is_blocked() or self.profile.is_off() or self.profile.keep_alive():
            return
        await self._safe_send(int(chat_id), REBOOT_NOTICE.format(label=label, n=self.pool.pending_count()))

    async def boot_recover_and_notify(self) -> str:
        """Re-derive full session state from disk at boot (Step 1.8), and send the
        reboot notice for any session that did not survive. Runs once per process.

        Recovery matrix (marker × recorded pane/pid × session.pid):
          * marker + recorded pane/pid ALIVE → resume SESSION_LIVE with full FIX B/C
            precision (recorded pane_id + launcher_pid + chat_id; lived_s derived
            from the marker timestamp). Closes the 2.2a "not re-derived" residual.
          * marker + recorded DEAD → the session died while the daemon was down:
            kill any engine leftover, emit session_end/reclaim_done, clear the
            marker, send the reboot notice, go IDLE.
          * no marker + LIVE session.pid → a terminal session (yield; IDLE).
          * no marker + STALE session.pid → a terminal session that did not survive:
            reboot notice to the profile's chat, go IDLE.
          * nothing → IDLE.
        """
        if self._booted:
            return "already"
        self._booted = True

        marker = self._read_handoff_marker()
        if marker is not None:
            pane_id = marker.get("pane_id")
            launcher_pid = marker.get("launcher_pid")
            chat_id = marker.get("chat_id")
            project = str(marker.get("project") or "")
            label = Path(project).name or self.profile.name
            sandbox = marker.get("sandbox") if isinstance(marker.get("sandbox"), str) else None

            pane_alive = False
            if pane_id and self.engine is not None:
                try:
                    pane_alive = self.engine.is_alive(self.profile.name, pane_id=pane_id)
                except EngineError:
                    pane_alive = False
            # 3.2: a sandbox session's pid is container-namespace — NOT a host-alive
            # signal. Recover its liveness from the pane only.
            pid_alive = (
                False if sandbox is not None
                else (_pid_is_alive(launcher_pid) if isinstance(launcher_pid, int) else False)
            )

            if pane_alive or pid_alive:
                # Survived the restart → resume SESSION_LIVE, full precision.
                self._session_pane_id = pane_id if isinstance(pane_id, str) else None
                self._launched_pid = launcher_pid if isinstance(launcher_pid, int) else None
                self._session_sandbox = sandbox
                self._handoff_chat_id = chat_id
                # Step 2.1: the recovered session gets a clean episode and its label
                # back from the marker — a block observed before the restart was
                # either already notified or is gone, and we cannot tell which.
                self._status_watcher.reset()
                self._session_label = label
                self._session_started_at = self._clock() - self._marker_age_s(marker)
                self._session_seen_alive = True
                self._set_state(STATE_SESSION_LIVE)
                await self._ensure_menu("session")
                log.info(
                    "boot: poller[%s] recovered LIVE daemon session (pane %s)",
                    self.profile.name, pane_id,
                )
                return "session-live"

            # Recorded but DEAD → the session did not survive. Reclaim + notify.
            log.warning(
                "boot: poller[%s] daemon session did not survive — reclaiming",
                self.profile.name,
            )
            lived = int(self._marker_age_s(marker))
            self._kill_engine_session()
            self._emit(EVENT_SESSION_END, reason=END_EXITED, lived_s=lived)
            self._clear_handoff_marker()
            self._emit(EVENT_RECLAIM_DONE, backoff_409s=0)
            await self._send_reboot_notice(chat_id, label)
            self._reset_session_fields()
            await self._ensure_menu("idle")
            return "reclaimed"

        # No marker: distinguish a live terminal session from a dead (stale) one.
        if self.profile.has_stale_session_pid():
            log.warning(
                "boot: poller[%s] terminal session did not survive (stale pid)",
                self.profile.name,
            )
            await self._send_reboot_notice(self.profile.chat_id(), self.profile.name)
            return "stale-terminal"
        return "idle"

    # ---- restricted keep-alive (Stage 3) ---------------------------------

    async def _keep_alive_step(self, sleep: SleepFn) -> None:
        """One keep-alive cycle for a ``keep_alive`` (restricted) profile.

        The daemon does NOT idle-poll these — it keeps the in-sandbox session alive:
        watch it while up (pane-only liveness, no Telegram poll — the plugin inside
        the box owns the token); when it's down, relaunch (haiku + restricted prompt
        + its dedicated box) with backoff, or — if the box isn't logged in / it keeps
        dying — stop relaunching, tell the operator to log in (once), and poll the bot
        to refuse control commands / answer with an 'offline' note until it recovers.
        """
        if self.session_state == STATE_SESSION_LIVE:
            if self._restricted_alive():
                await sleep(SESSION_WATCH_S)
            else:
                await self._on_restricted_death()
            return

        if self.session_state != STATE_KEEP_ALIVE_DOWN:
            self._set_state(STATE_KEEP_ALIVE_DOWN)

        sb = self.profile.sandbox_name()
        paused = self.profile.is_paused()
        available = (
            self.engine is not None and self.sandbox_mgr is not None and sb is not None
        )

        creds = False
        if available and not paused:
            try:
                creds = bool(self.sandbox_mgr.creds_present(sb))
            except Exception:
                creds = False
            # A completed `abs restricted login` (creds absent → present) is a fresh
            # chance: clear the fast-death cap so keep-alive resumes on its own.
            if creds and self._creds_seen is False:
                self._relaunch_failures = 0
                self._relaunch_attempt = 0
                self._login_notified = False
            self._creds_seen = creds

        under_cap = self._relaunch_failures < self.cfg.restricted_relaunch_cap
        if available and not paused and creds and under_cap:
            await self._relaunch_restricted(sleep)
            return

        # Waiting: notify the operator if this is a login situation (not just paused),
        # then poll to keep the bot responsive (refuse control, offline note).
        if available and not paused and (not creds or not under_cap):
            await self._notify_login_needed()
        await self._keep_alive_wait_poll(sleep)

    def _restricted_alive(self) -> bool:
        """Liveness for the current restricted session — engine PANE only (the
        container-namespace pid is invisible to the host). Marks the session healthy
        (clearing the failure streak + login notice) once it survives the start grace."""
        if not self._engine_pane_alive():
            return False
        if (
            not self._restricted_seen_alive
            and (self._clock() - self._restricted_launched_at)
            >= self.cfg.session_start_grace_s
        ):
            self._restricted_seen_alive = True
            self._relaunch_failures = 0
            self._relaunch_attempt = 0
            self._login_notified = False
        return True

    async def _on_restricted_death(self) -> None:
        """The restricted session's pane died. Emit session_end, clean the engine
        leftover, count a FAST death (never came alive — the login/crash signal), and
        drop to KEEP_ALIVE_DOWN so the next cycle relaunches or waits."""
        lived = 0
        if self._restricted_launched_at:
            lived = max(0, int(self._clock() - self._restricted_launched_at))
        reason = END_EXITED if self._restricted_seen_alive else END_FAILED_START
        self._emit(EVENT_SESSION_END, reason=reason, lived_s=lived)
        self._kill_engine_session()
        if not self._restricted_seen_alive:
            self._relaunch_failures += 1
            log.warning(
                "poller[%s] restricted session died fast (%ds, streak %d/%d)",
                self.profile.name, lived, self._relaunch_failures,
                self.cfg.restricted_relaunch_cap,
            )
        self._session_pane_id = None
        self._launched_pid = None
        self._session_sandbox = None
        self._restricted_seen_alive = False
        self._set_state(STATE_KEEP_ALIVE_DOWN)

    def _restricted_request(self, sandbox: str) -> HandoffRequest:
        """Build the launch decision for the restricted session: its dedicated box,
        haiku (or the profile's configured model), and the restricted system prompt."""
        wd = ""
        if self.sandbox_mgr is not None:
            try:
                wd = self.sandbox_mgr.host_workdir(sandbox) or ""
            except Exception:
                wd = ""
        return HandoffRequest(
            chat_id=self.profile.chat_id(),
            project_path=wd,
            label=f"restricted {self.profile.name}",
            away=False,
            sandbox=sandbox,
            model=self.profile.model() or "haiku",
            restricted=True,
        )

    async def _relaunch_restricted(self, sleep: SleepFn) -> None:
        """(Re)launch the restricted session into its box, with backoff between
        consecutive attempts. On create failure, count it and stay down; on success,
        record the pane and go SESSION_LIVE (health is judged later in the watch)."""
        if self._relaunch_attempt > 0:
            delay = restricted_backoff(
                self._relaunch_attempt - 1,
                self.cfg.restricted_relaunch_backoff_s,
                self.cfg.restricted_relaunch_backoff_max_s,
            )
            if delay > 0:
                await sleep(delay)
                if self._stop.is_set():
                    return
        sb = self.profile.sandbox_name()
        if sb is None:
            return
        req = self._restricted_request(sb)
        # Pre-launch marker so a crash mid-launch recovers via the pane (sandbox).
        self._write_handoff_marker(req.project_path, "restricted", req.chat_id, sandbox=sb)
        try:
            handle = self._create_session(req)
        except Exception as exc:
            self._relaunch_failures += 1
            self._relaunch_attempt += 1
            log.warning(
                "poller[%s] restricted relaunch failed (%s); streak %d/%d",
                self.profile.name, exc, self._relaunch_failures,
                self.cfg.restricted_relaunch_cap,
            )
            self._emit(
                EVENT_ERROR, level="error", where="restricted_relaunch", message=str(exc)
            )
            return
        self._session_pane_id = handle.pane_id
        self._launched_pid = handle.pid
        self._session_sandbox = sb
        self._restricted_launched_at = self._clock()
        self._session_started_at = self._restricted_launched_at
        self._restricted_seen_alive = False
        self._relaunch_attempt += 1
        self._handoff_chat_id = req.chat_id
        self._write_handoff_marker(
            req.project_path, "restricted", req.chat_id,
            pane_id=handle.pane_id, launcher_pid=handle.pid, sandbox=sb,
        )
        self._emit(
            EVENT_RESTRICTED_RELAUNCH,
            sandbox=sb, model=req.model, attempt=self._relaunch_attempt,
        )
        self._emit(EVENT_SESSION_START, pane_id=handle.pane_id, pid=handle.pid)
        self._set_state(STATE_SESSION_LIVE)
        log.info("poller[%s] restricted session (re)launched in box %s", self.profile.name, sb)

    async def _notify_login_needed(self) -> None:
        """Tell the operator's chat the box needs login — once per down-transition."""
        if self._login_notified:
            return
        self._login_notified = True
        self._emit(
            EVENT_RESTRICTED_LOGIN_NEEDED,
            level="warning", sandbox=self.profile.sandbox_name(),
        )
        log.warning("poller[%s] restricted box needs login — notifying operator", self.profile.name)
        chat = self.profile.chat_id()
        if chat is not None:
            await self._safe_send(int(chat), RESTRICTED_LOGIN_NEEDED_MSG.format(name=self.profile.name))

    async def _keep_alive_wait_poll(self, sleep: SleepFn) -> None:
        """While a restricted session is DOWN and not relaunching, poll the bot so it
        stays responsive: refuse control commands, answer ABS STATUS, and reply with
        an 'offline' note to anything else (the in-box assistant is down). Advances the
        offset. Naps ``keep_alive_check_s`` so a login/pause change is re-evaluated."""
        try:
            updates = await self.client.get_updates(
                offset=self.offset, timeout=self.cfg.poll_timeout_s
            )
            self.last_poll_at = utc_now_iso()
        except Conflict409:
            # A session grabbed the token between checks — nap and re-evaluate.
            await sleep(self.cfg.keep_alive_check_s)
            return
        except TelegramError:
            await sleep(self.cfg.keep_alive_check_s)
            return
        if updates:
            await self._process_restricted_batch(updates)
        await sleep(self.cfg.keep_alive_check_s)

    async def _process_restricted_batch(self, updates: list[dict[str, Any]]) -> None:
        """Handle a down-window batch with the restricted grammar, advancing the
        offset last (same crash-order as _process_batch, minus pooling)."""
        max_uid = self.offset - 1 if self.offset is not None else -1
        for update in updates:
            ex = extract(update)
            if ex is None:
                continue
            if ex.update_id > max_uid:
                max_uid = ex.update_id
            await self._handle_restricted(ex)
        if max_uid >= 0:
            self.offset = max_uid + 1
            self._save_offset(self.offset)

    async def _handle_restricted(self, ex: Extracted) -> None:
        """Down-window handling for a restricted bot (session control is operator-only,
        the in-box assistant is offline). Allowlist first (D10)."""
        if ex.from_id is None or not self.profile.is_allowed(ex.from_id):
            return
        norm = normalize_command(ex.text)
        if is_start(ex.text) or is_off(ex.text) or is_block(ex.text) or norm == "ABS EXIT":
            self._emit(EVENT_COMMAND, name="restricted-control-refused")
            await self._reply(ex, RESTRICTED_CONTROL_REFUSED)
            return
        if is_status(ex.text):
            self._emit(EVENT_COMMAND, name="ABS STATUS")
            await self._reply(ex, self._restricted_status_text())
            return
        await self._reply(ex, RESTRICTED_OFFLINE_MSG)

    def _restricted_status_text(self) -> str:
        state = "live" if self.session_state == STATE_SESSION_LIVE else "starting up"
        return (
            f"🔒 Restricted assistant '{self.profile.name}' — {state}.\n"
            f"Model: {self.profile.model() or 'default'}\n"
            f"Daemon: absd {__version__}"
        )

    # ---- run loop --------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run(
        self,
        max_cycles: int | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        """Loop ``poll_once`` until stopped (or ``max_cycles`` reached).

        ``sleep`` is injectable so tests advance without real time. Dispatches on
        the session-state (PLAN.md 4.1): SESSION_LIVE watches engine liveness every
        few seconds; RECLAIM runs the grace/probe/backoff to completion; IDLE polls
        (409 backs off 2,4,8… capped at ``reclaim_backoff_max_s`` and resets on the
        next non-409 outcome; a yield naps :data:`YIELD_RECHECK_S`).
        """
        # Boot recovery (Step 1.8): re-derive full state from disk (resume a
        # surviving daemon session with FIX B/C precision, reclaim a dead one, and
        # send the reboot notice). Runs once per process; a no-op on restarts.
        await self.boot_recover_and_notify()
        if self.profile.keep_alive():
            # Restricted keep-alive (Stage 3): a recovered LIVE session is treated as
            # freshly healthy (watched pane-only); anything else drops to DOWN so the
            # keep-alive loop relaunches it. No ABS "/" menu — its controls are
            # operator-only.
            if self.session_state == STATE_SESSION_LIVE:
                self._restricted_launched_at = self._clock()
                self._restricted_seen_alive = True
                self._session_sandbox = self.profile.sandbox_name()
            else:
                self._set_state(STATE_KEEP_ALIVE_DOWN)
        else:
            # Register the "/" menu matching our state at boot (Step 2.2; debounced).
            await self._ensure_menu(
                "session" if self.session_state == STATE_SESSION_LIVE else "idle"
            )

        cycles = 0
        backoff: float | None = None
        while not self._stop.is_set():
            if self.profile.keep_alive():
                await self._keep_alive_step(sleep)
                self.write_status()
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    return
                continue
            if self.session_state == STATE_SESSION_LIVE:
                alive = await self.watch_once()
                if alive:
                    await sleep(SESSION_WATCH_S)
            elif self.session_state == STATE_RECLAIM:
                await self.reclaim(sleep=sleep)
            else:
                # Step 2.3: rate-limited itself; a no-op on all but the rare cycle
                # where a marker has outlived the session it described.
                await self.sweep_stale_handoff()
                try:
                    processed = await self.poll_once()
                    backoff = None  # any non-409 outcome clears the backoff
                    if processed == -1 and self.session_state == STATE_IDLE:
                        await sleep(YIELD_RECHECK_S)
                except Conflict409:
                    backoff = (
                        BACKOFF_INITIAL_S
                        if backoff is None
                        else min(backoff * 2, self.cfg.reclaim_backoff_max_s)
                    )
                    log.debug(
                        "poller[%s] 409 — backing off %.1fs", self.profile.name, backoff
                    )
                    await sleep(backoff)
                except asyncio.CancelledError:
                    raise
                except TelegramError:
                    # An operational Bot API / network error (already retried once
                    # in the client). Stay alive — a transient outage must not kill
                    # the task or trip the supervisor's loud restart — nap + retry.
                    log.warning(
                        "poller[%s] telegram error — retrying shortly", self.profile.name
                    )
                    await sleep(YIELD_RECHECK_S)
                # NOTE: any OTHER exception is unexpected (a bug / corrupted state).
                # It is deliberately NOT caught here — it propagates to the daemon's
                # per-profile supervisor (absd.__main__._run_profile), which logs it
                # loudly and RESTARTS this poller with backoff, isolated from the
                # other profiles' tasks (PLAN.md Step 1.4 supervision requirement).

            # Status snapshot for `abs daemon status` (best-effort, never raises).
            self.write_status()

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
