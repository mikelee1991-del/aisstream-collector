# aisstream-collector

Public, Actions-hosted live AIS collector for [FishScraper](https://github.com/mikelee1991-del/FishScraper).

FishScraper stays **private**. This repo is **public** so the long-running `aisstream.io` websocket job uses free GitHub Actions minutes instead of the private-repo quota.

## What it does

1. Opens one aisstream websocket (SoCal bbox + fleet filter).
2. Writes daily parquet shards.
3. Uploads them to Release tag `aisstream-live`.
4. Private FishScraper `aisstream-ingest` / `ais-watch` download and merge those shards into the map.

## One-time setup

1. Add secret **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `AISSTREAM_API_KEY`
   - Value: key from [aisstream.io](https://aisstream.io/) → API Keys (same key FishScraper used)
2. Enable Actions if prompted.
3. **Actions → aisstream live collect → Run workflow** (or wait for the 5h cron).
   Scheduled runs collect for 4 hours then upload and exit green. Manual
   `hours=0` still runs until cancelled; shards are uploaded in the collect
   step so a runner SIGTERM cannot skip the Release publish.

## Sync boat list from FishScraper

```bash
cp ../FishScraper/deploy/aisstream/accepted_names.json deploy/accepted_names.json
# also update scripts/config.py MMSI maps if those changed in FishScraper
```

## Not for

Cadastre extract, fish-report scrape, map rebuild — those stay in private FishScraper.
