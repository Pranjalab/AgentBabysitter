"""Two reports from the A100 box, 1 Oct 2026.

**You could not make a second bot.** A session was live on `default`, the operator
wanted another bot for another project, and `abs` refused:

    ✗ Profile 'default' is in use by a live Claude Code session (pid 19735, …).
      Telegram allows one poller per bot, so this one cannot be started as well.
      Attach to it / End it / Or use another bot: abs --profile <name>

Every option there assumes a bot you already have. The one command that *makes*
one, `abs start new-bot`, was not mentioned — and worse, it was guarded by the
same assertion, so it refused too. There was no way forward short of ending the
session he was using.

The guard was wrong for that command. Provisioning only SENDS the new bot's PIN
through the trusted bot — a single sendMessage — and the pairing that follows
polls the NEW bot. Telegram's one-poller-per-bot rule never comes into it.

**Voice died silently on a shared GPU host.** A root-owned CUDA MPS daemon makes
every other user's CUDA init hang for ever; torch's `cuda.is_available()`, which
the speech engine imports, blocked for hours on 17 Sep and no voice note went
out. The operator's fix lived as a local edit on that one machine, which is also
why `git pull --ff-only` had been refusing to update it. Upstreamed here.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"
SRC = ABS_SH.read_text()


# ---- making a second bot while the first is working ---------------------------------


def test_new_bot_is_not_blocked_by_a_live_session_on_another_profile():
    """The reported dead end. `cmd_new_bot` must not assert the current profile is
    free — the bot it provisions is a different bot."""
    body = SRC[SRC.index("cmd_new_bot() {"):]
    body = body[:body.index("\n}\n")]
    assert "assert_no_live_session" not in body, (
        "a live session on the relay profile must not stop you making a second bot"
    )


def test_new_bot_still_only_sends_through_the_live_bot():
    """The guard is safe to drop *because* the trusted bot is only used to relay the
    PIN. If that ever becomes a poll, the guard has to come back — this test is the
    tripwire."""
    body = SRC[SRC.index("cmd_new_bot() {"):]
    body = body[:body.index("\n}\n")]
    assert "getUpdates" not in body, (
        "new-bot polling the relay bot would collide with its live session"
    )
    assert "do_pairing" in body, "pairing still happens, on the NEW bot"


def test_the_refusal_names_the_command_that_makes_a_new_bot():
    m = re.search(r'die "Profile \'\$PROFILE\' is in use by a live Claude Code session.*?"',
                  SRC, re.S)
    assert m, "the in-use refusal moved"
    msg = m.group(0)
    assert "abs start new-bot" in msg, (
        "every other option assumes a bot you already have"
    )
    # and the options it already had must survive
    for opt in ["abs --profile <name>", "--reclaim"]:
        assert opt in msg, opt


# ---- the CUDA MPS hang ---------------------------------------------------------------


def test_mps_pipe_directory_is_pointed_somewhere_harmless():
    assert 'export CUDA_MPS_PIPE_DIRECTORY=' in SRC
    line = next(l for l in SRC.splitlines() if l.startswith('export CUDA_MPS_PIPE_DIRECTORY='))
    assert ':-' in line, "must honour a value the host already set"


def test_it_is_set_before_anything_can_import_torch():
    """Useless if it lands after the first thing that initialises CUDA."""
    export_at = SRC.index('export CUDA_MPS_PIPE_DIRECTORY=')
    for later in ['speak_kokoro', 'transcribe.py', 'cmd_run()', 'main()']:
        if later in SRC:
            assert export_at < SRC.index(later), f"{later} comes before the export"


def test_a_host_that_really_shares_an_mps_server_is_left_alone(tmp_path):
    script = tmp_path / "probe.sh"
    script.write_text(SRC.replace('main "$@"\n', "") + '\nprintf "%s" "$CUDA_MPS_PIPE_DIRECTORY"\n')
    env = dict(os.environ, HOME=str(tmp_path), ABS_HOME=str(tmp_path / ".abs"),
               CUDA_MPS_PIPE_DIRECTORY="/var/run/our-own-mps")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    assert r.stdout.strip().endswith("/var/run/our-own-mps"), r.stdout


def test_otherwise_it_points_at_nothing(tmp_path):
    script = tmp_path / "probe.sh"
    script.write_text(SRC.replace('main "$@"\n', "") + '\nprintf "%s" "$CUDA_MPS_PIPE_DIRECTORY"\n')
    env = dict(os.environ, HOME=str(tmp_path), ABS_HOME=str(tmp_path / ".abs"))
    env.pop("CUDA_MPS_PIPE_DIRECTORY", None)
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    out = r.stdout.strip()
    assert out.endswith("/nonexistent"), out
    assert not Path("/nonexistent").exists(), "the whole point is that it is not there"
