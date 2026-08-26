#!/usr/bin/env bash
# One-time (and repeatable) macOS dependency bootstrap.
set -eu

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "setup-macos.sh must be run on macOS." >&2
    exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3.11 >/dev/null 2>&1; then
        PYTHON_BIN=python3.11
    else
        echo "Python 3.11 is required. Install it with: brew install python@3.11" >&2
        exit 2
    fi
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)'; then
    echo "$PYTHON_BIN must be Python 3.11 (set PYTHON_BIN to the correct executable)." >&2
    exit 2
fi

VENV=.venv-macos
STAMP="$VENV/.deps-installed"

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating $VENV with $PYTHON_BIN..."
    "$PYTHON_BIN" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)'; then
    echo "$VENV was created with a different Python version; remove it and rerun setup." >&2
    exit 2
fi

echo "Installing Ambient dependencies into $VENV..."
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements.txt
touch "$STAMP"

if ! command -v ollama >/dev/null 2>&1; then
    echo "warning: Ollama is not on PATH; install it and pull the configured gate model." >&2
fi
if ! command -v claude >/dev/null 2>&1; then
    echo "warning: the Claude CLI is not on PATH; install it and sign in before answering." >&2
fi

echo
echo "macOS setup complete."
echo "Run: ./run-macos.sh"
echo "The first capture may prompt for Microphone access for Terminal (or your terminal app)."
echo "For system audio, install BlackHole 2ch, restart, and create a Multi-Output Device; see README.md."
