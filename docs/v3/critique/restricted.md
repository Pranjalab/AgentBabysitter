# Critique — Stage 3: the restricted assistant profile

`abs restricted create <name>` provisions a locked-down helper bot: an everyday
assistant (Q&A, web lookups, notes, calculations) that **refuses to write or run
project code** and **cannot start/stop sessions**. The daemon keeps its in-sandbox
session alive. It also closes the 3.2 in-container gap: sandbox sessions can now
carry a model + system prompt (the restricted assistant is the first user).

## The four enforcement layers (as built)

Restricted is ONE switch (`restricted: true` in rc.json) that implies four layers:

1. **Injected system prompt (SOFT).** `absd-session --restricted` reads the bundled
   `restricted-prompt.txt` and passes it to `claude --append-system-prompt`. It gives
   the assistant persona, the allowed-tasks framing, the no-code refusal with the
   exact upgrade sentence, and "you cannot start/stop ABS sessions."
2. **Model = Haiku.** rc.json `model: "haiku"` → the pane command carries `--model
   haiku`. A small, cheap, fast model — appropriate for an always-on assistant and a
   further nudge away from heavy engineering work.
3. **Dedicated sandbox.** Its own container (`sandbox: "<name>"`), created at the same
   time. Any code the model *is* coaxed into running runs INSIDE that box — non-root
   `dev`, no `--privileged`, no docker socket, one bind-mounted workdir (5.6).
4. **No host credentials (the real boundary).** `abs sandbox create --no-creds`: the
   box gets NO copy of `~/.claude`. It cannot read the operator's bot token or any
   host credential, and Claude Code logs in SEPARATELY inside it (`abs restricted
   login`). This is what actually contains a restricted assistant.

## Honest note on SOFT enforcement (read this)

**Layer 1 is a prompt, and a prompt is guidance, not a wall.** A determined or clever
user can coax code out of a prompt-only guard — rephrasing, role-play, "just
pseudocode", incremental extraction. We do NOT claim the prompt prevents code
generation. What we claim is:

- The prompt makes the assistant *behave* as a non-coding helper in the overwhelming
  common case, and refuse with a clear, honest upgrade message.
- The **real containment is layers 3 + 4**: even if the model writes and runs code,
  it runs in a throwaway box that holds **no host credentials and no host files** (one
  bind-mounted workdir). The blast radius is the box, and the box has nothing of the
  operator's. Session/profile control is not reachable from inside at all — it lives
  in `abs.sh` on the host.

So: treat the prompt as UX ("this assistant declines to build"), and the sandbox +
no-creds as security. If the threat model is "untrusted user must not extract the
operator's secrets or run code with them", layer 4 carries that, not layer 1.

## Session control is operator-only

The restricted bot's users cannot start/stop/hand off sessions:

- While the session is LIVE, `ABS START`/`ABS EXIT` reach the in-box claude (the
  plugin delivers them), whose prompt declines them.
- During the rare down-window (relaunch / waiting on login), the DAEMON — not a flow —
  polls and refuses `ABS START`/`ABS EXIT`/`ABS OFF`/`ABS BLOCK` with a control-refusal
  message. A keep-alive profile NEVER calls the normal `_begin_flow`, so a restricted
  bot can never launch a normal host session.
- Start/stop/destroy are terminal-only (`abs restricted …`), operator-authenticated.

## Keep-alive + relaunch + login-needed cap

A `keep_alive: true` profile is not idle-polled. The daemon runs a keep-alive loop:

- **Up:** watch the session pane (pane-only liveness across the docker boundary, as in
  3.2 — the container-namespace pid is invisible to the host). No Telegram poll (the
  plugin inside the box owns the token).
- **Down → relaunch** into its box with Haiku + the restricted prompt, backing off
  `base·2^attempt` (capped). A session that survives the start grace is "healthy" and
  clears the failure streak.
- **Fast death (never came alive)** → count it. This is the not-logged-in / crash
  signal. After `restricted_relaunch_cap` consecutive fast deaths (default 3), STOP
  relaunching (no runaway loop) and DM the operator once: "run `abs restricted login
  <name>`". A pre-launch `creds_present` check (a `test -s` on the box's credentials
  file, never read) short-circuits the same way without even launching.
- **Once per down-transition:** the login-needed DM is guarded by a flag, cleared when
  the session comes back healthy — so a long down never spams. Login-expiry later is
  the same fast-death path → the same single message.
- **Recovery after login:** the loop watches the box's login state; when creds appear
  (absent → present), it resets the failure cap and relaunches on its own — no
  terminal `start` needed.

Events: `restricted_relaunch` (with sandbox/model/attempt) and
`restricted_login_needed` land in the timeline.

## The 3.2 in-container gap this closes

3.2 shipped `absd-session` as a bare `claude --channels` (no model, no system prompt).
Stage 3 extends it to consume `--model`, `--append-system-prompt`, and `--restricted`
(bundled prompt), and `build_sandbox_launcher_argv` grows matching options. This is a
general capability — a *normal* sandbox session passes none and its argv is byte-for-
byte the 3.2 shape (regression-tested). Image bumped **v2 → v3** (bakes the prompt at
`/usr/local/share/absd/restricted-prompt.txt`). Migration: `abs sandbox build
--rebuild`, then re-create sandboxes.

## Honest gaps / what breaks first

- **The prompt is bypassable (above).** Containment is the box + no-creds, not the
  prompt. If someone rebuilds the restricted assistant's box from an OLD image (no
  baked prompt), layer 1 silently vanishes — `absd-session --restricted` finds no
  prompt file and injects nothing. The other three layers still hold. `abs sandbox
  build --rebuild` fixes it; the tag bump forces a fresh box on new machines.
- **Q&A sent during the exact down-window** (a sub-second relaunch gap, or the whole
  login-needed wait) is answered by the daemon with an "offline" note, NOT the
  assistant — the daemon consumes those updates (advances the offset). A message in
  that window doesn't reach the assistant; the user retries once it's back. Acceptable
  for a "rare down-window"; documented.
- **`abs restricted login` runs real interactive `claude` in the box** — it is the
  operator's job (device-code/browser). Not automated (no real login in tests).
- **Login-expiry detection is death-based**, not proactive: an expired session
  fast-dies, which trips the same cap → login-needed. There's a lag of up to a few
  relaunch attempts before the operator is told. Fine in practice; not instant.
- **No per-container resource limits** (carried from 3.1/3.2). A restricted box can use
  host CPU/RAM.
- **A restricted profile shares the daemon's `max_sessions` cap** with normal sessions
  (its live session counts). A machine full of restricted assistants competes with
  normal ABS START launches for the cap — by design, but worth knowing.
