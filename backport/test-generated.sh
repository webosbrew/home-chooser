#!/bin/sh
# Compatibility entry point: the rewriter is now the only runtime patch path.
HERE=$(cd "$(dirname "$0")" && pwd)
exec sh "$HERE/test-minimal-hook.sh" "$@"
