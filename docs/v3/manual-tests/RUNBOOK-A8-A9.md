# Runbook — A8 (kill ladder) and A9 (login detection)

Both start with `abs exit`, which ends the live session **and the Telegram bridge
with it**. So this file exists to be read *after* the assistant is gone. Work
through it top to bottom; every expected string below was read out of the source,
not remembered.

Bot: **@Claudepranbot** (profile `default`).

---

## ✅ Better: run A8 on the SECOND bot and keep your session

A8 does not actually need `abs exit`. What it needs is for the **daemon** to own
the bot — and on `abs_test_001_bot` the daemon already does, because there is no
terminal session on that profile. So the whole of A8 can run on the second bot
with the babysitter left running.

Two changes to the table below:
- Step 1 is not `abs exit`. It is **ending the sandbox session** left over from
  B3 — 📱 send `ABS EXIT` (**all caps**; lowercase `abs exit` is the *terminal*
  command and won't trigger the hook) to **@abs_test_001_bot**, or 🖥
  `abs sandbox stop v4box`.
- Step 7 is `abs --profile abs_test_001_bot on`, not plain `abs on`.
- Every 📱 step is in the **@abs_test_001_bot** chat.

A9 cannot be moved this way — the credentials file is machine-wide, so it affects
every profile at once. See the safe window at the bottom.

---

## A8 — kill ladder

The order matters. `ABS CLEAR POOL` only does something when the **daemon** owns
the bot *and* the pool has messages in it — so the session has to end first, and
the messages have to arrive after that.

| # | where | do this | expect |
|---|---|---|---|
| 1 | 🖥 | `abs exit` | session ends. Wait ~30s for the daemon to take the bot. |
| 2 | 📱 | send `hello one` | `🗂 No session running — message saved to pool (1). Send ABS START to begin.` |
| 3 | 📱 | send `hello two` | same, with `(2)` |
| 4 | 📱 | send `ABS CLEAR POOL` | `🗑 Pool cleared (2 message(s) removed).` |
| 5 | 📱 | send `ABS OFF` | `📴 Inbound off. Re-enable from the terminal: abs on` |
| 6 | 📱 | send anything | **no reply at all** — that is the pass |
| 7 | 🖥 | `abs on` | `✓ Inbound Telegram ENABLED for 'default' (allowlist).` |
| 8 | 📱 | send `hello three` | pooled again → inbound is back |

⚠ **Do not send `ABS BLOCK`.** It is the top of the ladder and deliberately
survives `abs on` — recovery needs a full `abs --profile default setup`.

If step 4 says `(0 message(s) removed)` that is not a failure, it just means the
pool was already empty. Re-do steps 2–3 first.

---

## A9 — login detection

⚠ **While the credentials file is moved, Claude Code will not start anywhere on
this machine** — including any attempt to bring the assistant back. Step 4 is not
optional, and nothing else should be running.

| # | where | do this | expect |
|---|---|---|---|
| 1 | 🖥 | `mv ~/.claude/.credentials.json ~/.claude/.credentials.json.bak` | — |
| 2 | 📱 | `ABS START` → pick any project → 🟢 Normal | — |
| 3 | 📱 | | `⚠ Claude Code is not logged in on this machine. Please run \`claude\` in a terminal and complete login, then try ABS START again.` and **no session starts** |
| 4 | 🖥 | `mv ~/.claude/.credentials.json.bak ~/.claude/.credentials.json` | **restore — do this immediately** |
| 5 | 🖥 | `ls -l ~/.claude/.credentials.json` | file is back |

The point of step 3 is twofold: the refusal message *and* no zombie session left
behind. Check with `abs daemon status` — it should still read idle, not
`session-live`.

---

### A9 the safe way — a window that restores itself

The danger is forgetting step 4, or something going wrong between 1 and 4, and
leaving the machine unable to start Claude Code at all. Run it as one block
instead: the `trap` restores the file on normal exit, on Ctrl-C and on kill.

```bash
( f=~/.claude/.credentials.json
  trap 'mv -f "$f.a9bak" "$f" 2>/dev/null && echo "✓ credentials restored"' EXIT INT TERM
  mv "$f" "$f.a9bak" || exit 1
  echo "moved — you have 90s. Send ABS START to @abs_test_001_bot now."
  sleep 90 )
```

Then 📱 `ABS START` on **@abs_test_001_bot** inside that window and watch for the
refusal. The file comes back on its own after 90 seconds.

⚠ Honest caveat: during those 90 seconds no Claude Code session on this machine
can refresh its token, including the one you are talking to. It is a small window
and an already-running session normally rides through it, but it is not zero risk.

## Getting the assistant back

```
cd ~/Projects/research
abs
```

Or from the phone: `ABS START` → pick the project → 🟢 Normal.

---

## What to report back

For each of A8 and A9: which step (if any) did **not** match the expected column,
and what it said instead. If everything matched, "A8 and A9 both clean" is enough.

After these two, the only thing left in the whole checklist is **C1–C5**, the
restricted assistant, which needs one more @BotFather token — separate from both
the babysitter bot and the website signup bot.
