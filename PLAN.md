# ABS v3 — Always-On Architecture: Implementation Plan

**Branch:** `v3-daemon` · **Base:** v2.6.0 (`main`) · **Status:** FINAL — approved for implementation (2026-07-23)
**Owner:** Pranjal · **Plan author:** Claudex · **Implementing agent:** any (written for an Opus-class agent with no access to the design conversation)

---

## 0. How to use this plan

This document is self-contained: every decision, constraint, and rationale an implementing
agent needs is here. Read sections 1–5 completely before writing any code.

Rules for the implementing agent:

1. **Work step by step, in order.** Each phase is split into numbered steps. Do not start a
   step until the previous step's *critique gate* is passed (see below).
2. **Every step has two verification tracks.** *Agent-side* (automated tests you write and
   run) and *Pranjal-side* (a manual checklist he executes from his phone and terminal).
   A step is DONE only when both pass.
3. **Critique gate.** At the end of each step, write a short self-review in
   `docs/v3/critique/<step-id>.md`: what was built, what is tested, what is NOT tested,
   known gaps, and any deviation from this plan with justification. Pranjal (and/or a
   reviewing agent) signs off before the next step starts.
4. **Never violate the locked decisions (section 3).** If one seems wrong during
   implementation, STOP and ask Pranjal — do not silently deviate.
5. **Do not commit secrets.** Bot tokens, allowlists, credentials, and anything under
   `~/.abs/` or `~/.claude/` never enter the repo, tests, or logs.
6. Commit per step with lowercase conventional-style messages; leave pushing to Pranjal.

---

## 1. Background — what ABS is today (v2.6.0)

ABS ("Agent Babysitter") pairs a private Telegram bot with a Claude Code session:

- `abs.sh` (~2,600 lines of bash, the whole product) is a **configurator + launcher**. It
  stores per-profile state under `~/.abs/profiles/<name>/` (bot token, allowlist,
  `state.json`, `session.pid`), builds a system prompt, writes session hooks, then
  `exec claude --channels plugin:telegram@claude-plugins-official ...`.
- The official **telegram plugin inside Claude Code owns the Telegram connection** — it
  long-polls `getUpdates` with the bot token from `TELEGRAM_STATE_DIR`.
- Hooks provide: smart-silent counters, a PreToolUse guard that blocks destructive Bash on
  Telegram-driven turns, a statusline, and the kill ladder (`ABS MUTE/UNMUTE/OFF/STOP/EXIT/BLOCK`).
- Profiles exist because **Telegram allows exactly one `getUpdates` consumer per bot token**
  — two concurrent sessions need two bots.

**The gap v3 closes:** when Claude Code is not running, nobody polls, the bot is deaf, and
nothing can be started remotely. ABS is a passenger, not a manager.

## 2. Goals and non-goals

### Goals

- **G1 — Always-on daemon (`absd`).** A single background daemon manages ALL profiles/bots
  on the system. When no session is live for a bot, the daemon polls that bot and responds.
- **G2 — Remote session start.** `ABS START` from Telegram: pick bot → pick project (or new
  folder) → pick permission mode → daemon launches Claude Code in a persistent terminal
  session the user can later attach to at the desk.
- **G3 — Message pool.** Non-command messages arriving while no session runs are queued per
  profile, acknowledged, and offered for forwarding when a session starts.
- **G4 — Persistent, attachable sessions.** Sessions run inside a session engine
  (herdr preferred, tmux fallback) supporting detach/reattach: start from Telegram, attach
  from terminal, detach, session keeps running.
- **G5 — Multi-bot concurrency (first-class).** N profiles ⇒ N independent state machines.
  Three Claude Code sessions with three different bots running simultaneously on one system
  must work, each independently startable/stoppable/attachable, while the daemon keeps
  polling any idle bots.
- **G6 — Login detection.** If Claude Code isn't authenticated when a remote start happens,
  the user gets a predefined Telegram message ("please login from the terminal").
- **G7 — Sandbox sessions (Phase 3).** `abs sandbox`: Docker-based Ubuntu environment with
  Claude Code inside; project files live inside the sandbox; ports forwarded; credentials
  copied in at creation (never mounted).
- **G8 — Blocked-session notifications (Phase 2, herdr only).** When Claude Code blocks on
  a prompt in a remotely-started session, ping Telegram.

### Non-goals (v3)

- Building our own terminal multiplexer. Never.
- Forking, vendoring, or copying code from herdr (see D3 — license).
- Multi-user / multi-tenant sandboxes. Sandboxes are for Pranjal's own isolation only.
- Remote login automation (future; v3 only *detects* and instructs).
- Replacing the in-session telegram plugin. The plugin remains the in-session channel.

