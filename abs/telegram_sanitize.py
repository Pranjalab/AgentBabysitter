"""Clean up raw pane output before forwarding to Telegram.

Claude Code's TUI is gorgeous in a terminal but noisy in a chat client:
box-drawing characters, banner art, dim separator runs, and the
``? for shortcuts · ← for agents`` chrome at the bottom of the input
area all leak through when we send the raw snapshot.

``clean_for_telegram`` strips:

- ANSI escape sequences (belt-and-braces; tmux capture is usually plain
  but the raw_snapshot path can still carry them).
- Box-drawing and block-element runs (``─━│┌┐└┘╭╮╰╯┃═█▐▛▝``).
- Lines that are >50% non-alphanumeric "chrome" — banners and rulers.
- The bottom-of-pane UI strip (``? for shortcuts``, ``esc to cancel``).
- Runs of blank lines (collapsed to one).
- Trailing whitespace on every line.

The output is plain text safe to drop into a Telegram message body or
into an LLM prompt without confusing the model with frame chrome.
"""

from __future__ import annotations

import re


# --- character classes ----------------------------------------------------

# All the box / block / banner glyphs the abs + Claude Code UIs use.
_BOX_CHARS = set("─━│┌┐└┘╭╮╰╯┃═╔╗╚╝╠╣╦╩╬▐▛▜▝█▏▕▎▍▌▋▊▉▒░▓")

# ANSI CSI escape sequences (colour, cursor moves, …).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")

# UI-chrome lines we know we never want to forward.
_CHROME_LINE_PATTERNS = (
    re.compile(r"\?\s*for\s*shortcuts"),
    re.compile(r"esc\s*to\s*(cancel|interrupt)"),
    re.compile(r"←\s*for\s*agents?"),
    re.compile(r"ctrl[\-+]?[a-z]\s*to\s*"),  # "ctrl-c to exit", "ctrl-v to paste"
    re.compile(r"shift\s*\+\s*tab"),
    re.compile(r"^\s*tab\s+to\s+"),         # "tab to switch agent"
)

# A line is "decorative" if a high fraction of its visible chars are
# box/banner glyphs. Tuneable: 0.5 catches `═══════` / `▐▛███▜▌` while
# leaving regular text alone.
_DECORATION_THRESHOLD = 0.5

# Token marker abs itself injects in some message paths.
_UNSUMMARIZED_PREFIX_RE = re.compile(r"^\s*\[unsummarized[^\]]*\]\s*", re.IGNORECASE)


# --- public API -----------------------------------------------------------


def clean_for_telegram(text: str, max_chars: int = 3500) -> str:
    """Strip pane chrome, ANSI, and decorative runs from ``text``.

    ``max_chars`` caps the final output so we don't exceed Telegram's
    4096-byte message limit even with Markdown overhead. Truncated
    output ends with a single ``…`` marker.
    """
    if not text:
        return ""

    # Stage 1: strip ANSI.
    text = _ANSI_RE.sub("", text)

    # Stage 2: drop leading "[unsummarized: foo]" tags that older fallback
    # paths might attach — we already report the reason locally.
    text = _UNSUMMARIZED_PREFIX_RE.sub("", text)

    cleaned_lines: list[str] = []
    prev_blank = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _is_chrome(line):
            continue
        if _is_decorative(line):
            continue
        # Remove inline box chars (e.g. a label trapped in a frame border).
        line = _strip_inline_box(line)
        # If stripping leaves the line empty, treat as blank.
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
            cleaned_lines.append("")
            continue
        prev_blank = False
        cleaned_lines.append(line)

    # Trim leading/trailing blank lines.
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    out = "\n".join(cleaned_lines)
    if max_chars and len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


def extract_assistant_reply(snapshot: str, max_lines: int = 60) -> str:
    """Pull just Claude's spoken reply (the ``⏺ ...`` blocks) from a snapshot.

    Anchors on the LATEST submitted user message (``❯ <text>`` that
    isn't a menu option), so previous turns in the scrollback don't
    leak into "what did Claude just say". Without this anchor, a
    chatty session ends up rendering ALL historical ⏺ replies stacked
    together in the chat-reply panel.

    ``✻ ...`` "thinking" lines and the trailing input chrome are
    dropped. Returns the assistant text with ``⏺ `` markers removed,
    or an empty string when no assistant content was found after the
    anchor.
    """
    if not snapshot:
        return ""
    lines = snapshot.splitlines()

    # --- find the anchor ------------------------------------------------
    #
    # Walk backward looking for the most recent line that is a submitted
    # user message:
    #   - starts with ``❯`` followed by content
    #   - is NOT a menu option (``❯ 1. Yes``)
    #   - is NOT the empty input area (just ``❯`` with nothing after)
    anchor = 0
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].lstrip()
        if not _SUBMITTED_USER_LINE_RE.match(stripped):
            continue
        if _MENU_OPTION_LINE_RE.match(stripped):
            continue
        anchor = i + 1   # collect lines AFTER the user message
        break

    collected: list[str] = []
    in_block = False
    for raw in lines[anchor:]:
        stripped = raw.rstrip()
        text = stripped.lstrip()
        if not text:
            in_block = False
            continue
        if text.startswith("✻"):
            in_block = False
            continue
        if text.startswith("⏺"):
            without_marker = re.sub(r"^\s*⏺\s?", "", stripped)
            collected.append(without_marker)
            in_block = True
            continue
        if in_block and stripped.startswith((" ", "\t")):
            cleaned = stripped.lstrip().lstrip("⎿").lstrip()
            if cleaned:
                collected.append(cleaned)
            continue
        in_block = False

    tail = collected[-max_lines:]
    return clean_for_telegram("\n".join(tail))


# Matchers used by ``extract_assistant_reply`` to find the latest user
# message anchor. Kept module-local so the public surface stays clean.
_SUBMITTED_USER_LINE_RE = re.compile(r"^❯\s+\S")
_MENU_OPTION_LINE_RE = re.compile(r"^❯\s+\d+\.\s")


# --- internal -------------------------------------------------------------


def _is_chrome(line: str) -> bool:
    if not line.strip():
        return False
    for pat in _CHROME_LINE_PATTERNS:
        if pat.search(line):
            return True
    return False


def _is_decorative(line: str) -> bool:
    """True if the line is mostly box/banner glyphs (e.g. ``▐▛███▜▌``).

    Lines under 3 visible chars are not considered decorative (could be
    legitimately short content like "ok").
    """
    visible = line.strip()
    if len(visible) < 3:
        return False
    box_count = sum(1 for ch in visible if ch in _BOX_CHARS)
    if box_count == 0:
        return False
    return (box_count / len(visible)) >= _DECORATION_THRESHOLD


def _strip_inline_box(line: str) -> str:
    """Remove box-drawing chars when they appear inline in an otherwise
    text-bearing line (e.g. ``│  hello world  │``)."""
    return "".join(ch for ch in line if ch not in _BOX_CHARS)
