"""Arrow-key picker — like Claude Code's menus.

A small ``prompt_toolkit`` ``Application`` that renders a list of rows
with a ``❯`` cursor on the selected row, lets the user navigate with
arrow keys, select with Enter, delete with ``d`` (with one-keypress
confirmation), and cancel with ``q`` / Ctrl-C / Ctrl-D.

Used by the startup picker. Tests use ``pick_numeric`` (a non-TTY
fallback that takes an ``input_fn``) so the CI doesn't need a real TTY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl


@dataclass
class PickerRow:
    """One row in the picker.

    Attributes
    ----------
    text:       what the user sees
    payload:    arbitrary value returned when this row is selected
    deletable:  if True, pressing 'd' on this row offers to delete it.
                The ``on_delete`` callback (passed to ``pick_with_arrows``)
                receives ``payload`` and is expected to actually delete
                the underlying resource (jsonl file, tmux session, etc.)
    delete_hint: short label shown during delete confirmation
                ("delete session", "kill tmux", ...)
    """
    text: str
    payload: Any
    deletable: bool = False
    delete_hint: str = "delete"


def _is_tty() -> bool:
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty()


async def pick_with_arrows(
    rows: list[PickerRow],
    header: str = "",
    on_delete: Callable[[PickerRow], None] | None = None,
) -> Any | None:
    """Show an arrow-key picker. Returns the selected payload, or None on cancel.

    `on_delete(row)` is called when the user confirms a delete; the
    picker then removes that row from the visible list and continues.
    """
    state = {
        "idx": 0,
        "pending_delete": False,  # True after 'd' is pressed; waits for 'y'
    }

    def render() -> FormattedText:
        out: list[tuple[str, str]] = []
        if header:
            out.append(("class:header", header + "\n"))
            out.append(("", "\n"))
        for i, row in enumerate(rows):
            if i == state["idx"]:
                style = "fg:ansigreen bold"
                marker = "❯ "
            else:
                style = ""
                marker = "  "
            out.append((style, f"{marker}{row.text}\n"))

        if state["pending_delete"]:
            target = rows[state["idx"]]
            out.append(("", "\n"))
            out.append((
                "fg:ansired bold",
                f"⚠ {target.delete_hint}: {target.text}\n",
            ))
            out.append((
                "fg:ansiyellow",
                "  Press [y] to confirm · any other key to cancel\n",
            ))
        else:
            out.append(("", "\n"))
            out.append((
                "class:hint",
                "  ↑↓ move · Enter select · d delete · q cancel\n",
            ))
        return FormattedText(out)

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if state["pending_delete"]:
            state["pending_delete"] = False
            return
        if rows:
            state["idx"] = (state["idx"] - 1) % len(rows)

    @kb.add("down")
    def _down(event):
        if state["pending_delete"]:
            state["pending_delete"] = False
            return
        if rows:
            state["idx"] = (state["idx"] + 1) % len(rows)

    @kb.add("pageup")
    def _pgup(event):
        if rows:
            state["idx"] = max(0, state["idx"] - 5)
        state["pending_delete"] = False

    @kb.add("pagedown")
    def _pgdn(event):
        if rows:
            state["idx"] = min(len(rows) - 1, state["idx"] + 5)
        state["pending_delete"] = False

    @kb.add("home")
    def _home(event):
        state["idx"] = 0
        state["pending_delete"] = False

    @kb.add("end")
    def _end(event):
        if rows:
            state["idx"] = len(rows) - 1
        state["pending_delete"] = False

    @kb.add("enter")
    def _enter(event):
        if state["pending_delete"]:
            # Enter is not the confirm key — cancel the pending delete.
            state["pending_delete"] = False
            return
        if rows:
            event.app.exit(result=rows[state["idx"]].payload)

    @kb.add("d")
    def _delete(event):
        if not rows:
            return
        if not rows[state["idx"]].deletable:
            return
        # Arm a pending-delete prompt; user must press 'y' next.
        state["pending_delete"] = True

    @kb.add("y")
    def _confirm_y(event):
        if not state["pending_delete"]:
            return
        state["pending_delete"] = False
        row = rows[state["idx"]]
        if on_delete is not None:
            try:
                on_delete(row)
            except Exception:  # noqa: BLE001 — keep picker alive
                pass
        rows.pop(state["idx"])
        if not rows:
            event.app.exit(result=None)
            return
        state["idx"] = min(state["idx"], len(rows) - 1)

    @kb.add("<any>")
    def _any_other(event):
        # Any other key while pending_delete: cancel.
        if state["pending_delete"]:
            state["pending_delete"] = False

    @kb.add("q", eager=True)
    @kb.add("c-c", eager=True)
    @kb.add("c-d", eager=True)
    def _quit(event):
        event.app.exit(result=None)

    control = FormattedTextControl(text=render, focusable=True, show_cursor=False)
    window = Window(content=control, always_hide_cursor=True)
    app = Application(
        layout=Layout(window),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
        erase_when_done=True,
    )
    return await app.run_async()


def pick_numeric(
    rows: list[PickerRow],
    header: str = "",
    input_fn: Callable[[str], str] = input,
    on_delete: Callable[[PickerRow], None] | None = None,
) -> Any | None:
    """Numeric-typing fallback when stdin isn't a TTY (tests / pipes).

    Same data shape as ``pick_with_arrows`` but uses ``input()`` for
    selection and a ``d<n>`` prefix to delete row n (e.g., ``d3``).
    """
    while True:
        if header:
            print(header)
        for i, row in enumerate(rows, start=1):
            marker = "d" if row.deletable else " "
            print(f"  [{i}]{marker} {row.text}")
        print("  d<n> to delete row n · q to quit")
        raw = input_fn("\nPick [number]: ").strip().lower()
        if raw in ("q", "quit", "exit"):
            return None
        if raw.startswith("d") and raw[1:].isdigit():
            n = int(raw[1:])
            if 1 <= n <= len(rows) and rows[n - 1].deletable:
                if on_delete is not None:
                    on_delete(rows[n - 1])
                rows.pop(n - 1)
                if not rows:
                    return None
                continue
            print("  not deletable / out of range")
            continue
        try:
            n = int(raw)
        except ValueError:
            print("  type a number, or `d<n>` to delete, or q to quit")
            continue
        if 1 <= n <= len(rows):
            return rows[n - 1].payload
        print(f"  out of range (1..{len(rows)})")