## 3. Locked decisions (do not re-litigate)

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | One daemon (`absd`) manages **all** profiles | Simpler ops than N daemons; single systemd unit |
| D2 | Daemon language: **Python 3.11+ (asyncio)**, CLI stays bash. Confirmed final 2026-07-23; a future port to Rust or TS/Bun (single-binary distribution) is an explicit option, which is why 4.4 exists | Multi-bot polling + JSON socket work needs real async; repo already ships Python (`speak.py`, `transcribe.py`); workload is pure I/O glue — Rust buys nothing now and slows the feedback loops the handoff/herdr work needs |
| D3 | herdr used **arms-length only**: separate binary driven via CLI + socket API. Never fork/vendor/copy code | herdr is AGPL-3.0; ABS is MIT and stays MIT. Subprocess use creates no AGPL obligation; copied code would |
| D4 | **ABS must be fully functional without herdr.** Engine adapter with `herdr` and `tmux` backends; auto-select herdr if installed, else tmux. herdr adds *enhancements* (status events, nicer UI), never *requirements* | Independence requirement from Pranjal; also hedges herdr's pre-1.0 churn and single-maintainer risk |
| D5 | Remote-start permission mode: **ask per start** in the ABS START flow (Normal vs Away/acceptEdits) | Per-session choice; Normal default |
| D6 | New folders from Telegram: **only inside one configured workspace root** (e.g. `~/Projects`), set at the terminal. Existing projects must be registered at the terminal or picked from the root's children | Remote arbitrary-path creation is what a compromised channel would abuse |
| D7 | Sandbox runtime: **Docker** | Ubiquity; Podman may come later |
| D8 | Sandbox credentials: **copy** host `~/.claude` credentials into the sandbox at creation; copies diverge independently; host copy never touched | Better isolation than mounting; known caveat: OAuth refresh-token rotation may stale the copy (R6) |
| D9 | Daemon-side Telegram command surface is a **small fixed allowlisted grammar** (section 7.4); everything else pools | Small attack surface while unattended |
| D10 | The daemon **enforces the profile allowlist itself** on every inbound update, before any parsing | Cannot rely on the plugin's checks when the plugin isn't running |
| D11 | Kill ladder extends to the daemon: `ABS OFF` stops that profile's polling too; `ABS BLOCK` locks the profile at daemon level; both re-enabled only from the terminal | "Off means off" |
| D12 | Phasing: **Phase 0 spike → Phase 1 daemon core → Phase 2 status/UX → Phase 3 sandbox.** Sequential, no parallel phases | Sandbox depends on the session layer |
| D13 | herdr binary version is **pinned** by the installer; upgrades are explicit | Pre-1.0 CLI/API churn |
| D14 | Pool messages are **kept until explicitly cleared or forwarded**, never silently dropped | User trust |

## 4. Target architecture

```
                    ┌───────────────────────────────────────────────┐
                    │            absd (Python, systemd user unit)   │
                    │                                               │
 Telegram Bot A ◄──►│ Poller A ──┐   ┌─ registry (projects, root)   │
 Telegram Bot B ◄──►│ Poller B ──┼──►│  pool store (per profile)    │
 Telegram Bot C ◄──►│ Poller C ──┘   └─ engine adapter ─────────┐   │
                    └────────────────────────────────────────── │ ──┘
                        pollers run ONLY while their bot        │
                        has no live Claude Code session         ▼
                                              ┌─────────────────────────────┐
                                              │ session engine (one of:)    │
                                              │  herdr server  ── preferred │
                                              │  tmux (isolated socket) ──  │
                                              │      fallback               │
                                              ├─────────────────────────────┤
   terminal:  abs attach <profile>  ─────────►│ session "abs-<profile>"     │
                                              │  └─ claude --channels ...   │◄──► Telegram Bot X
                                              │     (plugin owns polling    │     (while session live)
                                              │      while session lives)   │
                                              └─────────────────────────────┘
```

### 4.1 Per-profile state machine (the heart of the design)

Each profile runs an independent asyncio task with four states:

- **IDLE_POLLING** — no session. Daemon long-polls `getUpdates` (timeout ≈ 50 s). Handles
  the command grammar; pools everything else; acks every update by advancing the offset.
- **HANDOFF** — a start was requested. Daemon: (1) exits the poll loop, (2) issues one
  final `getUpdates` with `timeout=0, offset=last+1` to commit its offset, (3) writes
  `~/.abs/profiles/<p>/daemon-handoff.json` (timestamp, reason), (4) asks the engine
  adapter to create the session, (5) transitions to SESSION_LIVE. Never polls again until
  RECLAIM completes.
- **SESSION_LIVE** — plugin owns the token. Daemon only watches liveness (engine
  `is_alive()` + `session.pid`) every few seconds. No Telegram traffic from the daemon for
  this bot (all outbound notifications for a live profile are suppressed except
  session-death notification, which is sent *after* reclaim).
- **RECLAIM** — session ended. Grace delay (default 5 s), then attempt one probe
  `getUpdates timeout=0`; on HTTP 409 back off (2 s, 4 s, 8 s… max 60 s — the plugin is
  still alive, e.g. the session is restarting); on success → send "session ended" note →
  IDLE_POLLING.

