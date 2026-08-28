#!/usr/bin/env bash
# Launch Ambient on macOS with an isolated Python environment.
set -euo pipefail

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "run-macos.sh must be run on macOS." >&2
    exit 2
fi

MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
if ! [[ "$MACOS_MAJOR" =~ ^[0-9]+$ ]] || [ "$MACOS_MAJOR" -lt 14 ]; then
    echo "Ambient's macOS release requires macOS 14 or newer; found ${MACOS_VERSION:-unknown}." >&2
    exit 3
fi

if [ "$(uname -m)" != "arm64" ]; then
    echo "Ambient's macOS release currently requires Apple Silicon; Intel is blocked because its required PyTorch wheel is no longer security-supported." >&2
    exit 3
fi

VENV=.venv-macos
STAMP="$VENV/.deps-installed"
LOCK_FILE=requirements-macos-arm64.txt
WHISPER_MODEL=models/faster-whisper-large-v3-turbo-0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf
LOCK_DIGEST=""
if [ -f "$LOCK_FILE" ]; then
    LOCK_DIGEST="$(shasum -a 256 "$LOCK_FILE" | awk '{print $1}')"
fi
INSTALLED_DIGEST=""
if [ -f "$STAMP" ]; then
    INSTALLED_DIGEST="$(sed -n '1p' "$STAMP")"
fi
RAN_SETUP=0
if [ ! -x "$VENV/bin/python" ] \
    || [ -z "$LOCK_DIGEST" ] \
    || [ "$INSTALLED_DIGEST" != "$LOCK_DIGEST" ] \
    || [ setup-macos.sh -nt "$STAMP" ] \
    || [ ! -f "$WHISPER_MODEL/model.bin" ] \
    || [ "$(sed -n '1p' "$WHISPER_MODEL/.revision" 2>/dev/null || true)" != "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" ] \
    || ! command -v espeak-ng >/dev/null 2>&1; then
    ./setup-macos.sh
    RAN_SETUP=1
fi
if [ "$RAN_SETUP" -eq 0 ]; then
    # The dependency stamp proves which wheels were installed; verify the model
    # contents too. This catches deletion/corruption instead of letting
    # CTranslate2 fail later during the first live utterance.
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
        "$VENV/bin/python" scripts/fetch_whisper_model.py --output-root models
fi

# Voice mode uses the same local Kokoro model as Linux, but plays the PCM through
# CoreAudio rather than paplay. Only the two audited release artifacts below are
# accepted; a failed or mismatched download leaves the functional espeak-ng
# fallback in place.
KOKORO_BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
KOKORO_MODEL_SHA256=7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5
KOKORO_VOICES_SHA256=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d

sha256_matches() {
    local expected="$1"
    local path="$2"
    local actual
    [ -f "$path" ] || return 1
    actual="$(shasum -a 256 "$path" | awk '{print $1}')"
    [ "$actual" = "$expected" ]
}

download_verified_model() (
    local file="$1"
    local expected="$2"
    local target="models/$file"
    local status=0
    local temporary=""

    # Keep each download in a subshell so its EXIT/signal cleanup cannot leak
    # into the launcher. Interrupted transfers never accumulate hidden partial
    # model files, while concurrent launchers only remove their own mktemp path.
    trap 'status=$?; if [ -n "$temporary" ]; then rm -f -- "$temporary"; fi; exit "$status"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    if sha256_matches "$expected" "$target"; then
        return 0
    fi
    if [ -e "$target" ]; then
        echo "warning: rejecting $target because its SHA-256 does not match the audited release" >&2
        rm -f "$target"
    fi

    temporary="$(mktemp "models/.$file.XXXXXX")"
    echo "Downloading Kokoro voice model: $file..."
    if curl \
        --connect-timeout 15 \
        --fail \
        --location \
        --retry 3 \
        --speed-limit 1024 \
        --speed-time 30 \
        --proto '=https' \
        --proto-redir '=https' \
        --tlsv1.2 \
        --output "$temporary" \
        "$KOKORO_BASE/$file"; then
        :
    else
        status=$?
        rm -f "$temporary"
        return "$status"
    fi
    if ! sha256_matches "$expected" "$temporary"; then
        echo "warning: downloaded $file failed SHA-256 verification" >&2
        rm -f "$temporary"
        return 1
    fi
    chmod 0644 "$temporary"
    mv -f "$temporary" "$target"
    temporary=""
)

