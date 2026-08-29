#!/usr/bin/env python3
"""Collect live SoCal AIS from free aisstream.io websocket into daily parquet.

This is the free forward-looking alternative while Marine Cadastre 2026 bulk
files are unpublished. aisstream has NO historical backfill API — it only
streams live messages. Run continuously (or on a host with long sessions)
to build our own archive.

Setup:
  1. Create a free API key at https://aisstream.io/ (GitHub login)
  2. export AISSTREAM_API_KEY=...
  3. python3 scripts/collect_aisstream.py --hours 0   # 0 = run forever

Primary host is a single GitHub Actions session at a time
(.github/workflows/aisstream-collect.yml). Overlapping sockets get HTTP 429
from the free tier, so the workflow cancels the previous run on handoff.
Optional always-on VM: deploy/aisstream/README.md.

Output lands in data/processed/ais_daily/ais_YYYY-MM-DD.parquet with schema
compatible with detect_stops.py (plus ais_source='aisstream'). Same-day
restarts append and dedupe so rolling jobs do not wipe earlier hours.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import (  # noqa: E402
    AIS_BBOX,
    DATA_PROCESSED,
    DATA_RAW,
    MMSI_ALLOWLIST,
    MMSI_TO_REPORT_BOAT,
)

NAME_NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")


def normalize_name(name: str) -> str:
    return NAME_NORMALIZE_RE.sub("", (name or "").upper())

def _ws_connect():
    try:
        from websockets.sync.client import connect as ws_connect
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Install dependency: pip install websockets") from e
    return ws_connect

WS_URL = "wss://stream.aisstream.io/v0/stream"
OUT_DIR = DATA_PROCESSED / "ais_daily"
DEFAULT_ACCEPTED = ROOT / "deploy" / "aisstream" / "accepted_names.json"

# Free-tier aisstream rejects concurrent sockets with HTTP 429. Back off hard
# so overlapping/zombie sessions can clear instead of reconnect-storming.
RECONNECT_BASE_SEC = 5.0
RECONNECT_MAX_SEC = 60.0
RATE_LIMIT_BASE_SEC = 30.0
RATE_LIMIT_MAX_SEC = 900.0
# Scheduled/GHA sessions: bail early when the stream never delivers (don't burn
# a full 5h job on a dead/rate-limited key).
EMPTY_ABORT_AFTER_SEC = 20 * 60

_stop = False


def _handle_stop(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    print(f"received signal {signum}; will flush and exit…", flush=True)


def is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many" in text


def reconnect_delay(attempt: int, rate_limited: bool) -> float:
    """Exponential backoff; rate-limits start higher and cap at 15 minutes."""
    attempt = max(0, int(attempt))
    if rate_limited:
        return min(RATE_LIMIT_MAX_SEC, RATE_LIMIT_BASE_SEC * (2 ** min(attempt, 5)))
    return min(RECONNECT_MAX_SEC, RECONNECT_BASE_SEC * (2 ** min(attempt, 4)))


def classify_collect_status(messages: int, rate_limited: bool, note: str | None = None) -> tuple[str, str | None]:
    """Map session stats to a status string for aisstream_status.json."""
    if messages > 0:
        return "ok", note
    if rate_limited:
        return "rate_limited", note or (
            "aisstream returned HTTP 429 / no messages — usually overlapping "
            "collector sockets or a free-tier connection limit."
        )
    return "empty", note or (
        "No websocket messages received. Check API key, aisstream.io status, "
        "and that only one collector session is connected."
    )


def bbox_for_aisstream() -> list[list[list[float]]]:
    # aisstream bbox corners: [[lat, lon], [lat, lon]]
    return [[
        [AIS_BBOX["min_lat"], AIS_BBOX["min_lon"]],
        [AIS_BBOX["max_lat"], AIS_BBOX["max_lon"]],
    ]]


def load_accepted_names(path: Path | None, trips_path: Path) -> dict[str, str]:
    """Prefer a small JSON mapping (VM-friendly); fall back to fish-report trips."""
    if path and path.exists():
        payload = json.loads(path.read_text())
        raw = payload.get("accepted_names") or payload
        out = {str(k): str(v) for k, v in raw.items()}
        print(f"loaded {len(out)} accepted names from {path}", flush=True)
        return out
    from extract_ais import build_accepted_names  # noqa: WPS433

    accepted = build_accepted_names(trips_path)
    print(f"built {len(accepted)} accepted names from {trips_path}", flush=True)
    return accepted


def parse_message(msg: dict, accepted: dict[str, str]) -> dict | None:
    meta = msg.get("MetaData") or {}
    mmsi = meta.get("MMSI") or meta.get("Mmsi")
    if mmsi is None:
        return None
    try:
        mmsi = int(mmsi)
    except (TypeError, ValueError):
        return None

    body = msg.get("Message") or {}
    pos = (
        body.get("PositionReport")
        or body.get("StandardClassBPositionReport")
        or body.get("ExtendedClassBPositionReport")
        or {}
    )
    static = body.get("ShipStaticData") or {}

    lat = meta.get("latitude", meta.get("Latitude"))
    lon = meta.get("longitude", meta.get("Longitude"))
    if lat is None:
        lat = pos.get("Latitude", pos.get("latitude"))
    if lon is None:
        lon = pos.get("Longitude", pos.get("longitude"))
    if lat is None or lon is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if not (AIS_BBOX["min_lat"] <= lat_f <= AIS_BBOX["max_lat"]):
        return None
    if not (AIS_BBOX["min_lon"] <= lon_f <= AIS_BBOX["max_lon"]):
        return None

    name = (
        meta.get("ShipName")
        or meta.get("shipName")
        or static.get("Name")
        or static.get("ShipName")
        or ""
    )
    name = str(name).strip()
    norm = normalize_name(name) if name else ""
    report = MMSI_TO_REPORT_BOAT.get(mmsi) or accepted.get(norm)
    if report is None and mmsi not in MMSI_ALLOWLIST and norm not in accepted:
        return None

    sog = pos.get("Sog", pos.get("SOG", meta.get("Sog")))
    cog = pos.get("Cog", pos.get("COG", meta.get("Cog")))
    heading = pos.get("TrueHeading", pos.get("Heading", meta.get("heading")))
    ts = meta.get("time_utc") or meta.get("time")
    try:
        ts_clean = str(ts).replace(" +0000 UTC", "").replace(" UTC", "").strip()
        dt = pd.to_datetime(ts_clean, utc=True)
    except Exception:
        dt = pd.Timestamp.now(tz="UTC")

    dim = static.get("Dimension") or {}
    return {
        "mmsi": mmsi,
        "base_date_time": dt.to_pydatetime(),
        "longitude": lon_f,
        "latitude": lat_f,
        "sog": float(sog) if sog is not None else None,
        "cog": float(cog) if cog is not None else None,
        "heading": float(heading) if heading is not None else None,
        "vessel_name": name or None,
        "call_sign": static.get("CallSign") or meta.get("callSign"),
        "vessel_type": static.get("Type") or meta.get("Type"),
        "status": pos.get("NavigationalStatus"),
        "length": dim.get("A"),
        "width": dim.get("B"),
        "draft": static.get("MaximumStaticDraught"),
        "transceiver": None,
        "vessel_name_norm": norm or None,
        "report_boat_name": report,
        "date": pd.Timestamp(dt).tz_convert("UTC").strftime("%Y-%m-%d"),
        "ais_source": "aisstream",
    }


def flush_day_buffers(buffers: dict[str, list[dict]], out_dir: Path) -> int:
    written = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for day, rows in list(buffers.items()):
        if not rows:
            continue
        path = out_dir / f"ais_{day}.parquet"
        new_df = pd.DataFrame(rows)
        if path.exists():
            old = pd.read_parquet(path)
            merged = pd.concat([old, new_df], ignore_index=True)
        else:
            merged = new_df
        merged["base_date_time"] = pd.to_datetime(merged["base_date_time"], utc=True)
        merged = merged.drop_duplicates(
            subset=["mmsi", "base_date_time", "latitude", "longitude"],
            keep="last",
        ).sort_values(["mmsi", "base_date_time"])
        merged.to_parquet(path, index=False)
        written += len(rows)
        buffers[day] = []
        print(f"flushed {len(rows)} rows -> {path} (total {len(merged)})", flush=True)
    return written


def write_status(path: Path, **fields) -> dict:
    """Write docs/data/aisstream_status.json (safe to commit; no secrets)."""
    host = os.environ.get("AISSTREAM_HOST")
    if not host:
        host = "github-actions" if os.environ.get("GITHUB_ACTIONS") else "local"
    payload = {
        "title": "aisstream.io live collector",
        "host": host,
        "status": fields.pop("status", "ok"),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "github_run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "github_run_url": None,
    }
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    if repo and run_id:
        payload["github_run_url"] = f"{server}/{repo}/actions/runs/{run_id}"
    payload.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote status {path} status={payload['status']}", flush=True)
    return payload


def collect(
    api_key: str,
    hours: float,
    flush_every: int,
    accepted: dict[str, str],
    out_dir: Path,
    empty_abort_after_sec: float = EMPTY_ABORT_AFTER_SEC,
) -> dict:
    sub = {
        "APIKey": api_key,
        "BoundingBoxes": bbox_for_aisstream(),
        "FilterMessageTypes": [
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport",
            "ShipStaticData",
        ],
    }
    buffers: dict[str, list[dict]] = defaultdict(list)
    forever = hours <= 0
    started = time.time()
    deadline = None if forever else (started + hours * 3600)
    total = 0
    kept = 0
    reconnect_attempt = 0
    rate_limited_hits = 0
    saw_rate_limit = False
    last_heartbeat = time.time()
    abort_reason: str | None = None

    print(
        f"Connecting aisstream bbox "
        f"lat[{AIS_BBOX['min_lat']},{AIS_BBOX['max_lat']}] "
        f"lon[{AIS_BBOX['min_lon']},{AIS_BBOX['max_lon']}] "
        f"{'forever' if forever else f'for {hours}h'}…",
        flush=True,
    )

    while not _stop and (forever or time.time() < deadline):
        if (
            total == 0
            and empty_abort_after_sec > 0
            and (time.time() - started) >= empty_abort_after_sec
        ):
            abort_reason = (
                f"No messages after {int(empty_abort_after_sec)}s "
                f"(rate_limited_hits={rate_limited_hits}); aborting empty session early"
            )
            print(f"[error] {abort_reason}", flush=True)
            break
        try:
            with _ws_connect()(WS_URL, open_timeout=30, close_timeout=5) as ws:
                ws.send(json.dumps(sub))
                print("subscribed", flush=True)
                while not _stop and (forever or time.time() < deadline):
                    try:
                        raw = ws.recv(timeout=30)
                    except TimeoutError:
                        if time.time() - last_heartbeat >= 300:
                            print(
                                f"heartbeat messages={total} kept={kept} "
                                f"(waiting on stream)",
                                flush=True,
                            )
                            last_heartbeat = time.time()
                        if (
                            total == 0
                            and empty_abort_after_sec > 0
                            and (time.time() - started) >= empty_abort_after_sec
                        ):
                            abort_reason = (
                                f"Subscribed but received 0 messages after "
                                f"{int(empty_abort_after_sec)}s; aborting empty session early"
                            )
                            print(f"[error] {abort_reason}", flush=True)
                            break
                        continue
                    total += 1
                    reconnect_attempt = 0
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("error") or msg.get("Error"):
                        err = msg.get("error") or msg.get("Error")
                        print("aisstream error:", msg, flush=True)
                        if is_rate_limit_error(Exception(str(err))):
                            saw_rate_limit = True
                            rate_limited_hits += 1
                        time.sleep(reconnect_delay(reconnect_attempt, saw_rate_limit))
                        reconnect_attempt += 1
                        break
                    row = parse_message(msg, accepted)
                    if not row:
                        continue
                    buffers[row["date"]].append(row)
                    kept += 1
                    if kept % flush_every == 0:
                        flush_day_buffers(buffers, out_dir)
                        print(f"progress messages={total} kept={kept}", flush=True)
                        last_heartbeat = time.time()
                    elif time.time() - last_heartbeat >= 300:
                        print(f"heartbeat messages={total} kept={kept}", flush=True)
                        last_heartbeat = time.time()
                else:
                    # inner while exhausted without break
                    pass
                if abort_reason:
                    break
        except Exception as exc:
            rate_limited = is_rate_limit_error(exc)
            if rate_limited:
                saw_rate_limit = True
                rate_limited_hits += 1
            delay = reconnect_delay(reconnect_attempt, rate_limited)
            print(
                f"[warn] websocket error: {exc}; reconnecting in {delay:.0f}s"
                f"{' (rate limit)' if rate_limited else ''}",
                flush=True,
            )
            flush_day_buffers(buffers, out_dir)
            reconnect_attempt += 1
            # Sleep in chunks so SIGTERM can land quickly during long 429 backoff.
            slept = 0.0
            while slept < delay and not _stop:
                chunk = min(5.0, delay - slept)
                time.sleep(chunk)
                slept += chunk

    flush_day_buffers(buffers, out_dir)
    print(f"done messages={total} kept={kept}", flush=True)
    days = []
    if out_dir.is_dir():
        for p in sorted(out_dir.glob("ais_*.parquet")):
            days.append(p.name)
    status, note = classify_collect_status(total, saw_rate_limit, abort_reason)
    return {
        "messages": total,
        "kept": kept,
        "days": days,
        "out_dir": str(out_dir),
        "status": status,
        "note": note,
        "rate_limited_hits": rate_limited_hits,
        "elapsed_sec": round(time.time() - started, 1),
    }


def diagnose(
    api_key: str,
    seconds_per_probe: float = 90.0,
) -> dict:
    """Looser probes: world + SoCal, with/without message-type filters, no MMSI keep filter.

    Used to separate "our fleet filter is too tight" from "aisstream is silent".
    """
    probes = [
        {
            "name": "world_unfiltered",
            "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
            "FilterMessageTypes": None,
        },
        {
            "name": "world_position_only",
            "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
            "FilterMessageTypes": ["PositionReport"],
        },
        {
            "name": "socal_unfiltered",
            "BoundingBoxes": bbox_for_aisstream(),
            "FilterMessageTypes": None,
        },
        {
            "name": "socal_filtered_types",
            "BoundingBoxes": bbox_for_aisstream(),
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "ShipStaticData",
            ],
        },
    ]
    results = []
    any_rate_limit = False
    for probe in probes:
        if _stop:
            break
        sub: dict = {
            "APIKey": api_key,
            "BoundingBoxes": probe["BoundingBoxes"],
        }
        if probe["FilterMessageTypes"]:
            sub["FilterMessageTypes"] = probe["FilterMessageTypes"]
        started = time.time()
        deadline = started + seconds_per_probe
        total = 0
        by_type: dict[str, int] = defaultdict(int)
        sample_keys: list[str] = []
        rate_limited = False
        error_text = None
        print(
            f"diagnose probe={probe['name']} for {seconds_per_probe:.0f}s "
            f"filters={probe['FilterMessageTypes'] or 'ALL'}",
            flush=True,
        )
        try:
            with _ws_connect()(WS_URL, open_timeout=30, close_timeout=5) as ws:
                ws.send(json.dumps(sub))
                print(f"  subscribed {probe['name']}", flush=True)
                while not _stop and time.time() < deadline:
                    try:
                        raw = ws.recv(timeout=min(30.0, max(1.0, deadline - time.time())))
                    except TimeoutError:
                        continue
                    total += 1
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        by_type["<non_json>"] += 1
                        continue
                    if msg.get("error") or msg.get("Error"):
                        err = str(msg.get("error") or msg.get("Error"))
                        error_text = err
                        if is_rate_limit_error(Exception(err)):
                            rate_limited = True
                            any_rate_limit = True
                        print(f"  aisstream error: {msg}", flush=True)
                        break
                    mtype = (
                        msg.get("MessageType")
                        or next(iter((msg.get("Message") or {}).keys()), None)
                        or "<unknown>"
                    )
                    by_type[str(mtype)] += 1
                    if len(sample_keys) < 5:
                        meta = msg.get("MetaData") or {}
                        sample_keys.append(
                            f"{mtype}:{meta.get('MMSI') or meta.get('Mmsi')}:{meta.get('ShipName') or ''}"
                        )
                    if total in (1, 10, 50, 100) or total % 200 == 0:
                        print(f"  progress {probe['name']} messages={total}", flush=True)
        except Exception as exc:
            error_text = str(exc)
            if is_rate_limit_error(exc):
                rate_limited = True
                any_rate_limit = True
            print(f"  [warn] {probe['name']}: {exc}", flush=True)
        elapsed = round(time.time() - started, 1)
        row = {
            "name": probe["name"],
            "messages": total,
            "elapsed_sec": elapsed,
            "by_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
            "sample": sample_keys,
            "rate_limited": rate_limited,
            "error": error_text,
        }
        results.append(row)
        print(
            f"  done {probe['name']} messages={total} elapsed={elapsed}s "
            f"types={row['by_type']}",
            flush=True,
        )

    total_messages = sum(r["messages"] for r in results)
    if total_messages > 0:
        status = "ok"
        note = "Looser probes received traffic — fleet/MMSI filters or bbox were the bottleneck."
    elif any_rate_limit:
        status = "rate_limited"
        note = "Diagnose hit HTTP 429 / rate limit with zero messages."
    else:
        status = "empty"
        note = (
            "Subscribed successfully but received 0 frames even with world bbox / no "
            "message-type filter. Likely aisstream service-side outage "
            "(see https://github.com/aisstream/aisstream/issues/15)."
        )
    return {
        "status": status,
        "note": note,
        "messages": total_messages,
        "kept": 0,
        "probes": results,
        "seconds_per_probe": seconds_per_probe,
        "rate_limited_hits": sum(1 for r in results if r["rate_limited"]),
        "elapsed_sec": round(sum(r["elapsed_sec"] for r in results), 1),
        "days": [],
        "out_dir": None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api-key", default=os.environ.get("AISSTREAM_API_KEY", ""))
    ap.add_argument(
        "--hours",
        type=float,
        default=0.0,
        help="How long to collect (0 = forever / daemon mode until signal)",
    )
    ap.add_argument("--flush-every", type=int, default=200)
    ap.add_argument("--trips", type=Path, default=DATA_RAW / "fish_reports" / "by_year")
    ap.add_argument(
        "--accepted-names",
        type=Path,
        default=DEFAULT_ACCEPTED if DEFAULT_ACCEPTED.exists() else None,
        help="JSON {accepted_names: {NORM: Boat}} (defaults to deploy/aisstream/accepted_names.json)",
    )
    ap.add_argument("--out-dir", type=Path, default=Path(os.environ.get("AISSTREAM_OUT_DIR", str(OUT_DIR))))
    ap.add_argument(
        "--status-out",
        type=Path,
        default=None,
        help="Write aisstream_status.json here (no secrets)",
    )
    ap.add_argument(
        "--status-only",
        action="store_true",
        help="Write --status-out and exit (used when the API key is missing)",
    )
    ap.add_argument(
        "--status",
        default="awaiting_api_key",
        help="Status string for --status-only",
    )
    ap.add_argument(
        "--fail-if-empty",
        action="store_true",
        help="Exit non-zero when the session received 0 websocket messages",
    )
    ap.add_argument(
        "--empty-abort-after",
        type=float,
        default=None,
        help=(
            "Abort after this many seconds with 0 messages (0 disables). "
            "Default: 1200s for timed sessions, disabled for --hours 0."
        ),
    )
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="Run short looser probes (world bbox, no fleet filter) instead of collecting",
    )
    ap.add_argument(
        "--diagnose-seconds",
        type=float,
        default=90.0,
        help="Seconds per diagnose probe (default 90)",
    )
    args = ap.parse_args()

    if args.status_only:
        if not args.status_out:
            raise SystemExit("--status-only requires --status-out")
        write_status(
            args.status_out,
            status=args.status,
            note=(
                "Add repo secret AISSTREAM_API_KEY "
                "(https://aisstream.io/ → sign in → API Keys), then re-run "
                "the aisstream-collect workflow."
            ),
        )
        return

    if not args.api_key:
        raise SystemExit(
            "Set AISSTREAM_API_KEY or pass --api-key.\n"
            "Free key: https://aisstream.io/ (sign in → API Keys)"
        )

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if args.diagnose:
        stats = diagnose(args.api_key, seconds_per_probe=args.diagnose_seconds)
        if args.status_out:
            write_status(
                args.status_out,
                status=stats["status"],
                mode="diagnose",
                messages=stats["messages"],
                kept=stats["kept"],
                probes=stats["probes"],
                seconds_per_probe=stats["seconds_per_probe"],
                rate_limited_hits=stats["rate_limited_hits"],
                elapsed_sec=stats["elapsed_sec"],
                note=stats.get("note"),
            )
        if args.fail_if_empty and stats["messages"] <= 0:
            raise SystemExit(
                f"aisstream diagnose empty (status={stats['status']}). "
                f"{stats.get('note') or ''}".strip()
            )
        return

    accepted = load_accepted_names(args.accepted_names, args.trips)
    if args.empty_abort_after is None:
        empty_abort = EMPTY_ABORT_AFTER_SEC if args.hours > 0 else 0.0
    else:
        empty_abort = float(args.empty_abort_after)
    stats = collect(
        args.api_key,
        args.hours,
        args.flush_every,
        accepted,
        args.out_dir,
        empty_abort_after_sec=empty_abort,
    )
    if args.status_out:
        write_status(
            args.status_out,
            status=stats["status"],
            hours=args.hours,
            messages=stats["messages"],
            kept=stats["kept"],
            days=stats["days"],
            out_dir=stats["out_dir"],
            rate_limited_hits=stats["rate_limited_hits"],
            elapsed_sec=stats["elapsed_sec"],
            note=stats.get("note"),
        )
    if args.fail_if_empty and stats["messages"] <= 0:
        raise SystemExit(
            f"aisstream session empty (status={stats['status']}, "
            f"rate_limited_hits={stats['rate_limited_hits']}). "
            f"{stats.get('note') or ''}".strip()
        )


if __name__ == "__main__":
    main()
