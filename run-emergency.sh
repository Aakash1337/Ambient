#!/usr/bin/env bash
# Demo emergency path: run the exact last committed pre-voice application.
#
# This never checks out, resets, stashes, or otherwise changes the working
# tree.  It expands the pinned commit into a private temporary directory,
# reuses the already-installed Linux virtualenv, and removes the temporary
# copy after the app exits.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
    SCRIPT_PATH="$(readlink -f -- "$SCRIPT_PATH")"
fi
REPO_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
BASELINE_COMMIT="5d7bbe78099fa5b78ef3edb9709348d2b610e74d"
VENV_DIR="$REPO_DIR/.venv-linux"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Emergency baseline needs the existing .venv-linux environment." >&2
    echo "Run ./run.sh once to finish setup, then retry." >&2
    exit 2
fi

if ! git -C "$REPO_DIR" cat-file -e "$BASELINE_COMMIT^{commit}" 2>/dev/null; then
    echo "Emergency baseline commit $BASELINE_COMMIT is unavailable." >&2
    exit 2
fi

if ! command -v flock >/dev/null 2>&1; then
    echo "Emergency baseline needs the Linux flock command for safe single-instance startup." >&2
    exit 2
fi

SNAPSHOT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ambientqa-emergency.XXXXXX")"
cleanup() {
    rm -rf -- "$SNAPSHOT_DIR"
}
trap cleanup EXIT

git -C "$REPO_DIR" archive "$BASELINE_COMMIT" | tar -x -C "$SNAPSHOT_DIR"
# Keep the source byte-for-byte pre-voice, but make the demo configuration
# conservative: that baseline predates the discovery that its always-on audit
# doubled Claude spend, and its sweep was disconnected.  This edit exists only
# inside the disposable snapshot.
sed -i '/^\[ui\]/i verify = "off"\nsweep = "off"\n' "$SNAPSHOT_DIR/config.toml"
ln -s "$VENV_DIR" "$SNAPSHOT_DIR/.venv-linux"
mkdir -p "$REPO_DIR/logs"
ln -s "$REPO_DIR/logs" "$SNAPSHOT_DIR/logs"

# The emergency path may reuse the environment but must never mutate it.  A
# missing/older install stamp would make the archived run.sh invoke pip.
DEPS_STAMP="$VENV_DIR/.deps-installed"
if [ ! -f "$DEPS_STAMP" ] || [ "$SNAPSHOT_DIR/requirements.txt" -nt "$DEPS_STAMP" ]; then
    echo "Emergency baseline dependencies are not preinstalled; refusing to modify the shared environment." >&2
    echo "Run ./run-emergency.sh --check after restoring .venv-linux/.deps-installed." >&2
    exit 2
fi

if [ "${1:-}" = "--check" ]; then
    if [ "$#" -ne 1 ]; then
        echo "usage: ./run-emergency.sh [--check|--takeover]" >&2
        exit 2
    fi
    (
        cd "$SNAPSHOT_DIR"
        "$VENV_DIR/bin/python" -c \
            'from ambientqa.config import load_config; c = load_config("config.toml"); assert c.answer.verify == c.answer.sweep == "off"; import ambientqa.__main__'
        bash -n run.sh
    )
    echo "Emergency baseline is ready: $BASELINE_COMMIT"
    exit 0
fi

TAKEOVER=false
if [ "${1:-}" = "--takeover" ] && [ "$#" -eq 1 ]; then
    TAKEOVER=true
elif [ "$#" -ne 0 ]; then
    echo "usage: ./run-emergency.sh [--check|--takeover]" >&2
    exit 2
fi

# Starting a baseline beside a live current-version instance recreates the
# duplicate Whisper/GPU failure this escape hatch exists to avoid. The shared
# OS lock covers current builds; live heartbeat PIDs also cover an older build
# that was already running before the lock was introduced.
REGISTRY_DIR="${TMPDIR:-/tmp}/ambientqa-instances-$(id -u)"
APP_PIDS=()

