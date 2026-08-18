"""The arrow-key picker in abs.sh, driven through a real pty.

`menu_select` reads raw keys from /dev/tty and repaints by moving the cursor, so
nothing short of a pseudo-terminal exercises it — piping input tests the fallback
and nothing else. These tests fork a pty, type keys into it, and read back which
index the menu settled on.

Only the output helpers and the menu block are sourced, so `main` never runs.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import time

import pytest

ABS_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abs.sh")

UP = "\x1b[A"
DOWN = "\x1b[B"
ESC = "\x1b"
ENTER = "\r"

DEFAULT_ITEMS = ("alpha", "beta", "gamma")


def _menu_source() -> str:
    """The output helpers + the menu block, lifted out of abs.sh verbatim."""
    src = open(ABS_SH).read()
    return src[src.index("# --- output ---") : src.index("# --- telegram api ---")]


def _drive(tmp_path, keys, items=DEFAULT_ITEMS, default=0, env=None, cols=80):
    """Run menu_select in a pty, type `keys`, return (result, screen bytes).

    result is "RESULT=<index>" when the menu returned 0, "CANCELLED=-1" when it
    returned non-zero, and "<no result>" if it never got that far.
    """
    out_file = tmp_path / "result"
    script = tmp_path / "drive.sh"
    args = " ".join("'%s'" % i for i in items)
    script.write_text(
        "set -euo pipefail\n"
        + _menu_source()
        + f'\nif menu_select "Pick one" {default} {args}; then\n'
        + f'  printf "RESULT=%s\\n" "$MENU_INDEX" > "{out_file}"\n'
        "else\n"
        + f'  printf "CANCELLED=%s\\n" "$MENU_INDEX" > "{out_file}"\n'
        "fi\n"
    )

    child_env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols))
    child_env.pop("ABS_NO_TUI", None)
    child_env.update(env or {})

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs immediately
        os.execvpe("bash", ["bash", str(script)], child_env)

    out = b""
    typed = False
    deadline = time.time() + 20
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if not typed and b"Pick one" in out:
            time.sleep(0.2)
            for key in keys:
                os.write(fd, key.encode())
                time.sleep(0.1)
            typed = True
        if os.waitpid(pid, os.WNOHANG)[0]:
            # The last paint can still be sitting in the pty buffer after the
            # child exits — drain it, or the screen assertions read a half frame.
            while True:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    break
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            break
    os.close(fd)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    result = out_file.read_text().strip() if out_file.exists() else "<no result>"
    return result, out.decode(errors="replace")


@pytest.mark.parametrize(
    "keys,expected",
    [
        ([ENTER], "RESULT=0"),                      # Enter takes the default
        ([DOWN, ENTER], "RESULT=1"),
        ([DOWN, DOWN, ENTER], "RESULT=2"),
        ([UP, ENTER], "RESULT=2"),                  # up from the top wraps
        ([DOWN, DOWN, DOWN, ENTER], "RESULT=0"),    # down off the end wraps
        (["j", "j", "k", ENTER], "RESULT=1"),       # vi keys move too
        ([DOWN, " "], "RESULT=1"),                  # space selects
    ],
)
def test_the_arrows_move_the_highlight_and_enter_takes_it(tmp_path, keys, expected):
    result, _ = _drive(tmp_path, keys)
    assert result == expected


def test_typing_the_number_still_selects_it(tmp_path):
    """The old prompt wanted a number. Muscle memory has to keep working."""
    result, _ = _drive(tmp_path, ["3"])
    assert result == "RESULT=2"


def test_a_number_past_the_end_is_ignored_rather_than_obeyed(tmp_path):
    result, _ = _drive(tmp_path, ["9", DOWN, ENTER])
    assert result == "RESULT=1"


@pytest.mark.parametrize("key", ["q", ESC])
def test_backing_out_returns_non_zero_and_no_index(tmp_path, key):
    result, screen = _drive(tmp_path, [key])
    assert result == "CANCELLED=-1"
    assert "cancelled" in screen


def test_the_caller_chooses_which_row_starts_highlighted(tmp_path):
    result, _ = _drive(tmp_path, [ENTER], default=2)
    assert result == "RESULT=2"


def test_the_chosen_row_survives_as_one_line_of_scrollback(tmp_path):
    """The menu collapses on exit: the decision stays, the list doesn't."""
    _, screen = _drive(tmp_path, [DOWN, ENTER])
    tail = screen[screen.rindex("Pick one") :]
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", tail)
    lines = [ln for ln in plain.splitlines() if ln.strip()]
    assert lines[-1].strip() == "❯ beta"
    assert "alpha" not in lines[-1]


