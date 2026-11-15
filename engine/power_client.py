# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Fetch and cache NASA POWER monthly climate data for a point.

Input:  latitude, longitude and a year range.
Output: {"meta": {...}, "monthly": [{year, month, t2m, t2m_max, t2m_min,
        t2m_range, rh2m, ws2m, srad, precip}, ...]} with fill values as None
        and the API's annual aggregates (month "13") dropped.

The raw API response is cached untouched as JSON under data/cache/, so
repeated requests for the same point and period do not hit the API again.

Note on temperatures: in the monthly API, T2M_MAX / T2M_MIN are the most
extreme values of the month, not means of daily values. T2M_RANGE is the
monthly mean of the daily range, which climate.py uses to estimate mean
daily Tmax / Tmin for FAO-56.
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
TIMEOUT_S = 30
RETRIES = 2
FALLBACK_MAX_DEG = 0.5

# Our field name -> POWER parameter name.
FIELDS = {
    "t2m": "T2M",
    "t2m_max": "T2M_MAX",
    "t2m_min": "T2M_MIN",
    "t2m_range": "T2M_RANGE",
    "rh2m": "RH2M",
    "ws2m": "WS2M",
    "srad": "ALLSKY_SFC_SW_DWN",
    "precip": "PRECTOTCORR",
}

SPATIAL_RESOLUTION_NOTE = (
    "POWER values are grid-cell averages, not point measurements. Per the POWER methodology "
    "docs (Release 9 section): \"That resolution is 1.0° latitude by 1.0° longitude for the "
    "radiation data sets and ½° latitude by ⅝° longitude for the meteorological data sets.\" "
    "Source: https://power.larc.nasa.gov/docs/methodology/ (the Release 10 section does not "
    "restate the resolution)."
)

_CACHE_RE = re.compile(r"power_(-?\d+\.\d+)_(-?\d+\.\d+)_(\d{4})_(\d{4})\.json$")


class PowerUnavailableError(RuntimeError):
    """No POWER data could be obtained from the API or the cache."""


class PowerRequestError(ValueError):
    """The POWER API rejected the request (e.g. bad parameter or period)."""


def cache_path(lat: float, lon: float, start_year: int, end_year: int) -> Path:
    return CACHE_DIR / f"power_{lat:.2f}_{lon:.2f}_{start_year}_{end_year}.json"


def build_url(lat: float, lon: float, start_year: int, end_year: int) -> str:
    params = {
        "parameters": ",".join(FIELDS.values()),
        "community": "AG",
        "longitude": f"{lon:.2f}",
        "latitude": f"{lat:.2f}",
        "start": start_year,
        "end": end_year,
        "format": "JSON",
    }
    return requests.Request("GET", BASE_URL, params=params).prepare().url


def parse_response(
    data: dict, url: str, lat: float, lon: float, fetched_at: str, is_cached: bool
) -> dict:
    """Turn a raw POWER monthly JSON response into {"meta", "monthly"}."""
    fill = data["header"]["fill_value"]
    params = data["properties"]["parameter"]
    lon_api, lat_api, elevation = data["geometry"]["coordinates"]

    rows: dict[tuple[int, int], dict] = {}
    for field, name in FIELDS.items():
        for key, value in params[name].items():
            year, month = int(key[:4]), int(key[4:])
            if month == 13:  # annual aggregate
                continue
            row = rows.setdefault((year, month), {"year": year, "month": month})
            row[field] = None if value == fill else value

    return {
        "meta": {
            "source": "NASA POWER",
            "url": url,
            "lat_requested": lat,
            "lon_requested": lon,
            "grid_cell": {"lat": lat_api, "lon": lon_api},
            "elevation_m": elevation,
            "spatial_resolution_note": SPATIAL_RESOLUTION_NOTE,
            "fetched_at": fetched_at,
            "is_cached": is_cached,
            "units": {name: data["parameters"][name]["units"] for name in FIELDS.values()},
            "fill_value": fill,
            "time_standard": data["header"].get("time_standard"),
            "data_sources": data["header"].get("sources"),
            "api_messages": data.get("messages", []),
        },
        "monthly": [rows[k] for k in sorted(rows)],
    }


def _load_cache(path: Path) -> tuple[dict, str]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return data, mtime.isoformat()


def _nearest_cache(lat: float, lon: float, start_year: int, end_year: int) -> tuple[Path, float] | None:
    best = None
    for path in CACHE_DIR.glob("power_*.json"):
        m = _CACHE_RE.search(path.name)
        if not m or (int(m[3]), int(m[4])) != (start_year, end_year):
            continue
        dist = math.hypot(float(m[1]) - lat, float(m[2]) - lon)
        if dist <= FALLBACK_MAX_DEG and (best is None or dist < best[1]):
            best = (path, dist)
    return best


def _request(url: str) -> dict:
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(url, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            last_err = e
        else:
            if 400 <= resp.status_code < 500:
                raise PowerRequestError(f"POWER rejected the request ({resp.status_code}): {resp.text[:500]}")
            if resp.ok:
                return resp.json()
            last_err = RuntimeError(f"HTTP {resp.status_code}")
        if attempt < RETRIES:
            time.sleep(2**attempt)
    raise ConnectionError(f"POWER request failed after {RETRIES + 1} attempts: {last_err}")


def fetch_monthly(lat: float, lon: float, start_year: int, end_year: int) -> dict:
    """Return NASA POWER monthly data for (lat, lon) over [start_year, end_year].

    Coordinates are rounded to 0.01 degrees. Reads data/cache/ first; otherwise
    calls the API and caches the raw response. If the network fails, falls back
    to the nearest cached point within 0.5 degrees (adds "fallback_note" to
    meta), else raises PowerUnavailableError.
    """
    lat, lon = round(lat, 2), round(lon, 2)
    url = build_url(lat, lon, start_year, end_year)
    path = cache_path(lat, lon, start_year, end_year)

    if path.exists():
        data, fetched_at = _load_cache(path)
        return parse_response(data, url, lat, lon, fetched_at, is_cached=True)

    try:
        data = _request(url)
    except ConnectionError as e:
        nearest = _nearest_cache(lat, lon, start_year, end_year)
        if nearest is None:
            raise PowerUnavailableError(
                f"NASA POWER unreachable and no cached data within {FALLBACK_MAX_DEG}° "
                f"of ({lat}, {lon}) for {start_year}-{end_year}: {e}"
            ) from e
        fb_path, dist = nearest
        data, fetched_at = _load_cache(fb_path)
        out = parse_response(data, url, lat, lon, fetched_at, is_cached=True)
        out["meta"]["fallback_note"] = (
            f"NASA POWER was unreachable; using cached data from {fb_path.name} "
            f"({dist:.2f}° from the requested point)."
        )
        return out

    fetched_at = datetime.now(timezone.utc).isoformat()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return parse_response(data, url, lat, lon, fetched_at, is_cached=False)
