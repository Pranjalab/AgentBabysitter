"""Injected system prompts for ABS sessions (Stage 3 — the restricted assistant).

The restricted assistant is one of the four enforcement layers (prompt + model +
dedicated sandbox + no host credentials). This module is the SOFT one: the persona
+ rules that steer the model to answer everyday questions but refuse to write or run
project code, and to treat session control as operator-only.

Single source of truth: :data:`RESTRICTED_SYSTEM_PROMPT` is read from
``docker/sandbox/restricted-prompt.txt`` — the SAME file the Dockerfile bakes into
the sandbox image at ``/usr/local/share/absd/restricted-prompt.txt`` (where the
in-container ``absd-session --restricted`` launcher reads it). Keeping one file means
the host-side value the tests assert and the in-container value the model actually
sees can never drift.

Honesty (see docs/v3/critique/restricted.md): a prompt is guidance, not a sandbox.
A determined user can coax code out of a prompt-only guard. The real containment is
the *dedicated sandbox with no host credentials* — the prompt is the polite layer,
not the security boundary.
"""

from __future__ import annotations

from pathlib import Path

#: The exact sentence the assistant must use when refusing to build/run code. Kept
#: as a constant so tests and any host-side messaging share one wording with the
#: bundled prompt file (which contains this line verbatim).
RESTRICTED_UPGRADE_MESSAGE = (
    "This is a restricted assistant — ask the operator to upgrade your profile to "
    "build projects."
)

#: Where the Dockerfile installs the bundled copy inside the image (absd-session
#: reads this path when launched with --restricted).
IN_IMAGE_PROMPT_PATH = "/usr/local/share/absd/restricted-prompt.txt"

_PROMPT_FILE = (
    Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "restricted-prompt.txt"
)


def _load_restricted_prompt() -> str:
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - the file ships with the package
        # Fall back to a minimal prompt that still carries the load-bearing rule,
        # so a broken install degrades to a safe refusal rather than a bare model.
        return (
            "You are a restricted personal assistant. Do not write or run project "
            "code. When asked to build software, refuse with exactly: "
            f"{RESTRICTED_UPGRADE_MESSAGE} You cannot start or stop ABS sessions; "
            "session control is operator-only."
        )


#: The restricted-assistant system prompt (persona + no-code rule + no session
#: control). Read once at import from the canonical file.
RESTRICTED_SYSTEM_PROMPT = _load_restricted_prompt()
