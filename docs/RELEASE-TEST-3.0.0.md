# Release test — Agent Babysitter 3.0.0

The one checklist to run before publishing. Everything here is a **manual** test:
693 automated tests already cover what a machine can check, and none of them can
tell you whether a real Telegram bot, a real Docker container, or a real Claude
login behaves. That is what this is for.

**Time:** ~75 minutes for everything. ~35 for the Must-pass block alone.

**Before you start — two things, or several sections will lie to you:**

```sh
cd ~/Projects/research/AgentBabysitter
rm tests/test_dbg_tmp.py                    # my debug leftover; the guard blocked me removing it
systemctl --user restart absd               # ⚠️ see below
abs doctor                                  # everything green except the stale-error note
```

> **The daemon restart is not optional.** `absd` has been up since 5 August and is
> running code from *before* the sandbox orphan-poller fix — the bug where roughly
> half your replies stopped arriving. Sections D, E and F test code that isn't
> loaded until you restart it.

Record each step as **PASS / FAIL / SKIP**. A FAIL is worth more than a PASS: note
what you saw, and stop the release if it's in the Must-pass block.

---

# Must-pass — a FAIL here blocks the release

## A. Install and upgrade (5 min)

| # | Do | Expect |
| --- | --- | --- |
| A1 | `./install.sh` in the checkout | `Agent Babysitter 3.0.0 installed`, links `~/.local/bin/abs` → the checkout |
| A2 | `abs --version` (or `abs help` header) | `3.0.0` |
| A3 | `PREFIX=/tmp/absbin ./install.sh` | Installs to `/tmp/absbin/abs`, doesn't touch your real one |
| A4 | `cp abs.sh /tmp/absbin/abs && /tmp/absbin/abs restricted list` | **One** `✗` saying it needs the full checkout, plus a `git clone` line. **No** "Unexpected failure" |
| A5 | `abs doctor` | Core deps ✓, daemon ✓, engines ✓, config ✓ |

A4 is the bug fixed in `99db30b` — the first v3 command a curl-installed user
tries. Two errors there is a FAIL.

## B. The reply switches (10 min) — the thing you asked for

| # | Do | Expect |
| --- | --- | --- |
| B1 | `abs config` | `reply text on` · `reply voice on` · `auto-silent off` |
| B2 | Start a session. Ask it something small ("what's 18% of 240?") | Answer arrives **as text AND as a voice note** |
| B3 | Type 4–5 commands at the terminal, then have it finish a task | The report **still arrives**. This is the old bug — before, it went silent after 3 |
| B4 | `abs config reply-voice off`, new session, finish a task | Text only, no voice |
| B5 | `abs config reply-voice on` again | Both again; `abs config` shows `auto-silent off` |
| B6 | `abs config reply-text off` | Voice only. Ask for something with a **code block** → still arrives as text |
| B7 | With text off, `abs config reply-voice off` | **Refused**, pointing at `abs quiet on`. Mode unchanged |
| B8 | `abs config reply-text on`, then `abs quiet on`, finish a task | No report. `abs quiet off` → reports resume |

B3 is the whole point of today's change. If it goes quiet, the release is not
ready.

## C. Terminal menus (5 min)

| # | Do | Expect |
| --- | --- | --- |
| C1 | `abs` with 2+ profiles | Arrow-key menu; ↑/↓ moves, Enter picks |
| C2 | Same menu, press `3` | Jumps straight to row 3 |
| C3 | Same menu, press `q` | Cancels cleanly, no session |
| C4 | After picking | The menu collapses to **one** line in scrollback |
| C5 | `ABS_NO_TUI=1 abs` | Falls back to the old numbered prompt |
| C6 | `abs profiles \| cat` | No escape-sequence garbage through a pipe |

---

# Should-pass — a FAIL here is a known-issue note, not a blocker

## D. The daemon and remote start (10 min)

*Restart absd first.*

| # | Do | Expect |
| --- | --- | --- |
| D1 | `abs daemon status` | Active, per-profile dashboard |
| D2 | With no session: send the bot `hello` from the phone | 👀 reaction + "saved to pool (1)" |
| D3 | Send `ABS START` | Resume-first screen, or the project picker |
| D4 | Pick a project → Normal | "🚀 Started …" + `abs attach <profile>` |
| D5 | `abs attach <profile>` at the desk | Drops into the live session |
| D6 | Detach (`Ctrl-b d` tmux / `Ctrl-b q` herdr) | Session keeps running |
| D7 | `ABS EXIT` from the phone | "⏹ Session ended. I'm listening again" |
| D8 | Status bar during a session | `● Daemon` is **green** |
| D9 | `systemctl --user stop absd`, new session, wait 3 min | `● Daemon` goes **dim**. Restart it → green |

