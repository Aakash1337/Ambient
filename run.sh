#!/usr/bin/env bash
# Launch Ambient Q&A on Linux.
#
# Windows is the other first-class platform (python -m ambientqa inside .venv);
# this wrapper exists because Linux needs two departures from that:
#   - its own venv (.venv-linux -- .venv is the Windows environment); the
#     Windows-only pyaudiowpatch is skipped by its requirements.txt marker,
#     since Linux captures natively through PipeWire (pactl/parec), mic AND
#     system audio via monitor sources -- no PortAudio involved at all
#   - LD_LIBRARY_PATH pointing CTranslate2 at the pip CUDA libraries so
#     Whisper runs on the GPU
# PipeWire multiplexes every source, so devices are never "busy": several
# copies of the app can run at once. The first run bootstraps the venv
# automatically (about a minute).
set -eu
cd "$(dirname "$0")"
VENV=.venv-linux

# Gate the bootstrap on a stamp written only AFTER pip succeeds. Gating on
# bin/python alone mistakes an interrupted install for a finished one: venv
# creation makes that file first, so a network failure or Ctrl-C during the
# multi-hundred-MB wheel downloads would leave a dependency-less venv that
# every later run happily execs into a ModuleNotFoundError.
# A stamp older than requirements.txt means the dependency list changed since
# the last successful install (e.g. a git pull); rerun pip rather than exec
# into a ModuleNotFoundError.
STAMP="$VENV/.deps-installed"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
    echo "Installing dependencies into $VENV..."
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet -r requirements.txt
    touch "$STAMP"
fi

# WebRTC mic processing (noise suppression + automatic gain control), the same
# chain Windows runs in its audio stack. The raw USB mic at full PipeWire
# volume sits ~34dB above its hardware-neutral level: loud speech clips
# (measured 1.7% of samples) and quiet rooms drown in boosted noise -- both
# garble Whisper. config.toml pins mic_device to the processed "ec_mic"
# source this module provides.
if command -v pactl >/dev/null 2>&1; then
    if ! pactl list short sources 2>/dev/null | grep -q "ec_mic"; then
        pactl load-module module-echo-cancel \
            "aec_method=webrtc source_name=ec_mic source_properties=device.description=Echo-cancelled_Microphone" \
            >/dev/null 2>&1 \
            || echo "note: could not load PipeWire echo-cancel module; the pinned ec_mic source will be unavailable (press d in the app to pick the raw mic)"
    fi
fi

# The semantic gate calls Ollama; bring it up quietly if it is not running.
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    if command -v ollama >/dev/null 2>&1; then
        echo "Starting ollama..."
        nohup ollama serve >/tmp/ollama-ambientqa.log 2>&1 &
        sleep 1
    fi
fi

NVLIBS="$(find "$VENV"/lib/python*/site-packages/nvidia -maxdepth 2 -name lib -type d 2>/dev/null | paste -sd: || true)"
export LD_LIBRARY_PATH="${NVLIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$VENV/bin/python" -m ambientqa "$@"
