"""Minimal config for the public aisstream collector.

Keep AIS_BBOX / MMSI maps in sync with FishScraper's scripts/config.py when
those change. deploy/accepted_names.json is the primary boat-name filter.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"

# SoCal coastal bbox (WGS84) — matches FishScraper.
AIS_BBOX = {
    "min_lon": -119.05,
    "max_lon": -117.45,
    "min_lat": 33.20,
    "max_lat": 34.15,
}

MMSI_ALLOWLIST = {
    366855060,
    366977270,
    367621160,
    367550710,
    366977380,
    367038000,
    367034320,
    367158550,
    368014440,
    367655460,
    367095040,
    368089620,
    367169120,
    368078070,
    366915000,
    368269920,
    366849310,
    367175860,
    367576030,
}

MMSI_TO_REPORT_BOAT = {
    366855060: "New Del Mar",
    366977270: "Victory",
    367621160: "Freedom",
    367550710: "Triton",
    366977380: "Freelance",
    367038000: "Ahra-Ahn",
    367034320: "City of Long Beach",
    367158550: "Western Pride",
    368014440: "Monte Carlo",
    367655460: "Native Sun",
    367095040: "Enterprise",
    368089620: "Eldorado",
    367169120: "Toronado",
    368078070: "Thunderbird",
    366915000: "El Patron",
    368269920: "Spitfire",
    366849310: "Dana Pride",
    368370000: "Apollo",
    338068929: "Dreamer",
    367175860: "Independence",
}
