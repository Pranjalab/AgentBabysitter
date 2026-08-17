# Manual test — Observability (event log + dashboard)

Short check that the structured trail and the consolidated dashboard show the full
picture after one real session cycle. Your setup: one profile `default`, daemon
installed.

> One-time: this ships new modules, so make the daemon load them:
> `abs daemon install && systemctl --user restart absd`.

## 1. Run one ABS START / EXIT cycle

1. Make sure no session is live, then from the phone: **`ABS START`** → pick a
   project (or **▶ Resume**) → **Normal**. Wait for the "🚀 Started …" confirmation.
2. Send the session a quick task, then end it: **`ABS EXIT`** (or `/abs_exit`).
   Wait for **"⏹ Session ended."**.

## 2. The dashboard — `abs status`

3. At the terminal:

   ```
   abs status
   ```

   Expected: your usual v2 pairing block, then a **new v3 section** like:

   ```
   Agent Babysitter daemon (absd)
     daemon    running (absd 2.6.0, up 12m)
     profiles  1 managed

     default: polling  pool=0
       recent   llm (2m), AgentBabysitter (1h)
   ```

   During a *live* session (repeat step 1 and run `abs status` before EXIT) the
   `default` line reads `yielding-to-session` with a `session` line (project, `via
   herdr`, age) and an `attach   abs attach default` line.

4. `abs daemon status` shows the **same** dashboard (below the `systemctl status`
   output). Stop the daemon (`abs daemon stop`) and re-run — the header now says
   **stopped** and the per-profile last-known state still renders.

## 3. The machine trail — `events.jsonl`

5. Tail the raw event log:

   ```
   tail ~/.abs/daemon/events.jsonl
   ```

   Expected: one JSON object per line — `daemon_start`, `command` (ABS START),
   `handoff`, `session_start`, `poller_state`, then on EXIT `session_end`
   (`reason`, `lived_s`), `engine_kill`, `reclaim_done`. Note there is **no message
   text** anywhere — `message_pooled` lines carry only `update_id`.

6. Filter with `jq` — e.g. the session lifecycle for one profile:

   ```
   jq -c 'select(.profile=="default" and (.event=="handoff" or .event=="session_start" or .event=="session_end"))' \
     ~/.abs/daemon/events.jsonl
   ```

   or "how long did each session live?":

   ```
   jq -r 'select(.event=="session_end") | "\(.ts) \(.profile) \(.reason) lived=\(.lived_s)s"' \
     ~/.abs/daemon/events.jsonl
   ```

## What this does NOT cover

- Crash-vs-clean exit distinction in `session_end.reason` (Step 2.3) — it reads
  `exited` for both; `failed_start` and `foreign_takeover_cleared` are distinct.
- Log rotation beyond the crude 5 MB → `.old` roll (Step 1.8).
- A v2-only machine (no `absd/`/venv): `abs status` simply omits the v3 section.
