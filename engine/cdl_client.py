# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Field history from the USDA NASS Cropland Data Layer (CDL) via the CropScape web service.

Input:  latitude, longitude (contiguous US only).
Output: the CDL class for the last N available years at that 30 m pixel,
        mapped to Field Shift crop ids (data/cdl_crop_map.json, built from the
        official NASS legend), and the rotation inferred as the shortest
        repeating sequence.

Service (verified against https://nassgeodata.gmu.edu/CropScape/devhelp/getexamples.html
and real responses on 2026-11-15):
  GET https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue?year=YYYY&x=X&y=Y
  x, y in "USA Contiguous Albers Equal Area Conic (USGS version)" = EPSG:5070.
  Response: XML <Result>{x: ..., y: ..., value: 1, category: "Corn", color: "#FFD400"}</Result>
  (not JSON: unquoted keys). A year outside the archive returns a fault
  "Error: The year must be between 1997 and 2025." which gives the latest year.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
MAP_PATH = ROOT / "data" / "cdl_crop_map.json"
URL = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue"
SOURCE = "USDA NASS Cropland Data Layer (CropScape GetCDLValue)"
N_YEARS = 6
MAX_PERIOD = 3
TIMEOUT_S = 30
RETRIES = 3
# Rough contiguous-US box; the CDL does not cover Alaska, Hawaii or other countries.
CONUS = {"lat_min": 24.4, "lat_max": 49.5, "lon_min": -125.0, "lon_max": -66.9}
FALLOW = "fallow"
RESOLUTION_NOTE = (
    "The CDL is a 30 m classification of satellite imagery; a single pixel can be misclassified, "
    "especially at field edges, and accuracy varies by crop and state."
)
_VALUE_RE = re.compile(r'value:\s*(-?\d+)\s*,\s*category:\s*"([^"]*)"')
_RANGE_RE = re.compile(r"between\s+(\d{4})\s+and\s+(\d{4})")


class CdlUnavailableError(RuntimeError):
    """CropScape could not be reached or returned something unexpected."""


def in_conus(lat: float, lon: float) -> bool:
    return CONUS["lat_min"] <= lat <= CONUS["lat_max"] and CONUS["lon_min"] <= lon <= CONUS["lon_max"]


def to_albers(lat: float, lon: float) -> tuple[float, float]:
    """(x, y) in EPSG:5070 (NAD83 / Conus Albers), the CDL projection."""
    from pyproj import Transformer

    return Transformer.from_crs(4326, 5070, always_xy=True).transform(lon, lat)


