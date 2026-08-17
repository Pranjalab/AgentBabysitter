"""Who is holding this bot, and what abs should do about it.

Telegram allows one poller per token, so `abs` has to refuse when the bot is
taken. The refusal used to be one flat message — "already being polled (pid N) …
quit that session first" — for four situations that want four different answers:

* the operator's own live session (refuse, but say *which* session, and offer to
  attach rather than making them hunt for it);
* a `claude` they started by hand outside abs (same, and `session.pid` knows
  nothing about it, which is why the process tree is the source of truth here);
* a poller that outlived its session and is holding the token deaf (reclaim it —
  there is nothing to "quit");
* a pid file describing a process that no longer exists. **Pids are recycled** —
  this machine wrapped from ~3.8M to ~1.2M inside a day — so `kill -0` succeeding
  proves only that something owns that number now. Refusing over it is a refusal
  the operator can never clear, and signalling it would kill a stranger's process.

The forged pollers below set `argv[0]` to `bun server.ts`, which is what the real
plugin runs, so `poller_looks_real` sees what it would see in production. The
`owned` case builds a process genuinely named `absd` so the ancestor walk has
something real to find on any machine, rather than depending on a `claude` being
alive wherever the suite happens to run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"
PROFILE = "pollertest"

FORGED = 'exec -a "bun server.ts" sleep 300'

pytestmark = pytest.mark.skipif(
    not Path("/proc").is_dir(), reason="needs a Linux /proc for ps stat/ppid detail"
)


@pytest.fixture
def box(tmp_path):
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=1:fake\n")
    prof = tmp_path / ".abs" / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({
        "bot": "b", "chat_id": "42", "tg_dir": str(tg),
    }))
    spawned: list[subprocess.Popen] = []
    forged: list[int] = []

    class Box:
        bot_pid_file = tg / "bot.pid"

        def holder(self, pid):
            self.bot_pid_file.write_text(f"{pid}\n")

        def forge_orphan(self):
            """A poller reparented to init: its session and wrapper are both gone."""
            subprocess.run(["setsid", "bash", "-c", FORGED], check=False,
                           start_new_session=True)
            time.sleep(0.8)
            out = subprocess.run(["ps", "-eo", "pid,ppid,args"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                if "bun server.ts" in line and "setsid" not in line:
                    pid, ppid = line.split()[0], line.split()[1]
                    if ppid == "1":
                        forged.append(int(pid))
                        return int(pid)
            pytest.skip("could not forge a reparented poller here")

        def forge_owned(self):
            """A poller whose parent really is called `absd`.

            A copy of bash under that name gives the process the right `comm`,
            which is what the walk reads — `exec -a` would only change argv[0].
            """
            fake = tmp_path / "absd"
            shutil.copy(shutil.which("bash"), fake)
            # `& sleep 300`, not `& wait`: with `wait` the parent exits the moment
            # its child is killed, so "the owner is still alive" would be false for
            # a reason that has nothing to do with abs — and the test that proves
            # --reclaim spares the session would fail against correct code.
            p = subprocess.Popen([str(fake), "-c", f"({FORGED}) & sleep 300"])
            spawned.append(p)
            time.sleep(0.8)
            out = subprocess.run(["ps", "-eo", "pid,ppid,args"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                if "bun server.ts" in line and str(p.pid) == line.split()[1]:
                    forged.append(int(line.split()[0]))
                    return int(line.split()[0]), p.pid
            pytest.skip("could not forge an owned poller here")

        def spawn_bystander(self):
            """A live process that is NOT a poller — i.e. a recycled pid."""
            p = subprocess.Popen(["sleep", "60"])
            spawned.append(p)
            return p

        def call(self, snippet, reclaim=False):
            body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
            script = tmp_path / "call.sh"
            script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
            env = dict(os.environ)
            for k in list(env):
                if k.startswith("ABS_") or k.startswith("TELEGRAM_"):
                    env.pop(k, None)
            env.update(HOME=str(tmp_path), ABS_HOME=str(tmp_path / ".abs"))
            if reclaim:
                env["ABS_RECLAIM"] = "1"
            return subprocess.run(["bash", str(script)], capture_output=True,
                                  text=True, env=env, timeout=90)

        def free(self, reclaim=False):
            return self.call(
                'require_profile_free && echo PROCEED\n'
                'echo "verdict=$POLLER_VERDICT owner=$POLLER_OWNER_PID"',
                reclaim=reclaim,
            )

    yield Box()

    # By pid, never by pattern. `pkill -f "bun server.ts"` matches the plugin
    # poller of any LIVE session on this machine — including the one the operator
    # is talking to us through, which is exactly how this file took the real
    # Telegram bridge down the first time it ran.
    for pid in forged:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True)
    for p in spawned:
        p.kill()
        p.wait()


def _alive(pid):
    """Alive, and not merely unreaped.

    `kill -0` alone succeeds on a zombie, which made
    `test_reclaim_ends_the_poller_and_not_the_session` blind: a mutation that
    killed the owning process outright still passed, because Popen had not reaped
    it yet. The same trap the shell side has to avoid — see `poller_gone`.
    """
    if subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()
    return not stat.startswith("Z")


# ---- the pid file describing nothing -----------------------------------------


def test_a_dead_pid_is_not_a_reason_to_refuse(box):
    """`profile_live_pid` answers "nobody is polling" for a dead pid before any of
    the verdict logic runs, so there is no verdict to assert here — only that the
    launch is not blocked. Asserting `verdict=stale` was asserting an internal that
    this path deliberately never reaches."""
    box.holder(999999)
    run = box.free()
    assert "PROCEED" in run.stdout, run.stdout + run.stderr
    assert "already being polled" not in run.stdout + run.stderr


def test_a_recycled_pid_is_neither_a_refusal_nor_a_kill(box):
    """The one that has to be right. Something else owns that number now: refusing
    over it is unclearable, and signalling it would kill a stranger's process."""
    bystander = box.spawn_bystander()
    box.holder(bystander.pid)
    run = box.free()
    assert "PROCEED" in run.stdout, run.stdout + run.stderr
    assert "verdict=stale" in run.stdout
    assert _alive(bystander.pid), "abs signalled an unrelated process"
    assert not box.bot_pid_file.exists()


