#!/usr/bin/env bash
# Stop a background shard uploader started in its own session.
# Usage: stop_background_uploader.sh <pidfile>
# Missing pidfile is success. Kills the process group when that group is
# not this shell's group, so a leftover sleep/gh cannot upload alongside
# the always() step. Then removes the pidfile.
set -euo pipefail

pidfile="${1:?pidfile}"
if [ ! -f "$pidfile" ]; then
  exit 0
fi
pid=$(tr -d '[:space:]' < "$pidfile" || true)
rm -f "$pidfile"
if [ -z "$pid" ] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
  exit 0
fi
if ! kill -0 "$pid" 2>/dev/null; then
  exit 0
fi

pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)
my_pgid=$(ps -o pgid= -p "$$" 2>/dev/null | tr -d '[:space:]' || true)
if [ -n "$pgid" ] && [ "$pgid" != "$my_pgid" ]; then
  kill -TERM -- "-$pgid" 2>/dev/null || true
else
  kill -TERM "$pid" 2>/dev/null || true
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$pid" 2>/dev/null; then
    exit 0
  fi
  sleep 0.2
done

if [ -n "$pgid" ] && [ "$pgid" != "$my_pgid" ]; then
  kill -KILL -- "-$pgid" 2>/dev/null || true
else
  kill -KILL "$pid" 2>/dev/null || true
fi
exit 0
