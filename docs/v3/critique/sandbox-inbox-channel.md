# Critique — the deaf sandbox: making an in-box session actually reach Telegram

A live-test fix, not a planned step. The operator ran B3 (sandbox session over
Telegram, on a second bot) and reported: *"I run the ABS attach command, it connects
to the Claude code but ABS is not installed there. ABS status bar is not looking
there and no telegram message is getting to the ABS."*

Everything about the sandbox looked healthy — and it was. The session was launched
correctly (`daemon.log`: `HANDOFF complete — session launching in
…/sandboxes/box1`), the container was up, the network reached
`api.telegram.org` (HTTP 302, 0.4 s), the plugin files, `bun`, the bot token and the
allowlist were all present in the box. And yet the box never received a single
message, and the session ended on its own ~6.5 minutes later.

This file records what was actually wrong (two independent blockers), what was fixed,
and the one thing I got wrong on the first attempt.

## Diagnosis

The evidence that mattered was in the box, not the daemon:

    $ docker exec absd-sbx-box1 claude plugin list
      ❯ slack@claude-plugins-official
        Status: ✘ failed to load
        Error: Marketplace claude-plugins-official failed to load: cache-miss

`telegram@claude-plugins-official` was not even listed. So
`claude --channels plugin:telegram@claude-plugins-official` had nothing to resolve —
and it starts **silently** with no channel rather than failing loudly.

### Blocker 1 — host-absolute paths in the copied plugin metadata

    $ docker exec absd-sbx-box1 cat …/plugins/known_marketplaces.json
      "installLocation": "/home/pranjal/.claude/plugins/marketplaces/claude-plugins-official"

The box's home is `/home/dev`. That path does not exist there, so the marketplace
cache lookup misses. `installed_plugins.json` carried five more host paths.

This is the same *class* of bug as the `settings.json` hooks fixed a day earlier
(host `…/.ccgram/*.js` paths → "Cannot find module"): a wholesale
`docker cp ~/.claude` drags **host-absolute paths** into a container whose home
differs. Worth stating as a rule — anything copied into the box must be re-homed or
dropped, never assumed portable.

Proof it was causal, before writing any fix: rewriting the prefix by hand inside
box1 flipped the plugin from absent/`cache-miss` to `Version: 0.0.6 — ✔ enabled`.

### Blocker 2 — the workspace trust prompt (the real deadlock)

With the marketplace fixed, the channel *still* did not come up. Running claude in
the box under a pty and reading what it actually printed:

    Accessing workspace: /home/dev/workspace
    Quick safety check: Is this a project you created or one you trust?
    ❯ 1. Yes, I trust this folder    2. No, exit
      Enter to confirm · Esc to cancel

It blocks there **forever**. For a daemon-launched session that is a guaranteed
deadlock: nobody is at a keyboard to press Enter, so the Telegram channel is never
started and the box sits alive-but-deaf. This is exactly what the operator saw on
attach, and why the first fix alone was not enough.

The trust flag lives in `~/.claude.json` as
`projects[<abs path>].hasTrustDialogAccepted`. Note the interaction with the tidy-up
below: `projects` is dropped as host detail, so the box workspace must be *added*
back as a pre-trusted entry.

Pre-trusting is justified rather than convenient: the container is the trust
boundary — its only host mount is the operator's own dedicated sandbox folder, and
launching a session in it is an explicit operator action. Only the trust flags are
set; no other approval is granted on the operator's behalf.

## What was built

`SandboxManager._copy_credentials` is now a *sanitising* copy, not a blind one:

| step | what | why |
|---|---|---|
| 1 | `docker cp ~/.claude` → `/home/dev/.claude` | creds, `channels/`, `plugins/` |
| 2 | `~/.claude.json` → `/home/dev/.claude.json`, `projects` replaced by a pre-trusted `/home/dev/workspace` entry | required config (lives at HOME ROOT, so the dir copy misses it); kills the trust deadlock; drops host project history |
| 3 | strip `hooks` from copied `settings*.json` | host hook paths don't exist in the box |
| 4 | re-home `plugins/known_marketplaces.json`, `installed_plugins.json` | **the marketplace fix** — without it there is no channel at all |
| 5 | clear `channels/*/bot.pid` + `channels/*/inbox/*` — **at `start()`** | a host pid is meaningless in the box's PID namespace; a copied inbox would replay host messages |

