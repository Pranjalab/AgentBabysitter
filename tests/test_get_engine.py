"""``get_engine`` factory unit tests (PLAN.md 4.2, Step 1.2).

Pure — no tmux/herdr binary needed. ``auto`` selection is exercised both ways by
monkeypatching ``HerdrEngine.available`` so the branch is deterministic regardless
of what is installed on the box.
"""

from __future__ import annotations

import pytest

from absd.engines import HerdrEngine, TmuxEngine, get_engine


def test_get_engine_tmux() -> None:
    assert isinstance(get_engine("tmux"), TmuxEngine)


def test_get_engine_herdr() -> None:
    assert isinstance(get_engine("herdr"), HerdrEngine)


def test_get_engine_auto_prefers_herdr_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HerdrEngine, "available", lambda self: True)
    assert isinstance(get_engine("auto"), HerdrEngine)


def test_get_engine_auto_falls_back_to_tmux_when_herdr_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(HerdrEngine, "available", lambda self: False)
    assert isinstance(get_engine("auto"), TmuxEngine)


def test_get_engine_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_engine("nope")
    with pytest.raises(ValueError):
        get_engine("")
