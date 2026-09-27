# aisstream-collector

Public, Actions-hosted live AIS collector for [FishScraper](https://github.com/mikelee1991-del/FishScraper).

FishScraper stays **private**. This repo is **public** so the `aisstream.io` websocket job does not spend the private-repo Actions quota.

## What it does

1. Opens **one** aisstream websocket, subscribed to the SoCal bbox **and** `FiltersShipMMSI` for vessels of interest (not every ship in the box).
2. Records a position only when that MMSI is on the allowlist, or the normalized AIS name is on the FishScraper accepted-name list.
3. Writes daily parquet shards.
4. Uploads them to Release tag `aisstream-live`.
5. Private FishScraper `aisstream-ingest` / `ais-watch` download and merge those shards into the map. The release asset names are unchanged (`live-<run-id>-ais_YYYY-MM-DD.parquet` and `status.json`).

## Schedule and Actions minutes

Scheduled runs are four 45-minute samples on the SoCal fishing day (GitHub cron is UTC). They do not run overnight.

| UTC cron | PDT (UTC−7) | PST (UTC−8) | What it catches |
| --- | --- | --- | --- |
| `15 13 * * *` | 6:15–7:00 AM | 5:15–6:00 AM | Harbor departures |
| `15 17 * * *` | 10:15–11:00 AM | 9:15–10:00 AM | Morning, on the grounds |
| `15 21 * * *` | 2:15–3:00 PM | 1:15–2:00 PM | Afternoon |
| `15 0 * * *` | 5:15–6:00 PM | 4:15–5:00 PM | Returns |

**About 200 runner-minutes per day** (4 × 45 minutes, plus a couple of minutes of setup each run). The previous cron (`15 */5 * * *`, 4 hours each) was **about 1,200 minutes per day**. That is roughly an **80–85%** cut (on the order of **1,000 minutes/day**, or about **30,000 minutes/month**).

A stuck run is capped at 70 minutes for the collect step and 90 minutes for the job (previously 300 and 360). `cancel-in-progress` is unchanged, so a new run still replaces an overlapping one, and the SIGTERM trap still flushes shards and uploads them to `aisstream-live` before the runner exits.

Coverage tradeoff: tracks are four 45-minute slices, not a near-continuous archive. Gaps between windows are about 3–4 hours, and nothing is collected overnight. Boats are usually at the dock then. A multi-day boat is only seen when a window overlaps its trip.

PR runs stay a short smoke test (`hours=0.05`, about 3 minutes) so a pull request can prove collect + upload. **Actions → aisstream live collect → Run workflow** defaults to 0.75 hours. `hours=0` still runs until you cancel it or the 70-minute step timeout fires.

## Vessels of interest

aisstream requires a bounding box. `FiltersShipMMSI` is optional and, when omitted, the socket receives **every** vessel in the box. This collector always sends that MMSI list. It refuses to connect if the list is empty.

The allowlist is the **union** of:

- `deploy/accepted_names.json`: `mmsi_allowlist` and `mmsi_to_report_boat`
- `scripts/config.py`: `MMSI_ALLOWLIST` and `MMSI_TO_REPORT_BOAT`

aisstream accepts at most **200** nine-digit MMSI strings and has no ship-name subscribe filter. The files in this repo currently subscribe **21** MMSIs. `accepted_names` lists **120** boats; a boat with no MMSI in either file is a label and a recording rule, and it is not placed on the websocket. Add its MMSI or it will not be tracked. That is what keeps cargo, tankers, and the rest of the SoCal traffic off this collector.

A downloaded message is written only when:

- its MMSI is in the union above, or
- its normalized AIS name is in `accepted_names`

The SoCal bbox still applies, on the subscription and again before a row is written:

- longitude −119.05 to −117.45
- latitude 33.20 to 34.15

If aisstream ever ignored `FiltersShipMMSI`, a non-fleet ship whose name normalizes onto the accepted list (for example Liberty or Enterprise) could still be recorded. The subscribe list is what keeps the firehose off the socket.

## One-time setup

1. Add secret **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `AISSTREAM_API_KEY`
   - Value: key from [aisstream.io](https://aisstream.io/) → API Keys (same key FishScraper used)
2. Enable Actions if prompted.
3. **Actions → aisstream live collect → Run workflow**, or wait for the next fishing-day cron.

## Sync boat list from FishScraper

`deploy/accepted_names.json` is the FishScraper export (`scripts/export_accepted_names.py` in that repo). Copy it over when the fleet changes:

```bash
cp ../FishScraper/deploy/aisstream/accepted_names.json deploy/accepted_names.json
```

The JSON should include:

- `accepted_names` — normalized AIS name → report boat name (recording allowlist and labels)
- `mmsi_allowlist` — MMSIs to subscribe and record
- `mmsi_to_report_boat` — MMSI → report boat name (wins over `scripts/config.py` when both name the same MMSI)

If FishScraper `scripts/config.py` MMSI maps changed, update this repo's `scripts/config.py` too. The collector **unions** both sources. Removing a boat from only one file leaves it subscribed. Commit the JSON (and config, if you edited it) and push; the collect workflow picks them up on the next run.

To track a boat that is name-only today, add its 9-digit MMSI to `mmsi_allowlist` / `mmsi_to_report_boat` (and the config maps if you keep those in sync).

## Not for

Cadastre extract, fish-report scrape, map rebuild — those stay in private FishScraper.
