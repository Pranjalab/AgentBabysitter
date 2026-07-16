"""Agent Babysitter — a pip-installed launcher for the abs.sh bash program.

The whole tool is one bash script; this package only exists so `pip install
agent-babysitter` puts an `abs` command on PATH. `main()` hands straight over to
bash and never returns, so `abs` behaves identically whether it was installed via
pip or the curl one-liner.
"""

from __future__ import annotations

import os
import shutil
import sys

try:  # importlib.resources.files is 3.9+; the backport covers 3.8.
    from importlib.resources import as_file, files
except ImportError:  # pragma: no cover
    from importlib_resources import as_file, files  # type: ignore


def main() -> "int":
    bash = shutil.which("bash")
    if not bash:
        sys.stderr.write(
            "abs needs bash, which isn't on your PATH. Agent Babysitter is a bash "
            "program; the pip package only ships the launcher.\n"
        )
        return 1

    script = files(__package__) / "abs.sh"
    # as_file gives a real filesystem path even if the package were zipped.
    with as_file(script) as path:
        # execv replaces this process, so bash owns the tty directly — the hidden
        # token prompt and Ctrl-C during pairing behave exactly as they do when
        # abs.sh is run straight. On success this call does not return.
        os.execv(bash, ["bash", str(path), *sys.argv[1:]])

    return 0  # unreachable unless execv fails


if __name__ == "__main__":
    raise SystemExit(main())