Sessions started **at the terminal** (plain `abs`) must also flip the daemon to
SESSION_LIVE: `abs.sh` writes `session.pid` before exec (already does); the daemon's
IDLE_POLLING loop checks for a live `session.pid` before *every* poll cycle and yields
immediately (handles the race where a terminal launch and a daemon poll overlap; the 409
backoff covers the rest).

### 4.2 Engine adapter

Python interface (`absd/engines/base.py`):

```python
class Engine(Protocol):
    name: str
    def available(self) -> bool
    def create_session(self, profile: str, cwd: Path, command: list[str],
                       env: dict[str, str]) -> None      # headless, never attaches
    def is_alive(self, profile: str) -> bool
    def kill(self, profile: str) -> None
    def attach_command(self, profile: str) -> str         # printed for `abs attach`
    def list_sessions(self) -> list[SessionInfo]
```

- **TmuxEngine** — isolated socket (`tmux -L abs -f <our tuned conf>`), session name
  `abs-<profile>`. Tuned conf ships in repo (`assets/abs.tmux.conf`): `mouse on`,
  `history-limit 50000`, minimal status bar. This backend is the *reference
  implementation*: every feature must pass its tests on tmux alone (D4).
- **HerdrEngine** — named herdr session per profile (`herdr session ...`,
  socket `~/.config/herdr/sessions/abs-<profile>/herdr.sock`), workspace created with
  `--cwd`, claude run in a single fullscreen pane. Exact invocation is determined by the
  Phase 0 spike — the docs do not document headless creation, so the spike must find the
  working recipe (socket API `workspace.create` + `pane.run`, or CLI) and record it in
  `docs/v3/herdr-recipes.md`.
- Selection: `~/.abs/daemon/config.json` → `"engine": "auto" | "herdr" | "tmux"`.
  `auto`: herdr if binary present AND spike-verified recipe works, else tmux.
- The session command is always the **existing launcher**: the engine runs
  `bash <SCRIPT_PATH> --profile <p> --daemon-start [--away]` in the project cwd.
  `abs.sh` gains a `--daemon-start` flag (skip interactive prompts, skip update prompt,
  honor `--away` → acceptEdits). This reuses all v2.6.0 behavior — hooks, prompt, plugin
  wiring — with zero duplication. **Do not reimplement the launcher in Python.**

### 4.3 Filesystem layout (new pieces)

```
~/.abs/
  daemon/
    config.json          # engine, workspace_root, poll timings, max_sessions
    registry.json        # registered projects: [{path, label, added_at}]
    daemon.pid / daemon.log (rotated)
  profiles/<name>/
    pool.jsonl           # one JSON object per pooled message (0600)
    daemon-handoff.json  # handoff marker (transient)
    state.json           # existing; daemon adds .daemon_blocked flag for D11
repo:
  absd/                  # Python package (daemon, engines, telegram client, pool)
  assets/abs.tmux.conf
  tests/                 # pytest suite (unit + integration)
  docs/v3/               # critique gates, herdr recipes, manual test scripts
  PLAN.md                # this file
```

Everything under `~/.abs/` is written with `umask 077` semantics (0600/0700) like v2.

### 4.4 Implementation discipline — keep the daemon portable

absd must stay cheap to port to Rust or TS/Bun someday (D2). Concretely:

- **stdlib-first**: plain `asyncio`, `dataclasses`, `json`, `pathlib`. HTTP may use aiohttp;
  nothing else without a critique-gate note. No bot frameworks, no ORMs, no DI/plugin magic.
- **Logic lives in the state machine and pure functions**, not in decorators, metaclasses,
  or framework callbacks. If a behavior can't be unit-tested without I/O, restructure it.
- **The wire protocols are the spec**: Telegram Bot API shapes, the Engine protocol, and
  the on-disk state formats (4.3) are documented and stable — a future port re-implements
  those, not Python internals.

### 4.5 Telegram client (daemon side)

- Direct Bot API over HTTPS (aiohttp or urllib in a thread — implementer's choice, but no
  heavyweight bot frameworks; we need getUpdates, sendMessage, editMessageText,
  answerCallbackQuery, setMyCommands only).
- **Inline keyboards** for the ABS START flow (callback_query updates). Fallback: numbered
  text menus (reply "2") for clients where keyboards misbehave — implement keyboards first,
  text fallback accepted in the same step.
- Long-poll timeout 50 s; stagger poller start per profile by 1–2 s to avoid thundering herd.

## 5. Security model (applies to every step)

1. **Allowlist first.** Every inbound update: if `from.id` not in the profile's allowlist →
   log locally, do NOT reply, do NOT pool. (Replying leaks bot liveness to strangers.)
2. **Fixed grammar while unattended** (D9). Exact-match, case-insensitive, whole-message
   commands only (section 7.4). No free-text interpretation by the daemon — the daemon has
   no LLM; it is deterministic code.
3. **Telegram can never**: name an arbitrary filesystem path (D6), change the allowlist,
   unblock a blocked profile, re-enable a switched-off profile, or alter daemon config.
   Those are terminal-only.
4. **Session starts inherit v2 protections**: guard hook, silent hook, statusline, kill
   ladder — automatically, because the daemon launches through `abs.sh` (4.2).
