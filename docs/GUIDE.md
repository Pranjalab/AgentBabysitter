# Agent Babysitter — full guide

The [README](../README.md) covers what it is and how to start. This is the deeper
reference: profiles, voice setup, running it while you're away, where state
lives, limits, and troubleshooting.

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

## Where things live

Nothing in the repo holds state or secrets — it's all safe to fork. State lives
in `$HOME`:

| Path | What |
| --- | --- |
| `~/.claude/channels/telegram/.env` | Bot token (`600`) |
| `~/.claude/channels/telegram/access.json` | Allowlist + policy (`600`) |
| `~/.claude/channels/telegram/bot.pid` | Which process holds the poller |
| `~/.abs/profiles/<name>/rc.json` | Chat ID, mute state, which bot dir (`600`) |

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
