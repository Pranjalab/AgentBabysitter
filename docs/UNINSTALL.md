# Uninstalling Agent Babysitter

Everything ABS installs lives in your home directory. There is no system-wide
component, nothing in `/usr/local` except what Homebrew put there, and no
background service unless you installed the daemon yourself.

Three levels, in order. Stop wherever you like.

---

## Before you start

Close any running session, so nothing is holding a bot token or a poller open:

```sh
abs exit          # ends the session in this profile
abs status        # should show nothing running
```

If `abs exit` says the session is still running, `abs exit --force` ends it.

---

## 1 · Remove the `abs` command

```sh
rm -f ~/.local/bin/abs
```

That is the whole command — a symlink if you installed from a git clone, a single
file if you used the curl installer. Your bots, pairings and settings are
untouched, so reinstalling picks up exactly where you left off.

If you also installed the optional session engine:

```sh
rm -f ~/.local/bin/herdr
```

The installer may have added a PATH line to your shell profile. Check for it and
delete the two lines if they are there:

```sh
grep -n -A1 'Agent Babysitter' ~/.zshrc ~/.bashrc 2>/dev/null
```

---

## 2 · Remove ABS's data as well

This is the one to use before testing a fresh install — it puts the machine back
to never having run ABS.

**Look at it before you delete it.** It holds your bot tokens and your pairings:

```sh
ls -R ~/.abs | head -40
```

Then:

```sh
rm -rf ~/.abs
rm -rf ~/.claude/channels
```

- `~/.abs` — profiles, bot tokens, pairings, config, the v3 source and its venvs,
  the voice models, backups.
- `~/.claude/channels` — the per-profile workspace the Telegram plugin runs in.
  Created by ABS, used by nothing else.

On **Linux only**, if you installed the always-on daemon:

```sh
systemctl --user disable --now absd
rm -f ~/.config/systemd/user/absd.service
systemctl --user daemon-reload
```

The Telegram plugin itself is Anthropic's, installed into Claude Code rather than
into ABS. Remove it if you want Claude Code back as it was:

```sh
claude plugin uninstall telegram@claude-plugins-official
```

**Your bots still exist on Telegram.** Nothing here touches them. To retire one,
message [@BotFather](https://t.me/BotFather) → `/mybots` → pick it → *Delete Bot*.
Keeping it is fine: paste the same token back in and the bot works again.

---

## 3 · Remove the dependencies too

Only if you want them gone — all four are general-purpose tools you may well be
using for something else.

```sh
# Bun — only ABS's Telegram plugin needs it, but check first
rm -rf ~/.bun

# Claude Code
rm -rf ~/.local/bin/claude ~/.claude ~/.claude.json
```

Removing `~/.claude` signs you out of Claude Code and deletes its settings,
projects and history. That is usually not what you want.

jq and Python came from your package manager, so they go back the same way:

```sh
# macOS
brew uninstall jq
brew uninstall python3        # almost certainly in use by something else

# Debian / Ubuntu
sudo apt remove jq
```

Homebrew itself has [its own uninstaller](https://github.com/homebrew/install#uninstall-homebrew);
there is no reason to remove it just because ABS asked you to install it.

---

## Reinstalling

```sh
curl -fsSL https://agentbabysitter.com/install.sh | bash
```

It tells you what it will install before it installs anything. If you kept
`~/.abs`, your bots and pairings come straight back.
