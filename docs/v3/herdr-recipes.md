# herdr headless recipes

Verbatim working recipes from the Step 0.2 spike (2026-07-23). This file is the
spec `HerdrEngine` (Step 1.2) is built from. Every command below was run and
observed on this machine. Do **not** copy herdr source into this repo (D3, AGPL) —
these are commands and observed JSON shapes only.

## Pinned binary (D13)

| Field | Value |
| --- | --- |
| Version | **herdr 0.7.5** (latest stable at spike time; `prerelease: false`) |
| Asset | `herdr-linux-x86_64` |
| Download URL | `https://github.com/ogulcancelik/herdr/releases/download/v0.7.5/herdr-linux-x86_64` |
| Install path | `~/.local/bin/herdr` (chmod +x) |
| SHA-256 (this download) | `3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253` |
| Socket protocol version | `17` (`herdr api schema` → `protocol: 17`) |

Pinned install (no `curl | sh`, auditable):

```bash
curl -fL -o ~/.local/bin/herdr \
  https://github.com/ogulcancelik/herdr/releases/download/v0.7.5/herdr-linux-x86_64
chmod +x ~/.local/bin/herdr
herdr --version   # -> "herdr 0.7.5"
```

### First-run footprint / config

- herdr writes **no** `config.toml` on first run — it uses built-in defaults.
  (`herdr --default-config` prints them; do not materialize a file we don't need.)
- Update channel defaults to **`stable`** (`herdr channel show`). The spike did
  **not** change it. Do not run `herdr channel set …` or `herdr update`.
- Config dir: `~/.config/herdr/` — holds `.plugins.lock` and one dir per named
  session under `sessions/<name>/` (sockets + `herdr-server.log`).
- State dir: `~/.local/share/herdr/agent-detection/` — cached **remote** agent
  detection manifests, fetched automatically the first time a server runs so it
  can classify agents (e.g. claude). Disable background fetches with
  `[update] manifest_check = false` if we ever need determinism.

## Session model (how ABS drives it headlessly)

- Named session selector is the env var **`HERDR_SESSION=<name>`** (or `--session
  <name>` on any command). Every CLI call below inherits `HERDR_SESSION`.
- Per-session API socket (documented and confirmed):
  `~/.config/herdr/sessions/<name>/herdr.sock`
  (default session, unused by ABS, is `~/.config/herdr/herdr.sock`).
- Socket transport: **newline-delimited JSON**, one request per line, response
  echoes the request `id`. `herdr api schema --json` dumps the full protocol.
- **CLI subcommands do NOT auto-spawn a server.** Only the interactive `herdr`
  attach path spawns one. So ABS must start the headless server explicitly first
  (below); `workspace create` / `pane …` against a dead socket just error.

### 1. Start a headless server (NO client attaches)

`herdr server` is the explicit headless server (`herdr` alone would open the TUI).
It runs in the **foreground**, so background it. It creates the socket and runs
with nothing attached — exactly the daemon model.

```bash
HERDR_SESSION=abs-<profile> nohup herdr server >/path/to/server.log 2>&1 &
```

Readiness check — poll `session list` until the named session shows `running:true`
(the socket file may appear a beat before the server accepts calls):

```bash
HERDR_SESSION=abs-<profile> herdr session list --json
# {"sessions":[{"name":"default",...,"running":false},
#              {"name":"abs-<profile>","running":true,
#               "socket_path":".../sessions/abs-<profile>/herdr.sock"}]}
```

Server log line on start:
`herdr server running; you can use any herdr CLI command in another terminal.`

### 2. Create a workspace with a specific cwd (and inject env)

```bash
HERDR_SESSION=abs-<profile> herdr workspace create \
  --cwd /abs/project/path --label abs-<profile> --no-focus \
  --env KEY=VALUE --env KEY2=VALUE2
```

**Env injection (added Step 1.2).** `workspace create --env KEY=VALUE` (repeatable)
sets environment variables **for the launched process** — this is herdr's
per-pane env mechanism (the analogue of tmux `new-session -e`). Verified on 0.7.5:
the values reach the process launched by a later `pane run` (confirmed via
`/proc/<pid>/environ`) and do **not** appear on the pane's command line / `ps` /
pane title. Visibility tradeoff, same as tmux `-e`: any same-user process can read
them via `/proc/<pid>/environ`, so this is fine for non-secret launcher vars but
**secrets must never be passed this way** (PLAN 5.5). `HerdrEngine.create_session`
uses one `--env` per env item. (The Step 0.2 spike passed the pid-file path only
inside the command args; `--env` is the general mechanism and is what the engine
uses.)

Response (single JSON line) — pull the root pane id out of it:

```jsonc
{"result":{"type":"workspace_created",
  "workspace":{"workspace_id":"w1", ...},
  "tab":{"tab_id":"w1:t1", ...},
  "root_pane":{"pane_id":"w1:p1","cwd":"/abs/project/path", ...}}}
```

```bash
PANE=$(herdr workspace create --cwd "$CWD" --label "$L" --no-focus \
       | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['root_pane']['pane_id'])")
# -> w1:p1  (first workspace is always w1, root pane w1:p1)
```

Creating a workspace also creates its first tab and a root pane running an
interactive shell in `--cwd`. `--no-focus` keeps it headless-clean.

### 3. Run a long-lived command in the pane

`pane run` submits the command + Enter atomically into the pane's shell (honors
bracketed paste). This is how ABS launches the real session command
(`abs.sh --daemon-start …`); here we use the fake-claude harness.

