#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# JS-only corpus round trips: exact-byte edits, executed Home branches and refusal.
HERE=$(cd "$(dirname "$0")" && pwd)
exec sh "$HERE/test-minimal-hook.sh"
