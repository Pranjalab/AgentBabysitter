"""InteractionLog — plain-text per-session log."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cldx.interaction_log import InteractionLog, CHANNELS, logs_root


def test_path_is_lazy(tmp_path: Path):
    """Constructing an InteractionLog must not create the file."""
    log = InteractionLog(profile="auto-approve", pane="0:0.0", root=tmp_path)
    # No write yet → no file or date dir yet.
    assert log._path is None
    assert not any(tmp_path.iterdir())


def test_first_write_creates_dated_dir(tmp_path: Path):
    log = InteractionLog(profile="auto-approve", pane="0:0.0", root=tmp_path)
    log.cldx_note("hello")
    # ~/.cldx/logs/YYYY-MM-DD/HH-MM-SS_auto-approve_0_0_0.log
    files = list(tmp_path.rglob("*.log"))
    assert len(files) == 1
    p = files[0]
    # Parent is a date dir.
    assert re.match(r"\d{4}-\d{2}-\d{2}", p.parent.name)
    # Filename starts with HH-MM-SS_ and includes profile.
    assert re.match(r"\d{2}-\d{2}-\d{2}_auto-approve", p.name)
    assert "0_0_0" in p.name  # pane separator chars get sanitised
    log.close()


def test_header_and_footer_lines(tmp_path: Path):
    log = InteractionLog(profile="default", pane=None, root=tmp_path)
    log.cldx_note("ok")
    log.close()
    content = log.path.read_text()
    assert content.startswith("# cldx session log")
    assert "profile=default" in content
    assert content.rstrip().endswith("events")  # footer line includes count


def test_event_columns_align(tmp_path: Path):
    """The channel-direction column is fixed-width so grep / column-mode works."""
    log = InteractionLog(profile="default", root=tmp_path)
    log.terminal_in("hi")
    log.terminal_out("ack")
    log.cldx_decision("auto-yes Bash(ls)")
    log.cldx_action("sent 'y'")
    log.telegram_out("approval needed")
    log.telegram_in("y")
    log.claude_out("⏺ Done.")
    log.close()

    lines = [
        ln for ln in log.path.read_text().splitlines()
        if ln and not ln.startswith("#")
    ]
    # Every event line: "[TS] CHAN-DIR<padded>  message"
    pat = re.compile(r"^\[[^\]]+\]\s(\S[\S\- ]+?)\s(.*)$")
    for ln in lines:
        m = pat.match(ln)
        assert m, f"line didn't match prefix grammar: {ln!r}"
    # 7 events written.
    assert log.event_count == 7


def test_multiline_message_indents_continuations(tmp_path: Path):
    log = InteractionLog(profile="default", root=tmp_path)
    log.claude_out("line one\nline two\nline three")
    log.close()
    lines = [
        ln for ln in log.path.read_text().splitlines()
        if ln and not ln.startswith("#")
    ]
    # First line carries the prefix; second and third are indented to align.
    assert "line one" in lines[0]
    # The continuation must start with at least 22 spaces (timestamp + tag).
    assert lines[1].startswith(" " * 22)
    assert "line two" in lines[1]
    assert "line three" in lines[2]


def test_channel_canonical_set():
    """Documented channels stay stable so external tooling can grep on them."""
    assert set(CHANNELS) == {"terminal", "telegram", "cldx", "claude"}


def test_pane_chars_sanitised(tmp_path: Path):
    log = InteractionLog(profile="default", pane="myhost:1.2", root=tmp_path)
    log.cldx_note("ok")
    assert "myhost_1_2" in log.path.name


def test_logs_root_uses_home(monkeypatch, tmp_path: Path):
    """The default root sits under ``$CLDX_HOME/logs``."""
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    root = logs_root()
    assert root == tmp_path / "logs"
    assert root.exists()


def test_context_manager_closes(tmp_path: Path):
    with InteractionLog(profile="default", root=tmp_path) as log:
        log.cldx_note("hello")
    # Footer was written by close().
    assert "session ended" in log.path.read_text()