for arg in "$@"; do
    if [ "$arg" = "--voice" ]; then
        mkdir -p models
        if download_verified_model kokoro-v1.0.onnx "$KOKORO_MODEL_SHA256"; then
            :
        else
            status=$?
            if [ "$status" -ge 128 ]; then
                exit "$status"
            fi
            echo "note: could not verify kokoro-v1.0.onnx; using espeak-ng fallback" >&2
        fi
        if download_verified_model voices-v1.0.bin "$KOKORO_VOICES_SHA256"; then
            :
        else
            status=$?
            if [ "$status" -ge 128 ]; then
                exit "$status"
            fi
            echo "note: could not verify voices-v1.0.bin; using espeak-ng fallback" >&2
        fi
        break
    fi
done

# Never send transcript context to whichever process happened to claim Ollama's
# conventional shared port. Launch a per-run server on an ephemeral loopback
# port, prove that the child we spawned owns the listener, and make the Python
# client repeat that ownership check before every request.
OLLAMA_PID=""
OLLAMA_WATCHDOG_PID=""
OLLAMA_LOG=""
APP_PID=""

# shellcheck disable=SC2329  # reached through the trap-only cleanup function
stop_child() {
    local pid="$1"
    local attempt=0
    if [ -z "$pid" ]; then
        return
    fi
    kill "$pid" >/dev/null 2>&1 || true
    while kill -0 "$pid" 2>/dev/null && [ "$attempt" -lt 50 ]; do
        attempt=$((attempt + 1))
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
    wait "$pid" >/dev/null 2>&1 || true
}

# shellcheck disable=SC2329  # invoked indirectly by EXIT/signal traps
cleanup() {
    local status=$?
    trap - EXIT HUP INT TERM
    stop_child "$OLLAMA_WATCHDOG_PID"
    stop_child "$APP_PID"
    stop_child "$OLLAMA_PID"
    if [ -n "$OLLAMA_LOG" ]; then
        rm -f -- "$OLLAMA_LOG"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

export AMBIENTQA_REQUIRE_MANAGED_OLLAMA=1
export AMBIENTQA_OLLAMA_PID=0
export AMBIENTQA_OLLAMA_URL=http://127.0.0.1:1/api/chat

listener_owned_by() {
    local pid="$1"
    local port="$2"
    /usr/sbin/lsof \
        -nP -a -p "$pid" -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null \
        | awk -v expected="$pid" '$0 == expected { found = 1 } END { exit !found }'
}

if command -v ollama >/dev/null 2>&1; then
    OLLAMA_PORT="$(
        "$VENV/bin/python" -c \
            'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
    )"
    AMBIENTQA_OLLAMA_URL="http://127.0.0.1:$OLLAMA_PORT/api/chat"
    export AMBIENTQA_OLLAMA_URL
    OLLAMA_LOG="$(mktemp "${TMPDIR:-/tmp}/ambientqa-ollama.log.XXXXXX")"
    chmod 0600 "$OLLAMA_LOG"
    echo "Starting private Ollama gate..."
    OLLAMA_HOST="127.0.0.1:$OLLAMA_PORT" \
        OLLAMA_NO_CLOUD=1 \
        OLLAMA_DEBUG_LOG_REQUESTS=0 \
        ollama serve >"$OLLAMA_LOG" 2>&1 &
    OLLAMA_PID=$!
    export AMBIENTQA_OLLAMA_PID="$OLLAMA_PID"

    attempt=0
    ready=0
    while [ "$attempt" -lt 200 ]; do
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
            break
        fi
        if listener_owned_by "$OLLAMA_PID" "$OLLAMA_PORT" \
            && curl --noproxy '*' --max-time 2 -sf \
                "http://127.0.0.1:$OLLAMA_PORT/api/version" \
                >/dev/null 2>&1; then
            ready=1
            break
        fi
        attempt=$((attempt + 1))
        sleep 0.1
    done
    if [ "$ready" -ne 1 ]; then
        echo "warning: private Ollama failed to start; semantic gating will fail closed." >&2
        kill "$OLLAMA_PID" >/dev/null 2>&1 || true
        wait "$OLLAMA_PID" >/dev/null 2>&1 || true
        OLLAMA_PID=""
        export AMBIENTQA_OLLAMA_PID=0
    fi
else
    echo "warning: Ollama is unavailable; semantic gating will fail closed." >&2
fi

"$VENV/bin/python" -m ambientqa --config config.macos.toml "$@" &
APP_PID=$!

if [ -n "$OLLAMA_PID" ]; then
    (
        while kill -0 "$OLLAMA_PID" 2>/dev/null \
            && kill -0 "$APP_PID" 2>/dev/null; do
            sleep 0.2
        done
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null \
            && kill -0 "$APP_PID" 2>/dev/null; then
            echo "Private Ollama exited; stopping Ambient before the port can be reused." >&2
            kill -TERM "$APP_PID" >/dev/null 2>&1 || true
        fi
    ) &
    OLLAMA_WATCHDOG_PID=$!
fi

set +e
wait "$APP_PID"
status=$?
set -e
APP_PID=""
exit "$status"
