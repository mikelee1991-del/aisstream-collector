"""Vessels-of-interest subscribe + record filter. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_aisstream as collector  # noqa: E402
from config import MMSI_ALLOWLIST, MMSI_TO_REPORT_BOAT  # noqa: E402


def _position(mmsi: int, name: str, lat: float = 33.75, lon: float = -118.25) -> dict:
    return {
        "MetaData": {
            "MMSI": mmsi,
            "latitude": lat,
            "longitude": lon,
            "ShipName": name,
            "time_utc": "2026-09-27 18:00:00 UTC",
        },
        "Message": {
            "PositionReport": {"Sog": 8.0, "Cog": 180.0, "TrueHeading": 180},
        },
    }


class FleetFilterTests(unittest.TestCase):
    def test_subscription_is_mmsi_list_inside_socal_bbox(self) -> None:
        fleet = collector.load_fleet_filter(
            ROOT / "deploy" / "accepted_names.json",
            Path("unused"),
        )
        sub = collector.build_subscription("test-key", fleet)
        self.assertEqual(sub["APIKey"], "test-key")
        self.assertEqual(sub["BoundingBoxes"], collector.bbox_for_aisstream())
        self.assertEqual(sub["FilterMessageTypes"], collector.POSITION_MESSAGE_TYPES)
        mmsis = sub["FiltersShipMMSI"]
        self.assertTrue(mmsis)
        self.assertLessEqual(len(mmsis), collector.AISSTREAM_MAX_MMSI_FILTERS)
        self.assertTrue(all(len(m) == 9 and m.isdigit() for m in mmsis))

        payload = json.loads((ROOT / "deploy" / "accepted_names.json").read_text())
        expected = (
            set(MMSI_ALLOWLIST)
            | set(MMSI_TO_REPORT_BOAT)
            | {int(m) for m in payload["mmsi_allowlist"]}
            | {int(k) for k in payload["mmsi_to_report_boat"]}
        )
        self.assertEqual(set(mmsis), {str(m) for m in expected})
        # Config-only (not in the JSON MMSI list) and JSON-listed boats both subscribe.
        self.assertIn("367175860", mmsis)  # Independence
        self.assertIn("338068929", mmsis)  # Dreamer

    def test_json_mmsis_union_config_and_json_names_win(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accepted_names.json"
            path.write_text(
                json.dumps(
                    {
                        "accepted_names": {"CUSTOMBOAT": "Custom Boat"},
                        "mmsi_allowlist": [367000111],
                        "mmsi_to_report_boat": {"366855060": "Renamed Del Mar"},
                    }
                )
            )
            fleet = collector.load_fleet_filter(path, Path("unused"))
        self.assertEqual(fleet.accepted_names["CUSTOMBOAT"], "Custom Boat")
        self.assertIn(367000111, fleet.mmsi_allowlist)
        self.assertIn(367175860, fleet.mmsi_allowlist)
        self.assertEqual(fleet.mmsi_to_report_boat[366855060], "Renamed Del Mar")
        sub = collector.build_subscription("secret", fleet)
        self.assertIn("367000111", sub["FiltersShipMMSI"])
        self.assertNotIn("secret", sub["FiltersShipMMSI"])

    def test_refuses_bbox_only_subscription(self) -> None:
        fleet = collector.FleetFilter(
            accepted_names={"FREEDOM": "Freedom"},
            mmsi_to_report_boat={},
            mmsi_allowlist=set(),
        )
        with self.assertRaises(SystemExit):
            collector.build_subscription("test-key", fleet)

    def test_skips_mmsis_that_are_not_nine_digits(self) -> None:
        fleet = collector.FleetFilter(
            accepted_names={},
            mmsi_to_report_boat={123: "Too Short", 366855060: "New Del Mar"},
            mmsi_allowlist={-1, 1_000_000_000},
        )
        self.assertEqual(fleet.subscribe_mmsis(), ["366855060"])

    def test_missing_allowlist_file_does_not_open_a_socket(self) -> None:
        with self.assertRaises(SystemExit):
            collector.load_fleet_filter(Path("/tmp/does-not-exist-accepted-names.json"), Path("x"))

    def test_records_allowlist_and_accepted_names_only(self) -> None:
        fleet = collector.FleetFilter(
            accepted_names={"AGGRESSOR": "Aggressor"},
            mmsi_to_report_boat={366855060: "New Del Mar"},
            mmsi_allowlist={366855060, 367576030},
        )
        kept = collector.parse_message(_position(366855060, "WEIRD NAME"), fleet)
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept["report_boat_name"], "New Del Mar")

        mmsi_only = collector.parse_message(_position(367576030, "NO NAME MAP"), fleet)
        self.assertIsNotNone(mmsi_only)
        assert mmsi_only is not None
        self.assertIsNone(mmsi_only["report_boat_name"])

        by_name = collector.parse_message(_position(999999999, "Aggressor"), fleet)
        self.assertIsNotNone(by_name)
        assert by_name is not None
        self.assertEqual(by_name["report_boat_name"], "Aggressor")

        self.assertIsNone(collector.parse_message(_position(999999991, "MAERSK DENVER"), fleet))
        self.assertIsNone(
            collector.parse_message(_position(366855060, "New Del Mar", lat=20.0, lon=-118.25), fleet)
        )


if __name__ == "__main__":
    unittest.main()