```bash
HERDR_SESSION=abs-<profile> herdr pane run w1:p1 \
  "$REPO/tests/harness/fake-claude --mode normal --pid-file $PID_FILE"
```

The process runs in the pane's foreground; the pid file the launcher writes is
the same `session.pid` the daemon already relies on (PLAN 4.1).

### 4. Detect liveness programmatically (reliable signals)

Two independent, reliable signals — ABS should use BOTH:

```bash
# (a) session.pid written by the launcher — cheap, engine-agnostic:
kill -0 "$(cat "$PID_FILE")"

# (b) herdr's own view of the pane's foreground process:
HERDR_SESSION=abs-<profile> herdr pane process-info --pane w1:p1
```

`pane process-info` returns, verbatim shape:

```jsonc
{"result":{"type":"pane_process_info","process_info":{
  "pane_id":"w1:p1","shell_pid":727737,
  "foreground_process_group_id":729709,
  "foreground_processes":[
    {"pid":729709,"name":"bash","cmdline":"bash .../fake-claude --mode normal ...",
     "cwd":"/abs/project/path","argv":[...]},
    {"pid":729719,"name":"sleep","cmdline":"sleep 3600", ...}]}}}
```

**CORRECTION (Step 1.2 — the Step 0.2 wording was subtly wrong).** herdr **keeps
the pane and session alive after the launched command exits**: the pane's
interactive shell reclaims the foreground (like tmux `remain-on-exit on`). So
"session `running:true`" and "the pane has a foreground process" are BOTH still
true after the command dies (the idle shell is a foreground process) — neither is
a liveness signal on its own. Empirically verified: kill the launched command and
`session list` still shows `running:true`, `pane list` still shows `w1:p1`, and
`process-info` still returns a foreground process — the bare shell.

The reliable, launcher-agnostic signal is the **foreground process group vs the
shell**: while a command runs via `pane run` it owns the pane's foreground, so
`foreground_process_group_id != shell_pid`; when it exits, the shell reclaims the
foreground and `foreground_process_group_id == shell_pid`. Verified transition:
command foregrounds ~0.7 s after `pane run` (the pane shell sources `.bashrc`
first — conda init etc.), and the signal flips back within ~0.02 s of the command
dying.

So `HerdrEngine.is_alive(profile)` = session `running:true` **and**
`foreground_process_group_id != shell_pid` (from `pane process-info`). This is
engine-native and does **not** depend on the launcher writing `session.pid` —
correct, because the `Engine.is_alive(profile)` contract only receives the profile
and must match TmuxEngine, which keys on its own state, not on launcher
cooperation. The `foreground_process_group_id` (while running) equals the launched
command's own pid/`$$`, so it is also what the engine reports as `SessionInfo.pid`.
The launcher's `session.pid` remains a cheap independent daemon-level check
(PLAN 4.1), just not the engine's `is_alive` signal.

### 5. Attachability without an interactive TTY

- **Non-interactive proof (usable in tests/daemon):** a read-only observer
  streams live base64-ANSI frames from the pane — proves it is attachable/
  streamable with no TTY:

  ```bash
  timeout 4 herdr terminal session observe w1:p1 --cols 80 --rows 24
  # -> {"bytes":"G1s/MjAyNmg..."}   (newline-delimited terminal.frame records)
  ```

- **Full human attach needs a real terminal.** These are the human recipes
  (documented for Pranjal, verified interactively later):

  ```bash
  HERDR_SESSION=abs-<profile> herdr            # full workspace TUI
  HERDR_SESSION=abs-<profile> herdr session attach abs-<profile>
  ```

  Detach with `ctrl+b q`; the server + pane keep running. **Dead end:** running
  full attach without a TTY (e.g. stdin `</dev/null` in a script) **panics**
  (`ratatui … failed to initialize terminal: … No such device or address`). Do
  NOT use full attach for automation — use `terminal session observe`/`control`.

### 6. Clean kill + teardown (no orphans)

```bash
HERDR_SESSION=abs-<profile> herdr pane close w1:p1     # kills the pane's process group
HERDR_SESSION=abs-<profile> herdr session stop abs-<profile>   # stops the server
HERDR_SESSION=abs-<profile> herdr session delete abs-<profile> # removes saved session state
```

- `pane close` terminates the pane's whole process group — the fake-claude bash
  **and** its backgrounded `sleep` child were both gone afterwards (confirmed via
  `kill -0`). herdr kills the group, unlike a bare `SIGTERM` to the parent.
