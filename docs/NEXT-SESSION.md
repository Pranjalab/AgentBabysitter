# Start here — handoff after 3.0.2

Paste the block at the bottom into a fresh `abs` session. Everything above it is the
context that block refers to.

## The live bug, and why it is first

**Every launch on macOS crashes.** 3.0.2 upgrades correctly now, then dies:

```
/Users/pranjal/.local/bin/abs: line 1248: text: command not found
✗ Unexpected failure (exit 127) at line 1164
    command: voice_section="$(cat <<VOICEON
```

It reproduces on the operator's Mac every time and **not at all on Linux** —
`build_prompt` was driven directly with `reply_mode=both` on bash 5 and printed a
clean prompt. So this is a **bash 3.2 incompatibility**, and 3.2 is what macOS ships.

Leading hypothesis, unverified: bash 3.2 misparses a **heredoc inside a command
substitution** — `x="$(cat <<TAG … TAG)"`. `build_prompt` uses that shape three
times (`voice_section` twice, `reply_mode_section` once as of 3.0.2). The reported
line numbers land inside those bodies, and `text` / `release` being "not found" is
what you would see if 3.2 evaluated parts of the body it should have treated as
literal.

**Verify before fixing.** Do not refactor on the hypothesis alone:

- Get a real bash 3.2. `docker run --rm -it bash:3.2` is the cheapest route, or ask
  the operator to run one command on the Mac.
- Minimal repro first: a two-line script with `x="$(cat <<T` … `T)"` containing a
  backtick and a double quote. Confirm 3.2 breaks and 5 does not.
- Only then choose the fix. The obvious candidates, in order of preference:
  1. Assemble those sections without a heredoc at all — a plain single-quoted string
     with `printf '%s\n'`, which has no expansion surface to get wrong.
  2. Write the prompt to a temp file and pass `--append-system-prompt "$(cat file)"`.
- Whatever you choose, **add a bash-3.2 check to CI or the suite**. Both of tonight's
  crashes were invisible on bash 5, which is the only shell the tests run on. That
  gap is the real defect; the parse bug is just what fell through it.

## Also queued, in the order the operator asked for them

1. **Voice on by default** where the machine can speak. One line in `reply_mode()`,
   marked `TODO(3.0.3)`. It flips a contract ~18 tests assert (they leave
   `reply_mode` unset and expect `text`), so update those deliberately.
2. **The bar label defaults to the Claude account name**, not the literal `abs`.
   Same shape, marked in `bar_label()`, same test caveat.
3. **Strip emoji before the speech engine.** It reads them aloud as invented words.
   The obvious fix — a `perl -CSD` hop — was reverted on purpose: it puts a new
   external dependency inside the one path that must never fail, and with `pipefail`
   a machine without perl would break *every* voice reply. Needs a portable answer.
4. **The daemon without cloning.** The operator does not want a clone step; the
   installer no longer offers one, so a `curl` install currently has no daemon.
   Unpack the release tarball into `~/.abs/src` and install from there — no git, no
   prompt, full v3.

## State as of this handoff

- Released: `main` = 3.0.2, tags `v3.0.1` and `v3.0.2`, GitHub release for v3.0.1.
- 896 tests pass on Linux. Working tree clean. `agentbabysitter.com` serves the
  matching installer (`a3e99f57`).
- **Not announced.** `docs/ANNOUNCE-3.0.1.md` is written and unsent — do not send it
  while macOS launches are broken.
- Deferred feature: `restricted-assistant` branch, dormant and labelled experimental.

## The prompt to paste

> Read `docs/NEXT-SESSION.md` first.
>
> Priority one: `abs` launches fine on Linux but dies on macOS with `line 1248:
> text: command not found` at `voice_section="$(cat <<VOICEON`. It is a bash 3.2
> parsing problem — macOS ships 3.2, our tests only ever run bash 5. Reproduce it on
> a real bash 3.2 (`docker run --rm -it bash:3.2`) with a minimal case before
> changing anything, then fix `build_prompt` so it does not depend on that
> construct, and add a bash-3.2 check to the suite so this class cannot come back.
> Two crashes shipped this week that were invisible on bash 5; the missing 3.2
> coverage is the actual bug.
>
> Then, in order: voice on by default where the machine can speak; the Claude
> account name as the default status-bar label; emoji stripped before the speech
> engine without adding a runtime dependency; and installing the daemon by
> unpacking the release tarball into `~/.abs/src` so no clone is ever needed. The
> first two are marked `TODO(3.0.3)` in `abs.sh` and each flips a contract about
> eighteen tests assert — rewrite those tests deliberately rather than quickly.
>
> Ship as 3.0.3. Do not send the announcement in `docs/ANNOUNCE-3.0.1.md` until
> macOS launches cleanly.
