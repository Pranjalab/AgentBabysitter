# ABS v3 — Full Acceptance Test Checklist

One ordered run covering everything built: the always-on daemon (Phase 1), sandbox
sessions, and the restricted assistant. Each item says **where** (terminal 🖥 or
Telegram 📱) and **what you should see** (✅). Do them in order — later steps assume
earlier ones.

Legend: 🖥 terminal · 📱 Telegram · ⚠ caution · �️ needs a spare bot token

---

## STATUS (updated 2026-07-25, live-testing session)

**✅ Done / verified this session**
- Daemon current build running (has sandbox wiring). A1–A7 passed in earlier testing.
- A12 new-bot: the `set -e` crash **fixed**; PIN relay worked; `abs_test_001_bot` paired.
- B1 sandbox create: box1 created.
- **Sandbox credential bug fixed** (missing `~/.claude.json` + host `ccgram` hooks dragged
  into the box). box1 re-verified ready: claude runs, telegram plugin + bun + token present.
- Cleaned orphan/stray sessions that were causing poller contention.

**🔧 Sandbox-over-Telegram fixes (2026-07-25, after the live B3 failure)**
The box ran but was **deaf** — no status line, no messages. Two separate blockers,
both in the credential copy, both fixed + regression-tested:
1. `plugins/known_marketplaces.json` / `installed_plugins.json` recorded
   `installLocation` under the HOST home → marketplace **"cache-miss"** in the box →
   `--channels plugin:telegram@…` couldn't resolve → **no channel at all**. Now re-homed
   to `/home/dev`. (Proof: box went from *not listed* to `telegram 0.0.6 ✔ enabled`.)
2. Claude Code blocked forever on **"Is this a project you… trust?"** — unanswerable in
   a daemon-launched session. The box workspace is now **pre-trusted**.
Plus: stale `bot.pid`/`inbox` cleared at container start, host hooks + host project
history no longer copied in, and a new **deaf-session guard** — if a sandbox session's
in-box channel never comes up within the start-grace window the daemon reclaims the bot
and tells you, instead of sitting in SESSION_LIVE silently dropping every message.

**🧰 ABS is now INSIDE the box (2026-07-26, image v4)**
Up to v3 an in-box session ran bare `claude` — so the box had no `abs` command, no
status bar, no Bash guard, no `ABS STOP/EXIT/MUTE`, and `abs exit` could not reach it.
A sandbox session now runs the **same launcher the host runs**, just inside the
container (`bash /opt/abs/abs.sh --profile <p> --daemon-start …`). abs.sh + absd/ are
`docker cp`'d to `/opt/abs` at container start and before every session, and the
profile's `rc.json` is seeded in (minus host paths) so abs.sh sees it as paired.
⚠ **This needs a v4 box.** An existing v3 box keeps working but gains nothing — it must
be re-created: `abs sandbox destroy box1` (keeps your workdir) → `abs sandbox create box1`.
Details: `docs/v3/critique/abs-inside-the-sandbox.md`.

**✅ Passed 2026-08-05 (driven from the desk, no phone needed)**
- **A10** crash recovery — `systemctl --user restart absd` with a live session: the daemon
  re-derived `LIVE session (pid 487341); starting in yielding state`, did not reclaim, and
  the session survived the restart untouched.
- **A11** event trail — 92 events, valid JSONL, `jq` filters clean. Privacy holds: no key
  matching text/body/message/prompt/content anywhere; `message_pooled` carries only an
  `update_id`, `command` only the command *name*.
- **B1** create — `b4box` built on `absd-sandbox:v4`; `abs` present in the box after `start`.
- **B2** isolation/net/sync — creds present, claude 2.1.218, `git clone` works, `abs 2.6.0`
  in the box, `abs status` shows only `/home/dev/…` paths, `abs sandbox`/`abs daemon`
  refuse in-box (exit 1), host FS invisible (`/home/pranjal*` all absent), the workdir is
  the *only* mount, sync works both ways with `pranjal`↔`dev` uid mapping, `-p 3000` published.
- **B4** destroy — container removed, workdir and its marker file kept.
  📝 Behaviour is *safer* than this doc claimed: it does **not** prompt, it simply never
  deletes user data without `--purge`. Doc corrected below.

**✅ B3 PASSED 2026-08-05 — and found a silent bug**
The operator ran it: `ABS START` → 🏖 Sandbox → `v4box` on the second bot. The first
attempt got **no reply**. Attaching showed why — the box was up, the plugin had
delivered the message, and Claude *inside* simply was not logged in. After logging in
from a terminal it answered normally. So the round trip works; the failure was auth.