- `session stop` leaves **no** herdr process running (`pgrep herdr` empty). The
  session then shows `running:false`; `session delete` clears the residual dir.
- **Added Step 1.2:** `session stop` **alone** also reaps the pane's children —
  verified: `session stop` without a preceding `pane close` left the fake-claude
  and its `sleep` child dead (no orphans under init). `HerdrEngine.kill` still
  does `pane close` → `session stop` → `session delete` (belt-and-suspenders +
  clears saved state), but a bare `session stop` is sufficient to avoid orphans.

## Item 7 — agent status events (real claude), socket `events.subscribe`

Proven live with the real (logged-in) `claude` binary in a headless pane. This is
what Phase 2.1 (blocked-session pings, herdr-only) depends on.

### Subscribe (raw socket, newline-delimited JSON)

Connect a client to `~/.config/herdr/sessions/<name>/herdr.sock` and send ONE
request line; the socket stays open and pushes event lines.

```json
{"id":"sub_1","method":"events.subscribe","params":{"subscriptions":[{"type":"pane.agent_status_changed","pane_id":"w1:p1"}]}}
```

- Subscription filter requires `type` + `pane_id`; `agent_status` is **optional**
  (omit it to receive ALL transitions; include e.g. `"agent_status":"blocked"`
  to filter to one). `AgentStatus` enum: `idle | working | blocked | done | unknown`.
- First reply acknowledges: `{"id":"sub_1","result":{"type":"subscription_started"}}`.

### Verbatim events captured

Launched with `herdr pane run w1:p1 "claude"`, then `herdr agent prompt w1:p1
"<text>"`. Observed, verbatim (leading `[t]` is the subscriber's relative clock,
not part of the payload):

```jsonc
[ 0.00s] {"id":"sub_1","result":{"type":"subscription_started"}}
[ 1.21s] {"data":{"agent":"claude","agent_status":"idle","pane_id":"w1:p1","workspace_id":"w1"},"event":"pane.agent_status_changed"}
[10.23s] {"data":{"agent":"claude","agent_status":"working","pane_id":"w1:p1","workspace_id":"w1"},"event":"pane.agent_status_changed"}
[15.70s] {"data":{"agent":"claude","agent_status":"idle","pane_id":"w1:p1","workspace_id":"w1"},"event":"pane.agent_status_changed"}
```

Event shape for a status change: `{"event":"pane.agent_status_changed","data":{
"agent","agent_status","pane_id","workspace_id"}}`.

### What was and was NOT proven for item 7

- **Proven:** herdr auto-detects the real `claude` as an agent (`agent:"claude"`,
  no `herdr integration install` needed — screen-manifest detection) and pushes
  `working` and `idle` transitions over `events.subscribe`. `herdr agent list` /
  `herdr agent get w1:p1` also expose `agent_status` and `state_change_seq` for a
  poll-based fallback.
- **NOT captured: `blocked`.** The attempt asked claude to run a shell command
  (`echo spike-hello`); on THIS machine claude auto-approved it (its own
  permission config — ABS approved nothing) and it went `working → idle`, never
  `blocked`. Triggering `blocked` needs a claude in Normal mode facing a
  non-allowlisted approval prompt — deferred to human observation
  (`docs/v3/manual-tests/0.2.md`). herdr's blocked detection is deliberately
  strict (only matches known approval/question UI), so Phase 2.1 must budget for
  prompts that briefly read `idle` before herdr recognizes the block.

### CLI helpers seen during the run (for HerdrEngine / Phase 2)

```bash
herdr agent list                     # detected agents + status (JSON)
herdr agent get w1:p1                # one agent: agent_status, state_change_seq, terminal_title
herdr agent prompt w1:p1 "<text>" [--wait --until idle --until done --timeout 60000]
herdr pane read w1:p1 --source detection --lines 20   # bottom-buffer used by detection
```

**Caveat (`agent prompt --wait`):** a prompt whose turn settles in <5 s can return
`agent_prompt_stalled` ("no observed state change within 5000 ms") even though it
succeeded — herdr never saw a `working` flip. Treat `agent_prompt_stalled` as
non-fatal when the answer is fast; do not gate ABS logic on it.

## Gotchas / dead ends (so Phase 1 doesn't re-discover them)

1. CLI subcommands never auto-start a server — you MUST `herdr server` first.
2. `herdr server` is foreground; background it and poll `session list` for
   `running:true` (socket file appears slightly before the server is ready).
3. Full `herdr`/`herdr session attach` without a TTY panics — automation must use
   `terminal session observe` (read) / `terminal session control` (write).
4. `pane close` kills the process group (clean); a bare parent `SIGTERM` would
   orphan children (see critique 0.2 — a pre-existing fake-claude harness leak).
5. `agent prompt --wait` can false-negative with `agent_prompt_stalled` on fast turns.
6. `blocked` may momentarily show as `idle` until herdr matches the approval UI.
7. The herdr socket is NOT a security boundary — any same-user process can drive
   it (PLAN 5.7); ABS never delegates trust to herdr state.
