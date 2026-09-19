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

  Overview what the page is, the three slots with their sizes, where every file is
  System   read-only: the mechanics and the safety epilogue, as built
  Persona  ~/.abs/persona.md — tone, message types, the update card
  Hooks    ~/.abs/hooks.json — what a control phrase injects; add your own
  Project  <project>/CLAUDE.md — this repository's instructions (committed, shared)
  Global   ~/.claude/CLAUDE.md — locked until confirmed; it shapes every Claude session
  Memory   Claude Code's per-project memory: the index and one file per fact

Keys: F1–F7 or ^PgUp/^PgDn switch tabs (tabs are clickable too) · ^S save ·
^R reset to shipped · ^N new · ^T delete · ^Q or Esc quit. Saving validates the
same way a launch does: a persona over the cap or carrying `<channel` is refused
here rather than silently ignored later.
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


class QuitDialog(ModalScreen[str]):
    """Unsaved changes: save everything and quit, quit without saving, or stay.

    The two-button version offered quit-or-stay, and "save all, then quit" is
    what most people actually want when they reach for ^Q with edits open."""

    BINDINGS = [
        Binding("s", "answer('save')", "Save all & quit", priority=True),
        Binding("enter", "answer('save')", "Save all & quit", priority=True, show=False),
        Binding("q", "answer('quit')", "Quit without saving", priority=True),
        Binding("n", "answer('stay')", "Stay", priority=True),
        Binding("escape", "answer('stay')", "Stay", priority=True, show=False),
    ]
    DEFAULT_CSS = """
    QuitDialog { align: center middle; }
    QuitDialog > Vertical { width: 70; height: auto; padding: 1 2; }
    QuitDialog Horizontal { height: auto; align: center middle; margin-top: 1; }
    QuitDialog Button { margin: 0 1; }
    """

    def __init__(self, unsaved: str) -> None:
        super().__init__()
        self.unsaved = unsaved

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Unsaved changes in: {self.unsaved}.")
            with Horizontal():
                yield Button("Save all & quit (s)", id="save", variant="primary")
                yield Button("Quit without saving (q)", id="quit", variant="warning")
                yield Button("Stay (n)", id="stay")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "stay")

    def action_answer(self, what: str) -> None:
        self.dismiss(what)


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
    #hooks-list, #memory-list, #persona-list { width: 34; background: #0f1320; border: round #2c3450; margin-right: 1; }
    #hooks-list:focus, #memory-list:focus, #persona-list:focus { border: round #8b5cf6; }
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
    # The tab bar already names the tabs, and the Overview lists the keys, so the
    # footer keeps only the actions — seven F-keys plus five actions overflowed a
    # 110-column terminal and cut "Save" in half. The command palette is off for
    # the same reason: one more key in the footer, nothing in it we need.
    ENABLE_COMMAND_PALETTE = False
    TAB_ORDER = ["overview", "system", "persona", "hooks", "project", "global", "memory"]
    BINDINGS = [
        Binding("f1", "tab('overview')", "Overview", priority=True, show=False),
        Binding("f2", "tab('system')", "System", priority=True, show=False),
        Binding("f3", "tab('persona')", "Persona", priority=True, show=False),
        Binding("f4", "tab('hooks')", "Hooks", priority=True, show=False),
        Binding("f5", "tab('project')", "Project", priority=True, show=False),
        Binding("f6", "tab('global')", "Global", priority=True, show=False),
        Binding("f7", "tab('memory')", "Memory", priority=True, show=False),
        # F-keys need Fn on most Mac keyboards, so tabs are also reachable
        # without them (and by mouse).
        Binding("ctrl+pagedown", "tab_step(1)", "Next tab", priority=True, show=False),
        Binding("ctrl+pageup", "tab_step(-1)", "Prev tab", priority=True, show=False),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+r", "reset", "Reset", priority=True),
        Binding("ctrl+n", "new", "New", priority=True),
        Binding("ctrl+t", "delete", "Delete", priority=True),
        Binding("ctrl+u", "use", "Use persona", priority=True),
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
        self.project_dir = Path(self.paths["project"])
        self.project_claude = Path(self.paths.get("project_claude", str(self.project_dir / "CLAUDE.md")))
        self.memory_dir = Path(self.paths["memory_dir"])
        self.personas_dir = Path(self.paths.get("personas_dir", str(Path(self.paths["persona"]).parent / "personas")))
        self.persona_active = self.paths.get("persona_active", "default")
        self.shipped_personas: Dict[str, str] = dict(self.defaults.get("shipped_personas", {}))
        self.persona_selected = "default"
        self._persona_buffer: Dict[str, str] = {}
        self.hooks: Dict[str, str] = self._load_hooks()
        self.hook_selected: Optional[str] = None
        self.memory_selected: Optional[Path] = None
        self.dirty: Dict[str, bool] = {"persona": False, "hooks": False, "project": False, "global": False, "memory": False}

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
        return self._persona_read(self.persona_active)

    def _persona_names(self) -> List[str]:
        names = ["default"] + list(self.shipped_personas)
        if self.personas_dir.exists():
            for f in sorted(self.personas_dir.glob("*.md")):
                if f.stem not in names:
                    names.append(f.stem)
        return names

    def _persona_path(self, name: str) -> Path:
        return self.persona_path if name == "default" else self.personas_dir / f"{name}.md"

    def _persona_state(self, name: str) -> str:
        if self._persona_path(name).exists():
            return "yours"
        return "shipped"

    def _persona_read(self, name: str) -> str:
        if name in self._persona_buffer:
            return self._persona_buffer[name]
        p = self._persona_path(name)
        if p.exists():
            return p.read_text()
        if name == "default":
            return self.defaults["persona"]
        return self.shipped_personas.get(name, "")

    def _refresh_persona_list(self) -> None:
        lv = self.query_one("#persona-list", ListView)
        lv.clear()
        for name in self._persona_names():
            tag = "● active" if name == self.persona_active else self._persona_state(name)
            lv.append(ListItem(Label(f"{name:<12} {tag}"), name=name))
        for i, item in enumerate(lv.children):
            if getattr(item, "name", None) == self.persona_selected:
                lv.index = i
                break

    def _memory_files(self) -> List[Path]:
        if not self.memory_dir.exists():
            return []
        files = sorted(p for p in self.memory_dir.glob("*.md") if p.is_file())
        index = self.memory_dir / "MEMORY.md"
        if index in files:
            files.remove(index)
            files.insert(0, index)
        return files

    def _other_memory_projects(self) -> List[Path]:
        """Projects on this machine that have a memory, for when the one we were
        launched from has none — the directory is keyed on the cwd, and running
        the page from the wrong place shows an empty tab with no explanation."""
        root = self.memory_dir.parent.parent
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*/memory") if (p / "MEMORY.md").exists())[:8]

    def _memory_hint(self) -> str:
        base = (f"Project: {self.project_dir} — what Claude Code remembers about it, in "
                f"{self.memory_dir}. MEMORY.md is the index it loads at the start of every session "
                "here; each other file is one fact. ^N new fact · ^T delete the selected file. "
                "Takes effect at the next session.")
        if self._memory_files():
            return base
        others = self._other_memory_projects()
        if others:
            names = ", ".join(p.parent.name for p in others)
            return (base + f"  ⚠ Nothing here yet — the memory is keyed on the directory you ran "
                    f"`abs prompt` from. Projects that do have one: {names}. Run it from there.")
        return base + "  Nothing here yet: Claude Code writes the first fact when there is something worth keeping."

    def _overview_text(self) -> str:
        system = abs_text(self.profile, "show", "system")
        persona = self._persona_text()
        safety = abs_text(self.profile, "show", "safety")
        mech_tokens = approx_tokens(system) - approx_tokens(safety)
        yours = "yours" if self.persona_path.exists() else "shipped"
        hooks_state = "yours" if self.hooks_path.exists() else "shipped"
        n_custom = sum(1 for k in self.hooks if self._hook_kind(k) == "custom")
        mem_n = len(self._memory_files())
        lines = [
            "WHAT THIS PAGE IS",
            "Everything ABS says to Claude is plain text — no fine-tuning, no custom model.",
            "It enters a session at three moments, and this page shows all of it and lets",
            "you edit the parts that are yours.",
            "",
            "THE SYSTEM PROMPT, added once at launch, in this fixed order:",
            "",
            f"   ┌─ mechanics ≈{mech_tokens:>5,} tokens   locked    who is on the other end, the reply",
            "   │                                       tool, voice, quiet mode, the fallback",
            f"   ├─ persona   ≈{approx_tokens(persona):>5,} tokens   {yours:<9} tone, the three message types, the card",
            f"   └─ safety    ≈{approx_tokens(safety):>5,} tokens   locked    kill ladder, command guard, no secrets",
            "",
            "   The order is the security model: whatever the persona says, safety comes",
            "   after it. Only the middle is yours to change:",
            f"   persona  {self.persona_path}  {yours}  ·  active: {self.persona_active}  ·  more in {self.personas_dir}",
            "",
            "ALSO AT LAUNCH, read by Claude Code itself (not by ABS):",
            f"   project  {self.project_claude}  {'present' if self.project_claude.exists() else 'none'}",
            f"   global   {self.global_path}  {'present' if self.global_path.exists() else 'none'}  (locked until you confirm)",
            f"   memory   {self.memory_dir}  ({mem_n} files)",
            "",
            "PER TURN, when a control phrase arrives from Telegram:",
            f"   hooks    {self.hooks_path}  {hooks_state}, {n_custom} phrase(s) of your own",
            "",
            "TABS",
            "   F2 System   read the two locked slots exactly as the next launch builds them",
            "   F3 Persona  who the model is: default, ceo, cto, friend, yours — takes effect at the next launch",
            "   F4 Hooks    edit what a phrase injects, add your own — live for the next phrase",
            "   F5 Project  edit this repository's CLAUDE.md — committed, everyone who clones gets it",
            "   F6 Global   edit ~/.claude/CLAUDE.md — asks first; it shapes EVERY Claude Code session",
            "   F7 Memory   edit what Claude Code remembers about this project — next session",
            "",
            "KEYS   F1–F7 or ^PgUp/^PgDn tabs (click works too) · ^S save · ^R reset · ^N new · ^T delete · ^U use · ^Q quit",
            "",
            f"profile {self.profile} · persona and hooks are global to every profile and project",
        ]
        return "\n".join(lines)

    # ---- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial="overview", id="tabs"):
            with TabPane("Overview", id="overview"):
                yield TextArea(self._overview_text(), read_only=True, id="overview-text")
            with TabPane("System (locked)", id="system"):
                yield Static(
                    "The bridge mechanics and the safety epilogue, exactly as the next launch builds "
                    "them. Not editable: a persona must not be able to tell the model to stop replying "
                    "to Telegram, and whatever the persona says, this text comes after it.",
                    classes="hint",
                )
                yield TextArea(abs_text(self.profile, "show", "system"), read_only=True, id="system-text")
            with TabPane("Persona", id="persona"):
                yield Static("Who the model is. 'default' is ~/.abs/persona.md; the others are "
                             f"{self.personas_dir}/<name>.md, with ceo, cto and friend shipped as examples. "
                             "^N new · ^U use for new sessions · ^T delete · `abs --persona <name>` for one "
                             "session. Global to every profile and project; takes effect at the NEXT LAUNCH.",
                             classes="hint", id="persona-hint")
                with Horizontal(classes="pane"):
                    yield ListView(id="persona-list")
                    yield TextArea(self._persona_read("default"), id="persona-text")
            with TabPane("Hooks", id="hooks"):
                yield Static("What a control phrase, sent as a WHOLE MESSAGE from Telegram while a session "
                             "is live, injects into the model. MUTE / OFF / BLOCK act in the hook and never "
                             "reach the model, so they have no wording. ^N adds a phrase of your own; "
                             "{profile} is substituted. Live for the NEXT PHRASE after saving.",
                             classes="hint")
                with Horizontal(classes="pane"):
                    yield ListView(id="hooks-list")
                    yield TextArea("", id="hooks-text")
            with TabPane("Project", id="project"):
                yield Static(f"⚠ {self.project_claude} — this repository's instructions to Claude Code. "
                             "It is COMMITTED with the repo: everyone who clones it gets what you write "
                             "here, and every Claude Code session in this directory reads it, ABS or not. "
                             "Takes effect at the next session.", classes="hint")
                yield TextArea(self.project_claude.read_text() if self.project_claude.exists() else "",
                               id="project-text")
            with TabPane("Global", id="global"):
                yield Static(f"⚠ {self.global_path} — Claude Code's own global instructions, loaded by EVERY "
                             "Claude Code session on this machine, ABS or not. Locked until you confirm: "
                             "press ^S to unlock (it asks), edit, then ^S again to save. Takes effect at "
                             "the next session.", classes="hint")
                yield TextArea(self.global_path.read_text() if self.global_path.exists() else "",
                               read_only=True, id="global-text")
            with TabPane("Memory", id="memory"):
                yield Static(self._memory_hint(), classes="hint", id="memory-hint")
                with Horizontal(classes="pane"):
                    yield ListView(id="memory-list")
                    yield TextArea("", id="memory-text")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        for wid in ("overview-text", "system-text", "persona-text", "global-text", "project-text", "hooks-text", "memory-text"):
            ta = self.query_one(f"#{wid}", TextArea)
            self._baseline[wid] = ta.text
        self._refresh_persona_list()
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

    @on(ListView.Highlighted, "#persona-list")
    def _persona_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        name = event.item.name or "default"
        if name == self.persona_selected and self._baseline.get("persona-text") == self._persona_read(name):
            return
        self._commit_persona_editor()
        self.persona_selected = name
        self._load(self.query_one("#persona-text", TextArea), self._persona_read(name))
        self._update_status()

    def _commit_persona_editor(self) -> None:
        ta = self.query_one("#persona-text", TextArea)
        if ta.text != self._baseline.get("persona-text", ta.text):
            self._persona_buffer[self.persona_selected] = ta.text
            self.dirty["persona"] = True

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
        """Keep what was typed for the phrase we are leaving — typed, as against
        what was loaded, so a highlight right after a programmatic load cannot
        copy one phrase's text into another."""
        if self.hook_selected and self._hook_kind(self.hook_selected) != "enforced":
            ta = self.query_one("#hooks-text", TextArea)
            if not ta.read_only and ta.text != self._baseline.get("hooks-text", ta.text):
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
        elif wid == "project-text":
            self.dirty["project"] = True
        elif wid == "global-text" and not event.text_area.read_only:
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
            source = f"{self.persona_selected} · {self._persona_state(self.persona_selected)}"
            if self.persona_selected == self.persona_active:
                source += " · active for new sessions"
            flag = "  ⚠ over the cap" if len(text) > cap else ""
            flag += "  ⚠ contains <channel — will be refused" if forged(text) else ""
            st.update(f"persona · {source} · {len(text):,}/{cap:,} chars · ≈{approx_tokens(text):,} tokens"
                      f"{'  · unsaved' if self.dirty['persona'] else ''}{flag}")
        elif tab == "hooks":
            n_custom = sum(1 for k in self.hooks if self._hook_kind(k) == "custom")
            st.update(f"hooks · {self.hooks_path if self.hooks_path.exists() else 'shipped wording'} · "
                      f"{n_custom} of your own{'  · unsaved' if self.dirty['hooks'] else ''}")
        elif tab == "project":
            text = self.query_one("#project-text", TextArea).text
            st.update(f"project · {self.project_claude} · ≈{approx_tokens(text):,} tokens · committed with the repo"
                      f"{'  · unsaved' if self.dirty['project'] else ''}")
        elif tab == "global":
            ta = self.query_one("#global-text", TextArea)
            state = "locked — ^S to unlock" if ta.read_only else "UNLOCKED — every Claude Code session reads this"
            st.update(f"global · {self.global_path} · ≈{approx_tokens(ta.text):,} tokens · {state}"
                      f"{'  · unsaved' if self.dirty['global'] else ''}")
        elif tab == "overview":
            st.update(f"overview · profile {self.profile} · F2–F7 to open a tab")
        elif tab == "memory":
            sel = self.memory_selected.name if self.memory_selected else "—"
            st.update(f"memory · {len(self._memory_files())} files · editing {sel}"
                      f"{'  · unsaved' if self.dirty['memory'] else ''}")
        else:
            text = self.query_one("#system-text", TextArea).text
            st.update(f"system · locked · ≈{approx_tokens(text):,} tokens")

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self._update_status()

    # ---- actions ---------------------------------------------------------------

    # Focus first, then switch. TabbedContent activates the pane that contains
    # whatever has focus, so switching away from a tab whose editor is focused
    # snapped straight back — F-keys did nothing once you had typed anything.
    # Dropping focus before the switch, then focusing the new tab's editor,
    # is what makes the keys work from inside an editor.
    FOCUS_IN_TAB = {"overview": "#overview-text", "system": "#system-text", "persona": "#persona-text",
                    "hooks": "#hooks-text", "project": "#project-text", "global": "#global-text",
                    "memory": "#memory-text"}

    def action_tab(self, name: str) -> None:
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = name
        target = self.FOCUS_IN_TAB.get(name)
        if target:
            try:
                self.query_one(target).focus()
            except Exception:
                pass

    def action_tab_step(self, step: int) -> None:
        i = self.TAB_ORDER.index(self._active())
        self.action_tab(self.TAB_ORDER[(i + step) % len(self.TAB_ORDER)])

    def action_save(self) -> None:
        tab = self._active()
        if tab == "persona":
            self._commit_persona_editor()
            cap = self.defaults["persona_max_chars"]
            for name, text in list(self._persona_buffer.items()):
                if forged(text):
                    self.notify(f"Refused ({name}): a persona must not contain '<channel' — it could forge a Telegram message.", severity="error")
                    return
                if len(text) > cap:
                    self.notify(f"Refused ({name}): {len(text):,} characters is over the {cap:,} cap.", severity="error")
                    return
            for name, text in list(self._persona_buffer.items()):
                write_private(self._persona_path(name), text if text.endswith("\n") else text + "\n")
            saved = ", ".join(self._persona_buffer) or self.persona_selected
            self._persona_buffer.clear()
            self._baseline["persona-text"] = self.query_one("#persona-text", TextArea).text
            self.dirty["persona"] = False
            self._refresh_persona_list()
            self.notify(f"Saved {saved}. Takes effect at the next launch.")
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
        elif tab == "project":
            text = self.query_one("#project-text", TextArea).text
            self.project_claude.write_text(text if text.endswith("\n") else text + "\n")
            self._baseline["project-text"] = text
            self.dirty["project"] = False
            self.notify(f"Saved {self.project_claude}. Remember it is committed with the repo.")
        elif tab == "global":
            ta = self.query_one("#global-text", TextArea)
            if ta.read_only:
                def unlock(yes: bool) -> None:
                    if not yes:
                        return
                    ta.read_only = False
                    ta.focus()
                    self._update_status()
                    self.notify("Unlocked. Edit, then ^S to save.", severity="warning")
                self.push_screen(Confirm(
                    f"Are you sure you want to edit {self.global_path}? It is read by EVERY Claude Code "
                    "session on this machine, in every project, ABS or not.  (y / n)"), unlock)
                return
            text = ta.text
            self.global_path.parent.mkdir(parents=True, exist_ok=True)
            self.global_path.write_text(text if text.endswith("\n") else text + "\n")
            self._baseline["global-text"] = text
            self.dirty["global"] = False
            self.notify(f"Saved {self.global_path}. Every Claude Code session reads it from its next start.")
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
            name = self.persona_selected
            path = self._persona_path(name)
            if name != "default" and name not in self.shipped_personas:
                self.notify(f"{name} has no shipped text to reset to — ^T deletes it.", severity="warning")
                return
            def done(yes: bool) -> None:
                if not yes:
                    return
                if path.exists():
                    path.unlink()
                self._persona_buffer.pop(name, None)
                self._load(self.query_one("#persona-text", TextArea), self._persona_read(name))
                self.dirty["persona"] = any(self._persona_buffer)
                self._refresh_persona_list()
                self._update_status()
                self.notify(f"{name}: back on the shipped text.")
            self.push_screen(Confirm(f"Delete {path} and go back to the shipped '{name}'?"), done)
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

    def action_use(self) -> None:
        if self._active() != "persona":
            self.notify("Use is for the persona tab: makes the selected persona the one new sessions launch with.", severity="warning")
            return
        name = self.persona_selected
        (self.persona_path.parent / "persona.active").write_text(name + "\n")
        self.persona_active = name
        self._refresh_persona_list()
        self._update_status()
        self.notify(f"New sessions launch as '{name}'. A running session keeps its persona until restarted.")

    def action_new(self) -> None:
        tab = self._active()
        if tab == "persona":
            def made(name: Optional[str]) -> None:
                if not name:
                    return
                slug = name.lower().strip().replace(" ", "-")
                if slug in self._persona_names():
                    self.notify(f"'{slug}' already exists — select it in the list.", severity="warning")
                    return
                import re
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", slug):
                    self.notify("A persona name is lowercase letters, digits, - and _.", severity="error")
                    return
                self._commit_persona_editor()
                base = self._persona_read(self.persona_selected)
                self._persona_buffer[slug] = base.replace("You are ABS — said like the name \"Abish\".", f"You are ABS, acting as {slug}.", 1)
                self.dirty["persona"] = True
                self.persona_selected = slug
                self.personas_dir.mkdir(parents=True, exist_ok=True)
                self._refresh_persona_list()
                self._load(self.query_one("#persona-text", TextArea), self._persona_buffer[slug])
                self.query_one("#persona-text", TextArea).focus()
                self.notify(f"New persona '{slug}', copied from the selected one. Edit, then ^S.")
            self.push_screen(Ask("New persona name (copied from the selected one):", "reviewer"), made)
        elif tab == "hooks":
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
                self._load(self.query_one("#hooks-text", TextArea), "")
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
        if tab == "persona":
            name = self.persona_selected
            path = self._persona_path(name)
            if name == "default":
                self.notify("'default' is not deleted — ^R resets it to the shipped text.", severity="warning")
                return
            if not path.exists() and name not in self._persona_buffer:
                self.notify(f"{name} is a shipped example with no file; nothing to delete.", severity="warning")
                return
            def done_p(yes: bool) -> None:
                if not yes:
                    return
                if path.exists():
                    path.unlink()
                self._persona_buffer.pop(name, None)
                if self.persona_active == name:
                    active_file = self.persona_path.parent / "persona.active"
                    if active_file.exists():
                        active_file.unlink()
                    self.persona_active = "default"
                self.persona_selected = "default"
                self._refresh_persona_list()
                self._load(self.query_one("#persona-text", TextArea), self._persona_read("default"))
                self._update_status()
                self.notify(f"Deleted {name}.")
            self.push_screen(Confirm(f"Delete persona '{name}' ({path})?"), done_p)
        elif tab == "hooks":
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

    def _save_all(self) -> bool:
        """Save every dirty tab in turn; False if one was refused."""
        current = self._active()
        for tab, dirty in list(self.dirty.items()):
            if not dirty:
                continue
            self.action_tab(tab)
            self.action_save()
            if self.dirty[tab]:                      # refused (forged, over the cap)
                return False
        self.action_tab(current)
        return True

    def action_quit_page(self) -> None:
        if isinstance(self.screen, (Confirm, Ask, QuitDialog)):
            return                                   # a dialog is already up; answer it
        if any(self.dirty.values()):
            unsaved = ", ".join(k for k, v in self.dirty.items() if v)
            def done(answer: str) -> None:
                if answer == "quit":
                    self.exit()
                elif answer == "save":
                    if self._save_all():
                        self.exit()
            self.push_screen(QuitDialog(unsaved), done)
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
