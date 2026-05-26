#!/usr/bin/env bash
# cldx installer — pip install --user . + first-run config + PATH hint.
#
#   ./install.sh           # install / upgrade
#   ./install.sh --uninstall

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLDX_HOME="${CLDX_HOME:-$HOME/.cldx}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# --- ANSI helpers ---------------------------------------------------------
if [ -t 1 ]; then
  C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_BOLD=$'\e[1m'; C_RST=$'\e[0m'
else
  C_OK=''; C_WARN=''; C_ERR=''; C_BOLD=''; C_RST=''
fi
say()  { printf '%s%s%s\n' "$C_OK"  "✓ $1" "$C_RST"; }
warn() { printf '%s%s%s\n' "$C_WARN" "! $1" "$C_RST"; }
die()  { printf '%s%s%s\n' "$C_ERR" "✗ $1" "$C_RST" >&2; exit 1; }
head() { printf '\n%s%s%s\n' "$C_BOLD" "$1" "$C_RST"; }

# --- Pick a Python ≥ 3.11 -------------------------------------------------
pick_python() {
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver=$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo 0.0)
      maj=${ver%.*}; min=${ver#*.}
      if [ "$maj" -ge "$MIN_PYTHON_MAJOR" ] && [ "$min" -ge "$MIN_PYTHON_MINOR" ]; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

# --- Detect user-scripts dir for the chosen Python ------------------------
user_scripts_dir() {
  local py="$1"
  "$py" -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme=("osx_user" if __import__("sys").platform=="darwin" else "posix_user")))'
}

# --- Uninstall path -------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
  head "Uninstalling cldx"
  PY=$(pick_python) || die "no compatible Python ≥${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} on PATH"
  "$PY" -m pip uninstall --yes cldx || warn "cldx wasn't installed"
  warn "User state at ${CLDX_HOME} left in place. Delete manually if you want a clean slate."
  exit 0
fi

# --- Install path ---------------------------------------------------------
head "cldx installer"

PY=$(pick_python) || die "no Python ≥${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} found. Install one (e.g., \`brew install python@3.12\` or your distro's package) and re-run."
PY_VER=$("$PY" --version 2>&1)
say "Using $PY ($PY_VER)"

# 1. Install the package + dependencies.
head "Installing package (pip install --user .)"
"$PY" -m pip install --user --upgrade --quiet --break-system-packages 2>/dev/null \
    "$PROJECT_DIR" || \
  "$PY" -m pip install --user --upgrade --quiet "$PROJECT_DIR" || \
  die "pip install failed. Try \`$PY -m pip install --user .\` manually to see the error."
say "Package installed"

# 2. Bootstrap user state under ~/.cldx/ (idempotent).
head "Setting up ${CLDX_HOME}"
mkdir -p "${CLDX_HOME}/config" "${CLDX_HOME}/sessions"
if [ ! -f "${CLDX_HOME}/config/policy.yml" ]; then
  cp "${PROJECT_DIR}/cldx/defaults/policy.yml" "${CLDX_HOME}/config/policy.yml"
  say "Wrote ${CLDX_HOME}/config/policy.yml (default profile)"
else
  warn "${CLDX_HOME}/config/policy.yml already exists — left untouched"
fi

# 3. PATH check + hint.
SCRIPTS_DIR=$(user_scripts_dir "$PY")
head "Make sure \`cldx\` is on your PATH"
case ":$PATH:" in
  *":$SCRIPTS_DIR:"*)
    say "$SCRIPTS_DIR is already on \$PATH — you can run \`cldx\` now."
    ;;
  *)
    warn "$SCRIPTS_DIR is NOT on your \$PATH."
    if [ -n "${ZSH_VERSION:-}" ] || [ "${SHELL##*/}" = "zsh" ]; then
      RC=~/.zshrc
    else
      RC=~/.bashrc
    fi
    echo
    echo "   Add this line to ${RC}:"
    echo
    echo "     export PATH=\"${SCRIPTS_DIR}:\$PATH\""
    echo
    echo "   Then restart your shell, or run:  source ${RC}"
    ;;
esac

head "Done."
echo "    Run:    cldx --help"
echo "    Config: ${CLDX_HOME}/config/policy.yml"
echo "    Logs:   ${CLDX_HOME}/sessions/"
