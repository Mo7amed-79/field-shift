# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Fetch and cache NASA POWER monthly data (1991-2025), SoilGrids topsoil and MODIS NDVI for the demo points.

Run from the project root: python scripts/precache_demo.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.power_client import PowerRequestError, PowerUnavailableError, fetch_monthly  # noqa: E402
from engine.soil_client import SoilUnavailableError, fetch_soil  # noqa: E402
from engine.ndvi_client import NdviUnavailableError, fetch_ndvi_series  # noqa: E402

START_YEAR, END_YEAR = 1991, 2025
# SoilGrids often queues requests for 30-70 s; this is a batch job, so wait longer than the API does.
SOIL_TIMEOUT_S = 120
# The ORNL MODIS API often takes 40-60 s per request (and returns 502/503 under load).
NDVI_TIMEOUT_S = 180
DEMO_POINTS = Path(__file__).resolve().parent.parent / "data" / "demo_points.json"
POINTS = [(p["label"], p["lat"], p["lon"]) for p in json.loads(DEMO_POINTS.read_text(encoding="utf-8"))["points"]]


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # show progress even when piped or stopped early
    failures = 0
    for i, (name, lat, lon) in enumerate(POINTS):
        if i:
            time.sleep(1)
        try:
            raw = fetch_monthly(lat, lon, START_YEAR, END_YEAR)
            meta = raw["meta"]
            gaps = sum(1 for r in raw["monthly"] if None in r.values())
            print(
                f"POWER {'cached ' if meta['is_cached'] else 'fetched'} {name:<30} {lat:.2f},{lon:.2f}  "
                f"elev {meta['elevation_m']} m  {len(raw['monthly'])} months  {gaps} with gaps"
            )
        except (PowerUnavailableError, PowerRequestError) as e:
            failures += 1
            print(f"POWER FAIL    {name:<30} {lat:.2f},{lon:.2f}  {e}")

        time.sleep(1)
        try:
            soil = fetch_soil(lat, lon, timeout_s=SOIL_TIMEOUT_S)
            meta = soil["meta"]
            print(
                f"SOIL  {'cached ' if meta['is_cached'] else 'fetched'} {name:<30} {lat:.2f},{lon:.2f}  "
                f"{soil['texture_class']}" + (f"  NOTE: {meta['note']}" if meta["note"] else "")
            )
        except SoilUnavailableError as e:
            failures += 1
            print(f"SOIL  FAIL    {name:<30} {lat:.2f},{lon:.2f}  {e}")

        t0 = time.monotonic()
        try:
            ndvi = fetch_ndvi_series(lat, lon, timeout_s=NDVI_TIMEOUT_S)
            meta = ndvi["meta"]
            print(
                f"NDVI  {'cached ' if meta['is_cached'] else 'fetched'} {name:<30} {lat:.2f},{lon:.2f}  "
                f"{meta['period']}  {meta['points_kept']}/{meta['dates_requested']} dates  "
                f"{meta['request_count']} requests  {meta['elapsed_s'] if not meta['is_cached'] else 0:.0f} s "
                f"(wall {time.monotonic() - t0:.0f} s)"
            )
        except NdviUnavailableError as e:
            failures += 1
            print(f"NDVI  FAIL    {name:<30} {lat:.2f},{lon:.2f}  after {time.monotonic() - t0:.0f} s: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
