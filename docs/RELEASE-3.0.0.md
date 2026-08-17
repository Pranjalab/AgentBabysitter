# Releasing 3.0.1 (which carries 3.0.0)

Two halves: the notes that go out, and the commands that put them out. The notes
are written to be pasted into the GitHub release; the runbook is for the operator,
because every step in it is irreversible and none of it should be automated.

State at the time of writing: **853 tests passing**, `bash -n abs.sh` clean,
`abs doctor` green, working tree clean, `v3-daemon` ahead of `origin/main` by 77
commits with a **conflict-free** merge (`git merge-tree --write-tree main v3-daemon`
exits 0), nothing pushed.

---

## Part 1 — the release notes

Paste from here down into the GitHub release body.

### Agent Babysitter 3.0.0 — the always-on daemon

Until now ABS was a wrapper around one session: start it at the desk, and it
relayed. Close the terminal and the bot went deaf. 3.0.0 adds a daemon that holds
the bot when no session does, so the phone works whether or not you are at the
machine.

**Start a session from your phone.** `ABS START` gives you a project picker and
launches Claude Code on your machine — Normal or Away. `ABS EXIT` ends it. In
between, `ABS STATUS`, `ABS POOL`, `ABS MUTE`, `ABS OFF`.

**Nothing is lost while no session is running.** Messages that arrive with the bot
idle are pooled and acknowledged with 👀. When you start a session, the pool comes
up as tappable rows — tick the ones you want, or `📤 Send all`.

**It tells you when a session is stuck.** A session that stops for an approval and
sits there is invisible from a phone. A block held past 20 seconds pings the chat
that started it — once per block, not once per check. Answered at the desk in five
seconds, you hear nothing. (herdr backend only; tmux cannot report what a pane is
doing.)

**Away means away.** `bypassPermissions`, so nothing prompts — not file edits, not
Bash. What stands in for the prompts is a command guard that an Away session cannot
switch off, and which applies to every turn, not just the ones from Telegram:
privilege escalation, `rm -rf`, force-push, service stops, container and volume
destruction, machine-wide package changes, publishing, block devices, reading
secrets out, and piping a download into a shell. Ordinary work — `npm install`,
`docker stop`, a scoped `DELETE … WHERE`, deleting one file — runs untouched.

**Voice replies you don't have to keep asking for.** `abs config reply-voice on`
and every finished result arrives as a voice note *and* as text, enforced by hooks
rather than by the model remembering. The note goes first, because reading the
message first makes the audio a duplicate of something you already know.
`abs config voice-first off` if you'd rather read immediately.

**A sandbox is a real ABS environment.** `abs start sandbox` runs the session
inside a container with the status bar, the guard, the remote controls and a
`session.pid` that `abs exit` can signal — not a bare `claude` in a box.

**Smaller things that add up.** Arrow-key pickers everywhere (typing the number
still works). A `● Daemon` dot on the status bar, so you can see at a glance
whether a message sent after this session ends will land. `abs config label` to put
your own name on the bar. Real log rotation. An event log you can read.
`abs doctor`.

#### Upgrading from 2.x

```sh
cd <your AgentBabysitter checkout>
git pull
./install.sh
```

The daemon, sandboxes and the restricted assistant need the **checkout** — a
single-file `curl` install of `abs` has no v3 features and says so rather than
failing oddly. Nothing about a 2.x setup has to change: your profiles, pairings
and tokens are read where they already are.

#### Known limits

These are behaviours, not bugs, and they are documented rather than quietly true:

- **Blocked-session pings are herdr-only.** On tmux the feature is absent by
  design.
- **Quiet and auto-silent are advisory.** The prompt asks the session to check
  them. Reply mode *is* hook-enforced.
- **Voice mode still sends text** for code blocks, links and attachments — a voice
  note cannot carry them. In `voice`-only mode a message over 1200 characters also
  goes as text; with voice-first (mode `both`) a long message is instead *led* by a
  spoken opening and the full text follows.
- **The same sentence is not spoken twice within five minutes**, so a repeated
  report gives you text twice and voice once.
- **A normal sandbox is created *with* your credentials.** It isolates the
  filesystem, not the Claude account.
- **The command guard is a blocklist**, and a blocklist is never complete. It stops
  the irreversible things happening quietly while nobody is watching; it is not
  adversary-proof, and Away is still only for work you would leave alone.
- **macOS is untested** for the voice mirror and the status-bar dots.

#### Not in this release

