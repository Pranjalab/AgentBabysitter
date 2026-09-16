"""`abs prompt` — one page, five tabs: everything ABS says to Claude, and the
parts of it that are the operator's to change.

The operator's ask, in his words: "one editable interactive page which can open
and we can edit and personalize our own ABS setup" — persona, the global Claude
Code context, the hook wording (and new hooks), and the memory that builds as
findings come in.

What this module does NOT own is the text. The shipped persona, the shipped hook
wording, the locked mechanics and safety, and the paths all come from `abs.sh`
(`abs prompt defaults`, `abs prompt paths`, `abs prompt show system`), so there
is exactly one place the prompt lives and this page can never drift from what a
launch actually passes. This file is the editor, not the source.

Tabs:

  System   read-only: the mechanics and the safety epilogue, as built
  Persona  ~/.abs/persona.md — tone, message types, the update card
  Hooks    ~/.abs/hooks.json — what a control phrase injects; add your own
  Global   ~/.claude/CLAUDE.md — Claude Code's own personal instructions
  Memory   Claude Code's per-project memory: the index and one file per fact

Keys: F1–F5 switch tabs · ^S save · ^R reset to shipped · ^N new · ^T delete ·
^Q or Esc quit. Saving validates the same way a launch does: a persona over the cap
or carrying `<channel` is refused here rather than silently ignored later.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

# ---- the bridge to abs.sh ------------------------------------------------------


def _abs_script() -> str:
    """abs.sh: the one `abs prompt` launched us from, else the checkout's."""
    env = os.environ.get("ABS_SCRIPT_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve().parent.parent / "abs.sh"
    return str(here)


def abs_json(profile: str, *args: str) -> dict:
    out = subprocess.run(
        ["bash", _abs_script(), "--profile", profile, "prompt", *args],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"abs prompt {' '.join(args)} failed")
    return json.loads(out.stdout)


def abs_text(profile: str, *args: str) -> str:
    out = subprocess.run(
        ["bash", _abs_script(), "--profile", profile, "prompt", *args],
        capture_output=True, text=True, check=False,
    )
    return out.stdout


def approx_tokens(text: str) -> int:
    """Close enough to reason about cost; ~3.9 characters per token for prose."""
    return round(len(text) / 3.9)


def write_private(path: Path, text: str) -> None:
    """Atomic, owner-only — the same discipline abs.sh uses for rc.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


FORGERY = "<channel"


def forged(text: str) -> bool:
    return FORGERY in text.lower()


# ---- a yes/no that does not lose the operator's place --------------------------


class Confirm(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", "Yes", priority=True),
        Binding("enter", "answer(True)", "Yes", priority=True, show=False),
        Binding("n", "answer(False)", "No", priority=True),
        Binding("escape", "answer(False)", "No", priority=True, show=False),
    ]
    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Vertical { width: 60; height: auto; border: thick $accent; padding: 1 2; background: $surface; }
    Confirm Horizontal { height: auto; align: center middle; margin-top: 1; }
    Confirm Button { margin: 0 1; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.question)
            with Horizontal():
                yield Button("Yes", id="yes", variant="warning")
                yield Button("No", id="no", variant="primary")

    @on(Button.Pressed)
    def _answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class Ask(ModalScreen[Optional[str]]):
    """One line of input — a new hook phrase, a new memory file name."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True, show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)

    DEFAULT_CSS = """
    Ask { align: center middle; }
    Ask > Vertical { width: 64; height: auto; border: thick $accent; padding: 1 2; background: $surface; }
    Ask Horizontal { height: auto; align: center middle; margin-top: 1; }
    Ask Button { margin: 0 1; }
    """

    def __init__(self, question: str, placeholder: str = "") -> None:
        super().__init__()
        self.question = question
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.question)
            yield Input(placeholder=self.placeholder, id="answer")
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#answer", Input).focus()

    @on(Input.Submitted)
    def _submit(self) -> None:
        self.dismiss(self.query_one("#answer", Input).value.strip() or None)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#answer", Input).value.strip() or None)
        else:
            self.dismiss(None)


# ---- the page -----------------------------------------------------------------


