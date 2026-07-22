#!/usr/bin/env bash
#
# tests/integration/herdr_spike.sh — Step 0.2 herdr headless lifecycle spike.
#
# Proves the full headless session lifecycle (PLAN.md Step 0.2, items 1-6) with
# NO client ever attaching, using herdr's CLI + JSON socket API. This is the
# agent-verify artifact for the HerdrEngine go/no-go gate. The recipes it
# exercises are documented verbatim in docs/v3/herdr-recipes.md.
#
#   1. start a headless herdr server for a named session (no client)
#   2. create a workspace with a specific cwd (a scratch dir, NOT the repo)
#   3. run a long-lived command in a pane (tests/harness/fake-claude)
#   4. detect liveness programmatically (pane process-info + session.pid)
#   5. prove detach-less operation + non-interactive attachability
#      (terminal session observe streams frames; full TTY attach is a human recipe)
#   6. kill the pane cleanly, verify gone, stop the server, verify no orphans
#
# Item 7 (real-claude agent_status events) is NOT here: it needs a logged-in
# claude and human observation. See docs/v3/manual-tests/0.2.md.
#
# Exit 0 on success. Skips cleanly (exit 0 + "SKIP: herdr not installed") when
# herdr is absent. Cleans up after itself even on failure (trap).

set -u

SESSION="abs-spike-test-$$"
export HERDR_SESSION="$SESSION"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FAKE_CLAUDE="$REPO_ROOT/tests/harness/fake-claude"

STEP=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
step() { STEP=$((STEP+1)); printf '\n[%d] %s\n' "$STEP" "$1"; }
FAILED=0

herdr_bin=""
if command -v herdr >/dev/null 2>&1; then
  herdr_bin="$(command -v herdr)"
elif [ -x "$HOME/.local/bin/herdr" ]; then
  herdr_bin="$HOME/.local/bin/herdr"
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -z "$herdr_bin" ]; then
  echo "SKIP: herdr not installed"
  exit 0
fi

# Scratch working dir for the session cwd + pid file — NOT inside the repo.
SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/herdr-spike.XXXXXX")"
WS_CWD="$SCRATCH_DIR/ws"
PID_FILE="$SCRATCH_DIR/session.pid"
mkdir -p "$WS_CWD"

SERVER_LOG="$SCRATCH_DIR/server.log"

cleanup() {
  # Best-effort teardown; safe to call multiple times / on partial failure.
  herdr session stop "$SESSION"   >/dev/null 2>&1
  herdr session delete "$SESSION" >/dev/null 2>&1
  [ -d "$SCRATCH_DIR" ] && rm -rf "$SCRATCH_DIR" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "herdr: $herdr_bin ($(herdr --version 2>/dev/null))"
echo "session: $SESSION"
echo "scratch: $SCRATCH_DIR"

# --- 1. headless server, no client -----------------------------------------
step "start headless server for named session (no client attaches)"
# 'herdr server' is the explicit headless server; it runs in the foreground,
# so background it. HERDR_SESSION selects the named session.
nohup herdr server >"$SERVER_LOG" 2>&1 &
# Wait for the socket to come up.
SOCK="$HOME/.config/herdr/sessions/$SESSION/herdr.sock"
for _ in $(seq 1 50); do
  [ -S "$SOCK" ] && herdr session list --json 2>/dev/null | grep -q "\"$SESSION\"" && break
  sleep 0.2
done
if herdr session list --json 2>/dev/null | grep -q "\"name\":\"$SESSION\",\"running\":true" \
   || herdr session list --json 2>/dev/null | python3 -c "import json,sys;print(any(s['name']=='$SESSION' and s['running'] for s in json.load(sys.stdin)['sessions']))" 2>/dev/null | grep -q True; then
  ok "server running; socket at $SOCK"
else
  fail "server did not come up"
  echo "server log:"; cat "$SERVER_LOG" 2>/dev/null
  exit 1
fi

# --- 2. workspace with a specific cwd --------------------------------------
step "create workspace with --cwd (scratch dir, not the repo)"
WS_JSON="$(herdr workspace create --cwd "$WS_CWD" --label abs-spike --no-focus 2>&1)"
PANE_ID="$(printf '%s' "$WS_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['root_pane']['pane_id'])" 2>/dev/null)"
GOT_CWD="$(printf '%s' "$WS_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['root_pane']['cwd'])" 2>/dev/null)"
if [ -n "$PANE_ID" ] && [ "$GOT_CWD" = "$WS_CWD" ]; then
  ok "workspace created; root pane $PANE_ID; cwd $GOT_CWD"