# ---- a real poller with no session behind it ---------------------------------


def test_an_orphaned_poller_is_reclaimed_rather_than_reported(box):
    pid = box.forge_orphan()
    box.holder(pid)
    run = box.free()
    assert "PROCEED" in run.stdout, run.stdout + run.stderr
    assert "verdict=orphan" in run.stdout
    assert "Reclaimed" in run.stdout + run.stderr
    assert not _alive(pid), "the orphan kept the token"
    assert not box.bot_pid_file.exists()


def test_a_zombie_poller_counts_as_gone(box):
    """`kill -0` succeeds on a zombie forever, so anything waiting for it to fail
    waits for good — and then reports that it could not stop a stopped process."""
    p = subprocess.Popen(["bash", "-c", FORGED])
    p.kill()                      # killed, never reaped: state Z
    time.sleep(0.4)
    run = box.call(f'poller_gone {p.pid} && echo GONE || echo STILL-THERE')
    p.wait()
    assert "GONE" in run.stdout, run.stdout + run.stderr


# ---- a real poller a live session owns ---------------------------------------


def test_a_live_owner_is_named_not_just_numbered(box):
    poller, owner = box.forge_owned()
    box.holder(poller)
    run = box.free()
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert f"pid {owner}" in out, out          # whose it is, not just that it is
    assert "in use by a live" in out
    assert "abs --reclaim" in out              # and the way out if that is wrong
    assert _alive(poller), "refusing must not kill anything"


def test_reclaim_takes_the_bot_back_from_a_live_owner(box):
    """Attribution can be wrong in the unhelpful direction — a recycled pid whose
    tree happens to reach a live session. Without a flag that overrides `owned`
    there is no way out of that except hunting the pid by hand."""
    poller, _owner = box.forge_owned()
    box.holder(poller)
    run = box.free(reclaim=True)
    out = run.stdout + run.stderr
    assert "PROCEED" in run.stdout, out
    assert "LIVE session" in out                # said out loud, not silently
    assert not _alive(poller)
    assert not box.bot_pid_file.exists()


def test_reclaim_ends_the_poller_and_not_the_session(box):
    """It frees the bot; it does not kill someone's running Claude Code."""
    poller, owner = box.forge_owned()
    box.holder(poller)
    box.free(reclaim=True)
    assert _alive(owner), "abs killed the owning process, not just the poller"


# ---- nothing at all ----------------------------------------------------------


def test_no_pid_file_means_nothing_to_decide(box):
    run = box.free()
    assert "PROCEED" in run.stdout, run.stdout + run.stderr
    assert "verdict=" in run.stdout and "verdict=stale" not in run.stdout
