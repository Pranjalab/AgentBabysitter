# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] — 2026-08-10 — v3: the always-on daemon

- **The daemon says when a session is stuck.** A remotely-started session that
  stops to ask a question is invisible from the phone: the daemon has handed the
  bot to the session, the session is waiting for a human, and nothing says so.
  A block sustained past `blocked_debounce_s` (20s) now pings the chat that
  started it — once per block, not once per check. The debounce is the whole
  design: a block answered at the desk in five seconds never needed a phone ping,
  and herdr takes a beat to recognise an approval prompt as one.
  **herdr only.** tmux cannot report what the program in a pane is doing, so
  there the feature is absent rather than half-working — and `agent_status` is
  deliberately NOT on the `Engine` protocol, so tmux stays a complete backend
  instead of a non-conforming one.
  Where PLAN.md sketched an `events.subscribe` socket client, what shipped
  samples `pane list` on the existing 3s watch tick. Against a ≥20s debounce,
  push buys nothing but a reconnect path and a second long-lived task — and a
  window where a dropped connection silently stops the pings.
  `unknown` is treated as *no information*: it neither starts a block nor ends
  one. Reading it as "not blocked" would let one bad sample cancel a real,
  still-pending block, which is exactly when this feature needs to work.

- **Pooled messages are picked by tapping, not typing.** Each waiting message
  gets a ☐/☑ toggle and the action button becomes "📤 Send 2"; with nothing
  ticked it still reads "📤 Send all" and still means all, so the common case
  stays one tap rather than N+1. Ticks repaint the same screen in place instead
  of stacking one per tap, and past eight messages the toggles step aside for the
  typed protocol (`send 1,3`) rather than making the keyboard a wall.

- **A third dot on the status bar: `● Daemon`.** Green while `absd` has refreshed
  this profile's status file recently — i.e. the bot is being watched, so a
  message sent after this session ends will still land. It appears only where a
  daemon directory exists, so a v2 install sees the bar it always saw.

- **A handoff marker can no longer outlive its session.** Boot recovery caught
  that at startup; nothing caught it in a daemon that had been up for weeks after
  a machine slept, a session was hard-killed, or a reclaim was interrupted between
  killing the engine and clearing the marker — and the leftover collides with the
  next `ABS START`. A periodic sweep now reclaims one, conservatively: only when
  the marker is old, BOTH liveness signals are silent, and the poller is not in
  SESSION_LIVE (where `watch_once` already decides, with more context). An engine
  that cannot answer counts as alive — this is the one path that can end a session
  someone is using, and guessing there would destroy work. `stale_handoff_after_s`
  is validated to sit at or above the launch grace, so a session that is still
  booting can never be swept.

