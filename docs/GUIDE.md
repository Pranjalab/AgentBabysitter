# Agent Babysitter — full guide

The [README](../README.md) covers what it is and how to start. This is the deeper
reference: profiles, voice setup, running it while you're away, the daemon,
sandboxes, the restricted assistant, where state lives, limits, and
troubleshooting.

## Profiles — more than one session at once

**Telegram allows exactly one poller per bot token.** That's a hard platform
limit: two sessions on one bot fight over every message and one loses with a
`409 Conflict`. So concurrent sessions need **a bot each**.

A profile is one bot's pairing — its token, allowlist, and chat, kept together:

```sh
abs                        # picks the profile, or asks if you have several
abs --profile work         # use (or create) the 'work' bot
ABS_PROFILE=work abs       # same, from the environment
abs profiles               # who's who, and what's live
```

```
Agent Babysitter profiles
  default  @yourbot        live (pid 245324)
  work     @yourwork_bot   idle
```

Running `abs` with no arguments and more than one profile gives you a picker,
with in-use profiles marked. `abs` refuses to start a session on a profile that's
already being polled, and tells you which one — reusing a single bot across two
*simultaneous* sessions isn't supported because Telegram won't allow it, but
sequential reuse is fine.

### The pickers

Every list `abs` offers — bots, sessions to resume, projects, sandboxes, where a
new bot should run — is one arrow-key menu:

```
❯ default — @yourbot
  work — @yourwork_bot (in use)
  + Add a new bot
  ↑↓ move · enter select · 1-9 jump · q cancel
```

↑/↓ or `k`/`j` move, Enter takes it, `q` or Esc backs out — and **typing the
number still works**, so existing muscle memory is untouched. The chosen row
collapses to one line once you pick, so scrollback keeps the decision without the
menu around it.

Long rows are truncated to the terminal width rather than wrapped, because a
wrapped row desyncs the cursor arithmetic and would draw the highlight somewhere
other than the row it selects. Row widths are counted in printed *columns*, not
characters, since an emoji or a CJK glyph costs two.

Set `ABS_NO_TUI=1` to force the old numbered prompt. It also falls back on its
own with no terminal on stderr, with `TERM=dumb`, or with no `/dev/tty` — which is
what keeps these usable under `docker exec` without `-t`, over a pipe, and in CI.

## Voice notes

Send a voice note and Claude transcribes it. Ask for a reply in voice and it
speaks back. Both run locally — no audio leaves the machine.

```sh
abs say "text"                                # speak it AND send it (the usual way)
.venv/bin/python transcribe.py <file.oga>     # speech → text (faster-whisper)
.venv-tts/bin/python speak.py "text" out.ogg  # text → speech (chatterbox), file only
```

**Inbound.** A voice note arrives with `attachment_file_id` on the `<channel>`
tag. Claude fetches it with the plugin's `download_attachment` tool and runs
`transcribe.py`, then treats the transcript as if you'd typed it.

**Outbound.** `abs say` synthesizes *and* delivers the voice bubble. `speak.py`
alone only writes a file — because the plugin's `reply` tool attaches any
non-image as a *document*, so a generated `.ogg` shows up as a file to download
rather than a playable bubble. Only the Bot API's `sendVoice` gives you the real
thing, and that needs the token, so it lives in `abs.sh`.

```sh
abs say "the text to speak"
abs say --keep out.ogg "text"     # also keep the file
abs say - < story.txt             # read stdin
```

`speak.py --exag` is an emotion dial: `0.3` flat, `0.5` natural, `0.8+` animated.
Lower `--cfg` slows delivery, which pairs well with a high `--exag`.

### Reply mode — "always answer me in voice", enforced

Telling the assistant to always answer with a voice note works until the session
gets long and the instruction drifts out of the model's attention — the failure
mode of every standing preference kept in a prompt. `abs config reply` stores it,
and the session hooks act on it whether the model remembers or not.

```sh
abs config reply text     # text only (default)
abs config reply both     # the text goes out, and a voice note of it follows
abs config reply voice    # the voice note IS the reply; the text is suppressed
abs config                # shows the current mode
```

`both` mirrors from a `PostToolUse` hook; `voice` intercepts at `PreToolUse`,
speaks the message, and blocks the text. The model is *told* which mode is on —
but only so it doesn't also call `abs say` and send the same sentence twice.
Enforcement never depends on it.

What `voice` deliberately does **not** suppress: a message carrying a code block,
a link, or an attachment. A voice note can't carry any of those, and a blocked
message is one you never receive. For the same reason it refuses to turn on where
nothing can speak (`abs voice setup` first). The failure mode is always "text as
usual", never silence.