5. **Secrets discipline**: tokens/credentials never in logs, never in Telegram messages,
   never in the repo. Pool contents are user data: 0600, local only.
6. **Sandbox boundary (Phase 3)**: no `--privileged`, no docker-socket mount, non-root
   user inside, only explicit port publishes; copied credentials are the *only* host
   secrets inside, and that tradeoff is documented to the user at `sandbox create`.
7. The herdr socket is **not a security boundary** — any same-user process can drive it.
   ABS trust decisions never delegate to herdr state.

## 6. Phase 0 — Groundwork + herdr spike *(go/no-go gate)*

### Step 0.1 — Scaffolding
Create `absd/` package skeleton, `pyproject.toml` wiring (extend existing), pytest setup,
`tests/` with a trivial passing test, `docs/v3/` tree. `abs daemon` bash subcommand stub
(prints "not implemented").
- **Agent verify:** `pytest` green in repo venv; `shellcheck abs.sh` introduces no
  findings beyond the v2.6.0 baseline (the baseline is NOT zero — 22 pre-existing
  info/warning findings; the contract everywhere in this plan is "no NEW findings",
  proven by diffing shellcheck output against the branch point).
- **Pranjal verify:** `abs help` unchanged for existing commands; nothing about his
  current v2 workflow breaks (start/stop a normal session once).
- **Critique gate:** layout review — does the skeleton match section 4.3?

### Step 0.2 — herdr headless spike ⚠ *decides HerdrEngine viability*
Install pinned herdr on this machine (version recorded in `docs/v3/herdr-recipes.md`).
Prove, with NO client ever attaching: start server → named session → workspace with
`--cwd` → run a long-lived TUI command (use the fake-claude harness from 6.3 if ready, or
`htop`) in a pane → `is_alive` detectable → attach from a terminal shows it live → detach
→ kill session → cleanup. Then prove `events.subscribe` for `pane.agent_status_changed`
fires (run real `claude` once for this part only).
- **Agent verify:** a `tests/integration/herdr_spike.sh` script that runs the whole
  sequence non-interactively and exits 0. Record every working command verbatim in
  `docs/v3/herdr-recipes.md`.
- **Pranjal verify:** run `herdr`, see the spike session in the UI, attach, scroll with
  the mouse, detach. **This is also the UI evaluation he deferred** — if the UI
  disappoints, only `auto` engine default changes (D4 protects us); the plan proceeds.
- **Critique gate — GO/NO-GO:** headless recipe works → HerdrEngine proceeds as primary.
  Doesn't work → HerdrEngine drops to Phase 2 (best-effort) and tmux becomes default;
  note the decision and continue. **Do not stall the plan on herdr.**

### Step 0.3 — Fake-claude test harness
`tests/harness/fake-claude` (bash): mimics Claude Code's process shape — stays alive,
writes a `session.pid`-compatible PID, optionally simulates "not logged in" (exit 1 in
<5 s with an auth-ish message on stderr) and "crash after N seconds". A `fake-telegram`
aiohttp server mimicking Bot API (getUpdates with configurable queued updates, 409
injection, sendMessage capture). All daemon tests run against these — **no real Telegram
or Anthropic traffic in automated tests, ever.**
- **Agent verify:** harness has its own tests; 409 injection provably works.
- **Pranjal verify:** none (infrastructure).
- **Critique gate:** does the fake cover: offsets, 409, callback_query, allowlist cases?

## 7. Phase 1 — Daemon core (the product)