Why nothing warned: the pre-launch login check tests the **host** credentials, which
were fine. `creds_present()` (file exists and is non-empty) is the only box-side check
and it answers the wrong question — the credentials copied in at `create` are a frozen
snapshot that expires while the host's keep refreshing, so the file stays present and
well-formed while authentication fails. Demonstrated: on a box with `claudeAiOauth`
stripped but the file left at 757 bytes, `creds_present()` → `True`, `login_ok()` → `False`.

Fixed: `SandboxManager.login_ok()` asks `claude auth status` in the box (JSON, no
inference cost); the daemon gates a sandbox handoff on it and refuses with the exact
command instead of launching a box that will read messages and answer none.
`abs sandbox login <name>` is the fix it names — it did not exist before (only
`abs restricted login` did). A probe that cannot run returns `None` and **fails open**,
so a docker hiccup can never block a session that would have worked.

**🔲 Remaining — the whole list, and why each is blocked**

Everything else has passed. A1–A7 and A12 in earlier sessions; A10, A11, B1, B2, B4
from the desk on 2026-08-05; B3 by the operator the same day.

- **A8** (kill ladder) and **A9** (login detection) — both begin with `abs exit`, which
  ends the operator's live session and the Telegram bridge with it. A9 additionally
  moves `~/.claude/.credentials.json` aside, so nothing else can be running. One batch,
  at the desk.
- **C1–C5** (restricted assistant) — needs a spare bot token from @BotFather, separate
  from both the babysitter bot and the website signup bot. Runs *alongside* a live
  session (its own bot, own poller), so it does not need `abs exit`.

**🔧 Two fixes after the 2026-08-06 live run (messages going missing)**
The operator reported the box "not responding — some messages were not getting back".
Diagnosis from the event trail: a sandbox launch was reclaimed at 30s for
`failed_start`, the operator relaunched, and from then on updates were split.
1. **`engine.kill()` does not reach inside the container.** A sandbox session is a
   `docker exec` client; killing it leaves the in-box claude *and its Telegram
   plugin* running. Proven directly — started a process in a box via `docker exec`,
   killed the host client, and the in-box process was still there. Two pollers on one
   bot token means Telegram hands each update to whoever asks first, so half the
   messages vanish with nothing erroring. `SandboxManager.kill_session()` now reaps
   the in-box half (via the in-box `session.pid`, then a self-excluding `pkill`), and
   the daemon calls it from `_kill_engine_session` — the one choke point RECLAIM and
   handoff self-heal both use. Verified on a real container: session, plugin and pid
   file all gone.
2. **The 30s grace predated ABS-in-the-box** and was declaring healthy v4 launches
   dead — which is what triggered (1). Sandbox launches now use
   `sandbox_start_grace_s` (120s); host sessions keep 30s.

⚠ **Both need a daemon restart to take effect:** `systemctl --user restart absd`.

**Known gap, not covered by the login gate**
`login_ok` runs at *handoff*. A token that expires **mid-session** puts the box back in
the silent state, because the deaf-session guard watches whether the in-box Telegram
plugin is alive — and it is; only the model call fails. Detecting that needs a periodic
in-session probe, which is unbuilt. In practice a fresh in-box login lasts far longer
than a session, so this is a residual rather than a live problem.

**Two constraints that matter:**
1. A sandbox session on the `default` bot **shares @Claudepranbot**, so it *replaces* this
   babysitter — you can't run both at once (one bot, one poller). B3 requires `abs exit` first.
2. The restricted assistant uses its **own separate bot** (2nd token), so it runs *alongside*
   the babysitter with no conflict — C can be tested without exiting this session.

---

## STEP 0 — Load the latest code (do this first)

- 🖥 `systemctl --user restart absd`
- 🖥 `abs daemon status` → ✅ `active (running)`, absd version line, `profiles 1 managed`,
  `default: yielding-to-session` (because your terminal session is live).
- 🖥 `abs doctor` → ✅ every line a green ✓ (unit enabled, linger, both engines,
  config valid, files 0600, no daemon errors).

---

## PART A — The daemon (Phase 1)

### A1 — Daemon does NOT interfere with a live session
- 📱 With your normal session running, send the bot any message (e.g. "hi").
- ✅ It reaches THIS live session normally; `abs daemon status` still shows
  `yielding-to-session`, pool unchanged. The daemon stayed silent.

### A2 — Handover to the daemon + pooling
- 🖥 `abs exit` (ends the session). Wait ~30s.
- 📱 Send the bot a message.
- ✅ Daemon replies: "🗂 No session running — message saved to pool (N)…".
- 📱 Send `ABS STATUS` → ✅ daemon status reply (session state, pool count, version).
- 📱 Type `/` in the chat → ✅ menu shows `/abs_start`, `/abs_status`, `/abs_pool`.

