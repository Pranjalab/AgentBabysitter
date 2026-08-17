"""The profile picker, driven through a real pty — section C of the release test.

``test_menu_tty.py`` drives ``menu_select`` lifted out of abs.sh in isolation,
which pins the key handling. This drives the whole script — ``bash abs.sh`` with
several profiles on disk, so what is under test is ``pick_profile`` —
building the rows, painting them, collapsing the block afterwards, and handing
the chosen name to ``use_profile``.

The command driven is ``abs status``, which resolves a profile through the picker
and then only prints — so a test can press Enter on a real picker without ever
starting a Claude session, and the printed ``profile <name>`` line proves which
row was handed to ``use_profile``.

HOME is redirected along with ABS_HOME. Without that, ``migrate_legacy`` copies
the operator's real pairing into the temp home and the live ``default`` profile
turns up in the middle of the fixture's rows.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

UP = "\x1b[A"
DOWN = "\x1b[B"
ENTER = "\r"

PROFILES = (("alpha", "alphabot"), ("beta", "betabot"), ("gamma", "gammabot"))


@pytest.fixture
def home(tmp_path):
    """A fake HOME whose ~/.abs holds three profiles and nothing else."""
    h = tmp_path / "home"
    for name, bot in PROFILES:
        d = h / ".abs" / "profiles" / name
        d.mkdir(parents=True)
        (d / "rc.json").write_text(json.dumps({"bot": bot, "chat_id": "123"}))
    return h


def _drive(home, keys, args=("status",), env=None, settle=0.4, cols=100):
    """Run abs.sh in a pty, type ``keys``, return (raw output, exit code)."""
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs immediately
        e = dict(
            os.environ,
            TERM="xterm-256color",
            COLUMNS=str(cols),
            HOME=str(home),
            ABS_HOME=str(home / ".abs"),
        )
        e.pop("ABS_NO_TUI", None)
        e.update(env or {})
        os.execve("/bin/bash", ["bash", ABS_SH, *args], e)

    out = bytearray()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # the child exited and closed the slave side
                return False
            if not chunk:
                return False
            out.extend(chunk)
        return True

    pump(settle)
    for key in keys:
        os.write(fd, key.encode())
        if not pump(settle):
            break
    pump(1.0)
    os.close(fd)
    _, status = os.waitpid(pid, 0)
    return out.decode("utf-8", "replace"), os.waitstatus_to_exitcode(status)


def _screen(raw):
    """Replay our own escape sequences into a screen buffer.

    The picker repaints by moving the cursor up and clearing lines, so the raw
    stream contains every frame. Only what survives on screen is what the
    operator sees, and the collapse test is meaningless against the raw bytes.
    Handles just the sequences abs.sh emits: CUU/CUD, EL, SGR, cursor show/hide.
    """
    lines, row, col = [""], 0, 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\x1b":
            hide = re.match(r"\x1b\[\?25[lh]", raw[i:])
            if hide:
                i += hide.end()
                continue
            m = re.match(r"\x1b\[(\d*)([A-Za-z])", raw[i:])
            if not m:
                i += 1
                continue
            n, verb = int(m.group(1) or 1), m.group(2)
            if verb == "A":
                row = max(0, row - n)
            elif verb == "B":
                row += n
            elif verb == "K":
                while len(lines) <= row:
                    lines.append("")
                lines[row] = lines[row][:col] if m.group(1) == "1" else ""
            i += m.end()
            continue
        if ch == "\n":
            row, col = row + 1, 0
        elif ch == "\r":
            col = 0
        else:
            while len(lines) <= row:
                lines.append("")
            line = lines[row].ljust(col)
            lines[row] = line[:col] + ch + line[col + 1:]
            col += 1
        i += 1
        while len(lines) <= row:
            lines.append("")
    return "\n".join(lines)


def _highlighted(raw):
    """Every row the cursor sat on, in order, as it was painted."""
    return [
        m.strip().split(" ")[0]
        for m in re.findall(r"❯\x1b\[0m \x1b\[1m([^\x1b]+)", raw)
    ]


def _menu_rows(screen):
    """Surviving picker rows only — a row is "<name> — @<bot>" or the add row.

    Deliberately narrower than "any line mentioning alpha": `abs status` prints
    the resolved profile name several times, and counting those as menu rows
    would make the collapse assertion unfailable in the wrong direction.
    """
    return [
        line
        for line in screen.splitlines()
        if re.search(r"\b(alpha|beta|gamma)\b — @", line) or "Add a new bot" in line
    ]


# ---- C1: it renders, and the arrows move ------------------------------------


def test_the_picker_lists_every_profile_and_the_add_row(home):
    raw, _ = _drive(home, ["q"])
    for name, bot in PROFILES:
        assert f"{name} — @{bot}" in raw
    assert "+ Add a new bot" in raw
    assert "↑↓ move" in raw and "q cancel" in raw


def test_down_arrow_walks_the_highlight_down_the_list(home):
    raw, _ = _drive(home, [DOWN, DOWN, "q"])
    assert _highlighted(raw)[:3] == ["alpha", "beta", "gamma"]


def test_up_arrow_from_the_top_wraps_to_the_add_row(home):
    raw, _ = _drive(home, [UP, "q"])
    painted = _highlighted(raw)
    assert painted[0] == "alpha"
    assert painted[1] == "+"  # "+ Add a new bot"


# ---- C3: backing out --------------------------------------------------------


def test_q_cancels_without_choosing_anything(home):
    raw, code = _drive(home, ["q"])
    assert "Cancelled" in raw
    assert code != 0


# ---- C2: digit jump ---------------------------------------------------------


def test_a_digit_jumps_straight_to_that_row_and_selects_it(home):
    raw, code = _drive(home, ["3"])
    chosen = [line for line in _screen(raw).splitlines() if "❯" in line]
    assert chosen and "gamma" in chosen[-1]
    assert re.search(r"profile\s+gamma", raw)  # and gamma is what was resolved
    assert code == 0


def test_a_digit_past_the_end_of_the_list_is_ignored(home):
    """Four profiles rows + the add row = 5. `7` must not select anything."""
    raw, _ = _drive(home, ["7", "q"])
    assert "Cancelled" in raw


# ---- C4: the block collapses -----------------------------------------------


LONG = "_padded_out_so_residue_is_visible"


@pytest.fixture
def wide_home(tmp_path):
    """Same as ``home`` but with rows longer than anything printed afterwards.

    The first version of this test used the ordinary fixture and was vacuous:
    ``abs status`` prints lines of its own over the same rows, so removing the
    erase entirely still left nothing recognisable behind. Rows that outrun the
    following output make the difference observable — which is also exactly how
    a human spots it, as a tail of an old row hanging past the new text.
    """
    h = tmp_path / "home"
    for name, bot in PROFILES:
        d = h / ".abs" / "profiles" / name
        d.mkdir(parents=True)
        (d / "rc.json").write_text(json.dumps({"bot": bot + LONG, "chat_id": "123"}))
    return h


def test_the_menu_collapses_to_one_line_once_something_is_picked(wide_home):
    raw, _ = _drive(wide_home, [ENTER])
    screen = _screen(raw)

    # The hint is painted every frame and must not survive the collapse. This is
    # the unmistakable symptom of a missed erase: it reappears as a tail on
    # whatever short line was printed over it.
    assert "q cancel" in raw
    assert "q cancel" not in screen

    # Exactly two lines may still carry the long bot name: the collapsed row the
    # picker keeps on purpose, and `abs status` printing it as data afterwards.
    carrying = [line for line in screen.splitlines() if LONG in line]
    assert len(carrying) == 2, carrying
    row, printed = carrying
    assert " — @" in row and "alpha" in row
    assert printed.strip().startswith("bot ")


# ---- C5: the no-TUI fallback ------------------------------------------------


def test_abs_no_tui_falls_back_to_the_numbered_prompt(home):
    raw, _ = _drive(home, ["2\r"], env={"ABS_NO_TUI": "1"})
    assert "1. alpha" in raw and "3. gamma" in raw
    assert "Choice [Enter=1]" in raw
    assert "↑↓ move" not in raw


def test_the_numbered_fallback_honours_the_typed_number(home):
    raw, code = _drive(home, ["2\r"], env={"ABS_NO_TUI": "1"})
    assert re.search(r"profile\s+beta", raw)
    assert code == 0


# ---- C6: nothing leaks through a pipe ---------------------------------------


def test_profiles_through_a_pipe_carries_no_escape_bytes(home):
    proc = subprocess.run(
        ["bash", ABS_SH, "profiles"],
        capture_output=True,
        text=True,
        env=dict(os.environ, HOME=str(home), ABS_HOME=str(home / ".abs")),
    )
    assert "\x1b" not in proc.stdout + proc.stderr
    assert "alpha" in proc.stdout + proc.stderr


# ---- the isolation this file depends on -------------------------------------


def test_the_fixture_home_does_not_pick_up_the_real_default_profile(home):
    """Guards the guard. If HOME leaked, ``migrate_legacy`` would drop the
    operator's live pairing into the rows and every assertion above would be
    reasoning about someone else's bot."""
    raw, _ = _drive(home, ["q"])
    assert "default" not in raw