def load_map(path: Path = MAP_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def parse_value(text: str) -> tuple[int, str]:
    """(class code, category) from a GetCDLValue response; raises CdlUnavailableError otherwise."""
    m = _VALUE_RE.search(text)
    if not m:
        raise CdlUnavailableError(f"Unexpected CropScape response: {text[:200]!r}")
    return int(m.group(1)), m.group(2)


def parse_year_range(text: str) -> tuple[int, int] | None:
    m = _RANGE_RE.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _get(params: dict, timeout_s: float) -> str:
    last = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(2 * attempt)
        try:
            r = requests.get(URL, params=params, timeout=timeout_s)
            return r.text
        except requests.RequestException as e:
            last = e
    raise CdlUnavailableError(f"CropScape not reachable after {RETRIES} attempts: {last}")


def latest_year(x: float, y: float, timeout_s: float = TIMEOUT_S) -> int:
    """Ask for next year's CDL; the fault message states the available range."""
    probe = datetime.now(timezone.utc).year + 1
    text = _get({"year": probe, "x": f"{x:.3f}", "y": f"{y:.3f}"}, timeout_s)
    rng = parse_year_range(text)
    if rng:
        return rng[1]
    parse_value(text)  # the probe year exists
    return probe


def classify(code: int, crop_map: dict) -> dict:
    entry = crop_map["crop_map"].get(str(code))
    if entry:
        crops = entry["crops"]
        kind = "fallow" if crops == [FALLOW] else "crop"
        return {"kind": kind, "crops": [] if kind == "fallow" else crops, "legend_name": entry["legend_name"]}
    if str(code) in crop_map["non_crop_codes"]:
        return {"kind": "non_crop", "crops": None, "legend_name": crop_map["non_crop_codes"][str(code)]}
    return {"kind": "other_crop", "crops": None, "legend_name": None}


def _cache_path(lat: float, lon: float) -> Path:
    return CACHE_DIR / f"cdl_{lat:.4f}_{lon:.4f}.json"


def fetch_history(lat: float, lon: float, n_years: int = N_YEARS, timeout_s: float = TIMEOUT_S,
                  use_cache: bool = True) -> dict:
    """Raw CDL class per year for the last ``n_years`` available years (cached)."""
    if not in_conus(lat, lon):
        raise ValueError("The Cropland Data Layer covers the contiguous United States only.")
    path = _cache_path(lat, lon)
    if use_cache and path.exists():
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        data["meta"]["is_cached"] = True
        return data
    x, y = to_albers(lat, lon)
    last = latest_year(x, y, timeout_s)
    years, failed = [], []
    for yr in range(last - n_years + 1, last + 1):
        try:
            text = _get({"year": yr, "x": f"{x:.3f}", "y": f"{y:.3f}"}, timeout_s)
            code, category = parse_value(text)
            years.append({"year": yr, "value": code, "category": category, "raw": text})
        except CdlUnavailableError as e:
            failed.append({"year": yr, "error": str(e)})
        time.sleep(0.5)
    if not years:
        raise CdlUnavailableError(f"No CDL year could be read: {failed}")
    data = {
        "meta": {"source": SOURCE, "url": URL, "lat": lat, "lon": lon, "x_albers": x, "y_albers": y,
                 "crs": "EPSG:5070", "latest_year": last, "fetched_at": datetime.now(timezone.utc).isoformat(),
                 "failed_years": failed, "is_cached": False},
        "years": years,
    }
    if not failed:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    return data


def _period(tokens: list) -> tuple[int, int] | None:
    """(period, mismatches) for the shortest period 1..MAX_PERIOD with the fewest mismatches."""
    best = None
    for p in range(1, min(MAX_PERIOD, len(tokens) - 1) + 1):
        pairs = [(a, b) for a, b in zip(tokens, tokens[p:]) if a is not None and b is not None]
        if not pairs:
            continue
        mism = sum(a != b for a, b in pairs)
        if best is None or mism < best[1]:
            best = (p, mism)
    return best


def _signature(cycle: list[list[str]]) -> list[str]:
    return [c for year in cycle for c in (year or [FALLOW])]


def infer_rotation(history: dict, crop_map: dict, pack: dict | None = None) -> dict:
    """Yearly crops and the inferred rotation (shortest repeating sequence), with a confidence note."""
    yearly = []
    for y in sorted(history["years"], key=lambda r: r["year"]):
        c = classify(y["value"], crop_map)
        yearly.append({"year": y["year"], "cdl_value": y["value"], "cdl_category": y["category"], **c})
    n = len(yearly)
    non_crop = sum(y["kind"] == "non_crop" for y in yearly)
    base = {"status": "not_detected", "yearly": yearly, "rotation": None, "cycle": None, "n_years": None,
            "period_mismatches": None, "confidence": "none", "matched_pack_rotation": None,
            "source": SOURCE, "resolution_note": RESOLUTION_NOTE}
    if n < 3:
        return {**base, "confidence_note": f"Only {n} CDL years could be read; at least 3 are needed."}
    if non_crop > n / 2:
        cats = sorted({y["cdl_category"] for y in yearly if y["kind"] == "non_crop"})
        return {**base, "status": "not_cropland",
                "confidence_note": f"This pixel is not cropland in most years ({', '.join(cats)}). "
                                   "Click inside a field."}
    tokens = [tuple(y["crops"]) if y["kind"] in ("crop", "fallow") else None for y in yearly]
    best = _period(tokens)
    if best is None:
        return {**base, "confidence_note": "No repeating crop sequence could be found."}
    p, mism = best
    known = [t for t in tokens[-p:]]
    if any(t is None for t in known):
        return {**base, "confidence_note": "A recent year has a crop class Field Shift does not model."}
    cycle = [list(t) for t in known]  # most recent full period, chronological
    if not any(cycle):
        return {**base, "confidence_note": "Every recent year is fallow or idle."}
    # rotate so the cycle matches a pack rotation if one exists, else start with a crop year
    shifts = [cycle[i:] + cycle[:i] for i in range(p)]
    matched = None
    if pack:
        for rot in pack.get("typical_rotations", []):
            sig = _pack_signature(rot)
            for s in shifts:
                if _signature(s) == sig:
                    cycle, matched = s, rot["id"]
                    break
            if matched:
                break
    if not matched:
        while not cycle[0]:
            cycle = cycle[1:] + cycle[:1]
    cycles_seen = n / p
    unmodelled = sum(t is None for t in tokens)
    if mism == 0 and cycles_seen >= 2 and not unmodelled:
        confidence = "high"
    elif mism <= 1 and cycles_seen >= 2:
        confidence = "medium"
    else:
        confidence = "low"
    names = " → ".join(", ".join(y) if y else "fallow" for y in cycle)
    note = (f"Detected from the USDA Cropland Data Layer {yearly[0]['year']}–{yearly[-1]['year']}: a {p}-year "
            f"cycle ({names}) seen {cycles_seen:.1f} times with {mism} year(s) that break the pattern. "
            f"Confidence {confidence}. {RESOLUTION_NOTE}")
    return {**base, "status": "detected", "rotation": _signature(cycle), "cycle": cycle, "n_years": p,
            "period_mismatches": mism, "confidence": confidence, "confidence_note": note,
            "matched_pack_rotation": matched}


def _pack_signature(rot: dict) -> list[str]:
    n = rot.get("n_years") or max((c.get("year", 1) for c in rot["crops"]), default=1)
    sig = []
    for y in range(1, n + 1):
        sig += [c["crop"] for c in rot["crops"] if c.get("year", 1) == y] or [FALLOW]
    return sig
