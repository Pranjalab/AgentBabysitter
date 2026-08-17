"""tmux-specific tests for the tmux engine backend (PLAN.md Step 1.1/1.2).

The engine *lifecycle* behaviour (create/is_alive/kill/list/env/duplicate) is now
tested once, parameterized over BOTH backends, in ``test_engine_shared.py`` (the
D4 proof). This file keeps only what is genuinely tmux-shaped:
  - pure output-parsing helpers (run everywhere, no tmux needed),
  - the tmux attach-command string,
  - table formatting and the protocol structural check.
herdr's equivalents live in ``test_engine_herdr.py``; the factory in
``test_get_engine.py``.
"""

from __future__ import annotations

from absd.engines import TmuxEngine
from absd.engines.base import Engine, SessionInfo
from absd.engines.cli import format_sessions_table
from absd.engines.tmux import (
    PaneRecord,
    parse_pane_records,
    sessions_from_records,
)

# --------------------------------------------------------------------------- #
# Pure-function tests (no tmux needed)
# --------------------------------------------------------------------------- #


def test_parse_pane_records_basic() -> None:
    out = (
        "abs-work\t/home/x/proj\t4321\t0\n"
        "abs-other\t/tmp/o\t4322\t1\n"
    )
    recs = parse_pane_records(out)
    assert recs == [
        PaneRecord(session="abs-work", cwd="/home/x/proj", pid=4321, dead=False),
        PaneRecord(session="abs-other", cwd="/tmp/o", pid=4322, dead=True),
    ]


def test_parse_pane_records_skips_blank_and_malformed() -> None:
    out = "\n" "abs-a\t/p\t1\t0\n" "garbage-line-no-tabs\n" "\t\t\n"
    recs = parse_pane_records(out)
    # Only the well-formed abs-a row survives; the 3-empty-field row has 3 parts
    # (< 4) after split? "\t\t" -> ['', '', ''] = 3 parts -> skipped.
    assert len(recs) == 1
    assert recs[0].session == "abs-a"


def test_parse_pane_records_nonint_pid_becomes_none() -> None:
    recs = parse_pane_records("abs-a\t/p\tNOTPID\t0\n")
    assert recs[0].pid is None


def test_sessions_from_records_filters_non_abs_and_folds() -> None:
    recs = [
        PaneRecord(session="abs-work", cwd="/w", pid=10, dead=False),
        PaneRecord(session="scratch", cwd="/s", pid=11, dead=False),  # ignored
        PaneRecord(session="abs-aaa", cwd="/a", pid=12, dead=True),
    ]
    infos = sessions_from_records(recs)
    # sorted by profile: aaa before work
    assert [i.profile for i in infos] == ["aaa", "work"]
    aaa, work = infos
    assert aaa == SessionInfo(profile="aaa", name="abs-aaa", alive=False, cwd="/a", pid=12)
    assert work == SessionInfo(profile="work", name="abs-work", alive=True, cwd="/w", pid=10)


def test_sessions_from_records_alive_if_any_pane_live() -> None:
    recs = [
        PaneRecord(session="abs-x", cwd="/x", pid=1, dead=True),
        PaneRecord(session="abs-x", cwd="/x", pid=2, dead=False),
    ]
    infos = sessions_from_records(recs)
    assert len(infos) == 1
    assert infos[0].alive is True


def test_attach_command_exact_string() -> None:
    eng = TmuxEngine(socket_name="abs")
    assert eng.attach_command("work") == "tmux -L abs attach -t abs-work"


def test_attach_command_custom_socket() -> None:
    eng = TmuxEngine(socket_name="abs-test-xyz")
    assert eng.attach_command("p1") == "tmux -L abs-test-xyz attach -t abs-p1"


def test_tmux_engine_satisfies_protocol() -> None:
    # runtime_checkable structural check — no tmux-specific leak in the protocol.
    assert isinstance(TmuxEngine(), Engine)


def test_format_sessions_table_empty() -> None:
    assert format_sessions_table([]) == "No ABS sessions."


def test_format_sessions_table_rows() -> None:
    table = format_sessions_table(
        [SessionInfo(profile="work", name="abs-work", alive=True, cwd="/w", pid=1)]
    )
    assert "PROFILE" in table and "ALIVE" in table and "CWD" in table
    assert "work" in table and "yes" in table and "/w" in table
