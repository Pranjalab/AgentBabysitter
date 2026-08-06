# Critique — putting ABS inside the sandbox

The operator's framing, verbatim: *"we want to build the sandbox as a similar
project… we could do everything which Claude and ABS are able to do but in the
sandbox environment with a new bot. Please make sure we are able to run the ABS,
because if ABS is not connected in the sandbox then we won't be able to send text
to Claude in the sandbox."*

Two claims are bundled there, and only one of them is true. Worth separating before
the design, because the wrong reading leads to a much bigger and worse build.

## What was actually missing

**Message delivery does not need `abs` in the box.** The bot is polled from inside
the container by the Claude Code Telegram *plugin*, not by ABS. That path was fixed
in [sandbox-inbox-channel.md](sandbox-inbox-channel.md) (marketplace re-homing +
workspace pre-trust) and the in-box poller is verified running. So "no abs in the
box" was never why messages did not arrive.

**Everything else the operator noticed was real.** Up to v3 the in-box launcher ran
`claude --channels plugin:telegram…` *directly*, bypassing abs.sh entirely. A bare
claude has none of ABS:

| in a host session | in a v3 box |
|---|---|
| status bar (mute/active dot) | — |
| `PreToolUse` Bash guard on Telegram-driven turns | — |
| ABS remote controls: `ABS STOP` / `EXIT` / `MUTE` / `OFF` | — |
| `session.pid` → `abs exit` and the kill ladder | — |
| the ABS operating instructions in the system prompt | — |
| `abs` on `PATH` | `abs: command not found` |

That is the honest scope of the complaint: the box was a claude shell, not an ABS
environment.

## The design decision

The tempting answer — run a second `absd` daemon inside every container, each
owning its own bot — was rejected. It doubles the state machine, puts two pollers a
misconfiguration away from a 409 war over one token, and needs an init system the
container deliberately doesn't have. Orchestration stays host-side.

The cheaper and stricter answer: **an in-box session becomes the same launcher the
host daemon already runs**, just inside the container.

    host   : bash ~/…/abs.sh --profile <p> --daemon-start [flags]
    sandbox: bash /opt/abs/abs.sh --profile <p> --daemon-start [flags]

Everything in the table above is wired by `cmd_run`, so reusing it single-sources
the behaviour instead of reimplementing a second, drifting copy in the launcher.
`--daemon-start` was already built for exactly this shape: no start menu, no flood
prompt, no update prompt, and a loud death if the profile is unpaired rather than a
silent drop into interactive setup.

### What had to exist for that to work

1. **abs.sh inside the box.** Split by rate of change: the **image** (v4) carries
   the slow parts — `/opt/abs`, a venv with `aiohttp`, and an `abs` shim on `PATH`;
   the **code** (`abs.sh`, `absd/`, `agent_babysitter/`, and the launcher itself) is
   `docker cp`'d in at container start and again before every session. Editing
   abs.sh on the host therefore never needs a 4-minute rebuild, and a box that has
   been up for days can't run a stale launcher.

   The directory copies use `src/.`, not `src`. `docker cp src dest` **nests**
   (`dest/src`) once `dest` exists, so the plain form would build `/opt/abs/absd/absd`
   on the second sync. There is a test pinned to that specific argv.

2. **The profile's pairing inside the box.** `cmd_run` decides "is this profile
   paired?" from `$ABS_HOME/profiles/<p>/rc.json` — which lives under `~/.abs` and is
   therefore *not* part of the `~/.claude` credential copy. Without seeding it, every
   in-box session would have died with "profile is not paired". `tg_dir` and
   `voice_sample` are dropped on the way in: both are host-absolute paths, the same
   bug class as the plugin metadata and the ccgram hooks before them.

3. **One system prompt, not two.** abs.sh builds its own
   `--append-system-prompt` (the ABS operating instructions). The restricted-assistant
   persona used to be a competing second flag, so it now travels in
   `ABS_EXTRA_SYSTEM_PROMPT` and abs.sh merges the two into one value.

4. **`prepare_session(name, profile)`** — the sync + seed, called from *both* launch
   paths (the daemon's handoff and abs.sh's terminal `abs start sandbox`) immediately
   **after** `ensure_running`. That ordering is not incidental: it is all
   `docker exec`, which needs a running container, and putting this kind of work in
   `create` is precisely the mistake that made an earlier fix fail silently.

## Compatibility

A pre-v4 box has no `/opt/abs`. The sync detects that and does nothing (a
half-installed tree is worse than none), and the launcher keeps its v3 bare-claude
path. So an existing box keeps working exactly as it did — it just doesn't gain
anything. **To get in-box ABS an existing sandbox must be re-created** on the v4
image; the host workdir is user data and survives (`destroy` keeps it unless
`--purge`).

## What this closes

- `abs: command not found` in the box — fixed, `abs` is on `PATH`.
- **No in-box status line** — fixed; `statusLine` runs `/opt/abs/abs.sh … statusline`.
- **`ABS EXIT` from the phone cannot end an in-box session** — fixed. The
  `UserPromptSubmit` hook is now wired inside the box, and in-box `abs exit`
  signals the in-box `session.pid`. Verified against a live container: the target
  process went from alive to dead.

## Verified (on a real v4 container, `absd-sbx-v4box`)

Run with a stub `claude` on `PATH` so the exact argv could be read back:

- `abs` resolves in the box; `abs --version` → `Agent Babysitter 2.6.0`.
- `absd-session <profile> --permission-mode acceptEdits --continue "<pooled text>"`
  reaches `claude` as `--channels plugin:telegram@… --append-system-prompt <ABS
  instructions> --settings /home/dev/.abs/profiles/<p>/hooks.json --permission-mode
  acceptEdits --continue "<pooled text>"`.
- The generated `hooks.json` carries the statusLine, `UserPromptSubmit`,
  `PostToolUse` and the `PreToolUse` Bash guard — **all pointing at `/opt/abs`**, no
  host paths.
- `--restricted --model haiku` → the persona is merged into the single ABS system
  prompt (both strings present) and `--model haiku` is forwarded.
- An unpaired profile dies with the intended loud error, not a silent deaf box.
- Re-running the sync twice does not nest `/opt/abs/absd/absd`.
- In-box `abs exit` killed the process named by the in-box `session.pid`.
- `abs sandbox` / `abs daemon` / `abs restricted` inside the box refuse with the
  reason (host-side only) instead of half-working.

Suite: 460 → **472 passing**. Three of the new tests were mutation-checked
(idempotent dir copy, prompt-as-flag, daemon `prepare_session` wiring): neutering
each fix makes exactly that test fail. `shellcheck -S warning` clean on the new
files.

## Not verified by me

The **operator-message round trip into a box** — a real Telegram message reaching
claude inside the container and getting an answer. That needs the operator's phone;
everything up to the plugin's own poller is verified, the last hop is not.

## Residuals

- **A box still shares the host bot** unless it is launched on a second profile. One
  bot, one poller — a sandbox session on `default` replaces the host babysitter.
  A box with its **own** bot works today by pairing a second profile
  (`abs start new-bot`) and launching the sandbox under it; there is no one-command
  "sandbox + new bot" grammar yet.
- The **restricted assistant** shares this launch path and therefore inherits
  in-box ABS, but has still not been exercised end-to-end on a real second bot.
- `docker cp` of the code happens per session start. It is a few hundred KB and
  well under the docker timeout, but it is not free and it is not incremental.