### A3 — Pool forwarding (you already have pooled messages waiting)
- 📱 `ABS START` → pick a project → 🟢 Normal.
- ✅ Before launching: "📨 N pooled message(s) waiting" with previews + [Send all]/[Skip].
- 📱 Tap **Send all**.
- ✅ Session launches; its FIRST message to you references those pooled messages —
  ask it "what were the messages I sent while you were offline?" → it knows them.

### A4 — Resume (one-tap)
- 📱 `ABS EXIT` → wait → `ABS START`.
- ✅ First screen shows **▶ Resume <project> (age)** buttons + 🆕 New session.
- 📱 Tap the Resume button.
- ✅ One tap — no project/mode screens — session resumes. Ask it "what did we discuss
  earlier?" → ✅ it remembers (proves --continue worked).

### A5 — New-folder jail (security)
- 📱 `ABS START` → 🆕 New session → ➕ New folder → type `../evil`.
- ✅ Rejected ("can't contain path separators"). Try `hello world` → rejected (spaces).
  Try `test2` → ✅ accepted, created under ~/Projects only.

### A6 — Attach from the desk
- 📱 Start a session (any project).
- 🖥 `abs sessions` → ✅ lists the live session with its engine + folder.
- 🖥 `abs attach default` → ✅ you land INSIDE the running session.
- 🖥 Detach: herdr `Ctrl-b q` (tmux `Ctrl-b d`) → ✅ session keeps running, you're back
  at the shell.

### A7 — Terminal start menu + launch guard
- 🖥 With that session still live, open another terminal and run `abs`.
- ✅ It REFUSES: "Profile 'default' already has a live session… Attach with: abs attach default".
- 📱 `ABS EXIT` to end it. 🖥 Now run `abs`.
- ✅ The start menu appears: ▶ Resume rows + 🆕 New in this folder + 📁 Another project.
  Press Enter → resumes the top recent. (Bypass anytime: `abs --new`, `abs --resume`.)

### A8 — Kill ladder (idle)
- ⚠ `ABS CLEAR POOL` only does something when the **daemon is running the bot AND the pool
  has messages**. So the order matters: `abs exit` first (daemon takes over), then send the
  bot a couple of messages so they pool, THEN `ABS CLEAR POOL`.
- 🖥 `abs exit`. 📱 send 2 messages → they pool. 📱 `ABS CLEAR POOL` →
  ✅ "🗑 Pool cleared (2 message(s) removed)". (If the pool was empty you'll see 0 — that's
  not a failure, there was just nothing to clear.)
- 📱 `ABS OFF` → ✅ "Inbound off…"; now messages to the bot get NO reply.
- 🖥 `abs on` → ✅ bot answers again.
- ⚠ Skip `ABS BLOCK` unless you want to re-pair — recovery needs `abs setup`.

### A9 — Login detection
- 🖥 `mv ~/.claude/.credentials.json ~/.claude/.credentials.json.bak`
- 📱 `ABS START` → pick a project/mode.
- ✅ Refuses: "⚠ Claude Code is not logged in… run `claude` in a terminal…". No zombie session.
- 🖥 `mv ~/.claude/.credentials.json.bak ~/.claude/.credentials.json` (restore!).

### A10 — Crash/restart recovery
- 📱 Start a remote session and leave it live.
- 🖥 `systemctl --user restart absd`.
- 🖥 `abs daemon status` → ✅ the daemon re-derived state: still shows the session live
  (not killed, not falsely reclaimed).

### A11 — Event trail
- 🖥 `tail -n 20 ~/.abs/daemon/events.jsonl` → ✅ machine-readable lines for everything
  you just did (handoff, session_start, session_end, reclaim…), NO message text in them.
- 🖥 `jq 'select(.event=="session_end")' ~/.abs/daemon/events.jsonl` → ✅ filters cleanly.

### A12 — New bot �️ (needs a spare bot token from @BotFather)
- ⚠ **First end this main session** (`abs exit`). `abs start new-bot` refuses while the
  `default` bot has a live poller ("already has a live poller") — Telegram allows one
  poller per bot. The PIN relay does NOT need a live session (it uses the saved token +
  chat), so ending it is fine.
- 🖥 `abs start new-bot` → paste the new token → pick a directory.
- ✅ PIN arrives on YOUR phone via the trusted (existing `default`) bot.
- 📱 Send that PIN to the NEW bot → ✅ paired, session starts in the chosen folder.
- 🖥 `abs status` → ✅ 2 profiles now. After that session ends, within ~60s the daemon
  polls the new bot too (rescan).

---

## PART B — Sandbox sessions

### B1 — Build + create
- 🖥 `abs sandbox build` → ✅ builds **absd-sandbox:v4** (or "already present").
  ⚠ If you already have a v3 box, re-create it — v4 is what puts ABS inside:
  `abs sandbox destroy <name>` (your workdir is KEPT) then `abs sandbox create <name>`.
