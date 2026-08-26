#!/usr/bin/env bash
# Launch Ambient on macOS with an isolated Python environment.
set -eu

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "run-macos.sh must be run on macOS." >&2
    exit 2
fi

VENV=.venv-macos
STAMP="$VENV/.deps-installed"
if [ ! -x "$VENV/bin/python" ] || [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
    ./setup-macos.sh
fi

# Voice mode uses the same local Kokoro model as Linux, but plays the PCM through
# CoreAudio rather than paplay. Failed downloads are non-fatal; the app reports
# its configured fallback at runtime.
for arg in "$@"; do
    if [ "$arg" = "--voice" ]; then
        KOKORO_BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
        mkdir -p models
        for file in kokoro-v1.0.onnx voices-v1.0.bin; do
            if [ ! -f "models/$file" ]; then
                echo "Downloading Kokoro voice model: $file..."
                { curl -fL -o "models/$file.tmp" "$KOKORO_BASE/$file" \
                    && mv "models/$file.tmp" "models/$file"; } \
                    || echo "note: could not fetch $file; voice may be unavailable"
            fi
        done
        break
    fi
done

if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    if command -v ollama >/dev/null 2>&1; then
        echo "Starting ollama..."
        nohup ollama serve >"${TMPDIR:-/tmp}/ollama-ambientqa.log" 2>&1 &
    fi
fi

exec "$VENV/bin/python" -m ambientqa --config config.macos.toml "$@"
