# aisstream-collector

Public, Actions-hosted live AIS collector for [FishScraper](https://github.com/mikelee1991-del/FishScraper).

FishScraper stays **private**. This repo is **public** so the `aisstream.io` websocket job does not spend the private-repo Actions quota.

## What it does

1. Opens **one** aisstream websocket, subscribed to the SoCal bbox **and** `FiltersShipMMSI` for vessels of interest (not every ship in the box).
2. Records a position only when that MMSI is on the allowlist, or the normalized AIS name is on the FishScraper accepted-name list.
3. Writes one parquet shard per UTC day per run (`ais_YYYY-MM-DD_<run-id>.parquet`). Rows are flushed on the first kept position, then every 20 kept positions or 120 seconds, and again on SIGTERM/SIGINT. A cancelled or timed-out run does not wait for a 200-message batch.
4. Uploads them to Release tag `aisstream-live`. The release asset name is still `live-<run-id>-ais_YYYY-MM-DD.parquet` (plus `status.json`). The run id in the name is what keeps two overlapping runs from replacing each other's shard. Re-uploading the same run replaces that one asset with every row collected so far.
5. Private FishScraper `aisstream-ingest` / `ais-watch` download and merge those shards into the map. Ingest only keeps a file whose name ends in `ais_YYYY-MM-DD.parquet`.

## Schedule and Actions minutes

Scheduled collection covers the **whole SoCal offshore day**, from departures through returns, as one continuous span. Overnight at the dock is off.

| | PDT (UTC−7) | PST (UTC−8) | UTC |
| --- | --- | --- | --- |
| Collecting | 5:00 AM – 8:00 PM | 4:00 AM – 7:00 PM | 12:00 – 03:00 next day |
| Socket may open | 2:00 AM | 1:00 AM | 09:00 |
| Off | 8:00 PM – 2:00 AM | 7:00 PM – 1:00 AM | 03:00 – 09:00 |

GitHub-hosted jobs stop at 6 hours, so the day is several overlapping runs and `cancel-in-progress` hands off the single socket. The handoff is still the next runner's checkout and install, a few minutes. Each run is allowed to collect **5.75 hours**, about **1.75 hours** past the next cron, so a slightly late handoff does not open a hole. A run that is still connected at 03:00 UTC stops there.

GitHub has not been starting these crons on time. From 25 Oct through 12 Nov 2026 the old crons (`0 12`, `0 17`, `0 22` UTC) actually started a median of **6.3h, 4.5h, and 3.5h** late. The block ran from late morning through about 11 PM Pacific: morning departures were missed, and the only run that reached a normal end (and therefore uploaded, under the old 200-message flush) was the evening, when the boats are docked. Later crons were delayed less than earlier ones, so the covered span was also shorter than 15 hours.

The schedule is five crons at minute 17 (off the top of the hour), four hours apart:

| UTC cron | Why it is there |
| --- | --- |
| `17 5 * * *` | On time this is before 09:00 UTC, so it exits. A delay of about 6 hours starts it near 11:17 UTC and it collects. |
| `17 9 * * *` | On time this is the run that covers the 12:00 UTC open. It collects 5.75h (345 minutes), inside the 350-minute step. The 13:17 handoff cancels it while it is still connected, so 12:00–13:17 UTC is not a hole. |
| `17 13 * * *` | Midday handoff. |
| `17 17 * * *` | Afternoon handoff. |
| `17 21 * * *` | Reaches 03:00 UTC even when it starts on time (5.75h). The window cap stops it at 03:00 so a late start does not sit on the dock until midnight. |

A run that starts between 03:00 and 09:00 UTC exits before it installs Python or opens a socket. An earlier 10:30 UTC open made the on-time 05:17 and 09:17 runs both exit, which left 12:00–13:17 UTC with no socket. Shifting the old three crons earlier by the morning's lateness would not have covered the afternoon: the last cron is delayed less than the first, so it would have finished mid-afternoon. The last cron stays late enough to reach 03:00 UTC, and the extra crons keep each gap inside the 1.75-hour overlap. Returned hours never exceed 345 minutes, so the collect step stays inside its 350-minute timeout and the 360-minute job still has time for the upload.

A delay near the worst days in that stretch (about 10 hours on 2 Nov and 9 Nov) can still open the first socket around 15:00 UTC. Overlap cannot create coverage before the first run exists.

On a typical delayed day that is about **15–16 hours** of one runner, on the order of **950–1000 minutes**, plus a few minutes of setup. Quiet-hour crons cost a checkout and then exit. The old round-the-clock cron was about 1,200 minutes a day.

Boats already offshore at 12:00 UTC are picked up when the socket connects. Boats still out after 03:00 UTC are outside this window.

A vessel of interest sitting overnight — already offshore, or overnighting on the water, while collection is off — is not recorded until the morning window starts again. Overnight collection is off on purpose (03:00–09:00 UTC). That timing may change later; this note only records the limitation.

A stuck run is capped at 350 minutes for the collect step and 360 minutes for the job (the hosted-runner maximum). The step ends first so the `always()` upload can still run. PR runs stay a short smoke test (`hours=0.05`, about 3 minutes). **Actions → aisstream live collect → Run workflow** defaults to one 5.75-hour segment. `hours=0` runs until you cancel it or the step timeout fires. One manual job cannot hold the full fishing day; that takes the scheduled runs.

## Cancelled runs keep their rows

`cancel-in-progress` is what frees the single aisstream socket for the next run. GitHub then gives the cancelled job about a second: on 12 Nov 2026, run `43321010401` was cancelled at 6:19 PM PT after keeping 62 positions from 2:34 PM, the `always()` upload started 4ms later, and it uploaded **0 files** because the process was still blocked in a read with everything in memory. The same pattern dropped the daytime rows every day from at least 31 Oct.

The collector now writes the shard before that happens:

- the first kept row is flushed immediately, then every **20** kept rows or **120 seconds**, whichever comes first
- SIGTERM and SIGINT flush whatever is still buffered on the way out
- a background upload every 3 minutes pushes the shard to `aisstream-live` while the run is still going. It runs in its own session, and the `always()` step kills that session before it uploads, so the two calls do not share one copy path
- each upload copies into a private temp directory and skips a file that is not a complete parquet
- the workflow upload step runs with `if: always()` (`always()` is already true when the job is cancelled) so a handoff, a timeout, or a failure still publishes the shard

Two runs use different shard ids (the Actions run id), so the handoff cannot overwrite the other run's asset. At most the last two minutes of a hard-killed process can still be lost; a cancelled daytime segment is no longer discarded.

## Vessels of interest

aisstream requires a bounding box. `FiltersShipMMSI` is optional and, when omitted, the socket receives **every** vessel in the box. This collector always sends that MMSI list. It refuses to connect if the list is empty.

The allowlist is the **union** of:

- `deploy/accepted_names.json`: `mmsi_allowlist` and `mmsi_to_report_boat`
- `scripts/config.py`: `MMSI_ALLOWLIST` and `MMSI_TO_REPORT_BOAT`

aisstream accepts at most **200** nine-digit MMSI strings and has no ship-name subscribe filter. The files in this repo currently subscribe **20** MMSIs. `accepted_names` lists **120** boats; a boat with no MMSI in either file is a label and a recording rule, and it is not placed on the websocket. Add its MMSI or it will not be tracked. That is what keeps cargo, tankers, and the rest of the SoCal traffic off this collector.

Two MMSIs are blocked even if a later FishScraper export lists them again (`MMSI_BLOCKLIST` in `scripts/config.py`):

- **368269920** — a Newport Harbor sailboat, not the MDR sportfishing boat Spitfire. The name `SPITFIRE` stays in `accepted_names` so the real boat can still be labeled once it has a correct MMSI. It is not subscribed.
- **366341000** — a towing vessel that had been linked to San Pedro's Pride. It was not in this repo's maps; the blocklist keeps a sync from adding it.

A blocked MMSI is neither subscribed nor recorded, including when its AIS name is on the accepted-name list.

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

Do not re-add **368269920** or **366341000**. `MMSI_BLOCKLIST` drops them after the union, so a copied export cannot put the Newport sailboat or that tow back on the socket.

To track a boat that is name-only today, add its 9-digit MMSI to `mmsi_allowlist` / `mmsi_to_report_boat` (and the config maps if you keep those in sync).

## Architecture

- Live aisstream websocket collect and Release `aisstream-live`: this repo.
- Cadastre extract, dock fish-report scrape, and Releases `ais-daily-archive` / `fish-reports-latest`: [fishscraper-ingest](https://github.com/mikelee1991-del/fishscraper-ingest).
- Map rebuild and the Cloudflare Worker: private FishScraper, which consumes both public repos.
