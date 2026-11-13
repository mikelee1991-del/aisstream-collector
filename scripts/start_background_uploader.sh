#!/usr/bin/env bash
# Start the periodic shard upload in its own session and record that pid.
# Usage: start_background_uploader.sh <pidfile> <src_dir> <status_json>
# The caller returns immediately. stop_background_uploader.sh kills the session
# so a later upload does not run gh at the same time.
set -euo pipefail

pidfile="${1:?pidfile}"
src_dir="${2:?src dir}"
status_json="${3:?status json}"
mkdir -p "$(dirname "$pidfile")"

python3 -c '
import os, sys
os.setsid()
open(sys.argv[1], "w").write(str(os.getpid()))
os.execv("/bin/bash", ["bash", "-c", sys.argv[2], "bash", sys.argv[3], sys.argv[4]])
' "$pidfile" '
while true; do
  sleep 180
  bash scripts/upload_live_shards.sh "$1" "$2" || true
done
' "$src_dir" "$status_json" &
child=$!
# The child writes the pid only after setsid(), so the file means the
# uploader is already its own session and safe to kill as a group.
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
  if [ -s "$pidfile" ]; then
    exit 0
  fi
  sleep 0.02
done
echo "$child" > "$pidfile"
