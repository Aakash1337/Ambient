#!/usr/bin/env bash
# Launch Ambient with the OPT-IN web console instead of the terminal pane.
#
# This is deliberately a separate entry point: the terminal pane (./run.sh)
# stays the default and the known-good demo baseline, and the pinned
# emergency build (./run-emergency.sh) predates the web console entirely, so
# a web-console problem can never take either of them down. All the real
# environment bootstrapping lives in run.sh; this only adds the flag.
#
#   ./run-web.sh              # console at http://127.0.0.1:8802 (or next free port)
#   ./run-web.sh --voice      # same, with spoken answers
#
# To rehearse the console with no audio or models at all:
#   .venv-linux/bin/python scripts/webui_demo.py
exec "$(dirname "$0")/run.sh" --web "$@"
