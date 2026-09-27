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

Scheduled collection covers the **whole SoCal offshore day**, from departures through returns, as one continuous span. Overnight at the dock is off.

| | PDT (UTC−7) | PST (UTC−8) |
| --- | --- | --- |
| Collecting | 5:00 AM – 8:00 PM | 4:00 AM – 7:00 PM |
| Off (overnight) | 8:00 PM – 5:00 AM | 7:00 PM – 4:00 AM |

GitHub-hosted jobs stop at 6 hours, so that 15-hour span is three abutting runs. The next run is scheduled while the previous one is still connected; `cancel-in-progress` hands off the single socket. The previous run collects 15 minutes past the handoff, so a slightly late cron does not open a hole.

| UTC cron | Collect | PDT | PST |
| --- | --- | --- | --- |
| `0 12 * * *` | 5.25h, hands off at 17:00 UTC | 5:00 AM – 10:00 AM | 4:00 AM – 9:00 AM |
| `0 17 * * *` | 5.25h, hands off at 22:00 UTC | 10:00 AM – 3:00 PM | 9:00 AM – 2:00 PM |
| `0 22 * * *` | 5h, through 03:00 UTC | 3:00 PM – 8:00 PM | 2:00 PM – 7:00 PM |

On time, that is **15 hours of collection, about 910 runner-minutes per day** (900 minutes plus a few minutes of setup on each run). The old cron (`15 */5 * * *` × 4 hours, including overnight) was **about 1,200 minutes per day**. Dropping overnight saves **about 300 minutes per day**. The handoff itself is the next runner's checkout and install, a few minutes, and the SIGTERM trap still uploads shards to `aisstream-live` before the old runner exits.

Boats already offshore at the start are picked up when the 5:00 AM PDT / 4:00 AM PST run connects. Twilight boats still out after 8:00 PM PDT / 7:00 PM PST are outside this window.

A vessel of interest sitting overnight — already offshore, or overnighting on the water, while collection is off — is not recorded until the morning window starts again. Overnight collection is off on purpose (roughly 8:00 PM–5:00 AM Pacific). That timing may change later; this note only records the limitation.

A stuck run is capped at 340 minutes for the collect step and 360 minutes for the job (the hosted-runner maximum). PR runs stay a short smoke test (`hours=0.05`, about 3 minutes). **Actions → aisstream live collect → Run workflow** defaults to one 5.25-hour segment. `hours=0` runs until you cancel it or the step timeout fires. One manual job cannot hold the full fishing day; that takes the three scheduled runs.

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