A rewrite that would not re-parse as JSON is discarded rather than installed — better
to leave the original than to break the box with a corrupt config.

### The deaf-session guard (daemon)

The copy fix removes the cause; this removes the *silence*. A sandbox session's pane
liveness only proves the **host-side `docker exec` client** is running — it says
nothing about whether the session inside the box took over the bot. So a deaf box
left the daemon in `SESSION_LIVE`, **not polling**, quietly dropping every message
the operator sent. That is the worst possible failure mode: no error anywhere.

`Poller._sandbox_channel_failed()` now requires evidence — `pgrep -f
claude-plugins-official/telegram` inside the container — by the end of
`session_start_grace_s`. If it never appears: reclaim (polling resumes) and send
`SANDBOX_CHANNEL_DOWN_MSG`, a new `session_end` reason `sandbox_channel_down`.

Two deliberate properties:

- **Fails open.** No sandbox manager, or a docker error → treated as up. A probe we
  cannot run must never be grounds for killing a session the operator is using.
- **Latches.** Once the channel is seen, the check is done for that session; a later
  probe blip cannot kill a working session. Pane death remains the death signal.

## What I got wrong

The first version of step 5 ran the cleanup inside `_copy_credentials`, which runs
during `create`. But `docker create` leaves the container **stopped**, and
`docker exec` only works on a running one — so with `check=False` it failed
*silently*. I only caught it because I ran the real create path and the verification
commands errored with `container … is not running`.

Moved to `start()`, where the container is guaranteed up. `ensure_running()` was
deliberately left calling `start()` only when the box is **not** already running, so
the cleanup never deletes a *live* session's own `bot.pid` out from under it.

The lesson is the same one that produced this whole file: **a `check=False` call that
can never succeed looks exactly like one that always succeeds.** Verify against the
real thing.

## Tests

- 8 new unit tests in `tests/test_sandbox.py` — re-homing (including "skip when no
  host paths" and "never install unparseable JSON"), pre-trust entry, hooks strip,
  cleanup-on-start-not-create, and `ensure_running` leaving a live box untouched.
- 4 new in `tests/test_sandbox_session.py` — deaf session reclaimed after grace with
  the right reason *and* the right operator message (asserting the misleading login
  hint is **not** sent), latch behaviour, fail-open on probe error, and that a normal
  host session is never channel-checked.
- Both key tests were **mutation-checked**: neutering the fix makes them fail,
  restoring it makes them pass. A test that cannot fail proves nothing.
- Suite: 448 → 460 passing. `shellcheck abs.sh` clean.

## Residuals

> **Superseded (2026-07-26).** The first three below are closed by image v4 — see
> [abs-inside-the-sandbox.md](abs-inside-the-sandbox.md). An in-box session now runs
> through abs.sh, so it gets the status line, the hooks, the Bash guard and a
> `session.pid` that `abs exit` can signal. They are kept here for the record.

- ~~**In-box kill ladder still open.**~~ `ABS EXIT` from the phone did not end an in-box
  session (the hooks that implement the ladder are host-side, and are explicitly
  stripped from the box). End a sandbox session with `abs sandbox stop <name>`.
- ~~**No in-box status line.**~~ The operator expected the ABS status bar inside the box.
  `statusLine` is host-scripted; wiring an in-box equivalent was unbuilt.
- ~~**`abs` is not installed in the box**~~ — was by design (orchestration stays
  host-side). v4 installs it; the *orchestration* verbs still refuse in-box.
- The `restricted` assistant shares the in-box launch path, so it inherits both fixes;
  it has **not** yet been exercised end-to-end on a real second bot. **Still open.**
