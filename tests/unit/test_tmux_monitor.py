"""TmuxMonitor: ANSI stripping, diff, on_change/on_stable callback wiring."""

from __future__ import annotations

import asyncio

import pytest

from src.tmux_monitor import TmuxMonitor


# --- ANSI stripping --------------------------------------------------------

def test_strip_ansi_csi():
    assert TmuxMonitor.strip_ansi("\x1b[31mhello\x1b[0m") == "hello"


def test_strip_ansi_csi_complex():
    assert TmuxMonitor.strip_ansi("\x1b[1;32;48;5;236mfoo\x1b[0m") == "foo"


def test_strip_ansi_osc_window_title():
    """OSC sequences (window titles) must not leak through as `0;title`."""
    raw = "\x1b]0;my window\x07tail"
    assert TmuxMonitor.strip_ansi(raw) == "tail"


def test_strip_ansi_cursor_hide_show():
    raw = "\x1b[?25lloading\x1b[?25h"
    assert TmuxMonitor.strip_ansi(raw) == "loading"


def test_strip_ansi_plain_passthrough():
    assert TmuxMonitor.strip_ansi("plain text") == "plain text"


def test_strip_ansi_mixed():
    raw = "\x1b[31mhello\x1b[0m \x1b]0;t\x07world"
    assert TmuxMonitor.strip_ansi(raw) == "hello world"


# --- diff_tail -------------------------------------------------------------

def test_diff_tail_empty_old_returns_full_new():
    assert TmuxMonitor.diff_tail("", "abc\n") == "abc\n"


def test_diff_tail_appended_returns_only_suffix():
    assert TmuxMonitor.diff_tail("a\nb\n", "a\nb\nc\n") == "c\n"


def test_diff_tail_scrolled_falls_back_to_tail():
    """When old isn't a prefix of new, return the last few lines of new."""
    old = "x\ny\nz\n"
    new = "completely\ndifferent\nlines\n"
    result = TmuxMonitor.diff_tail(old, new)
    assert "lines" in result


# --- watch() callbacks -----------------------------------------------------

async def test_watch_on_change_fires_on_each_diff():
    m = TmuxMonitor("fake:0.0", poll_interval=0.01, stable_polls=2)
    seqs = iter(["a\n", "ab\n", "abc\n"])

    async def fake_capture():
        try:
            return next(seqs)
        except StopIteration:
            m.stop()
            return "abc\n"

    m.capture = fake_capture
    calls = []

    async def on_change(_diff, snap):
        calls.append(snap)

    await asyncio.wait_for(m.watch(on_change=on_change), timeout=2)
    assert len(calls) == 3


async def test_watch_on_stable_fires_once_per_quiet_epoch():
    m = TmuxMonitor("fake:0.0", poll_interval=0.01, stable_polls=2)
    # Change once, then go quiet, change again, go quiet.
    seqs = iter(["a\n", "ab\n", "ab\n", "ab\n", "abc\n", "abc\n", "abc\n"])

    async def fake_capture():
        try:
            return next(seqs)
        except StopIteration:
            m.stop()
            return "abc\n"

    m.capture = fake_capture
    stable_calls = []

    async def on_stable(snap):
        stable_calls.append(snap)

    await asyncio.wait_for(m.watch(on_stable=on_stable), timeout=2)
    assert len(stable_calls) == 2


async def test_watch_stop_terminates_loop():
    m = TmuxMonitor("fake:0.0", poll_interval=0.01, stable_polls=2)
    calls = 0

    async def fake_capture():
        nonlocal calls
        calls += 1
        if calls > 5:
            m.stop()
        return f"snapshot {calls}\n"

    m.capture = fake_capture
    await asyncio.wait_for(m.watch(), timeout=2)
    assert calls >= 5 and calls < 20  # stop took effect
