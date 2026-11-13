#!/usr/bin/env bash
# Decide whether a scheduled collect should open the aisstream socket, and
# for how long. One line on stdout: "skip" or "collect <hours>".
#
# The SoCal fishing day is 12:00 UTC through 03:00 UTC next day
# (4:00 AM–7:00 PM PST / 5:00 AM–8:00 PM PDT). The socket may open at
# 09:00 UTC so the on-time 09:17 UTC run is already connected at the
# 12:00 departure. 03:00–09:00 UTC is quiet: the on-time 05:17 run exits.
#
# A run must finish inside the 350-minute collect step (the job cap is
# 360). Returned hours never exceed 345 minutes (5.75h), leaving the last
# 5 minutes of the step for the in-step upload. The always() upload then
# still has the job's final 10 minutes if the step is killed at 350.
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
open=$((9 * 60))    # 09:00 UTC
close=$((3 * 60))   # 03:00 UTC
# 345 minutes = 5.75h. Step timeout is 350; job timeout is 360.
max_min=345

if [ "$msm" -ge "$close" ] && [ "$msm" -lt "$open" ]; then
  echo "skip"
  exit 0
fi

if [ "$msm" -ge "$open" ]; then
  remain_min=$(((24 * 60 - msm) + close))
else
  remain_min=$((close - msm))
fi
if [ "$remain_min" -gt "$max_min" ]; then
  remain_min=$max_min
fi

awk -v planned="$planned" -v remain="$remain_min" 'BEGIN {
  cap = remain / 60.0
  hours = planned + 0
  if (hours <= 0 || hours > cap) hours = cap
  if (hours < (1 / 60)) hours = 1 / 60
  printf "collect %.4f\n", hours
}'