> **STATUS: Phase 1 COMPLETE.** All steps DONE (0.1–0.3 groundwork, 1.1–1.8),
> plus pulled-forward Step 2.2 (resume-first start + Telegram "/" menu) and
> observability (event log + dashboard). Per-step critiques in
> `docs/v3/critique/`; the Phase-1 retrospective + carried-to-Phase-2 gaps are in
> `docs/v3/critique/1.8.md`. `ABS_VERSION` intentionally not yet bumped (release is
> Pranjal's call).

### Step 1.1 — Engine adapter with TmuxEngine
Implement `Engine` protocol + TmuxEngine + tuned conf + `abs attach <profile>` /
`abs sessions` bash commands (they shell into the adapter via `python -m absd.engine ...`).
- **Agent verify:** pytest integration: create headless session running fake-claude in a
  temp dir → `is_alive` true → attach_command output sane → kill → `is_alive` false.
  Two sessions for two fake profiles simultaneously (G5 seed).
- **Pranjal verify:** `abs sessions` lists a manually created test session; `abs attach`
  drops him in; mouse scroll works (tuned conf); detach; session survives.
- **Critique gate:** interface honest? (nothing herdr-specific leaked into it)

### Step 1.2 — HerdrEngine *(skip if 0.2 said NO-GO)*
Same tests as 1.1, herdr backend, using the 0.2 recipes. `engine: auto` selection logic.
- **Agent verify:** the 1.1 integration suite parameterized over both engines passes.
- **Pranjal verify:** same as 1.1 but inside herdr's UI; sidebar shows the session.
- **Critique gate:** does every test pass on BOTH engines? (D4 proof)

### Step 1.3 — Daemon skeleton + single-profile poller (read-only)
`absd` process: config load, one profile, IDLE_POLLING against fake-telegram; allowlist
enforcement (5.1); pools every non-command message with ack reply "🗂 No session running —
message saved to pool (n). Send ABS START to begin."; `ABS STATUS` and `ABS POOL` read
commands. Systemd user unit + `abs daemon install|start|stop|status|logs` + linger check.
- **Agent verify:** pytest: allowlisted vs stranger messages; pool file contents + 0600;
  offset advance across restarts (no message replayed/lost — kill daemon mid-batch in
  test); `ABS STATUS` reply content.
- **Pranjal verify (first live moment):** with no session running, message his bot from
  his phone → gets the pool ack. A friend's account (or his second account) gets silence.
  `systemctl --user status absd` healthy; survives logout (linger).
- **Critique gate:** security review of 5.1–5.3 against the code, line by line.

### Step 1.4 — Multi-profile pollers (G5)
All configured profiles get independent state-machine tasks; staggered polling; per-profile
pools; daemon notices pre-existing live sessions at boot (session.pid + engine liveness)
and starts those profiles in SESSION_LIVE.
- **Agent verify:** pytest with 3 fake bots: mixed states (one live, two idle) behave
  independently; one bot's 409 doesn't affect others; daemon restart mid-state recovers.
- **Pranjal verify:** his real second/third bots (he has multiple profiles) — messages to
  each idle bot pool separately; `abs daemon status` shows per-profile state.
- **Critique gate:** race review — boot-time detection vs terminal launches (4.1 note).

### Step 1.5 — ABS START flow + handoff + launch
The big one. `ABS START` → inline keyboard: profile's own flow (each bot starts only its
own profile — bot identity IS the profile) → project keyboard (registered projects from
`registry.json` + direct children of `workspace_root` + "➕ New folder") → if new: daemon
asks for a name, validates (`[a-zA-Z0-9._-]{1,64}`, no path separators, created under
workspace_root only — D6) → permission keyboard (🟢 Normal / 🟡 Away-acceptEdits, D5) →
HANDOFF (4.1) → engine launches `abs --profile <p> --daemon-start [--away]` in the chosen
cwd → confirmation message with `abs attach <p>` hint → SESSION_LIVE. Also: `abs.sh`
`--daemon-start` flag; project registration CLI `abs project add|list|rm <dir>`;
`abs config workspace-root <dir>`.
RECLAIM on session end → "⏹ Session ended" → polling resumes. Terminal `abs` launches
still work unchanged when the daemon is running (4.1 race handling).
- **Agent verify:** pytest end-to-end against fakes: full callback flow; handoff offset
  committed exactly once; fake-claude session appears in engine; killing fake-claude
  triggers RECLAIM + notification + polling resume; 409 during reclaim backs off; new-
  folder validation rejects `../evil`, absolute paths, empty names; Away flag reaches the
  launcher argv.
- **Pranjal verify (the headline demo):** phone-only: `ABS START` → pick project → Normal
  → get confirmation → send the session a real task via Telegram → walk to terminal →
  `abs attach` → see the session live → detach → `ABS EXIT` from phone → "session ended"
  arrives → bot answers again as daemon. Then repeat with THREE bots/projects at once (G5).
- **Critique gate:** full security walkthrough of the flow as a hostile reviewer
  ("what can a stolen phone do?"); handoff timing diagram reviewed against 4.1.

### Step 1.6 — Login detection (G6)
Best-effort, version-tolerant, in this order: (a) pre-launch: credentials file presence
check (`~/.claude/.credentials.json` non-empty) → if missing, don't launch; send
predefined "⚠ Claude Code is not logged in on this machine. Please run `claude` in a
terminal and complete login, then try ABS START again."; (b) post-launch: session exiting
< 20 s ⇒ same message + "session ended immediately (possible login issue)". Never parse
version-specific CLI output as the primary signal.
- **Agent verify:** pytest: fake-claude in not-logged-in mode triggers (b); missing-creds
  fixture triggers (a); logged-in path launches normally.
- **Pranjal verify:** temporarily rename his credentials file (terminal, manually, and
  restore after) → ABS START from phone → gets the predefined message, no zombie session.
- **Critique gate:** confirm no credential file contents are ever read/logged — presence
  only.

> **SHIPPED** (see `docs/v3/critique/1.6-1.7.md`): pre-launch stat-only presence+size
> check immediately before HANDOFF (contents never read — verified); post-launch
> `failed_start` gets the login-issue note; `error{where:login_precheck}` emitted.

