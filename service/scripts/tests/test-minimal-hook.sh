#!/bin/sh
# Offline structural, behavioural and mutation checks; never touches a TV.
HERE=$(cd "$(dirname "$0")/.." && pwd)
exec python3 -I "$HERE/tests/test_minimal_hook.py" "$@"
