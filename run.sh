#!/usr/bin/env bash
# Launch Ambient on Linux.
#
# Windows is the other first-class platform (python -m ambientqa inside .venv);
# this wrapper exists because Linux needs two departures from that:
#   - its own venv (.venv-linux -- .venv is the Windows environment); the
#     Windows-only pyaudiowpatch is skipped by its requirements.txt marker,
#     since Linux captures natively through PipeWire (pactl/parec), mic AND
#     system audio via monitor sources -- no PortAudio involved at all
#   - LD_LIBRARY_PATH pointing CTranslate2 at the pip CUDA libraries so
#     Whisper runs on the GPU
# PipeWire multiplexes sources with other applications. A second copy of this
# app is refused by default because it would duplicate Whisper and Claude work.
# The first run bootstraps the venv automatically (about a minute).
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

# The desktop shortcut passes --choose and gets a small Textual splash before
# any audio source, model, network service, or application lock is touched.
# Direct terminal workflows stay deterministic: no argument means Assist,
# --voice means Voice, and --assist is an explicit Assist alias.
if [ "${1:-}" = "--choose" ]; then
    if [ "$#" -ne 1 ]; then
        echo "usage: ./run.sh [--choose|--assist|--voice]" >&2
        exit 2
    fi
    set +e
    "$VENV/bin/python" -m ambientqa.mode_picker
    PICKER_STATUS=$?
    set -e
    case "$PICKER_STATUS" in
        0)  set -- ;;
        10) set -- --voice ;;
        20|130) exit 0 ;;
        30) exec ./run-emergency.sh --takeover ;;
        # Launched from the app menu there is no terminal to read a URL from,
        # so the web pick also opens the default browser once the server is up.
        40) set -- --web --open-browser ;;
        # Combined Web Voice is still one controller/capture pipeline; --voice
        # only adds spoken delivery and its Normal/Conversational controls.
        50) set -- --web --voice --open-browser ;;
        *)
            echo "Unable to choose a launch mode (picker exited $PICKER_STATUS)." >&2
            exit 2
            ;;
    esac
elif [ "${1:-}" = "--assist" ]; then
    if [ "$#" -ne 1 ]; then
        echo "usage: ./run.sh [--choose|--assist|--voice]" >&2
        exit 2
    fi
    set --
fi

# WebRTC mic processing (noise suppression + automatic gain control), the same
# class of processing Windows applies itself. The raw USB mic at full PipeWire
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

# Voice mode (--voice) speaks answers with the local Kokoro model; fetch its
# ~310 MB of model files on the first voice launch only. Download to a temp
# name first: an interrupted curl must not leave a half-file that the app
# then mistakes for a real model. A failed download is not fatal -- the app
# falls back to espeak-ng and says so in the status bar.
for arg in "$@"; do
    if [ "$arg" = "--voice" ]; then
        KOKORO_BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
        mkdir -p models
        for file in kokoro-v1.0.onnx voices-v1.0.bin; do
            if [ ! -f "models/$file" ]; then
                echo "Downloading Kokoro voice model: $file..."
                { curl -fL -o "models/$file.tmp" "$KOKORO_BASE/$file" \
                    && mv "models/$file.tmp" "models/$file"; } \
                    || echo "note: could not fetch $file; voice falls back to espeak-ng"
            fi
        done
        break
    fi
done

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
