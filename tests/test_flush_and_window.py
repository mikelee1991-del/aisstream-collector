"""Partial-shard flush, unique release names, and the fishing-day window."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_aisstream as collector  # noqa: E402


def _row(mmsi: int, minute: int) -> dict:
    return {
        "mmsi": mmsi,
        "base_date_time": f"2026-11-12T22:{minute:02d}:00+00:00",
        "longitude": -118.25,
        "latitude": 33.75,
        "sog": 8.0,
        "cog": 180.0,
        "heading": 180.0,
        "vessel_name": "FREEDOM",
        "call_sign": None,
        "vessel_type": None,
        "status": None,
        "length": None,
        "width": None,
        "draft": None,
        "transceiver": None,
        "vessel_name_norm": "FREEDOM",
        "report_boat_name": "Freedom",
        "date": "2026-11-12",
        "ais_source": "aisstream",
    }


class FlushTests(unittest.TestCase):
    def test_due_on_first_row_interval_or_count(self) -> None:
        self.assertFalse(
            collector.due_for_flush(
                pending=0,
                kept=1,
                elapsed_since_flush=999,
                flush_every=20,
                flush_seconds=120,
                count_just_hit=True,
            )
        )
        self.assertTrue(
            collector.due_for_flush(
                pending=1,
                kept=1,
                elapsed_since_flush=0,
                flush_every=20,
                flush_seconds=120,
                count_just_hit=True,
            )
        )
        self.assertFalse(
            collector.due_for_flush(
                pending=5,
                kept=5,
                elapsed_since_flush=30,
                flush_every=20,
                flush_seconds=120,
                count_just_hit=True,
            )
        )
        self.assertTrue(
            collector.due_for_flush(
                pending=5,
                kept=5,
                elapsed_since_flush=120,
                flush_every=20,
                flush_seconds=120,
                count_just_hit=False,
            )
        )
        self.assertTrue(
            collector.due_for_flush(
                pending=1,
                kept=20,
                elapsed_since_flush=1,
                flush_every=20,
                flush_seconds=120,
                count_just_hit=True,
            )
        )

    def test_flush_writes_partial_and_second_run_does_not_clobber(self) -> None:
        import pandas as pd

        with self._tmpdir() as out:
            first = {"2026-11-12": [_row(367621160, 1)]}
            collector.flush_day_buffers(first, out, "111")
            self.assertEqual(first["2026-11-12"], [])
            path_a = out / "ais_2026-11-12_111.parquet"
            self.assertTrue(path_a.exists())
            self.assertEqual(len(pd.read_parquet(path_a)), 1)

            more = {"2026-11-12": [_row(367621160, 2)]}
            collector.flush_day_buffers(more, out, "111")
            self.assertEqual(len(pd.read_parquet(path_a)), 2)

            other = {"2026-11-12": [_row(366855060, 3)]}
            collector.flush_day_buffers(other, out, "222")
            path_b = out / "ais_2026-11-12_222.parquet"
            self.assertTrue(path_b.exists())
            self.assertEqual(len(pd.read_parquet(path_a)), 2)
            self.assertEqual(len(pd.read_parquet(path_b)), 1)
            names = {
                collector.release_asset_name(path_a.name),
                collector.release_asset_name(path_b.name),
            }
            self.assertEqual(
                names,
                {
                    "live-111-ais_2026-11-12.parquet",
                    "live-222-ais_2026-11-12.parquet",
                },
            )
            for name in names:
                self.assertRegex(name, r"ais_\d{4}-\d{2}-\d{2}\.parquet$")

    def test_sigterm_flushes_pending_rows(self) -> None:
        import pandas as pd

        with self._tmpdir() as out:
            buffers = {"2026-11-12": [_row(367621160, 4), _row(367621160, 5)]}
            collector._session.clear()
            collector._session.update(
                buffers=buffers,
                out_dir=out,
                shard_id="sig",
                in_flush=False,
                last_flush=0,
            )
            collector._stop = False
            previous = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, collector._handle_stop)
            try:
                signal.raise_signal(signal.SIGTERM)
                self.assertTrue(collector._stop)
                self.assertEqual(buffers["2026-11-12"], [])
                path = out / "ais_2026-11-12_sig.parquet"
                self.assertEqual(len(pd.read_parquet(path)), 2)
            finally:
                signal.signal(signal.SIGTERM, previous)
                collector._stop = False
                collector._session.clear()

    def test_upload_dry_run_uses_unique_release_names(self) -> None:
        with self._tmpdir() as out:
            collector.flush_day_buffers(
                {"2026-11-12": [_row(367621160, 6)]},
                out,
                "43321010401",
            )
            collector.flush_day_buffers(
                {"2026-11-12": [_row(366855060, 7)]},
                out,
                "43351358592",
            )
            status = out / "status.json"
            status.write_text("{}\n")
            env = os.environ.copy()
            env["UPLOAD_DRY_RUN"] = "1"
            env.pop("GITHUB_RUN_ID", None)
            proc = subprocess.run(
                ["bash", str(ROOT / "scripts" / "upload_live_shards.sh"), str(out), str(status)],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("dry-run ")]
        self.assertIn("dry-run live-43321010401-ais_2026-11-12.parquet", lines)
        self.assertIn("dry-run live-43351358592-ais_2026-11-12.parquet", lines)
        self.assertIn("dry-run status.json", lines)
        self.assertIn("upload_live_shards done files=2", proc.stdout)
        self.assertFalse(list(ROOT.glob("live-*.parquet")))
        self.assertFalse((ROOT / "status.json").exists())

    def test_upload_skips_a_partial_parquet_and_keeps_a_private_copy(self) -> None:
        with self._tmpdir() as out:
            collector.flush_day_buffers(
                {"2026-11-12": [_row(367621160, 8)]},
                out,
                "111",
            )
            partial = out / "ais_2026-11-12_222.parquet"
            partial.write_bytes(b"PAR1" + b"\x00" * 20)
            self.assertFalse(collector.file_is_complete_parquet(partial))
            good = next(out.glob("ais_2026-11-12_111.parquet"))
            self.assertTrue(collector.file_is_complete_parquet(good))
            status = out / "status.json"
            status.write_text('{"status": "ok"}\n')
            env = os.environ.copy()
            env.pop("GITHUB_RUN_ID", None)
            env.pop("UPLOAD_DRY_RUN", None)
            log = out / "gh.log"
            bindir = out / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(
                "#!/bin/bash\n"
                f'printf "%s\\n" "$*" >> "{log}"\n'
                'if [ "$1" = "release" ] && [ "$2" = "view" ]; then exit 0; fi\n'
                'if [ "$1" = "release" ] && [ "$2" = "upload" ]; then\n'
                '  printf "file=%s\\n" "$4" >> "{log}"\n'
                '  printf "head=%s\\n" "$(dd if="$4" bs=1 count=4 2>/dev/null || true)" >> "{log}"\n'
                "  exit 0\n"
                "fi\n"
                "exit 0\n"
            )
            fake_gh.chmod(0o755)
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
            before = set(ROOT.glob("live-*.parquet"))
            proc = subprocess.run(
                ["bash", str(ROOT / "scripts" / "upload_live_shards.sh"), str(out), str(status)],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            recorded = log.read_text()
        self.assertIn("skip partial ais_2026-11-12_222.parquet", proc.stderr)
        self.assertIn("uploaded live-111-ais_2026-11-12.parquet", proc.stdout)
        self.assertNotIn("live-222", proc.stdout)
        self.assertIn("uploaded status.json", proc.stdout)
        self.assertEqual(before, set(ROOT.glob("live-*.parquet")))
        self.assertFalse((ROOT / "status.json").exists())
        self.assertNotIn(str(ROOT / "live-111-ais_2026-11-12.parquet"), recorded)
        self.assertNotIn(str(ROOT / "status.json"), recorded)
        self.assertIn("/tmp/", recorded)
        self.assertIn("live-111-ais_2026-11-12.parquet", recorded)
        self.assertNotIn("live-222-ais_2026-11-12.parquet", recorded)

    def test_write_status_replaces_atomically(self) -> None:
        with self._tmpdir() as out:
            path = out / "status.json"
            collector.write_status(path, status="ok", kept=1)
            first = json.loads(path.read_text())
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["kept"], 1)
            collector.write_status(path, status="ok", kept=2)
            second = json.loads(path.read_text())
            self.assertEqual(second["kept"], 2)
            leftovers = [p.name for p in out.iterdir() if p.name != "status.json"]
            self.assertEqual(leftovers, [])

    def test_stop_background_uploader_kills_the_session(self) -> None:
        with self._tmpdir() as out:
            pidfile = out / "uploader.pid"
            proc = subprocess.Popen(
                [
                    "python3",
                    "-c",
                    "import os, sys\n"
                    "os.setsid()\n"
                    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                    "os.execv('/bin/bash', ['bash', '-c', 'sleep 60 & wait'])\n",
                    str(pidfile),
                ]
            )
            try:
                for _ in range(50):
                    if pidfile.exists() and pidfile.read_text().strip().isdigit():
                        break
                    time.sleep(0.05)
                else:
                    self.fail("uploader pidfile was not written")
                pid = int(pidfile.read_text().strip())
                subprocess.run(
                    ["bash", str(ROOT / "scripts" / "stop_background_uploader.sh"), str(pidfile)],
                    check=True,
                )
                self.assertFalse(pidfile.exists())
                # Reap the zombie before kill -0, which still succeeds on an unreaped child.
                self.assertEqual(proc.wait(timeout=5), -signal.SIGTERM)
                gone = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
                self.assertNotEqual(gone.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_start_background_uploader_is_its_own_session(self) -> None:
        with self._tmpdir() as out:
            pidfile = out / "uploader.pid"
            subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "start_background_uploader.sh"),
                    str(pidfile),
                    str(out),
                    str(out / "status.json"),
                ],
                check=True,
                cwd=ROOT,
            )
            pid = int(pidfile.read_text().strip())
            try:
                pgid = subprocess.check_output(
                    ["ps", "-o", "pgid=", "-p", str(pid)], text=True
                ).strip()
                self.assertEqual(pgid, str(pid))
            finally:
                subprocess.run(
                    ["bash", str(ROOT / "scripts" / "stop_background_uploader.sh"), str(pidfile)],
                    check=True,
                )
            gone = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
            self.assertNotEqual(gone.returncode, 0)

    def _tmpdir(self):
        import tempfile

        class _Dir:
            def __enter__(self):
                self._tmp = tempfile.TemporaryDirectory()
                return Path(self._tmp.__enter__())

            def __exit__(self, exc_type, exc, tb):
                return self._tmp.__exit__(exc_type, exc, tb)

        return _Dir()


class FishingWindowTests(unittest.TestCase):
    def _decide(self, when: str, planned: str = "5.75") -> tuple[str, str]:
        from datetime import datetime, timezone

        epoch = int(datetime.fromisoformat(when).replace(tzinfo=timezone.utc).timestamp())
        proc = subprocess.run(
            ["bash", str(ROOT / "scripts" / "fishing_window.sh"), planned, str(epoch)],
            check=True,
            capture_output=True,
            text=True,
        )
        parts = proc.stdout.strip().split()
        return parts[0], parts[1] if len(parts) > 1 else ""

    def test_quiet_hours_skip_and_the_open_is_capped_at_03_utc(self) -> None:
        # Overnight, including one minute before 09:00 UTC: no socket.
        # An on-time 05:17 run is in this quiet window and exits.
        self.assertEqual(self._decide("2026-11-12T08:00:00"), ("skip", ""))
        self.assertEqual(self._decide("2026-11-12T08:59:00"), ("skip", ""))
        # 09:00 UTC is open, so the on-time 09:17 run collects and is
        # already up at 12:00. 10:29 used to be the last quiet minute.
        action, hours = self._decide("2026-11-12T09:00:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 5.75, places=2)
        action, hours = self._decide("2026-11-12T09:17:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 5.75, places=2)
        action, hours = self._decide("2026-11-12T10:29:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 5.75, places=2)
        # A huge plan is cut to 345 minutes so it fits the 350-minute step.
        action, hours = self._decide("2026-11-12T09:17:00", planned="18")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 5.75, places=2)
        self.assertLessEqual(float(hours) * 60, 345)
        # Fishing day open, still under the 5.75h plan.
        action, hours = self._decide("2026-11-12T12:00:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 5.75, places=2)
        # Stop at 03:00 UTC, not 5.75h later.
        action, hours = self._decide("2026-11-12T23:00:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 4.0, places=2)
        # One hour left in the fishing day.
        action, hours = self._decide("2026-11-13T02:00:00")
        self.assertEqual(action, "collect")
        self.assertAlmostEqual(float(hours), 1.0, places=2)
        # Closed at 03:00 UTC.
        self.assertEqual(self._decide("2026-11-13T03:00:00"), ("skip", ""))


if __name__ == "__main__":
    unittest.main()
