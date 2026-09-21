"""The launch, in a real pty: update check → persona page → project menu.

21 Sep, after the Mac run: "the update check happens once the user selects the
location … it needs to be checked before that", and "before the project
selection also, let's provide a similar page where the user can select the
personality". Driven through the real abs.sh with a stub claude that records
its argv, so the persona picked on the page is checked where it matters — in
the system prompt the session is launched with.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    abs_home = home / ".abs"
    prof = abs_home / "profiles" / "default"
    prof.mkdir(parents=True)
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\n")
    (tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": [42]}))
    (prof / "rc.json").write_text(json.dumps({
        "bot": "b", "chat_id": 42, "tg_dir": str(tg), "reply_mode": "text", "voice_offer_done": True,
    }))
    (abs_home / "voice.declined").write_text("")
    proj = tmp_path / "proj"
    proj.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()
    argv_file = tmp_path / "claude_argv"
    (binp / "claude").write_text(
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in plugin) echo "telegram@claude-plugins-official"; exit 0 ;; esac\n'
        f"printf '%s\\n' \"$@\" > {argv_file}\nexit 0\n"
    )
    (binp / "claude").chmod(0o755)
    (tmp_path / "VERSION").write_text("9.9.9\n")     # "a newer abs exists"

    class Box:
        rc = prof / "rc.json"

        def env(self, **extra):
            e = dict(os.environ, HOME=str(home), ABS_HOME=str(abs_home),
                     PATH=f"{binp}:{os.environ.get('PATH', '')}", ABS_VOICE_ROOT=str(tmp_path / "novoice"),
                     TERM="xterm-256color", ABS_VERSION_URL=f"file://{tmp_path}/VERSION")
            e.pop("TELEGRAM_STATE_DIR", None)
            e.update(extra)
            return e

        def launch(self, keys, extra_argv=(), **extra):
            """Run `abs` in a pty, feed keys, return the screen text and claude's argv."""
            if argv_file.exists():
                argv_file.unlink()
            child_env = self.env(**extra)          # built BEFORE the fork clears the environment
            pid, fd = pty.fork()
            if pid == 0:
                os.chdir(proj)
                os.environ.clear()
                os.environ.update(child_env)
                os.execv("/bin/bash", ["/bin/bash", str(ABS_SH), *extra_argv])
            buf = b""

            def drain(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([fd], [], [], 0.2)
                    if r:
                        try:
                            buf += os.read(fd, 65536)
                        except OSError:
                            return

            drain(5)
            for k in keys:
                os.write(fd, k)
                drain(2)
            drain(2)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
            text = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", buf).decode("utf8", "replace")
            argv = argv_file.read_text() if argv_file.exists() else ""
            return text, argv

    return Box()


DOWN = b"\x1b[B"
ENTER = b"\r"


def test_the_update_check_comes_before_any_choice(box):
    text, _ = box.launch([b"n" + ENTER, ENTER])
    i_update = text.find("new Agent Babysitter is available")
    i_persona = text.find("Who am I this session?")
    assert i_update >= 0, text[-800:]
    assert i_persona >= 0, text[-800:]
    assert i_update < i_persona


def test_the_persona_page_lists_abs_first_and_preselects_it(box):
    text, argv = box.launch([b"n" + ENTER, ENTER])
    rows = [l for l in text.splitlines() if re.search(r"^\s*❯?\s*(abs|ceo|cto|friend)\s", l)]
    assert [r.split()[1] if r.strip().startswith("❯") else r.split()[0] for r in rows][:4] == ["abs", "ceo", "cto", "friend"], rows
    assert "This session's persona is 'abs'" in argv


def test_the_pick_on_the_page_reaches_the_system_prompt(box):
    _, argv = box.launch([b"n" + ENTER, DOWN, DOWN, ENTER, ENTER])   # abs → ceo → cto
    assert "ROLE — CTO" in argv
    assert "This session's persona is 'cto'" in argv


def test_the_page_preselects_the_active_persona(box):
    (box.rc.parent.parent.parent / "persona.active").write_text("friend\n")
    _, argv = box.launch([b"n" + ENTER, ENTER])
    assert "This session's persona is 'friend'" in argv


def test_persona_flag_skips_the_page(box):
    text, argv = box.launch([b"n" + ENTER, ENTER], extra_argv=("--persona", "ceo"))
    assert "Who am I this session?" not in text
    assert "ROLE — CEO" in argv


def test_the_page_can_be_turned_off(box):
    rc = json.loads(box.rc.read_text())
    rc["no_persona_menu"] = True
    box.rc.write_text(json.dumps(rc))
    text, argv = box.launch([b"n" + ENTER, ENTER])
    assert "Who am I this session?" not in text
    assert "This session's persona is 'abs'" in argv


def test_default_is_an_alias_for_abs_on_disk(box):
    """A persona.active written by the beta says 'default'; it must still mean abs."""
    (box.rc.parent.parent.parent / "persona.active").write_text("default\n")
    _, argv = box.launch([b"n" + ENTER, ENTER])
    assert "This session's persona is 'abs'" in argv


def test_the_bar_refreshes_on_a_timer_while_idle(box):
    """`abs quiet on` from another terminal showed nothing on the bar until the
    next message: Claude Code re-runs the status line on conversation events and
    goes quiet when idle. A refreshInterval keeps muted / off / persona current."""
    _, argv = box.launch([b"n" + ENTER, ENTER])
    lines = argv.splitlines()
    settings = Path(lines[lines.index("--settings") + 1])
    cfg = json.loads(settings.read_text())
    assert cfg["statusLine"]["refreshInterval"] == 5
    assert "statusline" in cfg["statusLine"]["command"]