else
  fail "workspace create failed (pane='$PANE_ID' cwd='$GOT_CWD')"; echo "$WS_JSON"; exit 1
fi

# --- 3. long-lived command in a pane ---------------------------------------
step "run long-lived fake-claude in the pane"
herdr pane run "$PANE_ID" "$FAKE_CLAUDE --mode normal --pid-file $PID_FILE" >/dev/null 2>&1
# Wait for the pid file the launcher writes (== abs.sh session.pid semantics).
FC_PID=""
for _ in $(seq 1 25); do
  [ -s "$PID_FILE" ] && FC_PID="$(cat "$PID_FILE" 2>/dev/null)" && [ -n "$FC_PID" ] && break
  sleep 0.2
done
if [ -n "$FC_PID" ] && kill -0 "$FC_PID" 2>/dev/null; then
  ok "fake-claude running; session.pid=$FC_PID"
else
  fail "fake-claude did not start / pid file not written"; exit 1
fi

# --- 4. programmatic liveness ----------------------------------------------
step "detect liveness programmatically (pane process-info + session.pid)"
PROC_JSON="$(herdr pane process-info --pane "$PANE_ID" 2>&1)"
if printf '%s' "$PROC_JSON" | grep -q "fake-claude"; then
  ok "pane process-info shows fake-claude in the foreground"
else
  fail "pane process-info did not report fake-claude"; echo "$PROC_JSON"
fi
if kill -0 "$FC_PID" 2>/dev/null; then
  ok "session.pid $FC_PID is alive (kill -0)"
else
  fail "session.pid not alive"
fi

# --- 5. detach-less operation + non-interactive attachability ---------------
step "prove detach-less operation and attachability (no client)"
# Nothing is attached: confirm the server reports no foreground client by
# showing the session is running while we only ever drove it over the socket.
if herdr session list --json 2>/dev/null | python3 -c "import json,sys;print(any(s['name']=='$SESSION' and s['running'] for s in json.load(sys.stdin)['sessions']))" 2>/dev/null | grep -q True; then
  ok "session runs headless (only socket/CLI ever touched it)"
else
  fail "session not running headless"
fi
# Attachability WITHOUT an interactive TTY: a read-only terminal observer streams
# live frames from the pane. Full 'herdr session attach' needs a real TTY (it is
# a human recipe in docs/v3/manual-tests/0.2.md); do NOT run it here.
OBS="$(timeout 4 herdr terminal session observe "$PANE_ID" --cols 80 --rows 24 2>&1 | head -c 200)"
if printf '%s' "$OBS" | grep -q '"bytes"'; then
  ok "terminal session observe streamed a frame (pane is attachable)"
else
  fail "could not observe pane terminal stream"; echo "$OBS"
fi

# --- 6. clean kill + no orphans --------------------------------------------
step "kill pane, verify gone, stop server, verify no orphans"
herdr pane close "$PANE_ID" >/dev/null 2>&1
GONE=0
for _ in $(seq 1 25); do
  kill -0 "$FC_PID" 2>/dev/null || { GONE=1; break; }
  sleep 0.2
done
if [ "$GONE" = 1 ]; then
  ok "pane close terminated fake-claude (pid $FC_PID gone)"
else
  fail "fake-claude survived pane close (pid $FC_PID)"
fi

herdr session stop "$SESSION" >/dev/null 2>&1
sleep 1
if herdr session list --json 2>/dev/null | python3 -c "import json,sys;print(any(s['name']=='$SESSION' and s['running'] for s in json.load(sys.stdin)['sessions']))" 2>/dev/null | grep -q True; then
  fail "server still running after session stop"
else
  ok "server stopped; session no longer running"
fi
# No herdr server process for THIS session should remain.
if pgrep -af 'herdr server' 2>/dev/null | grep -q "$SESSION"; then
  fail "orphan herdr server process for $SESSION"
else
  ok "no orphan herdr server process for this session"
fi

echo
if [ "$FAILED" = 0 ]; then
  echo "PASS: herdr headless lifecycle spike (items 1-6)"
  exit 0
else
  echo "FAIL: one or more spike steps failed"
  exit 1
fi
