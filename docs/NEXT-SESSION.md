# Start here — handoff after 3.2.0

The four items that were queued behind the macOS crash are all done and released.
This file says what changed, what is worth knowing before touching it, and what is
actually left.

## What shipped

**3.0.3 — the macOS crash.** Every launch on a Mac with voice installed died:

```
/Users/pranjal/.local/bin/abs: line 1248: text: command not found
✗ Unexpected failure (exit 127) at line 1164
    command: voice_section="$(cat <<VOICEON
```

bash 3.2 — still `/bin/bash` on macOS — finds the closing `)` of `$( … )` with a
scanner that has no idea here-documents exist, so it lexes the prose body as
shell. The apostrophes in "it's" and "don't" ended the substitution early and the
next line of prose ran as a command. The three prompt blocks live in their own
functions now (`_prompt_reply_both`, `_prompt_voice_on`, `_prompt_voice_off`).

Reproduced on Linux with `docker run --rm bash:3.2` before anything was changed,
and confirmed byte-identical output afterwards. **It was not limited to reply mode
`both`** — voice being installed at all was enough, in any mode.

**The suite runs bash 3.2 now** (`tests/test_bash32.py`), and that is the part
that matters. Three crashes in one week were invisible on bash 5, which was the
only shell the tests had ever run on. Twelve tests: a static grep banning
`$(cat <<TAG … TAG)` across every shipped shell script (no Docker needed — this is
the rule that keeps the class dead), `build_prompt` driven under a real `bash:3.2`
container across all five reply-mode/voice combinations, and the prompt compared
byte for byte against bash 5's. Reverting the fix turns 7 of the 12 red.

**3.1.0 — the three prompt-adjacent items.**

- Voice on by default where `voice_can_speak`. Never `voice`, only `both`: an
  unattended default must not be the one mode that suppresses the written record.
- The status bar shows the Claude account name, seeded once at launch and stored.
- Emoji stripped before the speech engine, as UTF-8 byte ranges under `LC_ALL=C`.
- The context percentage is colour-graded, and the usage footer is appended by
  abs rather than left to the model to remember. Both asked for mid-session.

**3.2.0 — `abs src install`.** The v3 source arrives as a tarball in `~/.abs/src`,
so a `curl … | bash` install gets the daemon, sandboxes and the start menu with no
git and no question. `abs_src_root()` is the single place that decides where the
source is.

**3.2.1 — voice notes wedged on macOS.** Found within minutes of the operator
testing 3.1.0 on the Mac: text arrived, audio never did, three TTS processes sat
on the box unfinished. The synthesis lock was `flock`, which is Linux-only, so on
macOS there was no lock at all and every reply loaded its own copy of the model.
Nothing was time-bounded either — which mattered more, because it made
`_voice_fallback_text` (the "voice failed, send the words instead" path)
**unreachable on the only platform that needed it**: a run that never returns
never sets a failure status.

The Mac session reached the same root cause independently and sent a patch; its
pid-based lock reaping and its TERM→KILL escalation are in the shipped fix.

## Traps worth knowing before you touch any of this

**A default that is "whatever the machine can do" makes deleting a key dangerous.**
`text` used to be stored by DELETING `.reply_mode`, because unset meant text. With
unset now meaning "voice if this box can speak", deleting would hand back `both`
and make `abs config reply text` look ignored. Both `text` and `reply-voice off`
write the value explicitly now, and `abs config reply auto` is the way back to the
default. The same trap applies to `bar_label --clear`, which is why
`.bar_label_seeded` exists.

**`ABS_VOICE_ROOT` and `ABS_SRC_ROOT` exist so tests can pin the machine.** Once a
default depends on what is installed, a test that leaves the setting unset is
asserting something about the developer's laptop. Pin both branches explicitly.

**The usage footer must never reach the speech engine**, and must never push a
message past Telegram's 4096-character ceiling — Telegram rejects rather than
truncates, and the retry fails identically. Both are enforced by ordering inside
`cmd_voice_then_text` and by a length check in `with_usage_footer`. Both have
tests; neither is obvious from reading the call site.

## What is actually left

1. **BSD sed is unverified.** The emoji strip is confirmed identical under GNU sed
   and busybox sed, on bash 5 and bash 3.2. macOS ships BSD sed and there is no
   copy of it on the Linux box. The construct is plain POSIX and should be fine,
   but "should be" is not "is" — check a report with an emoji in it on the Mac.

2. **The announcement is still unsent.** `docs/ANNOUNCE-3.0.1.md` is written and
   was held back while macOS launches were broken. They are not broken any more,
   so the only reason left is that it needs a read-through for the version numbers.

3. **Nothing is tagged.** The operator's call: tags are for versions he has been
   satisfied with, and 3.0.3 / 3.1.0 / 3.2.0 all went out untagged so he could
   test on the Mac. `main` carries them; `v3.0.1` and `v3.0.2` are the newest tags.

4. **The restricted assistant is still dormant.** `restricted-assistant` branch,
   labelled experimental, blocked since July on a third @BotFather token. Nothing
   changed here.

5. **`abs src install` fetches `main` when there is no tag.** That is deliberate —
   a version can ship before anyone tags it, and "no tag yet" must not mean "no
   daemon". Once tagging resumes it will prefer the tag. Worth remembering that
   until then, a `--force` reinstall tracks main rather than a fixed release.

## State

- `main` and `v3-daemon` both at 3.2.1. `agentbabysitter.com` serves the matching
  installer, verified by running it from the live site in a clean container.
- 990 tests pass on Linux, including the bash 3.2 container tests. Working tree
  clean.
- The installer needs Python 3.11+ for the v3 source step, and says so without
  failing when it is absent.

## The lesson from tonight, in one line

Three of the four bugs in this file were things that could not happen on the
machine the tests run on. The bash 3.2 suite closes that for shell parsing; the
voice wedge was the same shape wearing different clothes — `flock` present here,
absent there — and nothing catches that class automatically yet. When a code path
branches on what the OS provides, the branch that this machine never takes is the
one to write a test for.