- **Lifecycle chaos test** (Step 2.3's critique gate): seeded random walks over
  message/start/hard-kill/daemon-restart/409/clock-jump, asserting after every
  single operation that no message consumed from Telegram exists nowhere in the
  pool, that the offset never goes backwards, and that a quiesced poller always
  converges to IDLE with no marker left behind. `ABS_CHAOS_ITERS=500` runs the
  plan's full length.
  Two of its invariants were vacuous when first written — one read the pool to
  decide what the pool should contain, the other pointed at `pool.json` when the
  file is `pool.jsonl` — and both survived deliberate mutants (a pool that drops
  one write in five, a sweep that never clears the marker). They are derived from
  the server side now, and every mutant is caught.

- **`abs config reply text|both|voice` — "always answer me in voice", enforced.**
  Asking the assistant to always reply with a voice note worked until the session
  got long and the instruction drifted out of the model's attention, which is the
  failure mode of every standing preference kept in a prompt. Reply mode is stored
  state and the session hooks act on it: `both` mirrors every outbound Telegram
  message as a voice note from PostToolUse, and `voice` intercepts the message at
  PreToolUse, speaks it, and blocks the text so the voice note *is* the reply. The
  model is told which mode is on — but only so it doesn't ALSO call `abs say` and
  send the same sentence twice. Enforcement never depends on it.
  Markdown is stripped before speaking (a URL read aloud is a minute of alphabet),
  the same sentence is never spoken twice within five minutes, and synthesis is
  serialised behind a lock and detached from the hook, which has a 5s budget
  against TTS's ~30s.
  `voice` still lets a message through as text when it carries a code block, a
  link, or an attachment — a voice note cannot carry any of them, and a blocked
  message is one the operator simply never receives. Same reason it refuses to
  engage at all on a machine that can't speak: the failure mode of this feature
  must be "text as usual", never silence.
  Three ways it could still have lost a message, all found in review and closed
  before release: the repeat-suppressor applied in `voice` mode too, so saying the
  same sentence twice inside five minutes blocked the text and skipped the audio;
  a failed synthesis recorded itself as "already said", so the retry vanished as
  well; and a synthesis that failed after the text was suppressed produced nothing
  at all — it now falls back to sending the words as text. Reply bodies also go to
  the speech engine on stdin rather than in argv, where `/proc/<pid>/cmdline`
  exposed them to every user on the box for the ~30s synthesis takes.

- **The terminal pickers are arrow-key menus now.** Every list `abs` offers —
  which bot, which session to resume, which project, which sandbox, where a new
  bot should run — is one `menu_select` with ↑/↓ (or k/j) moving a highlight,
  Enter taking it, and Esc/q backing out. Typing the number still works, so
  nothing anyone already does stops working, and the chosen row collapses to a
  single line so the scrollback keeps the decision without the whole menu.
  Long rows are truncated to the terminal width rather than wrapping — a wrapped
  row would desync the cursor arithmetic and smear the menu on every redraw.
  It degrades instead of breaking: no terminal on stderr, `TERM=dumb`, no
  `/dev/tty`, or `ABS_NO_TUI=1` all fall back to the old numbered prompt, which
  is what keeps these functions usable under `docker exec` without `-t`, over a
  pipe, and in CI.
  Row widths are counted in printed columns, not characters, because an emoji or a
  CJK glyph costs two — and every one of these menus carries an emoji. Labels come
  from folder names, so they are stripped of newlines and of every escape sequence
  except colour; a row that splits in two puts the highlight somewhere other than
  where it is drawn, which is worse than a smear. Verified against real bash 3.2
  (what stock macOS ships), where the escape-sequence timeout had to stop being
  fractional — 3.2 rejects that outright and the arrow keys did nothing.

- **Fixed: a reclaimed sandbox session left an orphan poller stealing messages.**
  Live-testing symptom: roughly half the operator's replies stopped arriving, with
  nothing erroring. `engine.kill()` closes the pane's process group on the *host*,
  and a sandbox session is a `docker exec` client — killing that client does **not**
  kill the claude it started inside the container, nor its Telegram plugin (verified
  against a real container, not assumed). That orphan kept polling the bot, and since
  Telegram gives each update to whichever consumer asks first, the next session saw
  only a random half of them. Teardown now reaps the in-container half too, via the
  in-box `session.pid`, and warns if a survivor is detected instead of passing in
  silence. The reap is TERM, then wait, then KILL: the first version asked whether
  anything had survived immediately after sending TERM, which both reported healthy
  shutdowns as survivors and let a claude that ignores TERM live on — the exact
  orphan this exists to prevent.
- **Fixed: the launch grace window predated ABS-in-the-box.** `session_start_grace_s`
  is 30s, set when an in-box session was bare `claude`. A v4 box must `docker cp` ABS
  in, run abs.sh, boot claude and start the plugin before its channel exists, so
  healthy launches were being declared dead — which is what triggered the orphan
  above. Sandbox launches now get their own `sandbox_start_grace_s` (120s); host
  sessions keep the tight 30s.

- **A sandbox that isn't logged in now says so.** Found in live testing: a sandbox
  session started, received the operator's Telegram message, and answered nothing —
  Claude *inside* the box was not authenticated. Nothing warned, because the
  pre-launch check tests the **host** credentials, and the only box-side check asks
  whether the credentials *file* exists. It does: the copy made at `create` is a
  frozen snapshot that expires while the host's keeps refreshing, so it stays present
  and well-formed while authentication fails. The daemon now asks `claude auth status`
  inside the box and refuses the handoff with the exact fix rather than launching a
  session that will read messages and reply to none. A probe that cannot run counts as
  *unknown* and fails open.
- **`abs sandbox login <name>`** — log Claude in inside a box, one time. Mirrors
  `abs restricted login`, which was previously the only one of the pair that existed.

The big v3 story: `abs` was a passenger — when Claude Code wasn't running, the bot
was deaf. v3 adds **`absd`**, a background systemd user daemon that polls every
idle bot so you can start, resume, and manage sessions entirely from Telegram, and
picks them up at the desk. Built in Python (asyncio, stdlib + aiohttp); the CLI
stays bash. All behind a small fixed grammar with the security model unchanged.

### Added
- **Always-on daemon (`absd`)** — one systemd user service manages every profile;
  polls idle bots, enforces the profile allowlist itself, and answers a small
  fixed command grammar. `abs daemon install|start|stop|status|logs`.
- **Remote session start — `ABS START`.** From Telegram: pick a project (registered
  projects + workspace-root children + "➕ New folder", jailed under one configured
  root) → pick Normal / Away → the daemon launches Claude Code in a persistent,
  attachable session and confirms with `abs attach <profile>`.
- **Session engines** — herdr (preferred) or tmux (reference), interchangeable
  behind one adapter; `abs sessions` / `abs attach [profile]` (searches both).
  Precise per-pane liveness so an attach can never be mistaken for the session.
- **Message pool** — messages that arrive while nothing runs are kept per profile
  (never dropped), acknowledged, and offered to **forward** as a starting
  session's opening prompt (`send all` / `send 1,3` / `skip`). `ABS POOL`,
  `ABS CLEAR POOL`.
- **Resume-first start**, both doors: Telegram `ABS START` offers up to 3 one-tap
  "▶ Resume" buttons; interactive `abs` at the terminal shows the same picker
  (`--resume` / `--new` to skip, `abs config start-menu off`).
- **Telegram "/" menu** — `/abs_start /abs_status /abs_pool` while idle, `/abs_exit`
  + `/usage` in-session (auto-switched by the daemon).
- **Kill ladder while idle** — `ABS OFF` / `ABS BLOCK` stop the daemon for that bot
  (recover only from the terminal), `ABS CLEAR POOL`.
- **Login detection** — a stat-only credentials presence check before launch
  (contents never read); a session that dies immediately reports a likely login
  issue.
- **Crash/restart recovery** — on boot the daemon re-derives full state from disk:
  a surviving daemon session resumes with precise pane/pid tracking, a dead one is
  reclaimed with a reboot notice; sessions that didn't survive a restart notify you
  and the pool is kept.
- **Observability** — a structured, metadata-only event log
  (`~/.abs/daemon/events.jsonl`, never message text) and a consolidated dashboard
  in `abs status` / `abs daemon status`; `abs doctor` diagnoses the whole stack.
- **Real log rotation** for `daemon.log` and `events.jsonl` (size-based, N
  generations); the installer refreshes the unit and can install a pinned herdr.
- **Sandbox sessions — `abs sandbox build|create|list|start|stop|destroy`, and
  🏖 Sandbox as an `ABS START` target.** Claude Code runs inside a long-lived Ubuntu
  container: non-root `dev`, no `--privileged`, no docker socket, and exactly one
  host mount — a dedicated `~/Projects/sandboxes/<name>` folder — so work syncs live
  and nothing else on the host is visible. Credentials are **copied** in (never
  mounted), sanitised on the way: `~/.claude.json` included, host hooks stripped,
  the plugin marketplace re-homed to `/home/dev`, and the box workspace pre-trusted
  (without those last two, a box starts with no Telegram channel or blocks forever
  on the trust prompt). If an in-box session's channel never comes up, the daemon
  reclaims the bot and says so instead of silently swallowing messages.
- **ABS itself runs inside the sandbox** (image `absd-sandbox:v4`). A box session is
  the *same* launcher the host runs — `abs.sh --profile <p> --daemon-start` — just
  inside the container, so the box gets the ABS status bar, the `PreToolUse` Bash
  guard, the `ABS STOP`/`EXIT`/`MUTE` remote controls, and a `session.pid` that
  `abs exit` can signal (**`ABS EXIT` from the phone now ends an in-box session**).
  `abs` is on `PATH` in the box; the orchestration verbs (`abs sandbox|daemon|
  restricted`) refuse in-box, since sandboxes are managed from outside. abs.sh and
  `absd/` are copied into `/opt/abs` at container start and before every session, so
  a host-side fix reaches a box without an image rebuild. Existing boxes must be
  re-created to pick up v4 (the host workdir is kept).

## [2.6.0] — 2026-07-22

### Added
- **`abs voice setup` — voice is now installable by everyone, not just a dev
  checkout.** Voice previously worked only where `abs` was a symlink into a repo
  clone with the venvs built by hand; a `curl | bash` install never shipped the
  scripts or built the engines, so `abs say` and voice transcription failed on
  every real install. `abs voice setup` fixes that end to end: it checks
  `ffmpeg`, installs [`uv`](https://docs.astral.sh/uv/) if missing, has `uv` fetch
  the Python versions Whisper (3.13) and Chatterbox (3.11) each need, downloads
  `transcribe.py`/`speak.py`, and builds both venvs. Idempotent; `--force`
  rebuilds.
- **`abs voice status`** — a green/red check of every voice piece (scripts, both
  venvs, `ffmpeg`, `uv`) so a broken install is legible at a glance instead of
  only surfacing as a mid-task failure.
- **The installer offers voice as an opt-in step.** After the base install it
  asks whether to set voice up now and hands off to `abs voice setup` (skipped
  cleanly on non-interactive installs).

### Changed
- **Voice engines for an installed `abs` now live in `~/.abs/voice`** rather than
  beside the command, keeping multi-GB venvs out of `~/.local/bin` and letting
  `abs uninstall` remove them with the rest of the state. A dev checkout is
  unchanged — its scripts and venvs stay next to `abs.sh`.
- **The Telegram system prompt's VOICE section is now conditional on voice
  actually being installed.** When the venvs are absent it tells the agent plainly
  that voice isn't set up and to point the user at `abs voice setup`, instead of
  asserting a working pipeline and sending the agent down dead paths.

## [2.5.1] — 2026-07-20

### Fixed
- **Status-bar Voice dot now reflects real activity, not just capability.** It was
  green whenever `.venv-tts` was installed, which said nothing about whether voice
  was actually flowing. Now it's green only when a voice note was genuinely sent
  within a recency window (`ABS_VOICE_ACTIVE_SECS`, default 120s) and dim
  otherwise — parallel to how the Text dot means "reports are flowing." `abs say`
  stamps `.last_voice_ts` on each successful send. Updates on Claude Code's next
  status-line render.

## [2.5.0] — 2026-07-19

### Added
- **On-launch update prompt.** Every launch now checks GitHub for a newer release
  and, on an interactive terminal, asks `Update now and relaunch? [y/N]` (default
  No). Yes updates abs in place and re-execs the new version into the same session
  (same profile and passthrough flags); No launches the current version. The check
  is synchronous but tightly timed out (≤3s) with an offline fallback, so it never
  hangs a launch; non-interactive sessions (systemd/nohup/CI) print a one-line
  banner instead of prompting. `abs config update-check off` suppresses it.
- **`abs update`.** Update abs in place to the latest release on demand. Detects
  how abs was installed — a git checkout fast-forwards (`git pull --ff-only`); a
  standalone copy re-runs the official installer over the same file — and verifies
  the on-disk version actually advanced before reporting success.

### Changed
- **Update check is now on-launch, not once-a-day.** The previous daily,
  background, one-launch-behind cache meant a fresh release could stay invisible
  for up to ~24h plus a launch. The cache is now an offline fallback only.

## [2.4.0] — 2026-07-19

### Added
- **Voice model selector.** `abs config voice standard|turbo` picks the default
  TTS model — standard keeps the emotion/pacing dials (`--exag`/`--cfg`); turbo
  (ChatterboxTurboTTS, bundled in chatterbox-tts) generates ~1.8× faster on GPU
  (measured) with no dials. Per-call override: `abs say --turbo` / `--standard`.
- **Voice cloning / selectable voice.** `abs config voice-sample <file>` clones a
  voice from any short reference clip (normalised to a wav in the profile dir) and
  applies it to every spoken reply, both models; `--clear` reverts to the built-in
  voice. Per call: `abs say --audio-prompt <wav>` / `--default-voice`.
- **`abs say` flag pass-through** — `--turbo`, `--standard`, `--device`,
  `--audio-prompt`, `--exag`, `--cfg`, etc. reach `speak.py` so you can A/B models
  and voices from the CLI and send either as a real voice note.

### Changed
- **Faster, more accurate transcription.** Greedy decode (`beam_size=1`),
  `condition_on_previous_text=False`, adaptive CPU threads, and a project-vocabulary
  `initial_prompt` — measured ~12% faster and 87%→100% word accuracy on a sample
  (project names like "Agent"/"git" stop getting mangled). Language auto-detects by
  default; `ABS_STT_LANG=en` pins it for a further speed win.
- **Cross-platform voice devices.** `speak.py` auto-selects `cuda` if present, else
  `cpu`; loudness-normalised, VoIP-tuned Opus output. Apple MPS is opt-in
  (`--device mps`), not the default: benchmarked on an M-series Mac, chatterbox TTS
  runs ~1.6-1.9× *slower* on MPS than CPU (small-batch autoregressive loop + MPS
  op-fallback copies), so auto stays on CPU there. STT (`small`) stays on CPU on Mac
  regardless (CTranslate2 has no Metal backend). Ships `voicelab.sh` to benchmark
  STT+TTS on any machine and `docs/VOICE_MAC_TESTING.md` for the Mac setup.

## [2.3.0] — 2026-07-18

### Added
- **Remote control ladder — hook-enforced kill switches.** Five phrases you send
  from Telegram as a whole message, acted on by the session hook *itself* — so
  they work even if the model is compromised (it never runs them):
  - `ABS MUTE` / `ABS UNMUTE` — mute / resume proactive reports (catch-up on resume).
  - `ABS OFF` — cut inbound *and* outbound Telegram; the session keeps working. Terminal-only to re-enable.
  - `ABS STOP` — halt the current plan at the next step and wait for a new instruction.
  - `ABS EXIT` — close the session (asks to confirm if mid-task); restart with `abs`.
  - `ABS BLOCK` — lock the bot out entirely until a deliberate `abs setup`.
- **Destructive-command guard.** A `PreToolUse` hook blocks a small, high-confidence
  set of destructive Bash commands (`rm -rf`, `git push --force`, `reset --hard`,
  `DROP`/`TRUNCATE`, `DELETE`-without-`WHERE`, reading `.env`/keys, …) when the turn
  was **driven from Telegram** — a remote message is lower-trust than the operator
  at the desk. From the terminal, nothing is blocked. Opt out: `abs config guard off`.
- `abs exit` ends the running session; `abs config guard on|off` toggles the guard.

### Security
- These turn the previously *advisory* prompt rules into *enforced* ones for the
  obvious high-damage cases, and add a kill switch that doesn't depend on trusting
  the model. Honest limit: defense-in-depth, not a sandbox (a determined
  compromised model could obfuscate a command) — Claude Code's own permission
  system stays the real boundary. Documented in README, SECURITY.md, and the site.

## [2.2.2] — 2026-07-18

### Fixed
- **Conversation-log secret scrubbing hardened** (from a security audit). Now also
  catches JWTs, PEM private keys, passwords embedded in URLs, lower/mixed-case
  `key=` and `password=` pairs, Slack/Google keys, and Telegram tokens with short
  bot IDs. Control characters are stripped before writing, so a logged escape
  sequence can't replay in the terminal when you view the log with `abs log`.
- **`abs say` works on macOS** — replaced the GNU-only `mktemp --suffix` with a
  portable temp file, so voice-out no longer dies on macOS.
- **Installer no longer over-claims ownership** — it only trusts a bare `abs.sh`
  filename for a *dangling* symlink (a deleted checkout); a live symlink to an
  unrelated `abs.sh` is left alone rather than silently overwritten.

### Security
- SECURITY.md and the website Security page now document the conversation log
  (what's scrubbed, that it's best-effort and local, how to disable/clear it),
  the trust-by-HTTPS install chain, and the daily update check.

## [2.2.1] — 2026-07-18

### Changed
- **Status-bar restyle.** The bottom-bar indicator now reads `abs:@bot · ● Text ·
  ● Voice · Fable 2% · Week 12% (resets on Thu) · 5H 22% (resets in 1h)`: the
  label is coloured (theme violet `abs:` + Telegram-blue `@bot`); two channel
  dots show whether **Text** (proactive reports) and **Voice** (local TTS) can
  reach Telegram right now; and each usage percentage is threshold-coloured —
  green under 60, amber to 80, coral to 90, muted brick above. Reset times moved
  into per-limit parentheses (weekly as a weekday, 5-hour as a countdown). All
  muted 256-colour tones. The Telegram usage footer stays plain text.

## [2.2.0] — 2026-07-18

### Added
- **Conversation backup.** abs keeps a local, date-segregated log of the session —
  your messages, its Telegram replies, and the tools it ran — under
  `~/.abs/<profile>/log/`, owner-only and never uploaded. Read it with `abs log`
  (`--list` for the days on record, `--date <day>` for one), delete it with
  `abs log --clear`. Anything shaped like a secret (bot tokens, `sk-…`, `ghp_…`,
  AWS keys, `KEY=…`) is scrubbed before writing — best-effort, since the log is
  local and owner-only anyway. Turn it off with `abs config log off`; when off,
  the per-tool hook cost isn't paid at all.

## [2.1.6] — 2026-07-18

### Added
- **Instant acknowledgment on inbound.** The moment a Telegram message lands, abs
  drops a 👀 reaction on it straight from the session hook — guaranteed and before
  any work starts, so you know it was received. Never double-messages the way a
  text ack would. Opt out with `abs config ack off`.
- **Voice transcript echo.** When you send a voice note, abs replies with
  `Heard: "…"` before acting on it, so you can verify the transcription and correct
  or stop it mid-task instead of finding out at the end.

## [2.1.5] — 2026-07-18

### Fixed
- **Re-running the installer now updates an existing install** instead of
  refusing it. It recognizes any prior Agent Babysitter — the git symlink *or* a
  curl/pipx static copy — by its version constant and replaces it in place, while
  still refusing to clobber the unrelated v1 Python namesake. This is how every
  user updates: just re-run the one-line installer (or `git pull`).
- **The update banner now shows on the first run after a release.** The version
  check fetches synchronously on a cold cache instead of only in the background,
  so a newer version is flagged immediately rather than on the second launch.

### Added
- **The installer offers to install Claude Code** if it's missing, the same way
  it offers Bun — `curl -fsSL https://claude.ai/install.sh | bash`, into
  `~/.local/bin`, no sudo.

## [2.1.4] — 2026-07-18

### Changed
- **Usage glance reordered and relabeled** — now reads `Fable 0 · Week 9% · 5H
  15% resets in 2m`: Fable first, then the weekly all-models limit, then the
  5-hour window with its next-reset time tucked onto the same segment.

## [2.1.3] — 2026-07-18

### Changed
- **Usage glance always shows the Fable weekly limit**, including at 0% — reverts
  the 2.1.2 hide-at-0 behavior. The `/usage` output omits the Fable line until
  the model is used this week, so whenever it's present we surface it.

## [2.1.2] — 2026-07-18

### Changed
- **Status bar shows the bot handle** — the indicator now reads `abs@yourbot`
  instead of `abs:default`. One bot per profile means the handle identifies the
  session just as uniquely, and it's what you actually recognize.
- **Usage glance drops `Fable 0%`** — a per-model weekly limit at 0% is noise in
  a bar that's fighting for width; it reappears once that model has real usage.

## [2.1.1] — 2026-07-18

### Fixed
- **Usage glance reset time** — the "resets in …" readout could show a nonsense
  window (e.g. `resets in 8755h 18m`) right after a 5-hour session rolled over.
  A cached reset stamp that had just passed was mistaken for a Dec→Jan year-wrap
  and pushed a full year out. It now only rolls the year forward for stamps more
  than 300 days past (the real wrap case) and shows `now` for a just-passed
  window until the next refresh.

## [2.1.0] — 2026-07-18

### Added
- **Launch defaults per profile** — `abs config model <name>` (`--clear` to unset)
  and `abs config silent on|off`, stored in `rc.json` and applied at launch. An
  explicit `abs --model …` on the command line still wins.
- **Smart auto-silent** — after 3 consecutive terminal prompts, proactive reports
  mute automatically (you're clearly at the desk). A Telegram message — or
  `abs quiet off` — resumes them. No idle timer, so reading at your desk never
  starts a buzz. Wired as a session hook via `--settings` (merges with your own).
- **Status-bar indicator** — a small dot in Claude Code's bottom bar shows the
  live state: green = reports flowing, gray = silent/auto-silent, hollow = inbound
  off. `abs config statusline off` opts out (e.g. if you run your own statusLine).
- **Usage glance** — your 5-hour, weekly, and per-model (Fable) limits, plus the
  next reset, show in the terminal status bar (`● abs:default · 5h 5% · week 7% ·
  Fable 0% · resets in 3h`) and as a footer on Telegram reports. The numbers are
  cached from `/usage` (token-free) and refreshed lazily; tune the interval with
  `abs config usage-refresh <minutes>` (default 5).
- **Startup flood control** — on start, `abs` drains any Telegram backlog older
  than the launch and asks at the terminal what to do (default: discard), so a
  new session no longer opens buried under old messages.
- **Version + update check** — `abs version` prints the installed version, and
  the installer reports what it installed. Once a day (backgrounded, no tokens)
  abs checks the `VERSION` file on `main`; if a newer release exists it shows a
  one-line banner at launch with the right update command for your install (`git
  pull` vs the curl one-liner). Opt out with `abs config update-check off`.

### Changed
- Inbound Telegram messages are now always replied to, even while reports are
  muted — a reply is never a "proactive send."

## [2.0.0] — 2026-07-16

The project was renamed from **Claude RC** to **Agent Babysitter**, and the
command from `crc` to `abs`. This release also supersedes an earlier, unrelated
tool that briefly held the `agent-babysitter` name (a tmux + local-LLM approach);
that version is preserved on the `v1` branch and the `v1.0.5` tag.

### Changed
- Command is now `abs`; state lives in `~/.abs`. Existing Claude RC profiles and
  pairings migrate automatically on first run (non-destructive).
- `usage` progress bars use `●`/`○` (the old `░` rendered as broken glyphs on
  phones); overridable via `ABS_BAR_FULL` / `ABS_BAR_EMPTY`.
- Documentation restructured: a shorter, feature-first README, with the full
  reference in [`docs/GUIDE.md`](docs/GUIDE.md) and the threat model in
  [`SECURITY.md`](SECURITY.md).

### Added
- First-run setup opens with a welcome banner and a guided BotFather walkthrough.
- Inbound screenshots/photos: attach an image in Telegram and Claude reads it.
- `install.sh` refuses to overwrite an unrelated `abs` on `PATH` (`ABS_FORCE=1`
  to override).
- PyPI packaging (`pip install agent-babysitter`) as a thin launcher for the
  bundled `abs.sh`.

### Fixed
- `usage` no longer prints a garbled reset line for a limit at 0% (e.g. an unused
  weekly model); it inherits the shared weekly reset window instead.

## [1.0.5] — 2026-05-30

Final release of the original tool (tmux monitor + local-LLM policy engine).
Preserved on the [`v1`](https://github.com/Pranjalab/AgentBabysitter/tree/v1)
branch; not compatible with 2.x.
