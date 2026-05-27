"""TmuxController: verify the correct `tmux send-keys` invocations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cldx.tmux_controller import TmuxController, TmuxControllerError


class FakeProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return (b"", self._stderr)


@pytest.fixture
def captured_calls(monkeypatch):
    calls: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(list(args))
        return FakeProc()

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    return calls


async def test_send_yes_sends_y_then_enter(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_yes()
    assert captured_calls == [
        ["tmux", "send-keys", "-t", "pane:0.0", "y", "Enter"],
    ]


async def test_send_no_sends_n_then_enter(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_no()
    assert captured_calls[0] == [
        "tmux", "send-keys", "-t", "pane:0.0", "n", "Enter",
    ]


async def test_send_enter_sends_just_enter(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_enter()
    assert captured_calls[0] == ["tmux", "send-keys", "-t", "pane:0.0", "Enter"]


async def test_send_text_uses_literal_flag(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_text("hello world")
    # First call: literal text with -l flag.
    assert captured_calls[0] == [
        "tmux", "send-keys", "-l", "-t", "pane:0.0", "hello world",
    ]
    # Second call: Enter.
    assert captured_calls[1] == ["tmux", "send-keys", "-t", "pane:0.0", "Enter"]


async def test_send_text_no_submit_skips_enter(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_text("foo", submit=False)
    assert len(captured_calls) == 1
    assert "Enter" not in captured_calls[0]


async def test_send_escape_sends_escape(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_escape()
    assert captured_calls[0] == ["tmux", "send-keys", "-t", "pane:0.0", "Escape"]


async def test_send_digit_sends_single_keystroke(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_digit(3)
    assert captured_calls[0] == ["tmux", "send-keys", "-t", "pane:0.0", "3"]


async def test_send_digit_rejects_out_of_range():
    c = TmuxController("pane:0.0")
    with pytest.raises(ValueError):
        await c.send_digit(42)


async def test_send_arrow_select_validates_direction():
    c = TmuxController("pane:0.0")
    with pytest.raises(ValueError):
        await c.send_arrow_select(option_index=1, direction="Left")


async def test_send_arrow_select_presses_down_then_enter(captured_calls):
    c = TmuxController("pane:0.0")
    await c.send_arrow_select(option_index=2, direction="Down")
    # 2 Down presses then 1 Enter = 3 subprocess calls
    assert len(captured_calls) == 3
    assert captured_calls[0][-1] == "Down"
    assert captured_calls[1][-1] == "Down"
    assert captured_calls[2][-1] == "Enter"


async def test_send_keys_failure_raises(monkeypatch):
    async def fake_failing(*args, **kwargs):
        return FakeProc(returncode=1, stderr=b"no such pane")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_failing)
    c = TmuxController("bad:0.0")
    with pytest.raises(TmuxControllerError):
        await c.send_yes()
