"""FramedInputSession — bordered prompt_toolkit input box."""

from __future__ import annotations

import pytest

from cldx.framed_input import FramedInputSession


def test_construct_with_title_callable():
    """Construction must not touch the terminal; just stash the callable."""
    session = FramedInputSession(title_fn=lambda: " hello ")
    assert session._title_fn() == " hello "


def test_history_persists_across_calls():
    """The same session reuses its history (up-arrow recall works)."""
    session = FramedInputSession(title_fn=lambda: "")
    h1 = session._history
    h2 = session._history
    assert h1 is h2  # not rebuilt per prompt


def test_title_fn_is_re_evaluated():
    """A mutable closure means the title updates as state changes — the
    framed widget reads the callable each render."""
    state = {"label": "first"}
    session = FramedInputSession(title_fn=lambda: state["label"])
    assert session._title_fn() == "first"
    state["label"] = "second"
    assert session._title_fn() == "second"


def test_bridgeui_prompt_title_matches_pending_state(tmp_path, monkeypatch):
    """BridgeUI._prompt_title must mirror the pending-prompt state so the
    framed input box border shows the right hint."""
    from tests.unit.test_eager_classification import _make_bridge_ui
    from cldx.prompt_classifier import ClassifiedPrompt, PromptType

    ui = _make_bridge_ui(tmp_path, monkeypatch)

    # No pending prompt → bare title (now "Claude + TMUX").
    title = ui._prompt_title()
    assert "Claude" in title
    # No y/n suffix without a pending prompt.
    assert "y / n" not in title
    assert "y | n" not in title

    # YN prompt → title says (y / n).
    ui.pending = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN, extracted_command="Bash(ls)",
    )
    assert "y / n" in ui._prompt_title()

    # Menu prompt → title shows the available digits.
    ui.pending = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Bash(ls)",
        menu_options=("1. Yes", "2. Maybe", "3. No"),
    )
    title = ui._prompt_title()
    assert "1/2/3" in title
    assert "y" in title and "n" in title


def test_constants_define_prefix_and_tab_hint():
    """Static class constants — these are what render in the cell.
    The PREFIX must include a leading space (left padding inside the
    Frame) and the caret. The Tab hint must mention 'Tab' so users
    know how to accept the suggestion."""
    assert FramedInputSession.PREFIX.startswith(" ")
    assert "❯" in FramedInputSession.PREFIX
    assert "Tab" in FramedInputSession.TAB_HINT


def test_construct_with_suggestion_callable():
    """A suggestion_fn is optional; when supplied, it's stashed for the
    input box to read on every render."""
    state = {"text": "delete it"}
    session = FramedInputSession(
        title_fn=lambda: "claude",
        suggestion_fn=lambda: state["text"],
    )
    assert session._suggestion_fn() == "delete it"
    state["text"] = "remove the dir too"
    assert session._suggestion_fn() == "remove the dir too"


def test_construct_without_suggestion_returns_empty():
    """No suggestion_fn supplied → calling it returns empty string."""
    session = FramedInputSession(title_fn=lambda: "claude")
    assert session._suggestion_fn() == ""
