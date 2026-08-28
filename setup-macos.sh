#!/usr/bin/env bash
# One-time (and repeatable) macOS dependency bootstrap.
set -euo pipefail

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "setup-macos.sh must be run on macOS." >&2
    exit 2
fi

MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
if ! [[ "$MACOS_MAJOR" =~ ^[0-9]+$ ]] || [ "$MACOS_MAJOR" -lt 14 ]; then
    echo "Ambient's macOS release requires macOS 14 or newer; found ${MACOS_VERSION:-unknown}." >&2
    exit 3
fi

# PyTorch stopped publishing current Intel macOS wheels. The newest resolvable
# x86_64 wheel is 2.2.2 and has known vulnerabilities, so never silently build
# or install that environment. Remove this guard only after the runtime no
# longer needs torch or a supported Intel wheel is available.
case "$(uname -m)" in
    arm64) ;;
    x86_64)
        echo "Intel macOS setup is disabled: the required PyTorch stack has no supported security-clean x86_64 wheel." >&2
        exit 3
        ;;
    *)
        echo "Unsupported macOS architecture: $(uname -m)" >&2
        exit 3
        ;;
esac

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

if ! "$PYTHON_BIN" -c 'import platform; raise SystemExit(0 if platform.machine() == "arm64" else 1)'; then
    echo "$PYTHON_BIN must be a native Apple Silicon Python; Rosetta/x86_64 interpreters are unsupported." >&2
    exit 3
fi

VENV=.venv-macos
STAMP="$VENV/.deps-installed"
LOCK_FILE=requirements-macos-arm64.txt

if [ ! -f "$LOCK_FILE" ]; then
    echo "Missing hash-locked dependency file: $LOCK_FILE" >&2
    exit 2
fi
LOCK_DIGEST="$(shasum -a 256 "$LOCK_FILE" | awk '{print $1}')"

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating $VENV with $PYTHON_BIN..."
    "$PYTHON_BIN" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import platform, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and platform.machine() == "arm64" else 1)'; then
    echo "$VENV was created with a different Python version; remove it and rerun setup." >&2
    exit 2
fi

echo "Installing Ambient dependencies into $VENV..."
"$VENV/bin/python" -m pip install \
    --quiet \
    --require-hashes \
    --only-binary=:all: \
    --requirement "$LOCK_FILE"
"$VENV/bin/python" -m pip check

echo "Downloading and verifying the pinned Whisper speech model (~1.6 GB)..."
HF_HUB_DISABLE_PROGRESS_BARS=1 \
    "$VENV/bin/python" scripts/fetch_whisper_model.py --output-root models

if ! command -v espeak-ng >/dev/null 2>&1; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "espeak-ng is required for the offline voice fallback. Install Homebrew, then run: brew install espeak-ng" >&2
        exit 2
    fi
    echo "Installing the espeak-ng voice fallback..."
    brew install espeak-ng
fi

ESPEAK_PROBE="$(mktemp "${TMPDIR:-/tmp}/ambientqa-espeak.XXXXXX")"
if ! espeak-ng --stdout -- "Ambient fallback check" >"$ESPEAK_PROBE" 2>/dev/null \
    || [ ! -s "$ESPEAK_PROBE" ] \
    || [ "$(dd if="$ESPEAK_PROBE" bs=1 count=4 2>/dev/null)" != "RIFF" ]; then
    rm -f "$ESPEAK_PROBE"
    echo "espeak-ng is installed but failed its WAV synthesis check." >&2
    exit 2
fi
rm -f "$ESPEAK_PROBE"

printf '%s\n' "$LOCK_DIGEST" >"$STAMP"

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