## E. Blocked-session pings (10 min) — herdr only

*Skip entirely if `herdr --version` fails; on tmux the feature is absent by design.*

| # | Do | Expect |
| --- | --- | --- |
| E1 | `ABS START` → **Normal** (not Away — Away auto-approves, nothing will ever block) | Session starts |
| E2 | Give it a task needing approval, e.g. *"run `curl -s https://example.com \| head -5`"* | Within ~20–30s: "⏸ … is waiting for input or approval" |
| E3 | **Wait 2 more minutes without answering** | **No second ping.** A repeat is a FAIL |
| E4 | Answer in the chat | Task proceeds |
| E5 | Trigger a second approval in the same session | Pinged again — a new block is a new episode |
| E6 | A session where you answer every prompt at the desk within seconds | **No pings at all** |
| E7 | `grep session_blocked ~/.abs/daemon/events.jsonl` | One line per block, `blocked_for_s` present, **no message text** |

E6 matters more than E2: a feature that cries wolf gets muted, and then never
works when it counts.

## F. Pool multi-select (5 min)

| # | Do | Expect |
| --- | --- | --- |
| F1 | No session. Send `first`, `second`, `third` | Each acked as pooled |
| F2 | `ABS START` → project → Normal | Pool screen: three ☐ rows |
| F3 | Tap rows 1 and 3 | **Same message updates in place** — no new screen per tap. Button → `📤 Send 2` |
| F4 | Tap row 1 again | Unticks; button → `📤 Send all` |
| F5 | Tick 1+3, tap `📤 Send 2` | Session opens with `first` and `third`, **not** `second` |
| F6 | `ABS EXIT`, then `ABS POOL` | `second` still there |
| F7 | Pool 2, `ABS START`, tick one, tap `🙈 Skip` | **Nothing** forwarded; both still pooled |
| F8 | Pool 9+, `ABS START` | Toggles gone; screen says reply `send 1,3`. Typing `send 2,4` works |

---

# The restricted assistant (25 min)

Full checklist: **`docs/v3/manual-tests/restricted.md`** — run it as written; the
summary below is what it proves, so you know what a FAIL means.

**You need:** Docker running, and a **throwaway bot token from @BotFather**. This
is the third bot; C1–C5 have been blocked on it since July.

```sh
abs sandbox build --rebuild     # once — Stage 3 needs the v3 image
```

| Section | Proves |
| --- | --- |
| 1. `abs restricted create assistant` | Provisions a **no-credentials** box + bot; PIN relays via your `default` bot |
| 2. `abs restricted login assistant` | Logs Claude in **inside** the box; daemon brings it online by itself |
| 3. Everyday questions | Answers Q&A, lookups, arithmetic, notes — on Haiku |
| 4. *"write me a python script…"* | Refuses **verbatim**: "This is a restricted assistant — ask the operator to upgrade your profile to build projects." |
| 5. `ABS START` / `ABS EXIT` from that bot | **Refused.** A restricted bot can never launch a host session |
| 6. Log it out inside the box | Daemon gives up after a few tries and DMs you **once** |
| 7. `list` / `stop` / `start` / `destroy` | All work; profile drops from `abs daemon status` within ~60s |
| Containment | `docker exec absd-sbx-assistant test -e /home/dev/.claude/.credentials.json` → **1** before login |

**Be honest about what section 4 is worth.** It's a prompt, and prompts are
bypassable — try to talk it into writing code, and if you succeed that is *not* a
release blocker, because the prompt was never the containment. The containment is
the box with none of your files and none of your credentials, which is what the
containment spot-check proves. If **that** fails, stop.

---

# Known limits — expected behaviour, not bugs

Don't file these as FAILs:

- **Quiet and auto-silent are advisory.** The prompt asks the session to check
  `abs is-quiet`; nothing in a hook blocks the send. Reply mode *is* hook-enforced.
- **Voice mode still sends text** for code blocks, links, attachments, and
  anything over 1200 characters. A voice note can't carry them.
- **Same sentence isn't spoken twice within 5 minutes** — a repeated report gives
  text twice, voice once.
- **A normal sandbox is created WITH your credentials** copied in. It isolates the
  filesystem, not the Claude account. Only `abs restricted` gets `--no-creds`.
- **Blocked pings are herdr-only.** Absent on tmux by design.
- **Single-file installs have no v3.** Daemon, sandboxes and restricted all need
  the checkout.
- **macOS is untested** for the voice mirror (no `setsid`) and the statusline dot.

---

# Sign-off

```
Must-pass  A ___  B ___  C ___
Should     D ___  E ___  F ___
Restricted ___    Containment ___
```

Release when A, B and C pass and the containment spot-check passes. Anything else
that failed goes in the release notes as a known issue rather than silently.