def test_a_row_wider_than_the_terminal_is_cut_not_wrapped(tmp_path):
    """A wrapped row would desync the cursor maths and smear every redraw."""
    long_item = "New session in this folder (" + "/very/long/path" * 6 + ")"
    result, screen = _drive(tmp_path, [ENTER], items=(long_item, "beta"), cols=60)
    assert result == "RESULT=0"
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", screen)
    assert max(len(ln) for ln in plain.splitlines()) <= 60
    assert "…" in plain


def test_the_cursor_is_put_back_before_the_menu_returns(tmp_path):
    """A hidden cursor left behind would follow the operator into their shell."""
    _, screen = _drive(tmp_path, [ENTER])
    assert screen.count("\x1b[?25l") == screen.count("\x1b[?25h") == 1


# --- the fallback ------------------------------------------------------------
#
# These same functions run where raw keys are wrong or impossible: `docker exec`
# without -t, a pipe, CI. There they must be the old numbered prompt, unchanged.


@pytest.mark.parametrize("env", [{"ABS_NO_TUI": "1"}, {"TERM": "dumb"}])
def test_without_a_usable_terminal_it_is_the_old_numbered_prompt(tmp_path, env):
    result, screen = _drive(tmp_path, ["2" + ENTER], env=env)
    assert result == "RESULT=1"
    assert "2. beta" in screen
    assert "↑↓" not in screen


def test_the_fallback_still_takes_enter_as_the_default(tmp_path):
    result, _ = _drive(tmp_path, [ENTER], env={"ABS_NO_TUI": "1"})
    assert result == "RESULT=0"


def test_junk_at_the_fallback_prompt_is_a_back_out_not_a_pick(tmp_path):
    result, _ = _drive(tmp_path, ["zzz" + ENTER], env={"ABS_NO_TUI": "1"})
    assert result == "CANCELLED=-1"


