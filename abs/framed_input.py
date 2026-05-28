"""A bordered single-line input box, in the style of Claude Code's text input.

Visual structure (when no suggestion is available)::

    ╭─ claude ─────────────────────────────────────────────╮
    │  ❯ █                                                  │
    ╰───────────────────────────────────────────────────────╯

When Claude's pane shows a placeholder suggestion (e.g. ``delete it``
between two separator lines), the box surfaces it as dim italic text
after the cursor — Tab accepts it just like in Claude Code itself::

    ╭─ claude ─────────────────────────────────────────────╮
    │  ❯ |delete it          (Tab to accept)                │   ← dim
    ╰───────────────────────────────────────────────────────╯

Once the user starts typing, the placeholder disappears and only the
typed text is shown.
"""

from __future__ import annotations

from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
)
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.processors import (
    AfterInput,
    BeforeInput,
    ConditionalProcessor,
)
from prompt_toolkit.widgets import Frame


class FramedInputSession:
    """Bordered, padded input with prefix + suggestion + Tab-accept.

    Parameters
    ----------
    title_fn:
        Callable returning the string shown on the Frame's top border.
        Re-evaluated on every render so dynamic states reflect immediately.
    suggestion_fn:
        Optional callable returning the placeholder text to show when the
        buffer is empty. Tab in an empty buffer copies the suggestion
        into the buffer. Pass ``None`` (the default) to disable.
    """

    # Visible left padding inside the frame, before the caret.
    PREFIX = " ❯ "
    # The Tab-accept hint shown to the right of the dim suggestion.
    TAB_HINT = "  (Tab to accept)"

    def __init__(
        self,
        title_fn: Callable[[], str],
        suggestion_fn: Callable[[], str] | None = None,
    ) -> None:
        self._title_fn = title_fn
        self._suggestion_fn = suggestion_fn or (lambda: "")
        self._history = InMemoryHistory()

    async def prompt_async(self) -> str:
        buffer = Buffer(multiline=False, history=self._history)

        # --- suggestion text helper -------------------------------------

        def current_suggestion() -> str:
            try:
                return self._suggestion_fn() or ""
            except Exception:  # noqa: BLE001 — never break the prompt
                return ""

        buffer_is_empty = Condition(lambda: not buffer.text)
        has_suggestion = Condition(
            lambda: bool(current_suggestion()) and not buffer.text
        )

        # --- key bindings -----------------------------------------------

        kb = KeyBindings()

        @kb.add("enter")
        def _on_enter(event):
            event.app.exit(result=buffer.text)

        @kb.add("tab")
        def _on_tab(event):
            sugg = current_suggestion()
            if sugg and not buffer.text.strip():
                buffer.text = sugg
                buffer.cursor_position = len(sugg)

        @kb.add("c-d", eager=True)
        def _on_eof(event):
            event.app.exit(exception=EOFError)

        @kb.add("c-c", eager=True)
        def _on_ctrl_c(event):
            event.app.exit(exception=KeyboardInterrupt)

        # --- input control ----------------------------------------------

        # Render order in the cell:
        #   "[PREFIX][cursor][AfterInput-suggestion shown only when empty]"
        # i.e. when the buffer is empty, the placeholder appears right
        # after the cursor in dim italic — just like Claude Code's box.
        suggestion_processor = ConditionalProcessor(
            processor=AfterInput(
                text=lambda: current_suggestion() + self.TAB_HINT,
                style="italic fg:ansibrightblack",
            ),
            filter=has_suggestion,
        )

        input_control = BufferControl(
            buffer=buffer,
            input_processors=[
                BeforeInput(text=self.PREFIX, style="bold fg:ansicyan"),
                suggestion_processor,
            ],
        )
        input_window = Window(
            content=input_control,
            height=1,
            wrap_lines=False,
        )

        # Frame the input. The title callable is re-evaluated each render.
        framed = Frame(input_window, title=self._title_fn)
        layout = Layout(framed, focused_element=input_window)

        app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=False,
            mouse_support=False,
            erase_when_done=False,
        )
        return await app.run_async()