class PromptApp(App[None]):
    TITLE = "abs prompt"
    # The website's palette (agentbabysitter.com: --bg, --violet, --violet-2,
    # --cyan, --green, --orange, --text, --muted, --line), so the page looks like
    # the thing it belongs to. Corners are `round` wherever a terminal can draw
    # them — box characters, not pixels, but it reads as rounded.
    CSS = """
    Screen { background: #0a0d17; color: #eef1f8; }
    Header { background: #0f1320; color: #a78bfa; text-style: bold; }
    Footer { background: #0f1320; }
    Footer > .footer--key { background: #8b5cf6; color: #eef1f8; }
    Footer > .footer--description { color: #aab3c8; }
    Tabs { background: #0f1320; }
    Tab { color: #6b7590; }
    Tab.-active { color: #eef1f8; text-style: bold; }
    Underline > .underline--bar { color: #8b5cf6; background: #212842; }
    TabPane { padding: 0 1; }
    #status { height: 1; padding: 0 1; color: #6b7590; background: #0f1320; }
    TextArea { height: 1fr; background: #0f1320; border: round #2c3450; }
    TextArea:focus { border: round #8b5cf6; }
    TextArea > .text-area--cursor-line { background: #141a2b; }
    .hint { color: #6b7590; padding: 0 1; height: auto; }
    #hooks-list, #memory-list { width: 34; background: #0f1320; border: round #2c3450; margin-right: 1; }
    #hooks-list:focus, #memory-list:focus { border: round #8b5cf6; }
    ListView > ListItem { background: #0f1320; color: #aab3c8; }
    ListView > ListItem.-highlight { background: #141a2b; color: #eef1f8; }
    ListView:focus > ListItem.-highlight { background: #2c3450; color: #eef1f8; }
    .pane { height: 1fr; }
    Button { border: round #2c3450; background: #141a2b; color: #eef1f8; min-width: 10; }
    Button:hover { background: #212842; }
    Button.-primary { border: round #8b5cf6; background: #8b5cf6; color: #0a0d17; }
    Button.-warning { border: round #f5a623; background: #f5a623; color: #0a0d17; }
    Toast { background: #141a2b; border: round #8b5cf6; }
    Confirm > Vertical, Ask > Vertical { border: round #8b5cf6; background: #0f1320; }
    Input { background: #141a2b; border: round #2c3450; }
    Input:focus { border: round #8b5cf6; }
    """

    # priority=True on every one of these: the editors have focus almost all the
    # time, and a non-priority app binding is only consulted after the focused
    # widget declines the key. In practice that meant ^Q did nothing — reported
    # from a Mac, where the next thing tried was ⌘Q, which closes the terminal
    # and every session in it. None of these keys is used by TextArea (^D is,
    # which is why delete is ^T), and none is a tmux prefix (^B).
    BINDINGS = [
        Binding("f1", "tab('system')", "System", priority=True),
        Binding("f2", "tab('persona')", "Persona", priority=True),
        Binding("f3", "tab('hooks')", "Hooks", priority=True),
        Binding("f4", "tab('global')", "Global", priority=True),
        Binding("f5", "tab('memory')", "Memory", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+r", "reset", "Reset", priority=True),
        Binding("ctrl+n", "new", "New", priority=True),
        Binding("ctrl+t", "delete", "Delete", priority=True),
        Binding("ctrl+q", "quit_page", "Quit", priority=True),
        Binding("escape", "quit_page", "Quit", priority=True, show=False),
    ]

    def __init__(self, profile: str) -> None:
        super().__init__()
        self.profile = profile
        self.defaults = abs_json(profile, "defaults")
        self.paths = abs_json(profile, "paths")
        self.persona_path = Path(self.paths["persona"])
        self.hooks_path = Path(self.paths["hooks"])
        self.global_path = Path(self.paths["global"])
        self.memory_dir = Path(self.paths["memory_dir"])
        self.hooks: Dict[str, str] = self._load_hooks()
        self.hook_selected: Optional[str] = None
        self.memory_selected: Optional[Path] = None
        self.dirty: Dict[str, bool] = {"persona": False, "hooks": False, "global": False, "memory": False}

    # ---- data ------------------------------------------------------------------

    def _load_hooks(self) -> Dict[str, str]:
        if self.hooks_path.exists():
            try:
                data = json.loads(self.hooks_path.read_text())
                if isinstance(data, dict):
                    return {k: str(v) for k, v in data.items()}
            except json.JSONDecodeError:
                pass
        return dict(self.defaults["hooks"])

    def _persona_text(self) -> str:
        if self.persona_path.exists():
            return self.persona_path.read_text()
        return self.defaults["persona"]

    def _memory_files(self) -> List[Path]:
        if not self.memory_dir.exists():
            return []
        files = sorted(p for p in self.memory_dir.glob("*.md") if p.is_file())
        index = self.memory_dir / "MEMORY.md"
        if index in files:
            files.remove(index)
            files.insert(0, index)
        return files

    # ---- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial="persona", id="tabs"):
            with TabPane("System (locked)", id="system"):
                yield Static(
                    "The bridge mechanics and the safety epilogue. Built into abs.sh and not "
                    "editable: a persona must not be able to tell the model to stop replying to "
                    "Telegram, and whatever the persona says, this text comes after it.",
                    classes="hint",
                )
                yield TextArea(abs_text(self.profile, "show", "system"), read_only=True, id="system-text")
            with TabPane("Persona", id="persona"):
                yield Static(f"{self.persona_path} — how the model writes and when it speaks. "
                             "Missing file = the shipped persona.", classes="hint", id="persona-hint")
                yield TextArea(self._persona_text(), id="persona-text")
            with TabPane("Hooks", id="hooks"):
                yield Static("What a control phrase sent from Telegram injects into the model. "
                             "MUTE / OFF / BLOCK act in the hook and never reach the model, so they "
                             "have no wording. ^N adds a phrase of your own; {profile} is substituted.",
                             classes="hint")
                with Horizontal(classes="pane"):
                    yield ListView(id="hooks-list")
                    yield TextArea("", id="hooks-text")
            with TabPane("Global", id="global"):
                yield Static(f"{self.global_path} — Claude Code's own personal instructions, "
                             "loaded in every session on this machine. Not an ABS file; edited here "
                             "for convenience.", classes="hint")
                yield TextArea(self.global_path.read_text() if self.global_path.exists() else "",
                               id="global-text")
            with TabPane("Memory", id="memory"):
                yield Static(f"{self.memory_dir} — what Claude Code remembers about this project: "
                             "MEMORY.md is the index it loads; each other file is one fact. "
                             "^N new fact · ^T delete the selected file.", classes="hint")
                with Horizontal(classes="pane"):
                    yield ListView(id="memory-list")
                    yield TextArea("", id="memory-text")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        for wid in ("system-text", "persona-text", "global-text", "hooks-text", "memory-text"):
            ta = self.query_one(f"#{wid}", TextArea)
            self._baseline[wid] = ta.text
        self._refresh_hooks_list()
        self._refresh_memory_list()
        self._update_status()

    # ---- lists -----------------------------------------------------------------

    def _hook_kind(self, phrase: str) -> str:
        if phrase in self.defaults["enforced"]:
            return "enforced"
        if phrase in self.defaults["hooks"]:
            return "builtin"
        return "custom"

    def _refresh_hooks_list(self) -> None:
        lv = self.query_one("#hooks-list", ListView)
        lv.clear()
        rows: List[str] = list(self.defaults["enforced"]) + list(self.defaults["hooks"])
        rows += [k for k in self.hooks if k not in rows]
        for phrase in rows:
            kind = self._hook_kind(phrase)
            tag = {"enforced": "🔒 code", "builtin": "✎ shipped", "custom": "✎ yours"}[kind]
            if kind == "builtin" and self.hooks.get(phrase) != self.defaults["hooks"][phrase]:
                tag = "✎ edited"
            lv.append(ListItem(Label(f"{phrase:<12} {tag}"), name=phrase))
        if rows and self.hook_selected is None:
            lv.index = len(self.defaults["enforced"])  # first editable one

    def _refresh_memory_list(self) -> None:
        lv = self.query_one("#memory-list", ListView)
        lv.clear()
        for p in self._memory_files():
            lv.append(ListItem(Label(p.name), name=str(p)))
        if self._memory_files() and self.memory_selected is None:
            lv.index = 0

    @on(ListView.Highlighted, "#hooks-list")
    def _hook_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        self._commit_hook_editor()
        phrase = event.item.name or ""
        self.hook_selected = phrase
        ta = self.query_one("#hooks-text", TextArea)
        if self._hook_kind(phrase) == "enforced":
            self._load(ta, f"{phrase} is enforced by the hook itself and never reaches the model.\n"
                           "There is nothing to word. It cannot be edited or removed.")
            ta.read_only = True
        else:
            ta.read_only = False
            self._load(ta, self.hooks.get(phrase, ""))
        self._update_status()

    @on(ListView.Highlighted, "#memory-list")
    def _memory_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        self._commit_memory_editor()
        self.memory_selected = Path(event.item.name or "")
        ta = self.query_one("#memory-text", TextArea)
        self._load(ta, self.memory_selected.read_text() if self.memory_selected.exists() else "")
        self._update_status()

    def _commit_hook_editor(self) -> None:
        """Keep what was typed for the phrase we are leaving."""
        if self.hook_selected and self._hook_kind(self.hook_selected) != "enforced":
            ta = self.query_one("#hooks-text", TextArea)
            if not ta.read_only and self.hooks.get(self.hook_selected) != ta.text:
                self.hooks[self.hook_selected] = ta.text
                self.dirty["hooks"] = True

    _memory_buffer: Dict[Path, str] = {}

    def _commit_memory_editor(self) -> None:
        if self.memory_selected:
            ta = self.query_one("#memory-text", TextArea)
            self._memory_buffer[self.memory_selected] = ta.text

    # ---- dirty tracking and the status line ------------------------------------

    # What each editor last had LOADED into it, keyed by widget id. TextArea.Changed
    # fires for load_text() exactly as it does for a keystroke — and it fires
    # later, as a posted message, so a flag set around the call does not cover it.
    # Without this the memory tab was "unsaved" the moment it opened, so ^Q put up
    # a "quit anyway?" dialog on a page nobody had touched — which from the
    # operator's chair looked like ^Q doing nothing at all. Dirty means: the text
    # differs from what was loaded, or a list operation (new / delete) happened.
    _baseline: Dict[str, str] = {}

    def _load(self, ta: TextArea, text: str) -> None:
        self._baseline[ta.id or ""] = text
        ta.load_text(text)

    @on(TextArea.Changed)
    def _changed(self, event: TextArea.Changed) -> None:
        wid = event.text_area.id or ""
        if event.text_area.text == self._baseline.get(wid, event.text_area.text):
            self._update_status()
            return
        wid = event.text_area.id or ""
        if wid == "persona-text":
            self.dirty["persona"] = True
        elif wid == "global-text":
            self.dirty["global"] = True
        elif wid == "hooks-text" and not event.text_area.read_only:
            self.dirty["hooks"] = True
        elif wid == "memory-text":
            self.dirty["memory"] = True
        self._update_status()

    def _active(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    def _update_status(self) -> None:
        tab = self._active()
        st = self.query_one("#status", Static)
        if tab == "persona":
            text = self.query_one("#persona-text", TextArea).text
            cap = self.defaults["persona_max_chars"]
            source = "yours" if self.persona_path.exists() else "shipped default"
            flag = "  ⚠ over the cap" if len(text) > cap else ""
            flag += "  ⚠ contains <channel — will be refused" if forged(text) else ""
            st.update(f"persona · {source} · {len(text):,}/{cap:,} chars · ~{approx_tokens(text):,} tokens"
                      f"{'  · unsaved' if self.dirty['persona'] else ''}{flag}")
        elif tab == "hooks":
            n_custom = sum(1 for k in self.hooks if self._hook_kind(k) == "custom")
            st.update(f"hooks · {self.hooks_path if self.hooks_path.exists() else 'shipped wording'} · "
                      f"{n_custom} of your own{'  · unsaved' if self.dirty['hooks'] else ''}")
        elif tab == "global":
            text = self.query_one("#global-text", TextArea).text
            st.update(f"global · {self.global_path} · ~{approx_tokens(text):,} tokens"
                      f"{'  · unsaved' if self.dirty['global'] else ''}")
        elif tab == "memory":
            sel = self.memory_selected.name if self.memory_selected else "—"
            st.update(f"memory · {len(self._memory_files())} files · editing {sel}"
                      f"{'  · unsaved' if self.dirty['memory'] else ''}")
        else:
            text = self.query_one("#system-text", TextArea).text
            st.update(f"system · locked · ~{approx_tokens(text):,} tokens")

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self._update_status()

    # ---- actions ---------------------------------------------------------------

    def action_tab(self, name: str) -> None:
        self.query_one("#tabs", TabbedContent).active = name

    def action_save(self) -> None:
        tab = self._active()
        if tab == "persona":
            text = self.query_one("#persona-text", TextArea).text
            cap = self.defaults["persona_max_chars"]
            if forged(text):
                self.notify("Refused: a persona must not contain '<channel' — it could forge a Telegram message.", severity="error")
                return
            if len(text) > cap:
                self.notify(f"Refused: {len(text):,} characters is over the {cap:,} cap.", severity="error")
                return
            write_private(self.persona_path, text if text.endswith("\n") else text + "\n")
            self._baseline["persona-text"] = text
            self.dirty["persona"] = False
            self.notify(f"Saved {self.persona_path}. Takes effect at the next launch.")
        elif tab == "hooks":
            self._commit_hook_editor()
            bad = [k for k, v in self.hooks.items() if forged(v)]
            if bad:
                self.notify(f"Refused: {', '.join(bad)} contains '<channel'.", severity="error")
                return
            write_private(self.hooks_path, json.dumps(self.hooks, indent=2, ensure_ascii=False) + "\n")
            self._baseline["hooks-text"] = self.query_one("#hooks-text", TextArea).text
            self.dirty["hooks"] = False
            self._refresh_hooks_list()
            self.notify(f"Saved {self.hooks_path}. Live for the next control phrase.")
        elif tab == "global":
            text = self.query_one("#global-text", TextArea).text
            self.global_path.parent.mkdir(parents=True, exist_ok=True)
            self.global_path.write_text(text if text.endswith("\n") else text + "\n")
            self._baseline["global-text"] = text
            self.dirty["global"] = False
            self.notify(f"Saved {self.global_path}. Claude Code reads it at the next session.")
        elif tab == "memory":
            self._commit_memory_editor()
            for path, text in self._memory_buffer.items():
                if not path.exists() or path.read_text() != text:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text if text.endswith("\n") else text + "\n")
            self._memory_buffer.clear()
            self._baseline["memory-text"] = self.query_one("#memory-text", TextArea).text
            self.dirty["memory"] = False
            self._refresh_memory_list()
            self.notify("Saved memory.")
        else:
            self.notify("The system slot is locked — nothing to save.", severity="warning")
        self._update_status()

    def action_reset(self) -> None:
        tab = self._active()
        if tab == "persona":
            def done(yes: bool) -> None:
                if not yes:
                    return
                if self.persona_path.exists():
                    self.persona_path.unlink()
                self._load(self.query_one("#persona-text", TextArea), self.defaults["persona"])
                self.dirty["persona"] = False
                self._update_status()
                self.notify("Back on the shipped persona.")
            self.push_screen(Confirm(f"Delete {self.persona_path} and go back to the shipped persona?"), done)
        elif tab == "hooks":
            def done_h(yes: bool) -> None:
                if not yes:
                    return
                if self.hooks_path.exists():
                    self.hooks_path.unlink()
                self.hooks = dict(self.defaults["hooks"])
                self.hook_selected = None
                self.dirty["hooks"] = False
                self._refresh_hooks_list()
                self._update_status()
                self.notify("Back on the shipped wording; your own phrases are gone.")
            self.push_screen(Confirm(f"Delete {self.hooks_path}? Your own phrases go with it."), done_h)
        else:
            self.notify("Only the persona and the hooks have a shipped default to reset to.", severity="warning")

    def action_new(self) -> None:
        tab = self._active()
        if tab == "hooks":
            def made(name: Optional[str]) -> None:
                if not name:
                    return
                phrase = name.upper().strip()
                if not phrase.startswith("ABS "):
                    phrase = "ABS " + phrase
                if phrase in self.defaults["enforced"] or phrase in self.defaults["hooks"]:
                    self.notify(f"{phrase} is built in — select it in the list instead.", severity="warning")
                    return
                self._commit_hook_editor()
                self.hooks.setdefault(phrase, "")
                self.dirty["hooks"] = True
                self.hook_selected = phrase
                self._refresh_hooks_list()
                lv = self.query_one("#hooks-list", ListView)
                for i, item in enumerate(lv.children):
                    if getattr(item, "name", None) == phrase:
                        lv.index = i
                        break
                self.query_one("#hooks-text", TextArea).focus()
            self.push_screen(Ask("New control phrase (sent as a whole message from Telegram):", "ABS REVIEW"), made)
        elif tab == "memory":
            def made_m(name: Optional[str]) -> None:
                if not name:
                    return
                slug = name.lower().strip().replace(" ", "-")
                if not slug.endswith(".md"):
                    slug += ".md"
                path = self.memory_dir / slug
                if path.exists():
                    self.notify(f"{slug} already exists.", severity="warning")
                    return
                self._commit_memory_editor()
                stem = slug[:-3]
                self._memory_buffer[path] = (
                    f"---\nname: {stem}\ndescription: <one line, used to decide relevance>\n"
                    f"metadata:\n  type: project\n---\n\n<the fact>\n"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._memory_buffer[path])
                index = self.memory_dir / "MEMORY.md"
                line = f"- [{stem}]({slug}) — <hook>\n"
                if index.exists():
                    index.write_text(index.read_text().rstrip("\n") + "\n" + line)
                else:
                    index.write_text("# Memory index\n\n" + line)
                self.memory_selected = path
                self.dirty["memory"] = True
                self._refresh_memory_list()
                lv = self.query_one("#memory-list", ListView)
                for i, item in enumerate(lv.children):
                    if getattr(item, "name", None) == str(path):
                        lv.index = i
                        break
                self.query_one("#memory-text", TextArea).focus()
                self.notify(f"Created {slug} and an index line — fill in the description and the hook.")
            self.push_screen(Ask("New memory file (a short slug for one fact):", "release-tagging-rule"), made_m)
        else:
            self.notify("New is for hooks (a phrase) and memory (a fact).", severity="warning")

    def action_delete(self) -> None:
        tab = self._active()
        if tab == "hooks":
            phrase = self.hook_selected
            if not phrase or self._hook_kind(phrase) != "custom":
                self.notify("Only a phrase of your own can be deleted; built-ins can be reworded or reset.", severity="warning")
                return
            def done(yes: bool) -> None:
                if not yes:
                    return
                self.hooks.pop(phrase, None)
                self.hook_selected = None
                self.dirty["hooks"] = True
                self._refresh_hooks_list()
                self._load(self.query_one("#hooks-text", TextArea), "")
                self._update_status()
            self.push_screen(Confirm(f"Remove {phrase}? (^S afterwards to write the file.)"), done)
        elif tab == "memory":
            path = self.memory_selected
            if not path or path.name == "MEMORY.md":
                self.notify("Select a fact file; the index is not deleted from here.", severity="warning")
                return
            def done_m(yes: bool) -> None:
                if not yes:
                    return
                if path.exists():
                    path.unlink()
                self._memory_buffer.pop(path, None)
                index = self.memory_dir / "MEMORY.md"
                if index.exists():
                    kept = [l for l in index.read_text().splitlines() if f"({path.name})" not in l]
                    index.write_text("\n".join(kept) + "\n")
                self.memory_selected = None
                self._refresh_memory_list()
                self._load(self.query_one("#memory-text", TextArea), "")
                self._update_status()
                self.notify(f"Deleted {path.name} and its index line.")
            self.push_screen(Confirm(f"Delete {path.name} and its line in MEMORY.md?"), done_m)
        else:
            self.notify("Delete is for a phrase of your own or a memory file.", severity="warning")

    def action_quit_page(self) -> None:
        if isinstance(self.screen, (Confirm, Ask)):
            return                                   # a dialog is already up; answer it
        if any(self.dirty.values()):
            unsaved = ", ".join(k for k, v in self.dirty.items() if v)
            def done(yes: bool) -> None:
                if yes:
                    self.exit()
            self.push_screen(Confirm(f"Unsaved changes in: {unsaved}. Quit anyway?  (y / n)"), done)
        else:
            self.exit()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="abs prompt")
    ap.add_argument("--profile", default=os.environ.get("ABS_PROFILE", "default"))
    ns = ap.parse_args(argv)
    try:
        app = PromptApp(ns.profile)
    except RuntimeError as e:
        print(f"abs prompt: {e}", file=sys.stderr)
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