### Step 1.7 — Kill ladder + pool lifecycle integration (D11, D14, G3)
`ABS OFF` while idle: poller stops, marker set, terminal-only `abs on` re-enables.
`ABS BLOCK` while idle: daemon-level lock. `ABS CLEAR POOL` command. Pool forwarding v1
(simple): on session start, if pool non-empty, daemon sends "📨 n pooled messages — reply
`send all`, `send 1,3`, or `skip`" (text protocol; keyboard polish is Phase 2), then
forwards chosen messages into the session by writing them through the engine to the
session's Claude (via the telegram plugin's normal inbound path — i.e., daemon re-sends
them to the bot AFTER handoff so the plugin delivers them; verify ordering in tests).
- **Agent verify:** pytest for each ladder rung in daemon context; pool forward ordering
  (pooled messages arrive in the session in original order, marked forwarded, kept in
  file with `forwarded_at`); `skip` keeps them (D14).
- **Pranjal verify:** phone: pool 2 messages while idle → ABS START → choose `send all` →
  both appear in the session conversation; ABS OFF → bot goes silent → `abs on` at
  terminal revives it.
- **Critique gate:** does OFF truly mean off (daemon poller stopped, not just muted)?

> **SHIPPED with two corrections** (see `docs/v3/critique/1.6-1.7.md`): (1) pool
> selection is a FLOW step BEFORE handoff (not "on session start" — after handoff the
> plugin owns the token, so replies would go to the session); (2) selected messages
> are delivered as claude's INITIAL PROMPT via one `--prompt` argv element (not
> "re-sent to the bot" — bots never receive their own sendMessage). Kill ladder:
> ABS OFF → access.json dmPolicy=disabled, ABS BLOCK → rc.json .blocked=true, ABS
> CLEAR POOL → pool cleared; all text-only, allowlist-gated, event-emitting.
> forwarded_at stamped only on successful launch (D14).