The **restricted assistant** (`abs restricted`) is complete in code and covered by
unit tests, but nobody has provisioned one end to end, so it ships dormant and
labelled experimental rather than as a feature. Its containment *is* verified: the
box holds no credentials, has no `~/.claude` at all, and cannot see the host home.
Work continues on the `restricted-assistant` branch.

#### Verified by hand, and not

Everything in the release checklist passed by hand on 16–17 Aug: install and
upgrade, the reply switches, the terminal menus (driven through a pty harness), the
daemon and remote start, blocked pings, and pool multi-select.

Two things ship **unverified by hand** and are named here rather than implied:
voice-only mode (`reply-text off`), which has automated coverage but no human has
watched a real voice-only reply; and the restricted assistant, above.

Fourteen bugs were found during the release testing itself. Three were in code the
full suite reported green; two surfaced only once a human actually launched an Away
session; and two — a launch killed by a slow network, and a bot that could not be
reclaimed after a pid was recycled — surfaced only from ordinary daily use.

---

## Part 2 — the runbook

Every command here is the operator's to run. They are irreversible in the way that
matters: once `main` moves and a tag is pushed, other people can have it.

### 1. Last check before anything moves

```sh
cd ~/Projects/research/AgentBabysitter
git status                      # must be clean
python -m pytest -q             # expect 853 passed
bash -n abs.sh                  # silent
abs doctor                      # green except the stale-error note
abs --version                   # Agent Babysitter 3.0.0
```

### 2. Merge into main

The house pattern is a real merge commit, not a fast-forward — `git log --merges
main` shows every previous release arriving that way.

```sh
git checkout main
git pull --ff-only origin main
git merge --no-ff v3-daemon -m "Merge v3-daemon: the always-on daemon (3.0.0)"
```

### 3. Tag it

**One tag, v3.0.1, carrying both changelog sections.** 3.0.0 was finished but never
pushed, so nobody has it and everything reaches users in this one release. The bump
is not cosmetic though: the operator's own install reports 3.0.0, so 3.0.1 is what
makes *him* see the "new version available" prompt — the upgrade mechanism every
future release depends on, and one no human has yet watched work end to end.

v2.6.0 never got a tag, which is why `git tag` stops at v2.5.1. Worth not
repeating.

```sh
git tag -a v3.0.1 -m "Agent Babysitter 3.0.1 — the always-on daemon"
```

### 4. Push

```sh
git push origin main
git push origin v3.0.1
git push origin v3-daemon              # keep the branch's history on the remote
git push origin restricted-assistant   # the deferred feature's branch
```

### 5. Publish the release

There are no GitHub releases on this repo yet — 3.0.0 would be the first. Part 1
of this file is the body:

```sh
gh release create v3.0.1 \
  --title "3.0.1 — the always-on daemon" \
  --notes-file docs/RELEASE-3.0.0.md
```

`--notes-file` takes the whole file, this runbook included. Either trim Part 2
first or paste Part 1 into `gh release create --notes-file -` from a scratch copy.

### 6. The distribution channels — checked 17 Aug, and two of them are stale

This is the part that decides whether anyone actually *gets* 3.0.0, and none of it
follows automatically from the tag.

| Channel | State today | What it needs |
| --- | --- | --- |
| `VERSION` on `origin/main` | **2.6.0** | Pushing main. Every installed `abs` polls this file — until it moves, no existing user is ever told 3.0.0 exists |
| `agentbabysitter.com/install.sh` | **stale 2.x copy** | Updating `Pranjalab/agentbabysitter-web` |
| PyPI `agent-babysitter` | **2.0.0** | Publishing 3.0.0, or removing the claim from the README |
| The checkout (`git pull && ./install.sh`) | current | nothing |

**The installer is served by GitHub Pages, not by this repo.** `curl -sI
https://agentbabysitter.com/install.sh` answers `server: GitHub.com`, and the file's
checksum matches neither this branch nor `origin/main` — it is a hand-copied
snapshot living in `Pranjalab/agentbabysitter-web`. It predates v3: no herdr
install, no daemon step. Pushing main does **not** update it.

It does set `REPO=raw.githubusercontent.com/…/main`, which fixes the ordering for
you: **push main first** and even the stale installer immediately starts fetching
`abs.sh` 3.0.0. Update the site second. The other way round and the site's fresh
installer would still pull a 2.6.0 script.

