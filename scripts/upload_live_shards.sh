#!/usr/bin/env bash
# Upload flushed ais_*.parquet + status.json to Release $LIVE_RELEASE.
# Safe to call more than once (gh --clobber). Does not print secrets.
#
# Each run's shard is live-<shard-id>-ais_YYYY-MM-DD.parquet. The shard id
# is in the local filename (ais_YYYY-MM-DD_<id>.parquet) so two overlapping
# runs cannot replace each other's release asset. Re-uploading one run
# replaces that run's asset with the rows accumulated so far.
# UPLOAD_DRY_RUN=1 prints the asset names and does not call gh.
set -euo pipefail

LIVE_RELEASE="${LIVE_RELEASE:-aisstream-live}"
SRC_DIR="${1:-data/processed/ais_live}"
STATUS_SRC="${2:-docs/data/aisstream_status.json}"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DRY="${UPLOAD_DRY_RUN:-}"
NOTES="Rolling aisstream.io live shards (vessels of interest only: SoCal bbox + MMSI allowlist).
Consumed by private mikelee1991-del/FishScraper ingest.
Do not edit by hand."

asset_name() {
  PYTHONPATH="$SCRIPT_DIR" python3 -c '
import os, sys
import collect_aisstream as c
print(c.release_asset_name(sys.argv[1], os.environ.get("GITHUB_RUN_ID")))
' "$1"
}

if [ -z "$DRY" ]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI is required to upload shards" >&2
    exit 1
  fi
  if ! gh release view "$LIVE_RELEASE" >/dev/null 2>&1; then
    gh release create "$LIVE_RELEASE" \
      --title "aisstream live shards" \
      --notes "$NOTES"
  fi
fi

shopt -s nullglob
uploaded=0
for f in "$SRC_DIR"/ais_*.parquet; do
  base=$(basename "$f")
  case "$base" in
    .*) continue ;;
  esac
  dest=$(asset_name "$base")
  if [ -n "$DRY" ]; then
    echo "dry-run $dest"
    uploaded=$((uploaded + 1))
    continue
  fi
  cp "$f" "$dest"
  gh release upload "$LIVE_RELEASE" "$dest" --clobber
  echo "uploaded $dest"
  uploaded=$((uploaded + 1))
done

if [ -f "$STATUS_SRC" ]; then
  if [ -n "$DRY" ]; then
    echo "dry-run status.json"
  else
    cp "$STATUS_SRC" status.json
    gh release upload "$LIVE_RELEASE" status.json --clobber
    echo "uploaded status.json"
  fi
fi

echo "upload_live_shards done files=${uploaded}"