### Step 1.8 — Phase 1 hardening + docs
Log rotation, daemon crash recovery (systemd `Restart=on-failure` + state re-derivation
from disk on boot), reboot notification ("🔄 machine restarted; sessions X,Y did not
survive; pools intact"), `abs doctor` extension for daemon+engine diagnosis, README/GUIDE
updates, CHANGELOG, `install.sh` installs daemon unit + optional pinned herdr.
- **Agent verify:** kill -9 the daemon under pytest → restart → state recovered, no
  double-poll, no lost pool entries. Full suite green on both engines.
- **Pranjal verify:** real reboot of his machine → daemon auto-starts → reboot notice on
  phone → ABS START works. Fresh-machine dry-run of `install.sh` in a container.
- **Critique gate:** Phase-1 retrospective; update this PLAN.md with any drift before
  Phase 2.

> **SHIPPED** (see `docs/v3/critique/1.8.md`): full boot-time state re-derivation
> from disk (recovery matrix — surviving daemon session resumes with FIX B/C
> precision, dead one reclaims; lived_s from the marker timestamp); real N-generation
> size rotation for daemon.log + events.jsonl (reader spans rotated files); per-boot
> reboot notice; new `abs doctor`; installer refreshes the unit + optional pinned
> herdr; README/GUIDE/CHANGELOG. 50-iteration seeded chaos-recovery test. Carried to
> Phase 2: crash-vs-clean session_end (2.3), blocked-session pings (2.1), pool/flow
> keyboard polish (2.2), true SIGKILL fuzz (2.3).

## 8. Phase 2 — Status events & UX polish

### Step 2.1 — Blocked-session notifications (G8, herdr engine only)
Daemon (or a small watcher task) subscribes to the herdr session socket
`pane.agent_status_changed`. `blocked` ≥ 20 s (debounce) → "⏸ <profile>/<project> is
waiting for input/approval — `abs attach <p>` or answer via Telegram." `done` → optional
(config) "✅ task finished" ping. On tmux engine: feature silently absent (D4), documented.
- **Agent verify:** integration test against a real herdr session running fake-claude
  that fakes a blocked screen (use the recipes + herdr detection docs); debounce tested.
- **Pranjal verify:** start remote session, give a task that triggers a permission prompt,
  phone pings within ~30 s; approving via Telegram clears it.
- **Critique gate:** false-positive rate acceptable? (report observed pings over a day)

### Step 2.2 — Pool & flow UX polish
Inline-keyboard pool selection (multi-select toggles + Send/Skip buttons) replacing the
text protocol; `ABS STATUS` shows per-profile session/pool/usage glance; statusline dot
reflects daemon state; menu registration (`abs menu`) adds the new commands to Telegram's
"/" menu for daemon mode.

> **Pulled forward + shipped after the 1.5 live demo** (see `docs/v3/critique/2.2a.md`):
> **(a) resume-first ABS START** — successful launches (daemon HANDOFF *and* terminal
> `abs`) are recorded in `~/.abs/daemon/recents.json`; the flow's first screen offers up
> to 3 "▶ Resume <label> (<age>)" one-tap buttons (recorded mode + `--continue`) plus
> "🆕 New session"; and **(b) the Telegram "/" menu** — the daemon registers
> `/abs_start /abs_status /abs_pool` while idle and `/abs_exit`+`usage` in-session (via
> `set_my_commands`, debounced), the three `/abs_*` accepted as grammar aliases, and
> session-side `/abs_exit` wired into the abs.sh control hook. The inline-keyboard pool
> selection and the statusline dot remain for the full 2.2.
>
> **Observability also pulled forward + shipped** (see `docs/v3/critique/obs.md`):
> a structured, metadata-only event log (`absd/events.py` → `~/.abs/daemon/events.jsonl`,
> 12-event stable vocabulary, size-capped, corruption-tolerant reader) emitted from
> every daemon code path, and a consolidated read-only dashboard (`absd/status.py`)
> shared by `abs status` (appended after the v2 block) and `abs daemon status`
> (daemon header + per-profile state/live-session/pool/recents).
- **Agent verify:** callback-query unit tests for the keyboard state machine.
- **Pranjal verify:** the full phone flow feels good — his subjective sign-off is the gate.
- **Critique gate:** UX review with Pranjal; list of paper cuts → fix before Phase 3.

### Step 2.3 — Session lifecycle completeness
Crash vs clean-exit distinction in the "session ended" notice (exit code from engine when
available); `ABS START` on an already-live profile offers attach hint instead; max
concurrent sessions config; stale-handoff cleanup (marker older than N min with no live
session → warn + reclaim).
- **Agent verify:** pytest for each lifecycle edge; chaos test: random kills of daemon,
  session, or fake-telegram over 500 iterations — invariants: never two pollers on one
  token (fake-telegram asserts), pool never loses an acked message.
- **Pranjal verify:** none beyond spot checks — this step is agent-verification-heavy.
- **Critique gate:** chaos-test report reviewed.

## 9. Phase 3 — Sandbox (`abs sandbox`)

### Step 3.1 — Image + lifecycle CLI
`docker/sandbox/Dockerfile`: `ubuntu:24.04`, non-root user `dev`, git/curl/jq/build
basics, Node LTS, Claude Code installed, entrypoint keeping the container alive.
`abs sandbox build|create|start|stop|list|destroy`. `create <name>`: named volume for
`/home/dev` (project lives INSIDE, G7), **copies** host `~/.claude` credentials in
(`docker cp`, D8) with a one-line printed warning about the tradeoff, `--ports a:b,...`
publishes. No `--privileged`, no host mounts beyond nothing (only `docker cp` at create).
- **Agent verify:** build in CI-style script; container runs as non-root; `docker inspect`
  asserts no privileged/extra mounts; creds file exists inside with 0600 and differs from
  host after an in-container modification (divergence proof); published port reachable.
- **Pranjal verify:** `abs sandbox create t1 --ports 3000:3000` → shell in → claude is
  logged in (copied creds) → build a hello web app → open `localhost:3000` on host.
- **Critique gate:** security checklist 5.6 verified against `docker inspect` output.

> **BUILT** (see `docs/v3/critique/3.1.md`). **Design change (user-requested):** the
> project lives in a DEDICATED HOST FOLDER `<sandbox_root>/<name>` (default
> `~/Projects/sandboxes`, config `sandbox_root`, 0700) **bind-mounted** at
> `/home/dev/workspace` — not solely inside a named volume — so work syncs live to a
> local dir; that one folder is the only host path the container sees. `docker/sandbox/
> Dockerfile` (ubuntu:24.04, non-root `dev` at host uid, git/curl/jq/ripgrep/build/
> python3/Node LTS/Claude Code), `absd/sandbox.py` (`SandboxManager` + `abs sandbox
> build|create|list|start|stop|destroy`). 5.6 verified via `docker inspect`
> (Privileged=false, one bind, User=dev, no socket); creds copied (D8) not mounted,
> divergence proven; destroy keeps the workdir (user data) unless `--purge`.

### Step 3.2 — Sandbox sessions through the whole stack
Sandboxed profile sessions: engine pane runs `docker exec -it <name> abs-entry` where the
container carries enough ABS to run `claude --channels` with a profile's telegram state
**copied in at create** (token exposure documented; recommend a dedicated bot per sandbox
in docs). ABS START project keyboard gains a "🏖 sandbox:<name>" section. Ports announced
in the start confirmation. Attach/detach/kill ladder all work identically.
- **Agent verify:** parameterized Phase-1 e2e suite runs with a sandbox target (fake-claude
  inside the container); lifecycle + reclaim still correct when the container stops.
- **Pranjal verify:** phone-only: ABS START → pick sandbox → task: "build and serve a page
  on :3000" → open it on the host browser → attach from terminal into the container
  session → destroy sandbox; host filesystem untouched throughout (spot-check).
- **Critique gate:** re-run the hostile-reviewer walkthrough including container escape
  surface; document the R6 token-staleness observation plan.

> **BUILT** (see `docs/v3/critique/3.2.md`). A sandbox is a real ABS session target:
> the engine pane runs `docker exec -it absd-sbx-<name> absd-session <profile> …`, so
> claude runs INSIDE the box and the plugin polls from there. **Liveness adaptation:**
> a sandbox session's pid is container-namespace (host can't `kill -0`), so the daemon
> uses the ENGINE PANE ONLY (the marker records `sandbox: <name>`; FIX-C host-pid
> clobber is skipped; recovery uses the pane). The CONTAINER survives session end
> (reclaim kills only the engine pane; `abs sandbox stop|destroy` is separate). Flow:
> "🏖 Sandbox…" project entry → picker (existing + New) → mode → handoff. Terminal:
> `abs start sandbox [name]` (writes host session.pid = the docker-exec-client pid so
> the daemon yields). Image bumped v1→v2 (bun for the plugin's MCP server + the
> `absd-session` launcher). Same-bot creds only; dedicated-bot for untrusted work is
> a later stage.