pid_is_running() {
    local pid="$1" state
    kill -0 "$pid" 2>/dev/null || return 1
    state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
    [ "$state" != "Z" ]
}

scan_app_pids() {
    APP_PIDS=()
    [ -d "$REGISTRY_DIR" ] || return 0
    local marker pid command_line
    for marker in "$REGISTRY_DIR"/*; do
        [ -f "$marker" ] || continue
        pid="${marker##*/}"
        case "$pid" in
            *[!0-9]*|'') continue ;;
        esac
        if ! pid_is_running "$pid" || [ ! -r "/proc/$pid/cmdline" ]; then
            continue
        fi
        command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        case " $command_line " in
            *" -m ambientqa "*) APP_PIDS+=("$pid") ;;
        esac
    done
}

terminate_app_pids() {
    scan_app_pids
    [ "${#APP_PIDS[@]}" -gt 0 ] || return 0
    local pid attempt any_running
    for pid in "${APP_PIDS[@]}"; do
        echo "EMERGENCY TAKEOVER: stopping Ambient PID $pid" >&2
        kill -TERM "$pid" 2>/dev/null || true
    done
    for attempt in {1..50}; do
        any_running=false
        for pid in "${APP_PIDS[@]}"; do
            if pid_is_running "$pid"; then
                any_running=true
            fi
        done
        [ "$any_running" = false ] && break
        sleep 0.1
    done
    for pid in "${APP_PIDS[@]}"; do
        if pid_is_running "$pid"; then
            echo "PID $pid did not stop in 5s; forcing it down." >&2
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    for attempt in {1..20}; do
        any_running=false
        for pid in "${APP_PIDS[@]}"; do
            if pid_is_running "$pid"; then
                any_running=true
            fi
        done
        [ "$any_running" = false ] && break
        sleep 0.1
    done
    for pid in "${APP_PIDS[@]}"; do
        if pid_is_running "$pid"; then
            echo "Unable to stop verified Ambient PID $pid; fallback not started." >&2
            return 1
        fi
        rm -f -- "$REGISTRY_DIR/$pid"
    done
}

if [ "$TAKEOVER" = true ]; then
    terminate_app_pids || exit 3
fi

# Close the simultaneous-launch race. Shared with
# InstanceRegistry.claim_exclusive(); the wrapper retains fd 9 for the
# extracted baseline's whole lifetime.
LOCK_FILE="${TMPDIR:-/tmp}/ambientqa-app-$(id -u).lock"
exec 9>"$LOCK_FILE"
LOCKED=false
for attempt in {1..30}; do
    if flock -n 9; then
        LOCKED=true
        break
    fi
    [ "$TAKEOVER" = true ] || break
    terminate_app_pids || exit 3
    sleep 0.1
done
if [ "$LOCKED" = false ]; then
    echo "Ambient is already starting or running (application lock held)." >&2
    echo "Quit it with q, or use ./run-emergency.sh --takeover if it is frozen." >&2
    exit 3
fi

if [ "$TAKEOVER" = true ]; then
    # Catch any explicitly unsafe --allow-multiple process that raced the lock.
    terminate_app_pids || exit 3
else
    scan_app_pids
    if [ "${#APP_PIDS[@]}" -gt 0 ]; then
        echo "Ambient is already running as PID ${APP_PIDS[0]}." >&2
        echo "Quit it with q, then run ./run-emergency.sh again." >&2
        echo "If the pane is frozen, use ./run-emergency.sh --takeover." >&2
        exit 3
    fi
fi

echo "EMERGENCY MODE: starting the pinned pre-voice baseline $BASELINE_COMMIT"
echo "The working tree is untouched; voice and all uncommitted changes are excluded."
echo "Costly answer verification and missed-question sweeping are disabled for demo safety."
(
    cd "$SNAPSHOT_DIR"
    ./run.sh
)
