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
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from absd import __version__
from absd.config import DaemonConfig
from absd.pool import Pool, PooledMessage, utc_now_iso
from absd.profiles import Profile
from absd.telegram import Conflict409, TelegramClient

log = logging.getLogger("absd.poller")

# Exact ack a pooled message earns (PLAN.md Step 1.3 — verbatim, do not reword).
POOL_ACK = "🗂 No session running — message saved to pool ({n}). Send ABS START to begin."
# ABS START is not wired until Step 1.5; it still pools, with a distinct note.
START_ACK = (
    "🗂 ABS START isn't wired up yet — that lands in a later update. "
    "Your message is saved to the pool ({n}) for when a session begins."
)

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


def is_status(text: str | None) -> bool:
    return normalize_command(text) == "ABS STATUS"


def is_pool_cmd(text: str | None) -> bool:
    return normalize_command(text) == "ABS POOL"


def is_start(text: str | None) -> bool:
    return normalize_command(text) == "ABS START"


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


# ---- offset persistence -------------------------------------------------------


class Poller:
    """IDLE_POLLING state machine for one profile (PLAN.md 4.1)."""

    def __init__(
        self,
        profile: Profile,
        client: TelegramClient,
        cfg: DaemonConfig,
        state_dir: Path,
    ) -> None:
        self.profile = profile
        self.client = client
        self.cfg = cfg
        self.pool = Pool(profile.pool_path)
        self.state_path = Path(state_dir) / f"poller-{profile.name}.json"
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

    # ---- one cycle -------------------------------------------------------

    async def poll_once(self) -> int:
        """Run one poll cycle. Returns the number of updates processed.

        Raises :class:`Conflict409` up to the caller (the run-loop handles the
        backoff). Does nothing and returns ``-1`` when the profile must yield
        (live session / blocked / off) — the caller distinguishes that from an
        empty poll to decide how long to sleep.
        """
        reason = self.profile.yield_reason()
        if reason is not None:
            log.debug("poller[%s] yielding: %s", self.profile.name, reason)
            return -1

        updates = await self.client.get_updates(
            offset=self.offset, timeout=self.cfg.poll_timeout_s
        )
        if not updates:
            return 0

        await self._process_batch(updates)
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

        # D9 read commands: answered from local state, never pooled.
        if is_status(ex.text):
            await self._reply_status(ex)
            return
        if is_pool_cmd(ex.text):
            await self._reply_pool(ex)
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
        await self._reply(ex, render_status(self.profile.name, pid, self.pool.count()))

    async def _reply_pool(self, ex: Extracted) -> None:
        await self._reply(ex, render_pool(self.pool.read_all()))

    # ---- run loop --------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run(
        self,
        max_cycles: int | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        """Loop ``poll_once`` until stopped (or ``max_cycles`` reached).

        ``sleep`` is injectable so tests advance without real time. 409 backs off
        exponentially (2,4,8… capped at ``reclaim_backoff_max_s``) and resets on
        the next successful poll; a yield naps :data:`YIELD_RECHECK_S`.
        """
        cycles = 0
        backoff: float | None = None
        while not self._stop.is_set():
            try:
                processed = await self.poll_once()
                backoff = None  # any non-409 outcome clears the backoff
                if processed == -1:
                    await sleep(YIELD_RECHECK_S)
            except Conflict409:
                backoff = (
                    BACKOFF_INITIAL_S
                    if backoff is None
                    else min(backoff * 2, self.cfg.reclaim_backoff_max_s)
                )
                log.debug("poller[%s] 409 — backing off %.1fs", self.profile.name, backoff)
                await sleep(backoff)
            except asyncio.CancelledError:
                raise
            except Exception:  # a poll error must not kill the task
                log.exception("poller[%s] cycle error", self.profile.name)
                await sleep(YIELD_RECHECK_S)

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