### Stage 2 — `abs start new-bot` (provision a bot + profile from the terminal)

> **BUILT** (new scope, beyond the original plan; see `docs/v3/critique/newbot.md`).
> `abs start new-bot` creates a brand-new Telegram bot + ABS profile, pairs it, and
> launches a session — one interactive terminal flow, no daemon restart. **Token entry
> is terminal-only** (a token is a bearer credential; a Telegram-supplied token is the
> compromised-phone attack). The pairing PIN is **relayed to the operator's phone via an
> already-trusted bot** (the `default` profile's, else the first usable) as a convenience
> — it travels only to an already-paired+allowlisted chat, is short-lived
> (`PAIR_TIMEOUT`) and single-use, and confers nothing without the terminal-entered
> token. Reuses the whole setup path (`read_new_token`/`save_token` factored out of
> `prompt_token`; `do_pairing` gained optional relay args; `write_access`/`write_state`
> shared). Profile name derived from `@username` (`absd/newbot.py`, sanitized to
> `use_profile`'s jail). **Daemon profile rescan** added to the supervisor
> (`profile_rescan_s`, default 60s, 0 disables): re-runs `discover()`, spins up staggered
> pollers for new profiles, cleanly drops pollers for vanished ones, never disturbs
> existing pollers/live sessions, emits `profile_added`/`profile_removed`. Tests: daemon
> rescan, name derivation + relay-target selection (pure), and abs.sh guards
> (assert_no_live_session / interactive-only / bad-token) — no real Telegram.

## 10. Global testing & critique protocol

- **Never test against real Telegram/Anthropic in automated tests** (fakes from 0.3).
  Pranjal's manual checklists are the only live-API testing.
- Unit + integration tests run with `pytest` from the repo venv; integration tests that
  need tmux/herdr/docker skip cleanly with a visible SKIP reason when the tool is absent.
- Every step with a non-empty *Pranjal verify* gets a manual checklist in
  `docs/v3/manual-tests/<step-id>.md` — exact numbered phone/terminal actions with
  expected observations, executable without context. Steps whose Pranjal-verify is
  "none" may skip it or provide a minimal optional one. Checklists must stay
  *stable as later steps land* (scope commands to the step's own surface; don't
  hardcode whole-suite counts).
- Critique files (`docs/v3/critique/<step-id>.md`) must include: deviations, untested
  surface, and "what would break first in production" — honesty over polish.
- Existing v2 behavior is regression-tested each phase: TEST-LIST.md smoke items still
  pass (manual, Pranjal) and `shellcheck abs.sh` introduces no new findings vs the
  v2.6.0 baseline (see Step 0.1; a `.shellcheckrc`/baseline cleanup may land later as
  its own change).

## 11. Risk register

| ID | Risk | Mitigation |
|----|------|------------|
| R1 | herdr headless creation impossible/fragile (undocumented) | Phase 0.2 go/no-go; tmux reference engine (D4) |
| R2 | 409 handoff races lose/duplicate messages | Single-commit offset in HANDOFF; RECLAIM backoff; chaos test 2.3 |
| R3 | herdr pre-1.0 breaking changes | Pinned version (D13); recipes doc; adapter isolation |
| R4 | Daemon crash while sessions live | systemd restart; boot-time state re-derivation (1.4, 1.8) |
| R5 | Plugin polling behavior changes upstream | Handoff assumes only "plugin polls while alive"; RECLAIM probes rather than assumes |
| R6 | Sandbox credential copy goes stale (refresh-token rotation) | Documented; observed in 3.2; fallback = re-copy or per-sandbox login |
| R7 | Remote-start session stalls on permission prompt | D5 per-start mode choice + Phase 2.1 blocked pings |
| R8 | Reboot kills sessions silently | 1.8 reboot notification |
| R9 | Stolen phone / compromised Telegram | 5.1–5.3: fixed grammar, D6 path jail, terminal-only recovery for OFF/BLOCK, Tier-1 guard unchanged in sessions |
| R10 | Multi-poller rate limits / thundering herd | 50 s long-poll, staggered starts (4.4) |

## 12. Future (explicitly out of v3 scope — do not build)

- herdr marketplace **ABS plugin** (embed mute/usage/Telegram state in herdr's sidebar)
- Remote login flow from Telegram
- Podman backend; multi-user sandboxes
- Pool → scheduled tasks; usage-limit-aware start warnings
- Port absd to Rust or TS/Bun as a single binary, if ABS distribution demands it (see 4.4)
- Items in the ABS roadmap memory (separate backlog)
