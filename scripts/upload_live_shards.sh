#!/usr/bin/env bash
# Upload flushed ais_*.parquet + status.json to Release $LIVE_RELEASE.
# Safe to call more than once (gh --clobber). Does not print secrets.
set -euo pipefail

LIVE_RELEASE="${LIVE_RELEASE:-aisstream-live}"
SRC_DIR="${1:-data/processed/ais_live}"
STATUS_SRC="${2:-docs/data/aisstream_status.json}"
NOTES="Rolling aisstream.io live shards (fleet + SoCal bbox).
Consumed by private mikelee1991-del/FishScraper ingest.
Do not edit by hand."

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required to upload shards" >&2
  exit 1
fi

if ! gh release view "$LIVE_RELEASE" >/dev/null 2>&1; then
  gh release create "$LIVE_RELEASE" \
    --title "aisstream live shards" \
    --notes "$NOTES"
fi

shopt -s nullglob
uploaded=0
for f in "$SRC_DIR"/ais_*.parquet; do
  base=$(basename "$f")
  dest="live-${GITHUB_RUN_ID:-local}-${base}"
  cp "$f" "$dest"
  gh release upload "$LIVE_RELEASE" "$dest" --clobber
  echo "uploaded $dest"
  uploaded=$((uploaded + 1))
done

if [ -f "$STATUS_SRC" ]; then
  cp "$STATUS_SRC" status.json
  gh release upload "$LIVE_RELEASE" status.json --clobber
  echo "uploaded status.json"
fi

echo "upload_live_shards done files=${uploaded}"