- 🖥 **`abs sandbox create box1 --ports 3000:3000`** ← the container only exists AFTER this.
  (`build` makes the *image*; `create` makes the *container* named `absd-sbx-box1`.)
- ✅ Prints the credential-copy warning + the workdir path (~/Projects/sandboxes/box1)
  + the published port. `abs sandbox list` → ✅ shows box1.

### B2 — Isolation + net + sync (shell in directly)
- 🖥 `docker exec -it absd-sbx-box1 bash`  ← the name is `absd-sbx-<name>`, NOT the image
  name `absd-sandbox:v4`. Only works once B1's `create` has run.
- ✅ Inside: `claude` is logged in (copied creds); `git clone <any public repo>` works
  (net access); create a file in `/home/dev/workspace` → ✅ it appears on the host at
  `~/Projects/sandboxes/box1`. Nothing OUTSIDE that folder is visible from the box.
- ✅ **`abs --version`** → `Agent Babysitter …` (v4: ABS is installed in the box).
  `abs --profile <p> status` → the box's own paths (`/home/dev/…`), never host ones.
  `abs sandbox list` inside the box → ✅ refused ("host-side only"), by design.
- 🖥 (optional) start a server on :3000 inside → ✅ reachable at localhost:3000 in your
  host browser.

### B3 — Sandbox session over Telegram  ← **the one test that still needs your phone**
- ⚠ Needs a **v4** box (see B1). On `default` this REPLACES the babysitter (one bot,
  one poller), so `abs exit` first — or run it on your second bot, which leaves this
  session alone.
- 📱 `ABS START` → 🏖 Sandbox → pick the box (or ➕ New sandbox).
- ✅ **Send it a message and get a reply.** That is the whole test — it is the one hop
  nobody has verified yet.
- ✅ The reply comes from a session with the ABS **status bar** at the bottom (attach to
  see it), and the model knows the ABS instructions (ask "what does ABS EXIT do?").
- 🖥 `abs attach default` → ✅ drops you into the in-container session. Detach.
- ✅ 📱 `ABS EXIT` now ENDS an in-box session (v4 — the hook + `session.pid` are inside
  the box). `abs sandbox stop <name>` still works as the blunt version.
- ✅ After the session ends, `abs sandbox list` → the box still exists (container persists).
- ❌ If it stays silent: the daemon should notice within the start-grace window, reclaim
  the bot and tell you (`sandbox_channel_down`) rather than swallow your messages. Report
  which of the two you saw — silence vs. that message — they point at different bugs.

### B4 — Destroy
- 🖥 `abs sandbox destroy box1` → ✅ container removed, and the **workdir is kept** — it is
  your data, so it is never deleted without `--purge`. The command says so and prints the
  path. (It does not prompt; there is nothing destructive to confirm.)
- 🖥 `abs sandbox destroy box1 --purge` deletes the folder too.

---

## PART C — Restricted assistant ⏫ (needs a second spare bot token)

### C1 — Create + login
- 🖥 `abs restricted create assistant` → creates a NO-credentials sandbox + provisions
  its bot → ✅ PIN to your phone → 📱 send it to the new bot → paired.
- 🖥 `abs restricted login assistant` → ✅ drops you into interactive `claude` login
  inside the box (device-code / token — this is the separate login, no host creds).
- 🖥 `abs restricted list` → ✅ shows the assistant profile.

### C2 — Everyday assistant (allowed)
- 📱 From the RESTRICTED bot: "what's the weather in Mumbai?", "summarise https://…",
  "make a note: buy milk", "what's 18% of 2450?".
- ✅ Answers helpfully on Haiku (fast, cheaper). File/notes/memory work in its workspace.

### C3 — Coding refusal (the boundary)
- 📱 From the restricted bot: "write a python script that scrapes a website" or
  "build me a REST API".
- ✅ Refuses: "This is a restricted assistant — ask the operator to upgrade your profile
  to build projects." (Soft rule — real containment is the box + no host creds.)

### C4 — Users can't control the session
- 📱 From the restricted bot: `ABS START` / `ABS EXIT`.
- ✅ Refused (restricted, operator-only) — the assistant just stays up.
- 🖥 Only you manage it: `abs restricted stop assistant` / `abs restricted destroy assistant`.

### C5 — Login-needed ping (optional)
- 🖥 If you `abs restricted destroy` and recreate WITHOUT logging in, or the login
  expires, after a few relaunch attempts → ✅ the bot DMs you "run `abs restricted
  login assistant`" (once, no spam).

---

## What to report back

For anything that doesn't match the ✅, tell me: the step number, what you did, and what
actually happened (a screenshot helps). I'll fix it through the review cycle before we
build the final grammar-unification stage.