Under the hood: markdown is stripped before speaking (a URL read aloud is a minute
of alphabet), the same sentence is never spoken twice within five minutes,
synthesis is serialised behind a lock and detached from the hook (which has a 5s
budget against TTS's ~30s), and the message body reaches the engine on **stdin**,
never argv — `/proc/<pid>/cmdline` is world-readable.

### Setting up voice

Voice is optional — everything else works without it. The installer offers to
set it up, and you can (re)run it any time:

```sh
abs voice setup      # installs uv if needed, builds both venvs, fetches the scripts
abs voice status     # green/red check of every piece (scripts, venvs, ffmpeg, uv)
```

It needs `ffmpeg` (name it yourself — `sudo apt install ffmpeg` / `brew install
ffmpeg`); everything else, including [`uv`](https://docs.astral.sh/uv/) and the
two Python versions, `abs voice setup` handles. For an installed `abs` the engines
live in `~/.abs/voice`; in a dev checkout they sit beside `abs.sh`.

Under the hood it's just two `uv` environments — the same thing by hand:

```sh
uv venv .venv     --python 3.13 && VIRTUAL_ENV=.venv     uv pip install faster-whisper
uv venv .venv-tts --python 3.11 && VIRTUAL_ENV=.venv-tts uv pip install chatterbox-tts "setuptools<81"
```

**Two venvs, deliberately.** `chatterbox-tts` depends on a `numba` pin that only
builds on Python <3.10, so TTS lives in its own 3.11 environment; Whisper runs in
the main venv on 3.13. The `setuptools<81` pin is not optional — chatterbox's
watermarker needs `pkg_resources`, which setuptools ≥81 dropped, and the failure
surfaces four layers from its cause as `'NoneType' object is not callable`.

**Long text is chunked, and that's load-bearing.** One synthesis call stops at
chatterbox's token cap (~40 seconds of speech) and returns the short clip with no
error — a story would silently lose its ending. `speak.py` splits at sentence
boundaries and stitches the pieces, so nothing is dropped.

**Transcription runs on CPU, synthesis on GPU.** Whisper clears a voice note in
about half the time it took to record. Chatterbox wants CUDA and ~3GB of VRAM;
`speak.py --cpu` forces it onto the CPU (slower) if you'd rather not contend.

**Claude cannot hear its own output.** If it says a clip sounds a certain way,
it's guessing. The honest check is to run the output back through `transcribe.py`
and confirm the words survived — that catches truncation and garbling, but not
tone. For tone, you're the only ear.

## Away mode and the blocking problem

The most likely way this disappoints you: Claude hits a permission prompt
mid-task while you're out, and blocks. You get silence, not a report.

The injected prompt tells Claude to message you when it's blocked, which covers
most of it. If you want fewer stops:

```sh
ABS_AWAY=1 abs
```

That runs with `--permission-mode acceptEdits` — file edits no longer prompt.
Bash and other tools still ask. It's a real trade: you give up the review step on
edits in exchange for not being blocked. Use it when you trust the task.

## Staying alive while you're out

Agent Babysitter only works while the session is running. Close the terminal and
it's gone — there's no queue. Telegram holds updates for 24 hours, but nothing is
polling to collect them, so messages sent while it's down are effectively lost.
Use `tmux`:

```sh
tmux new -s claude
abs
# detach with Ctrl-b then d — reattach later with: tmux attach -t claude
```

**On a cloud box.** Telegram polls *outbound*, so the machine needs no public IP,
no open port, and no webhook — a VPS, a home server, or a work desktop you SSH
into all behave the same. Run setup once over SSH, start it in `tmux`, and close
the laptop. Two things change when nobody's at that terminal:

- **`off` strands you** — it can only be undone at the terminal, which now means
  SSHing back in. Use `quiet` instead; you can undo that from the phone.
- **Voice output wants a GPU.** On a CPU-only VPS, `speak.py --cpu` works but is
  slow. Transcription is CPU-only by design and is fine anywhere.

## The always-on daemon (v3)

The `tmux` trick above keeps a session alive, but you still have to *start* it at
the terminal. The v3 daemon removes that last tie: **`absd`** runs in the
background and polls your idle bots, so you can start, resume, and manage sessions
entirely from your phone.

### Setup

From a repo checkout (the daemon is Python and lives in the tree with its `.venv`):

```sh
abs daemon install                    # render + install the systemd user unit
systemctl --user enable --now absd    # start now, and on every login
sudo loginctl enable-linger $USER     # keep it running after you log out (once)
abs daemon status                     # unit health + a per-profile dashboard
abs doctor                            # diagnose deps, engine, config, perms
```

`install.sh` on a checkout offers all of this (and an optional pinned herdr) for
you. The daemon needs nothing open to the network — like the rest of ABS it only
makes outbound Telegram calls.

### Starting a session from Telegram

1. Register the projects you want to be able to start in (terminal-only — a
   compromised phone can never name a path):

   ```sh
   abs project add ~/Projects/myrepo
   abs config workspace-root ~/Projects   # root for remote "New folder" starts
   ```

2. From the phone, with no session running, send **`ABS START`** → pick a project
   (or **▶ Resume** a recent one) → **Normal** or **Away**. The daemon launches
   Claude Code in a persistent session and replies with `abs attach <profile>`.
3. Walk to the desk and `abs attach <profile>` to take over; detach (tmux
   `Ctrl-b d` / herdr `Ctrl-b q`) and it keeps running. `ABS EXIT` (or `/abs_exit`)
   ends it, and the bot goes back to listening.

Messages you send while nothing is running are **pooled** (never lost); when you
start a session they're offered to forward as its first prompt. Each waiting
message gets a ☐/☑ button — tick the ones you want and tap **📤 Send 2**, or tap
**📤 Send all** without ticking anything. Typing still works too (`send 1,3`,
`send all`, `skip`), and past eight pooled messages the buttons step aside for the
typed form rather than turning the screen into a wall.

### When the session gets stuck

A remotely-started session that stops to ask a question is invisible from the
phone: the daemon has handed the bot over, the session is waiting for a human, and
nothing says so. When the engine can report agent status, a block that lasts more
than `blocked_debounce_s` (default 20s) pings the chat that started it:

> ⏸ myrepo is waiting for input or approval (24s).
> Answer here, or attach at the terminal: `abs attach default`

Once per block, not once per check. The debounce is there because a block you
answer at the desk in five seconds never needed a phone ping, and because herdr
takes a beat to recognise an approval prompt.

**herdr only.** tmux has no way to tell what the program in a pane is doing, so on
tmux this feature is silently absent rather than half-working. Configure it in
`~/.abs/daemon/config.json`:

| Key | Default | What |
| --- | --- | --- |
| `blocked_notify` | `true` | Ping when a session sits blocked |
| `blocked_debounce_s` | `20` | How long it must stay blocked first |
| `done_notify` | `false` | Also ping when a turn finishes (off — the session's own reply usually says it better) |

### The engine (herdr vs tmux)

Sessions run inside a session engine so they survive detach/reattach. **herdr** is
preferred (nicer attach UI); **tmux** is the always-available fallback — everything
works on tmux alone. Which one is used is `~/.abs/daemon/config.json`'s `engine`
(`auto` → herdr if present, else tmux). `abs sessions` lists across both.

### Kill ladder, still terminal-recoverable

`ABS OFF` and `ABS BLOCK` now stop the *daemon* for that bot too — "off means off".
Both are recoverable only at the terminal (`abs on` / `abs setup`), so a stolen
phone can silence a bot but never quietly re-enable it.

## Sandboxes

A sandbox is a session with its own Ubuntu container. The project lives in one
dedicated host folder — `~/Projects/sandboxes/<name>` (`0700`, configurable via
`sandbox_root`) — bind-mounted at `/home/dev/workspace`. That folder is the only
host path the container can see, so work syncs live to somewhere you can open in
an editor while the container reaches nothing else.

```sh
abs sandbox build                          # once (again with --rebuild to update)
abs sandbox create web --ports 3000:3000   # named box + workspace + published port
abs start sandbox web                      # a session running INSIDE the box
abs sandbox list
abs sandbox stop web
abs sandbox destroy web                    # keeps the workspace; --purge removes it
```

From Telegram, `ABS START` grows a **🏖 Sandbox…** entry that lists your boxes.
Attach, detach, and the kill ladder all behave identically.

What the container does *not* get: `--privileged`, a docker socket, or any host
mount besides that one workspace folder — verifiable with `docker inspect`. Your
Claude credentials are **copied in** at create time (`docker cp`), never mounted,
so the box's login diverges from yours the moment either changes.

Two honest caveats. A normal sandbox is created with your credentials inside it,
so treat it as isolating your *filesystem*, not your Claude account — for genuinely
untrusted work, use a restricted assistant (below), which gets no host credentials
at all. And a sandbox session's process lives in the container's PID namespace,
which the host can't see; the daemon therefore tracks its liveness through the
engine pane alone.

## The restricted assistant

A different kind of bot: everyday questions, web lookups, notes, arithmetic — but
it refuses to write or run project code, and it cannot start or stop sessions.

```sh
abs restricted create assistant   # provisions a bot + a credential-free sandbox
abs restricted login assistant    # log Claude in INSIDE the box (one time)
abs restricted list
abs restricted stop assistant     # pause the keep-alive and stop the box
abs restricted destroy assistant
```

Ask it to build something and it answers, verbatim:

> This is a restricted assistant — ask the operator to upgrade your profile to
> build projects.

One switch (`restricted: true`) implies four layers: **(1)** an injected system
prompt carrying that refusal, **(2)** the Haiku model, **(3)** a dedicated sandbox,
**(4)** `--no-creds` — no host credentials copied, so the box logs in separately.

Worth being blunt about which of those are real. Layer 1 is a prompt, and prompts
are bypassable; layer 2 is a cost choice. The actual containment is 3 and 4: a
throwaway container holding none of your files and none of your credentials. Judge
it on those.

Unlike a normal profile, a restricted one isn't idle-polled — the daemon *keeps it
alive*, relaunching on death with exponential backoff. After a few consecutive
fast deaths (almost always a box that isn't logged in) it stops and DMs you once:
"run `abs restricted login <name>`". Once you do, it comes back on its own. While
it's down the daemon refuses `ABS START`/`ABS EXIT` from that bot — session control
is operator-only, so a restricted bot can never launch a normal host session.

## Where things live

Nothing in the repo holds state or secrets — it's all safe to fork. State lives
in `$HOME`:

| Path | What |
| --- | --- |
| `~/.claude/channels/telegram/.env` | Bot token (`600`) |
| `~/.claude/channels/telegram/access.json` | Allowlist + policy (`600`) |
| `~/.claude/channels/telegram/bot.pid` | Which process holds the poller |
| `~/.abs/profiles/<name>/rc.json` | Chat ID, mute state, reply mode, which bot dir (`600`) |
| `~/.abs/profiles/<name>/pool.jsonl` | Messages received while nothing was running (`600`) |
| `~/.abs/daemon/config.json` | Daemon settings — engine, timings, notifications (`600`) |
| `~/.abs/daemon/status-<name>.json` | Per-profile snapshot, rewritten each poll (`600`) |
| `~/.abs/daemon/events.jsonl` | Structured daemon event trail — **metadata only** (`600`) |
| `~/Projects/sandboxes/<name>/` | A sandbox's workspace — the only host path its box sees (`700`) |

`ABS_HOME` overrides where profiles live. Non-default profiles get their own
plugin directory (`~/.claude/channels/telegram-<name>/`); the `default` profile
keeps the plugin's own path, so upgrading moves nothing on disk.

## Known limits

- **No history, no search.** Telegram's Bot API exposes neither, so Claude only
  sees messages as they arrive. If it needs earlier context it'll ask you to
  paste it.
- **One session per bot.** A hard Telegram limit — use a profile per concurrent
  session.
- **Reports are a judgment call, not a guarantee.** "Message me when done" is an
  instruction Claude follows well, but it isn't mechanical. If you find it
  skipping sends, a `Stop` hook would make it deterministic.
- **This depends on a pre-1.0 third-party plugin.** It leans on the plugin's
  `bot.pid`, its `access.json` schema, and its reserved-command behavior — none
  of it documented API. If something breaks after `claude plugin update`, look
  there first.
- **4096 characters per message.** Longer replies are auto-chunked.

## Troubleshooting

**Bot doesn't reply to the PIN.** The session must not be running during pairing
— Telegram allows one poller per bot. Quit any running session and retry. Confirm
you're messaging the right bot (`t.me/<username>` from setup).

**Pairing was interrupted.** Just run `abs` again. The token is saved once it
validates, so setup goes straight back to the PIN. If the token was revoked in
the meantime, it falls back to asking for a new one.

**Messages don't reach Claude.** Run `abs status`. If inbound shows `OFF`, run
`abs on`. If `poller` shows `not running`, nothing is listening — start a session.

**Telegram went dead right after running something.** Check whether that
something shells out to `claude` without `--strict-mcp-config`. Without that flag
the subprocess loads the Telegram plugin, which seizes the bot's poller and kills
your session's. Restart the session to get the poller back.

**"terminated by other getUpdates".** Two things are polling the same bot.
`abs profiles` shows which are live.

**Plugin won't start.** It needs Bun on `PATH` — check `bun --version`. If Claude
Code was launched from a GUI that doesn't load your shell profile, Bun may be
missing from its environment.

**Reports never arrive but replies work.** Check `abs status` for
`reports: muted`, then `abs quiet off`.

## Uninstall

```sh
abs reset                                             # remove token, allowlist, state
rm ~/.local/bin/abs
claude plugin uninstall telegram@claude-plugins-official
```

Then `/deletebot` in [@BotFather](https://t.me/BotFather) if you're done with the
bot entirely.
