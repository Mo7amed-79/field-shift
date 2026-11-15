# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Print the rotation comparison for the demo points, from the cache.

Run from the project root (after scripts/precache_demo.py):
    python scripts/demo_report.py [label-substring] [--rotation rice,wheat] [--delta 2] [--cut 20]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import ndvi_client, regional, water_balance  # noqa: E402
from engine.pipeline import run_compare  # noqa: E402
from engine.power_client import fetch_monthly  # noqa: E402
from engine.soil_client import SoilUnavailableError, fetch_soil  # noqa: E402

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _windows(r: dict) -> str:
    return " · ".join(f"{w['label']} {MONTHS[w['start_month'] - 1]}–{MONTHS[w['end_month'] - 1]}" for w in r["windows"])


def _row(r: dict, tail: str) -> str:
    s = r["scores"]
    mark = " (current)" if r["is_current"] else ""
    need = "n/a" if r["water_mm_per_year"] is None else f"{r['water_mm_per_year']:.0f}"
    sav = "n/a" if r["water_saving_pct"] is None else f"{r['water_saving_pct']:+.1f}%"
    w = "n/a" if s["water"] is None else f"{s['water']:.0f}"
    return (f"{r['rank']:<5}{(r['name'] + mark):<26}{need:>7}{sav:>8}{w:>7}{s['soil_practices']:>7.0f}"
            f"{s['heat_risk']:>7.0f}{r['total']:>7.1f}  {tail}")


def _ndvi_summary(lat: float, lon: float, current: dict) -> None:
    data = ndvi_client.load_cached(lat, lon)
    if not data:
        print("\n  NDVI: not cached (run scripts/precache_demo.py)")
        return
    m, s = data["meta"], ndvi_client.detect_seasons(data)
    print(f"\n  NDVI ({m['product']}, {m['period']}, {m['points_kept']} composites, {m['request_count']} requests, "
          f"{m['elapsed_s']:.0f} s to fetch, tile {', '.join(m['tiles'])})")
    print("  mean monthly NDVI: " + " ".join(f"{MONTHS[i]} {v:.2f}" for i, v in enumerate(s["monthly_profile"]) if v is not None))
    print("  seasons: " + ("; ".join(
        f"{MONTHS[x['start_month'] - 1]}–{MONTHS[x['end_month'] - 1]} (peak {MONTHS[x['peak_month'] - 1]}, {x['peak_ndvi']:.2f})"
        for x in s["seasons"]) or "none") + f" · seasons_per_year {s['seasons_per_year']}"
          + f" · low cover (<{s['thresholds']['low_cover_ndvi']}): {[MONTHS[i - 1] for i in s['bare_or_low_cover_months']]}")
    check = ndvi_client.compare_with_calendar(s, current)
    print(f"  ndvi_check [{check['status']}]: {check['text']}")
    if check["warning"]:
        print(f"  NDVI WARNING: {check['warning']}")


def report(point: dict, crops: list[dict], rotation: list[str], delta: float, cut: float) -> None:
    lat, lon = point["lat"], point["lon"]
    raw = fetch_monthly(lat, lon, 1991, 2025)
    try:
        soil = fetch_soil(lat, lon)
    except SoilUnavailableError:
        soil = None
    pack = regional.find_pack(lat, lon)
    req = {"current_rotation": rotation, "water_access": point["water_access"],
           "priorities": {"water": 1, "soil": 1, "heat_risk": 1},
           "scenario": {"temp_delta_c": delta, "water_cut_pct": cut}, "area": None}
    out = run_compare(raw, soil, req, crops, water_balance.load_kc(), pack)

    print(f"\n=== {point['label']} ({lat}, {lon}) · {point['water_access']} · mode {out['mode']}"
          f" · pack {out['pack']['id'] if out['pack'] else 'none'} · temp {delta:+g} °C · water cut {cut:g}%")
    print(f"{'rank':<5}{'rotation':<26}{'mm/yr':>7}{'saving':>8}{'water':>7}{'soil*':>7}{'heat*':>7}{'total':>7}  confidence")
    for r in sorted(out["rotations"], key=lambda r: r["rank"]):
        print(_row(r, r["confidence"] + ("  OVER BUDGET" if r.get("exceeds_water_budget") else "")))
    print("  * soil = soil-health practices (proxy); heat = heat-stress exposure (proxy), higher = less exposed")
    if out["current_is_strong"]:
        print("  current_is_strong: true")
    for r in sorted(out["rotations"], key=lambda r: r["rank"]):
        print(f"\n  [{r['name']}] {_windows(r)}")
        print(f"  {r['why']}")
        print(f"  {r['not_assessed']}")
    if out["all_ranked"]:
        print("\n  All pack rotations (all_ranked):")
        for r in out["all_ranked"]:
            print(_row(r, ""))
    for r in out["rotations"]:
        for c in r["caveats"]:
            if "absolute maximum" in c:
                print(f"  NOTE [{r['name']}]: {c}")
    _ndvi_summary(lat, lon, out["rotations"][0])
    print(f"\n  Sensitivity: {out['sensitivity']['note']}")
    for w in out["warnings"]:
        print(f"  WARNING: {w}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("label", nargs="?", default="")
    ap.add_argument("--rotation", default="rice,wheat")
    ap.add_argument("--delta", type=float, default=0.0)
    ap.add_argument("--cut", type=float, default=0.0)
    args = ap.parse_args()
    crops = json.loads((ROOT / "data" / "crops.json").read_text(encoding="utf-8"))
    points = json.loads((ROOT / "data" / "demo_points.json").read_text(encoding="utf-8"))["points"]
    rotation = [c for c in args.rotation.split(",") if c]
    for p in points:
        if args.label.lower() in p["label"].lower():
            report(p, crops, rotation, args.delta, args.cut)
    return 0


if __name__ == "__main__":
    sys.exit(main())
