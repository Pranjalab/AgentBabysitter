# Critique — Observability (structured event log + consolidated dashboard)

Pulled ahead of 1.6/1.7 at the user's request. Two pieces: `absd/events.py`
(append-only JSONL trail) and `absd/status.py` (one read-only dashboard shared by
`abs status` and `abs daemon status`).

## What was built

- **`absd/events.py`** — `EventLog.emit(event, profile=?, level=?, **fields)`
  appends one JSON object per line to `~/.abs/daemon/events.jsonl` (0600),
  size-capped (5 MB, one `.old` roll), never raises. `iter_events(path, profile,
  since, event)` reads corruption-tolerantly (skips a torn trailing line). The 12
  event names are stable constants documented as a 4.4 spec surface.
- **Emission wired into every daemon path** (alongside, not replacing, the
  human-readable `daemon.log`): `daemon_start`/`daemon_stop` (`__main__`),
  `poller_state` on every session-state transition (`_set_state`), `handoff`,
  `session_start`, `session_end` (with reason + `lived_s`), `reclaim_done` (with
  `backoff_409s`), `message_pooled` (**update_id only, no text**), `command`,
  `menu_set`, `engine_kill`, `error`.
- **`absd/status.py`** — a pure `render_dashboard(DaemonInfo, [ProfileView])` plus
  `collect(abs_home, systemd)` that assembles it from status files + recents +
  `events.jsonl` + a best-effort `systemctl --user show`. `python -m absd.status`;
  bash `_v3_dashboard` shim appends it to `abs status` and drives `abs daemon
  status`, silent on a v2-only install.

## Metadata-only guarantee (the content-leak gate)

`message_pooled` carries `update_id` and nothing else — the text lives only in the
per-profile pool (0600). `test_pooling_emits_metadata_only` asserts the pooled
event has no `text` field AND that the literal message string never appears
anywhere in `events.jsonl`. So the event log can be shared for debugging (or read
by a future UI) without ever exposing what a user typed. No other event carries
free text except `error.message` (daemon-internal exception strings, never user
input) and `handoff.project` / recents labels (paths/labels the operator chose at
the terminal — not message content).

## Gate — can the second incident be reconstructed from `events.jsonl` alone?

Walking the second incident (daemon killed a live claude ~13 s after a fresh
launch) as if reading only the trail:

```
{"ts":"…T16:05:00Z","event":"daemon_start","version":"2.6.0","profiles":["default"]}
{"ts":"…T16:05:12Z","event":"command","profile":"default","name":"ABS START"}
{"ts":"…T16:05:20Z","event":"handoff","profile":"default","project":"/home/…/llm","mode":"normal","engine":"herdr","resume":false}
{"ts":"…T16:05:21Z","event":"session_start","profile":"default","pane_id":"w1:p1","pid":31875}
{"ts":"…T16:05:21Z","event":"poller_state","profile":"default","state":"session-live","from_state":"idle"}
   … (operator runs terminal abs / raw herdr attach — OUTSIDE the daemon) …
{"ts":"…T16:05:34Z","event":"poller_state","profile":"default","state":"reclaim","from_state":"session-live"}
{"ts":"…T16:05:34Z","event":"session_end","profile":"default","reason":"exited","lived_s":13}
{"ts":"…T16:05:34Z","event":"engine_kill","profile":"default","ok":true}
{"ts":"…T16:05:39Z","event":"reclaim_done","profile":"default","backoff_409s":0}
```

**What the trail makes obvious, unaided:** a session that was launched at 16:05:21
died `lived_s:13` later and was `engine_kill`ed — the exact "killed 13 s after
launch" signature. `session_start.pane_id`/`pid` pin *which* pane/process, and the
`poller_state session-live→reclaim` transition timestamps the decision. A future UI
or a debugging human reconstructs the daemon-side timeline **from this file alone**.

**Honest limit:** the *cause* (a terminal `abs`/raw attach clobbering `session.pid`
and resurrecting a pane) happens **outside** the daemon, so it is not a daemon
event — the trail shows the daemon's *view* (an unexpectedly fast, "clean-looking"
`exited`), not the external action. The reconstruction narrows it to "something
ended the session at 16:05:34"; correlating that with shell history / the engine's
own logs is the last mile. Post-fix, `session_end.reason` would read
`foreign_takeover_cleared` if a clobber occurred and the daemon *yielded* instead
of killing — which is itself the tell that the incident class recurred but was
handled. That reason is the single most valuable field for spotting a recurrence.

## What is NOT covered / gaps (what breaks first)

- **`session_end.reason` for a genuine crash vs clean exit is coarse.** It is
  `exited` for any post-alive death (crash or clean); the engine exit code isn't
  threaded through (that's Step 2.3). `failed_start` (never came alive) and
  `foreign_takeover_cleared` are distinguished; crash-vs-clean is not.
- **`dead-poller` detection is a heuristic** (status file not rewritten in >180 s
  while the daemon is running). At the 50 s production long-poll a genuinely-busy
  cycle is well under that, but a custom `poll_timeout_s > 180` would false-positive.
  Local-only, cosmetic.
- **Live-session reconstruction trusts the trail order.** `collect` replays
  handoff/session_start/session_end to find the current session; a truncated/rolled
  `events.jsonl` (past the 5 MB cap) could drop an old `session_start`, so a
  very-long-lived session whose start rolled off would show as "live" via the
  status file but with unknown engine/project. Rare; the status file still carries
  the live pid.
- **No cross-daemon-restart continuity for `lived_s`.** `_session_started_at` is
  in-memory (monotonic); a daemon restart mid-session loses it, so a `session_end`
  after a restart reports `lived_s` from the restart, not the original start. The
  `events.jsonl` `session_start` ts is still there for exact reconstruction — the
  field is just approximate. (Restart state re-derivation is Step 1.8.)
- **Rotation is crude** (size cap + one `.old`), same as `daemon.log`; full
  rotation is Step 1.8. A burst past 10 MB loses the oldest generation.
- **Single-writer assumption** holds because the daemon is one asyncio process
  (D1); the append is atomic per line, but two daemons (which D1 forbids) would
  interleave. Not defended against — out of scope by D1.
- **The dashboard's systemd query is Linux/user-unit specific**; on a box without
  `systemctl --user` the header reads "unknown" (still renders). macOS has no
  systemd path here — noted for the eventual cross-platform pass.

## Deviations from PLAN.md

- New modules `absd/events.py` + `absd/status.py`; `daemon-handoff.json` unchanged.
  The old `python -m absd --print-status` renderer stays (harmless) but bash now
  calls the richer `python -m absd.status`.
- `abs status` gains a v3 section appended after the v2 block (v2 output byte-for-
  byte unchanged); `abs daemon status` swaps its per-profile block for the shared
  dashboard, keeping the `systemctl status` line and the raw-file fallback.