def test_the_menu_block_is_valid_bash_on_its_own(tmp_path):
    """It is lifted out of abs.sh by these tests; keep that lift honest."""
    block = tmp_path / "block.sh"
    block.write_text(_menu_source())
    proc = subprocess.run(["bash", "-n", str(block)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --- labels that aren't plain ASCII ------------------------------------------
#
# Every real menu carries at least one emoji (🏖 🆕 📁 ▶) and the labels come from
# project names and folder paths, which can hold anything. A row that overflows
# the terminal wraps, and a wrapped row makes the fixed "move up N+1 lines" redraw
# count logical prints instead of screen rows — so the highlight lands somewhere
# other than where it is drawn. That is a selection-spoofing shape, not a smear.


def _display_columns(line):
    """Roughly what a terminal charges for a string: wide glyphs cost two."""
    wide = sum(
        1 for ch in line
        if 0x1100 <= ord(ch) <= 0x115F or 0x2E80 <= ord(ch) <= 0xA4CF
        or 0xAC00 <= ord(ch) <= 0xD7A3 or 0xF900 <= ord(ch) <= 0xFAFF
        or 0xFE30 <= ord(ch) <= 0xFE6F or 0xFF00 <= ord(ch) <= 0xFF60
        or 0x1F300 <= ord(ch) <= 0x1FAFF
    )
    return len(line) + wide


@pytest.mark.parametrize(
    "label",
    [
        "🏖 " * 30,                       # emoji, two columns each
        "研究プロジェクトのディレクトリ" * 4,   # CJK, two columns each
        "🆕 New session in " + "/very/long/path" * 5,   # the real shape, oversized
    ],
)
def test_a_wide_glyph_label_is_measured_in_columns_not_characters(tmp_path, label):
    result, screen = _drive(tmp_path, [ENTER], items=(label, "beta"), cols=40)
    assert result == "RESULT=0"
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", screen)
    widest = max(_display_columns(ln) for ln in plain.splitlines())
    assert widest <= 40, f"row would wrap at 40 columns: {widest}"


def test_emoji_labels_that_fit_are_left_alone(tmp_path):
    """The real menus are all short enough; truncating them would be a regression."""
    items = ("🏖 box1", "🆕 New session in this folder", "📁 Another project…")
    _, screen = _drive(tmp_path, [ENTER], items=items)
    for label in items:
        assert label in screen


def test_a_newline_in_a_label_cannot_split_the_row(tmp_path):
    """A two-line row desyncs the cursor maths and moves the highlight off-target."""
    result, screen = _drive(tmp_path, [DOWN, ENTER], items=("alpha\nSMUGGLED", "beta"))
    assert result == "RESULT=1"
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", screen)
    body = plain[plain.index("Pick one"):]
    assert "SMUGGLED" in body                      # the text survives...
    for line in body.splitlines():
        assert not line.strip() == "SMUGGLED"      # ...but never on a line of its own


@pytest.mark.parametrize("payload", ["\x1b[2J", "\x1b[5A", "\x1b[H", "\x1b(B"])
def test_a_stray_escape_sequence_in_a_label_is_defanged(tmp_path, payload):
    """A folder name can hold an escape sequence. It must not get to act like one."""
    result, screen = _drive(tmp_path, [ENTER], items=(f"alpha{payload}gone", "beta"))
    assert result == "RESULT=0"
    assert payload not in screen
    assert "gone" in screen          # the text survives, only the escape is removed


def test_a_label_keeps_the_colour_a_caller_put_in_it(tmp_path):
    """pick_profile marks a busy bot in yellow; sanitizing must not eat that."""
    _, screen = _drive(tmp_path, [ENTER], items=("alpha \x1b[33m(in use)\x1b[0m", "beta"))
    assert "\x1b[33m(in use)" in screen


# --- degenerate lists --------------------------------------------------------


def test_a_single_item_list_still_works_and_the_arrows_are_harmless(tmp_path):
    result, _ = _drive(tmp_path, [DOWN, UP, ENTER], items=("only",))
    assert result == "RESULT=0"


def test_a_default_index_past_the_end_falls_back_to_the_first_row(tmp_path):
    result, _ = _drive(tmp_path, [ENTER], default=99)
    assert result == "RESULT=0"


# --- the real call sites -----------------------------------------------------
#
# Everything above drives menu_select through a synthetic list. These drive the
# actual functions, because the risk that reaches a user is not the menu — it is a
# call site reading MENU_INDEX off by one, or mishandling a back-out. pick_profile
# is the sharpest: its last row is a synthetic "add a new bot" that is NOT in the
# names array.


def _abs_lib(tmp_path):
    """abs.sh with its final `main "$@"` stripped, so it can be sourced."""
    lib = tmp_path / "abslib.sh"
    lib.write_text("".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"'))
    return lib


def _abs_home(tmp_path, profiles):
    home = tmp_path / "abshome"
    for name, bot in profiles:
        d = home / "profiles" / name
        d.mkdir(parents=True)
        (d / "rc.json").write_text('{"bot": "%s", "chat_id": 1, "token": "x"}' % bot)
    return home


def _drive_callsite(tmp_path, keys, snippet, profiles, cols=100):
    out_file = tmp_path / "callsite"
    lib = _abs_lib(tmp_path)
    home = _abs_home(tmp_path, profiles)
    script = tmp_path / "cs.sh"
    script.write_text(f'source "{lib}"\n{snippet.format(out=out_file)}\n')

    env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols), ABS_HOME=str(home))
    env.pop("ABS_NO_TUI", None)
    env.pop("TELEGRAM_STATE_DIR", None)   # a stray export would repoint the profile

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover
        os.execvpe("bash", ["bash", str(script)], env)
    out, typed, deadline = b"", False, time.time() + 25
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if not typed and "❯".encode() in out:      # the menu has painted
            time.sleep(0.3)
            for key in keys:
                os.write(fd, key.encode())
                time.sleep(0.15)
            typed = True
        if os.waitpid(pid, os.WNOHANG)[0]:
            while True:                     # drain, or the screen is a half frame
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    break
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            break
    os.close(fd)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    result = out_file.read_text().strip() if out_file.exists() else "<no result>"
    return result, out.decode(errors="replace")


THREE_BOTS = [("alpha", "alphabot"), ("bravo", "bravobot"), ("charlie", "charliebot")]
PICK = 'pick_profile; printf "%s\\n" "$PROFILE" > "{out}"'


@pytest.mark.parametrize(
    "keys,expected",
    [
        ([ENTER], "alpha"),
        ([DOWN, ENTER], "bravo"),
        ([DOWN, DOWN, ENTER], "charlie"),
        (["2"], "bravo"),
    ],
)
def test_pick_profile_selects_the_bot_the_highlight_was_on(tmp_path, keys, expected):
    result, _ = _drive_callsite(tmp_path, keys, PICK, THREE_BOTS)
    assert result == expected


def test_pick_profile_shows_each_bots_username(tmp_path):
    _, screen = _drive_callsite(tmp_path, [ENTER], PICK, THREE_BOTS)
    assert "@bravobot" in screen


def test_pick_profile_last_row_is_add_a_bot_not_a_fourth_profile(tmp_path):
    """The synthetic row sits one past the names array — the classic off-by-one."""
    result, screen = _drive_callsite(tmp_path, [UP, ENTER, "newbot" + ENTER], PICK, THREE_BOTS)
    assert result == "newbot"
    assert "Name for the new profile" in screen


def test_pick_profile_backing_out_stops_rather_than_picking_something(tmp_path):
    result, screen = _drive_callsite(
        tmp_path, ["q"], PICK + ' || printf "died\\n" > "{out}"', THREE_BOTS
    )
    assert result == "<no result>"      # nothing was chosen at all
    assert "Cancelled" in screen


def test_a_lone_profile_is_taken_without_asking(tmp_path):
    """One bot is not a choice; the menu must not appear for it."""
    result, screen = _drive_callsite(tmp_path, [], PICK, [("solo", "solobot")])
    assert result == "solo"
    assert "Which bot?" not in screen


# ---- a long list: a window, and a search -------------------------------------
#
# The operator's project list is twenty-six folders deep, because it shows every
# child of his workspace root. A menu that tall pushes everything else off the
# screen, and scrolling past twenty-six rows to reach one is not a choice anyone
# enjoys making twice.

LONG_ITEMS = tuple(f"proj-{i:02d}-{n}" for i, n in enumerate(
    ["airllm", "bash", "bizz", "chat", "cheetah", "cldx", "dash", "enc", "hagent",
     "hostllm", "invest", "jesse", "llm", "lucy", "nemo", "ornith", "panda",
     "research", "rtsp", "sandbox"]))


def test_a_long_list_paints_a_window_not_the_whole_thing(tmp_path):
    _, screen = _drive(tmp_path, [ENTER], items=LONG_ITEMS)
    shown = [i for i in LONG_ITEMS if i in screen]
    assert len(shown) <= 10, f"painted {len(shown)} rows: {shown}"


def test_scrolling_reaches_past_the_window(tmp_path):
    """Twelve downs from the top lands on index 12 — only reachable if the window
    moved, since it starts showing 0-9."""
    result, screen = _drive(tmp_path, [DOWN] * 12 + [ENTER], items=LONG_ITEMS)
    assert result == "RESULT=12", screen[-400:]


def test_search_narrows_and_returns_the_original_index(tmp_path):
    """The index that comes back has to point into the FULL list. A filtered menu
    returning a filtered position is the bug this mapping exists to prevent — it
    would silently open the wrong project."""
    result, screen = _drive(tmp_path, ["/", "j", "e", "s", "s", "e", ENTER],
                            items=LONG_ITEMS)
    assert result == "RESULT=11", screen[-400:]


def test_letters_that_are_movement_keys_still_type_into_a_search(tmp_path):
    """`j`, `k` and `q` are down, up and cancel. Without the guard, searching for
    "jesse" would walk the cursor down and then quit — which is why the search
    opens with `/` rather than on the first letter typed."""
    result, screen = _drive(tmp_path, ["/", "j", ENTER], items=LONG_ITEMS)
    assert result != "CANCELLED=-1", screen[-400:]


def test_a_search_matching_nothing_keeps_the_last_good_set(tmp_path):
    """An empty menu has nothing to press enter on and reads as a crash."""
    result, screen = _drive(tmp_path, ["/", "j", "e", "z", "z", ENTER],
                            items=LONG_ITEMS)
    assert result.startswith("RESULT="), screen[-400:]


def test_a_short_list_keeps_its_digit_jump(tmp_path):
    """Everything above is new behaviour for long lists only. A short menu still
    jumps on a digit, with no search and no window."""
    result, screen = _drive(tmp_path, ["2"])
    assert result == "RESULT=1", screen[-400:]
    assert "search" not in screen
