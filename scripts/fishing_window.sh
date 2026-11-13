#!/usr/bin/env bash
# Decide whether a scheduled collect should open the aisstream socket, and
# for how long. One line on stdout: "skip" or "collect <hours>".
#
# The SoCal fishing day is 12:00 UTC through 03:00 UTC next day
# (4:00 AM–7:00 PM PST / 5:00 AM–8:00 PM PDT). The socket may open at
# 10:30 UTC so a run GitHub starts a little early is already connected
# at the first departure. 03:00–10:30 UTC is quiet: do not connect.
#
# Usage: fishing_window.sh <planned_hours> [epoch_seconds]
set -euo pipefail

planned="${1:?planned hours}"
now="${2:-$(date -u +%s)}"

if date -u -d "@${now}" +%H >/dev/null 2>&1; then
  hour=$(date -u -d "@${now}" +%H)
  min=$(date -u -d "@${now}" +%M)
else
  hour=$(date -u -r "${now}" +%H)
  min=$(date -u -r "${now}" +%M)
fi

msm=$((10#$hour * 60 + 10#$min))
open=$((10 * 60 + 30)) # 10:30 UTC
close=$((3 * 60))      # 03:00 UTC

if [ "$msm" -ge "$close" ] && [ "$msm" -lt "$open" ]; then
  echo "skip"
  exit 0
fi

if [ "$msm" -ge "$open" ]; then
  remain_min=$(((24 * 60 - msm) + close))
else
  remain_min=$((close - msm))
fi

awk -v planned="$planned" -v remain="$remain_min" 'BEGIN {
  cap = remain / 60.0
  hours = planned + 0
  if (hours <= 0 || hours > cap) hours = cap
  if (hours < (1 / 60)) hours = 1 / 60
  printf "collect %.4f\n", hours
}'