**PyPI is two majors behind** while the README says `pipx install agent-babysitter`.
A user following that line today gets 2.0.0 and none of this. Publish or delete the
line; leaving both as they are is the only wrong answer.

### 7. The gap the tag cannot close

`curl … | bash` installs **one file**. `install.sh` gates the daemon and herdr on
`[ -n "$here" ]` — a checkout — so the one-liner cannot offer them. Someone
following the README's headline install gets 3.0.0's script-level work (the reply
switches, voice-first, the command guard, the status bar, the label) and **none of
the features the release is named for**: no daemon, no `ABS START` from the phone,
no sandboxes.

Two honest ways out, and they are not equivalent:

1. **Teach `install.sh` to clone.** Run via curl with no checkout, offer to clone
   into a real directory and continue as a checkout install, so the daemon and herdr
   are offered and `abs update` works through `git pull`. The one-liner then delivers
   what the README promises. Roughly an hour with tests.
2. **Reorder the README** to lead with `git clone && ./install.sh` for v3 and demote
   the one-liner to "minimal install, no daemon". Ten minutes, no code — but the
   headline path stays second-class.

Doing neither ships a release whose own quick-start contradicts its release notes.

### If something goes wrong after step 4

Before the push, everything is local: `git checkout v3-daemon`, `git branch -f main
origin/main`, `git tag -d v3.0.0`. After the push, prefer a `3.0.1` over rewriting
a published tag — someone may already have fetched it.

### 8. What the security pass checked

A security review ran over the whole branch on 17 Aug before release: one pass to
find candidates, then each candidate attacked independently to discard the ones that
could not actually be reached. It found **two HIGH issues, both in code this branch
added, and both now fixed** — see the changelog entry "two ways the Away guard could
be walked past". They are recorded here rather than quietly repaired, because the
same pass is worth repeating and its negative results are worth keeping:

Verified correct, with the reason:

- **Tokens never reach argv.** `tg_api`/`tg_get`/`tg_send_voice` hand the URL to
  `curl -K -` on stdin specifically so the token cannot be read out of `ps`; the
  token file is written 0600; `absd/telegram.py` redacts it in `__repr__`.
- **`skipDangerousModePermissionPrompt` is per-session only** — written into
  `$ABS_DIR/hooks.json` and passed with `--settings`. Nothing in abs.sh writes
  `~/.claude.json` or any project `settings.json`, so an Away session never changes
  how an ordinary `claude` behaves.
- **The voice path never puts message text into a command.** The worker payload is
  built with `jq -n --arg` (values, not program text) and travels over stdin; the
  only interpolation into the spawned command line is `printf '%q'`-quoted.
- **The allowlist is enforced first** on every inbound path, before any command
  parsing or pooling, and updates that are not messages or callbacks are dropped
  while still advancing the offset. The daemon never writes the allowlist.
- **Launcher arguments are built as a list, never a shell string**, and pooled
  Telegram text can never become a `claude` flag: it always goes through
  `build_offline_prompt`, which prefixes it.
- **Folder and sandbox names from Telegram are double-gated** — an allow-list regex
  plus a structural parent check — so neither can escape the sandbox root.
- **The sandbox has one bind mount** (its own workdir), runs `--user dev`, is not
  privileged, and mounts no docker socket. `--no-creds` skips the credential copy
  entirely, and session preparation syncs only ABS code plus an `rc.json` with the
  host paths stripped.
- **A restricted profile cannot launch a host session**; `ABS START`/`EXIT`/`OFF`/
  `BLOCK` are refused for it explicitly.
- **`--reclaim` only ever signals a pid that looks like the plugin's poller**, and
  never an owned one without the operator's flag. The pid comes from the plugin's own
  file, not from anything a message controls.
- **The conversation log is redacted before writing** — token shapes, JWTs, `sk-`/
  `gh_`/`AKIA`/`AIza` keys, `user:pass@` URLs — and the event log carries metadata
  only, never message text.
- **Self-update is not an execution path**: a fetched VERSION that is not digits and
  dots is discarded, the update itself is `git pull --ff-only` or a fixed HTTPS
  installer behind an interactive prompt, and it is skipped entirely for
  Telegram-triggered launches.

Two things the pass raised that are **not** fixed and are release notes rather than
blockers: adding a `--` separator before the initial prompt in the launcher argv
(currently safe only because the prompt is always prefixed), and rejecting a leading
`--` in the in-container launcher's positional. Both are defence-in-depth on a path
that has no reachable exploit today.
