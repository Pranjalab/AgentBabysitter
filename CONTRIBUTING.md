# Contributing to Agent Babysitter

Thanks for taking the time — bug reports, "it broke on my setup" notes, and small
focused pull requests are all genuinely welcome.

## Ground rules

Agent Babysitter is **one bash script** (`abs.sh`) plus two optional Python
helpers for voice. Keep changes small and in that spirit — the whole point of the
project is to stay a thin, readable layer over the official Telegram plugin, not
to grow into a framework.

- Don't add a runtime dependency to the core script. It relies only on `bash`,
  `jq`, `curl`, `claude`, and `bun` (the plugin's server).
- Match the existing style: comments explain *why*, not *what*, and load-bearing
  or surprising behavior gets a short note.
- One logical change per pull request.

## Before you open a PR

```sh
bash -n abs.sh install.sh        # syntax check
shellcheck abs.sh install.sh     # if you have shellcheck installed
```

Then exercise the real flow in isolation — nothing touches your live setup:

```sh
# install to a temp prefix and run a fresh-user setup against temp state
PREFIX=/tmp/abs-bin ./install.sh
ABS_HOME=/tmp/abs-state /tmp/abs-bin/abs --profile test help
```

If your change touches pairing, usage parsing, or the injected prompt, say in the
PR how you verified it — ideally with the before/after output.

## Reporting bugs

Open an [issue](https://github.com/Pranjalab/AgentBabysitter/issues) with your OS,
`bash --version`, what you ran, and what happened. `abs status` output helps for
anything pairing- or session-related. For security issues, see
[SECURITY.md](SECURITY.md) — please don't file those as public issues.

## Scope

Good fits: portability fixes (macOS/BSD), clearer errors, docs, robustness around
the plugin's behavior. Out of scope: turning this into a general bot framework, or
anything that requires standing up a server or database. If you're unsure whether
an idea fits, open an issue first and ask — better than building it twice.
