"""Agent-status watching — blocked-session notifications (PLAN.md Step 2.1, G8).

A remotely-started session that stops to ask a question is invisible from the
phone: the daemon has handed the bot to the session, the session is waiting for a
human, and nothing says so. This module turns an engine's agent-status signal into
an at-most-once "it is waiting for you" ping.

**herdr only, by design (D4).** herdr detects a running agent from the pane's
screen and exposes ``agent_status`` per pane; tmux has no equivalent, so on tmux
the feature is silently absent rather than half-working. The daemon feature-detects
via ``getattr(engine, "agent_status", None)`` — the capability is deliberately NOT
part of the ``Engine`` protocol, so a backend that cannot do this stays a complete,
conforming backend.

**Polling, not a socket subscription.** PLAN.md sketched an ``events.subscribe``
client on the session socket. Sampling ``herdr pane list`` on the existing
SESSION_LIVE watch tick is what shipped, because the debounce below is measured in
tens of seconds while the watch tick is ~3s — a sustained block is sampled many
times over — and sampling has no reconnect path, no second long-lived task, and no
window during which a dropped connection silently stops the pings. The one thing
push would buy (sub-second latency) is worth nothing against a ≥20s debounce.

Everything here is pure: :class:`StatusWatcher` is fed ``(status, now)`` and
answers with a :class:`Notice` or ``None``. The daemon owns the sampling and the
sending; this owns *when* a notification is warranted.

The debounce exists because herdr's blocked detection is deliberately strict and
takes a beat to match an approval UI (``docs/v3/herdr-recipes.md`` item 7): a
prompt can read ``idle`` for a moment before it reads ``blocked``, and a block that
the operator answers in five seconds at the terminal never needed a phone ping.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- the AgentStatus enum, as herdr reports it (recipes item 7) --------------

AGENT_IDLE = "idle"
AGENT_WORKING = "working"
AGENT_BLOCKED = "blocked"
AGENT_DONE = "done"
AGENT_UNKNOWN = "unknown"

#: Every value herdr's ``AgentStatus`` enum can take. Anything outside this set is
#: treated as :data:`AGENT_UNKNOWN` — a newer herdr adding a status must never make
#: the daemon fire a wrong notification.
AGENT_STATUSES = (
    AGENT_IDLE,
    AGENT_WORKING,
    AGENT_BLOCKED,
    AGENT_DONE,
    AGENT_UNKNOWN,
)

#: Notification kinds a :class:`StatusWatcher` can raise.
NOTICE_BLOCKED = "blocked"
NOTICE_DONE = "done"


def normalize(status: object) -> str:
    """Coerce a raw engine value to a known status. Pure.

    ``None``, a non-string, or an unrecognised string all read as
    :data:`AGENT_UNKNOWN` — "no information", which is distinct from any positive
    statement about the agent and is treated as such below.
    """
    if isinstance(status, str) and status in AGENT_STATUSES:
        return status
    return AGENT_UNKNOWN


@dataclass(frozen=True)
class Notice:
    """One notification the daemon should send. Pure value type.

    ``blocked_for_s`` is how long the block had been observed when the notice
    fired (i.e. at least the debounce); it is 0 for a ``done`` notice.
    """

    kind: str
    status: str
    blocked_for_s: float = 0.0


class StatusWatcher:
    """Decides when an agent-status stream warrants a notification.

    Fed one sample per watch tick via :meth:`feed`. Rules:

    - **Blocked** fires once per *episode*, after the status has been ``blocked``
      continuously for ``debounce_s``. Staying blocked afterwards never re-fires;
      the operator was told, and repeating it is a notification loop.
    - **An episode ends** on any *positive* non-blocked status (``idle``,
      ``working``, ``done``). The next block is a new episode and may fire again.
    - **``unknown`` is not an answer.** herdr reports ``unknown`` when it cannot
      see an agent at all — during redraws, and for a pane whose agent it has not
      matched. Treating it as "not blocked" would cancel a real, still-pending
      block on a single bad sample, so it holds the episode open instead: it
      neither ends one nor starts one.
    - **Done** fires at most once per transition *into* ``done``, and only when
      ``notify_done`` is on (off by default — a finished turn is usually followed
      by the session's own Telegram reply, which says it better).

    Reusable across sessions: :meth:`reset` returns it to its initial state, which
    the daemon calls when a session ends so a new session starts clean.
    """

    def __init__(
        self,
        debounce_s: float = 20.0,
        notify_blocked: bool = True,
        notify_done: bool = False,
    ) -> None:
        self.debounce_s = max(0.0, float(debounce_s))
        self.notify_blocked = bool(notify_blocked)
        self.notify_done = bool(notify_done)
        self._blocked_since: float | None = None
        self._blocked_notified = False
        self._last: str = AGENT_UNKNOWN
        self.reset()

    # -- state --------------------------------------------------------------

    def reset(self) -> None:
        """Forget the current episode and the last-seen status."""
        self._blocked_since = None
        self._blocked_notified = False
        self._last = AGENT_UNKNOWN

    @property
    def last_status(self) -> str:
        """The last *positive* status fed (``unknown`` samples do not replace it)."""
        return self._last

    @property
    def blocked_pending(self) -> bool:
        """True while a block is being timed but has not yet been notified."""
        return self._blocked_since is not None and not self._blocked_notified

    def blocked_for(self, now: float) -> float:
        """Seconds the current block has been observed (0.0 when not blocked)."""
        if self._blocked_since is None:
            return 0.0
        return max(0.0, now - self._blocked_since)

    # -- the decision -------------------------------------------------------

    def feed(self, status: object, now: float) -> Notice | None:
        """Consume one sample; return a :class:`Notice` to send, or ``None``.

        ``now`` is a monotonic-ish clock in seconds (the daemon passes its own
        injected clock, so tests advance time without sleeping).
        """
        current = normalize(status)

        if current == AGENT_BLOCKED:
            if self._blocked_since is None:
                self._blocked_since = now
            self._last = current
            if (
                self.notify_blocked
                and not self._blocked_notified
                and (now - self._blocked_since) >= self.debounce_s
            ):
                self._blocked_notified = True
                return Notice(
                    kind=NOTICE_BLOCKED,
                    status=current,
                    blocked_for_s=max(0.0, now - self._blocked_since),
                )
            return None

        if current == AGENT_UNKNOWN:
            # No information: hold whatever episode is open, decide nothing.
            return None

        # A positive non-blocked status: any blocked episode is over.
        self._blocked_since = None
        self._blocked_notified = False
        previous, self._last = self._last, current

        if current == AGENT_DONE and self.notify_done and previous != AGENT_DONE:
            return Notice(kind=NOTICE_DONE, status=current)
        return None
