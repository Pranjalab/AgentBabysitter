# Releasing 3.0.0

Two halves: the notes that go out, and the commands that put them out. The notes
are written to be pasted into the GitHub release; the runbook is for the operator,
because every step in it is irreversible and none of it should be automated.

State at the time of writing: **833 tests passing**, `bash -n abs.sh` clean,
`abs doctor` green, working tree clean, `v3-daemon` **74 commits** ahead of
`origin/main`, nothing pushed.

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
- **Voice mode still sends text** for code blocks, links, attachments and anything
  over 1200 characters — a voice note cannot carry them.
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

Ten bugs were found during the release testing itself — three in code the full
suite reported green, and two only once a human actually launched an Away session.

---

## Part 2 — the runbook

Every command here is the operator's to run. They are irreversible in the way that
matters: once `main` moves and a tag is pushed, other people can have it.

### 1. Last check before anything moves

```sh
cd ~/Projects/research/AgentBabysitter
git status                      # must be clean
python -m pytest -q             # expect 833 passed
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

v2.6.0 never got a tag, which is why `git tag` stops at v2.5.1. Worth not
repeating.

```sh
git tag -a v3.0.0 -m "Agent Babysitter 3.0.0 — the always-on daemon"
```

### 4. Push

```sh
git push origin main
git push origin v3.0.0
git push origin v3-daemon              # keep the branch's history on the remote
git push origin restricted-assistant   # the deferred feature's branch
```

### 5. Publish the release

There are no GitHub releases on this repo yet — 3.0.0 would be the first. Part 1
of this file is the body:

```sh
gh release create v3.0.0 \
  --title "3.0.0 — the always-on daemon" \
  --notes-file docs/RELEASE-3.0.0.md
```

`--notes-file` takes the whole file, this runbook included. Either trim Part 2
first or paste Part 1 into `gh release create --notes-file -` from a scratch copy.

### 6. Elsewhere

- **agentbabysitter.com** — the waitlist site is a separate project (Vercel +
  MongoDB Atlas). Announcing there is a change to a different repo, so it is a
  separate decision and a separate review.
- **The README badge and install line** already say 3.0.0; nothing to change after
  the tag.

### If something goes wrong after step 4

Before the push, everything is local: `git checkout v3-daemon`, `git branch -f main
origin/main`, `git tag -d v3.0.0`. After the push, prefer a `3.0.1` over rewriting
a published tag — someone may already have fetched it.
