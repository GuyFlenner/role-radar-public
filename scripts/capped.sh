#!/usr/bin/env bash
# Log Budget — run a command but let only its TAIL into the conversation.
#
# Adopted from claude-sdlc's Log Budget rule (sdlc/SKILL.md: per-attempt raw
# output capped at the last 60 lines). Their note is the reason this ships as a
# script and not as prose: "This is the single most effective lever against
# repair-loop context bloat; do not skip it." Prose drifts; a script does not.
#
# Why it matters here: a tool result does not cost you once. It joins the
# conversation prefix and is re-read on EVERY later request in the session, at
# the cache-read rate. A 300-line log early in a long session is paid hundreds of
# times. Capping the output caps that annuity.
#
# The exit code of the wrapped command is preserved, so this is safe to use in
# gates and in `&&` chains.
#
# Usage:
#   bash scripts/capped.sh <max_lines> <command...>
#   bash scripts/capped.sh 60 uv run pytest
#   CAPPED_LINES=20 bash scripts/capped.sh - git log --oneline
#
# A `-` for max_lines means "use $CAPPED_LINES, default 60".

set -uo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: capped.sh <max_lines|-> <command...>" >&2
  exit 2
fi

max="$1"
shift
if [ "$max" = "-" ]; then
  max="${CAPPED_LINES:-60}"
fi
case "$max" in
  '' | *[!0-9]*)
    echo "capped.sh: max_lines must be a positive integer, got '$max'" >&2
    exit 2
    ;;
esac
if [ "$max" -lt 1 ]; then
  echo "capped.sh: max_lines must be >= 1" >&2
  exit 2
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# Capture stdout+stderr together: for a failing gate the interleaving is the
# signal, and splitting them hides which command produced which error.
"$@" >"$tmp" 2>&1
rc=$?

lines=$(wc -l <"$tmp" | tr -d '[:space:]')
if [ "$lines" -gt "$max" ]; then
  echo "[capped.sh: $((lines - max)) of $lines lines truncated — showing last $max]"
fi
tail -n "$max" "$tmp"

exit "$rc"
