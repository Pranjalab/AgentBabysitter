"""Arrow-key picker + numeric fallback."""

from __future__ import annotations

import pytest

from cldx.picker import PickerRow, pick_numeric


def _rows() -> list[PickerRow]:
    return [
        PickerRow(text="alpha", payload="A"),
        PickerRow(text="beta", payload="B", deletable=True, delete_hint="rm beta"),
        PickerRow(text="gamma", payload="G", deletable=True, delete_hint="rm gamma"),
    ]


def test_numeric_pick_returns_payload():
    chosen = pick_numeric(_rows(), input_fn=lambda _p: "2")
    assert chosen == "B"


def test_numeric_pick_quit_returns_none():
    assert pick_numeric(_rows(), input_fn=lambda _p: "q") is None


def test_numeric_pick_handles_out_of_range():
    inputs = iter(["99", "abc", "1"])
    chosen = pick_numeric(_rows(), input_fn=lambda _p: next(inputs))
    assert chosen == "A"


def test_numeric_pick_can_delete_row():
    """Typing 'd<n>' on a deletable row removes it; then user can pick again."""
    deleted: list[str] = []
    inputs = iter(["d2", "2"])
    chosen = pick_numeric(
        _rows(),
        input_fn=lambda _p: next(inputs),
        on_delete=lambda row: deleted.append(row.payload),
    )
    assert deleted == ["B"]
    # Row indexing shifted after delete: row 2 is now what was gamma.
    assert chosen == "G"


def test_numeric_pick_refuses_to_delete_non_deletable():
    """'d1' should not invoke on_delete for a non-deletable row."""
    deleted: list[str] = []
    inputs = iter(["d1", "1"])
    chosen = pick_numeric(
        _rows(),
        input_fn=lambda _p: next(inputs),
        on_delete=lambda row: deleted.append(row.payload),
    )
    assert deleted == []
    assert chosen == "A"


def test_numeric_pick_returns_none_when_all_deletable_rows_removed():
    """If the user deletes every row, the picker exits with None."""
    deleted = []
    inputs = iter(["d3", "d2", "d1"])  # last one is non-deletable so won't go through
    rows = _rows()
    # Make every row deletable so the picker can empty out.
    for r in rows:
        r.deletable = True
    chosen = pick_numeric(
        rows, input_fn=lambda _p: next(inputs),
        on_delete=lambda row: deleted.append(row.payload),
    )
    assert chosen is None
    assert sorted(deleted) == ["A", "B", "G"]
