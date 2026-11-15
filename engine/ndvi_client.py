# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Fetch MODIS NDVI time series and detect growing seasons.

Source: ORNL DAAC TESViS MODIS/VIIRS subsets REST API,
        https://modis.ornl.gov/rst/api/v1/ , product MOD13Q1 (250 m, 16-day).
The API returns one band per request and at most ten MODIS dates per request,
so fetch_ndvi_series pages through the dates. Band name, scale factor, fill
value and valid range are read from the API (/MOD13Q1/bands and the subset
response). The assembled series is cached under data/cache/.

Quality filtering is NOT applied: the pixel-reliability band would need a
second set of requests (one band per request) on a slow service.

Input:  latitude, longitude and a year range (default: the last 3 full years).
Output: NDVI series, detected seasons, and a comparison with a rotation's
        calendar. NDVI shows when a pixel is green; it does not identify crops.
"""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import requests
from scipy.signal import find_peaks

BASE_URL = "https://modis.ornl.gov/rst/api/v1"
PRODUCT = "MOD13Q1"
BAND = "250m_16_days_NDVI"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
TIMEOUT_S = 30
RETRIES = 2
SLEEP_BETWEEN_S = 0.5
MAX_DATES_PER_REQUEST = 10  # API documentation: "a limit of a maximum ten modis dates per request"
LOW_COVER_NDVI = 0.25  # heuristic threshold for bare or low-cover months
MIN_PROMINENCE = 0.08  # heuristic: minimum rise of a greenness peak above its surroundings
FLAT_RANGE = 0.12  # heuristic: profile range below this looks uncropped
TOOL_CITATION = (
    "ORNL DAAC. 2018. Terrestrial Ecology Subsetting & Visualization Services (TESViS) RESTful Web Service. "
    "ORNL DAAC, Oak Ridge, Tennessee, USA. https://doi.org/10.3334/ORNLDAAC/1600"
)
QUALITY_NOTE = (
    "Quality filtering not applied: the API returns one band per request, so the pixel-reliability band "
    "would double the number of requests. Cloudy composites may lower some NDVI values."
)
NOT_SPECIES_NOTE = "NDVI shows when the pixel is green; it does not identify which crop is grown."
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class NdviUnavailableError(RuntimeError):
    """No NDVI series could be obtained from the API or the cache."""


def default_years() -> tuple[int, int]:
    last = datetime.now(timezone.utc).year - 1
    return last - 2, last


def cache_path(lat: float, lon: float, start_year: int, end_year: int) -> Path:
    return CACHE_DIR / f"ndvi_{lat:.3f}_{lon:.3f}_{start_year}_{end_year}.json"


def partial_path(lat: float, lon: float, start_year: int, end_year: int) -> Path:
    """Progress of an unfinished fetch (band metadata, dates, finished chunks), so a retry resumes."""
    return CACHE_DIR / f"ndvi_{lat:.3f}_{lon:.3f}_{start_year}_{end_year}.partial.json"


def _save_partial(path: Path, progress: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(progress, f)


def _request(path: str, params: dict | None, timeout_s: float, counter: dict) -> object:
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        counter["requests"] += 1
        try:
            resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=timeout_s,
                                headers={"Accept": "application/json"})
            if resp.status_code == 400:
                raise NdviUnavailableError(f"ORNL API rejected the request: {resp.text[:300]}")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
        if attempt < RETRIES:
            time.sleep(2 ** (attempt + 1))
    raise ConnectionError(f"ORNL MODIS API request {path} failed after {RETRIES + 1} attempts: {last_err}")


def band_metadata(bands_response: dict, band: str = BAND) -> dict:
    """Scale factor, fill value and valid range of ``band`` from the /bands response."""
    entry = next(b for b in bands_response["bands"] if b["band"] == band)
    lo, hi = (float(x) for x in entry["valid_range"].split(" to "))
    return {
        "band": band,
        "description": entry.get("description"),
        "units": entry.get("units"),
        "scale_factor": float(entry["scale_factor"]),
        "add_offset": float(entry.get("add_offset") or 0),
        "fill_value": float(entry["fill_value"]),
        "valid_range": [lo, hi],
    }


def date_chunks(dates: list[dict], size: int = MAX_DATES_PER_REQUEST) -> list[list[dict]]:
    return [dates[i:i + size] for i in range(0, len(dates), size)]


def parse_subset(response: dict, meta: dict) -> list[dict]:
    """Scaled NDVI points from one /subset response (single pixel); fill and out-of-range values dropped."""
    scale = float(response.get("scale") or meta["scale_factor"])
    lo, hi = meta["valid_range"]
    points = []
    for row in response["subset"]:
        if row["band"] != meta["band"]:
            continue
        raw = row["data"][0]
        if raw == meta["fill_value"] or not lo <= raw <= hi:
            continue
        points.append({
            "date": row["calendar_date"], "modis_date": row["modis_date"],
            "ndvi": round(raw * scale + meta["add_offset"], 4),
        })
    return points


def load_cached(lat: float, lon: float, start_year: int | None = None, end_year: int | None = None) -> dict | None:
    """The cached series for this point and period, or None (never touches the network)."""
    if start_year is None or end_year is None:
        start_year, end_year = default_years()
    path = cache_path(round(lat, 3), round(lon, 3), start_year, end_year)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    data["meta"]["is_cached"] = True
    return data


def fetch_ndvi_series(
    lat: float, lon: float, start_year: int | None = None, end_year: int | None = None,
    timeout_s: float = TIMEOUT_S,
) -> dict:
    """Return the MOD13Q1 16-day NDVI series for the 250 m pixel at (lat, lon).

    Reads data/cache/ first. Finished chunks are saved as they arrive, so a
    fetch that fails part-way resumes from there on the next call. Raises
    NdviUnavailableError if the API cannot be reached and nothing is cached.
    """
    if start_year is None or end_year is None:
        start_year, end_year = default_years()
    lat, lon = round(lat, 3), round(lon, 3)
    cached = load_cached(lat, lon, start_year, end_year)
    if cached:
        return cached

    ppath = partial_path(lat, lon, start_year, end_year)
    progress = {"chunks": {}, "request_count": 0, "elapsed_s": 0.0}
    if ppath.exists():
        with ppath.open(encoding="utf-8") as f:
            progress = json.load(f)
    counter = {"requests": 0}
    t0 = time.monotonic()
    loc = {"latitude": lat, "longitude": lon}

    def checkpoint() -> None:
        _save_partial(ppath, {**progress, "request_count": progress["request_count"] + counter["requests"],
                              "elapsed_s": progress["elapsed_s"] + time.monotonic() - t0})

    try:
        if "band_metadata" not in progress:
            progress["band_metadata"] = band_metadata(_request(f"{PRODUCT}/bands", None, timeout_s, counter))
            checkpoint()
            time.sleep(SLEEP_BETWEEN_S)
        if "dates" not in progress:
            all_dates = _request(f"{PRODUCT}/dates", loc, timeout_s, counter)["dates"]
            progress["dates"] = [d for d in all_dates if start_year <= int(d["calendar_date"][:4]) <= end_year]
            checkpoint()
        for chunk in date_chunks(progress["dates"]):
            key = chunk[0]["modis_date"]
            if key in progress["chunks"]:
                continue
            time.sleep(SLEEP_BETWEEN_S)
            progress["chunks"][key] = _request(f"{PRODUCT}/subset", {
                **loc, "band": BAND, "startDate": key, "endDate": chunk[-1]["modis_date"],
                "kmAboveBelow": 0, "kmLeftRight": 0,
            }, timeout_s, counter)
            checkpoint()
    except ConnectionError as e:
        checkpoint()
        done = len(progress["chunks"])
        total = len(date_chunks(progress["dates"])) if "dates" in progress else "?"
        raise NdviUnavailableError(
            f"MODIS NDVI unavailable for ({lat}, {lon}) and not cached ({done}/{total} chunks saved; "
            f"the next attempt resumes): {e}"
        ) from e

    meta = progress["band_metadata"]
    dates = progress["dates"]
    series, tiles = [], set()
    for chunk in date_chunks(dates):
        resp = progress["chunks"][chunk[0]["modis_date"]]
        series += parse_subset(resp, meta)
        tiles |= {row.get("tile") for row in resp["subset"]}

    out = {
        "meta": {
            "product": PRODUCT,
            "band": BAND,
            "band_metadata": meta,
            "source": "NASA MODIS MOD13Q1 via ORNL DAAC TESViS REST API",
            "api": BASE_URL,
            "lat": lat, "lon": lon, "pixel": "single 250 m pixel (kmAboveBelow=0, kmLeftRight=0)",
            "tiles": sorted(t for t in tiles if t),
            "period": f"{start_year}–{end_year}",
            "dates_requested": len(dates),
            "points_kept": len(series),
            "request_count": progress["request_count"] + counter["requests"],
            "elapsed_s": round(progress["elapsed_s"] + time.monotonic() - t0, 1),
            "quality_filtering": QUALITY_NOTE,
            "citation": TOOL_CITATION,
            "product_citation": "TODO — add the MOD13Q1 product citation (the TESViS citation page asks for "
                                "both the tool and the product)",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "is_cached": False,
        },
        "series": series,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with cache_path(lat, lon, start_year, end_year).open("w", encoding="utf-8") as f:
        json.dump(out, f)
    ppath.unlink(missing_ok=True)
    return out


def monthly_profile(series: list[dict]) -> list[float | None]:
    """Mean NDVI per calendar month (index 0 = January) over all years."""
    by_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for p in series:
        by_month[int(p["date"][5:7])].append(p["ndvi"])
    return [mean(v) if v else None for v in by_month.values()]


def _fill_circular(profile: list[float | None]) -> list[float]:
    known = [i for i, v in enumerate(profile) if v is not None]
    if not known:
        return [0.0] * 12
    out = []
    for i, v in enumerate(profile):
        if v is not None:
            out.append(v)
            continue
        prev = max((k for k in known if k < i), default=known[-1] - 12)
        nxt = min((k for k in known if k > i), default=known[0] + 12)
        w = (i - prev) / (nxt - prev)
        out.append(profile[prev % 12] * (1 - w) + profile[nxt % 12] * w)
    return out


def _smooth(profile: list[float]) -> list[float]:
    """Circular 1-2-1 moving average."""
    return [0.25 * profile[i - 1] + 0.5 * profile[i] + 0.25 * profile[(i + 1) % 12] for i in range(12)]


def detect_seasons(ndvi_series: dict) -> dict:
    """Greenness seasons from the mean annual NDVI profile.

    Returns {"seasons": [{start_month, peak_month, end_month, peak_ndvi}], "seasons_per_year",
    "monthly_profile", "smoothed_profile", "bare_or_low_cover_months", "looks_cropped", "thresholds"}.
    A season spans the months where smoothed NDVI is above the midpoint between its peak and the
    lowest value on each side.
    """
    raw_profile = monthly_profile(ndvi_series["series"])
    filled = _fill_circular(raw_profile)
    smooth = _smooth(filled)
    looks_cropped = (max(smooth) - min(smooth)) >= FLAT_RANGE and max(smooth) >= LOW_COVER_NDVI

    seasons = []
    if looks_cropped:
        tiled = smooth * 3
        peaks, _ = find_peaks(tiled, prominence=MIN_PROMINENCE)
        for p in sorted({int(p) % 12 for p in peaks if 12 <= p < 24}):
            # walk to the lowest point on each side (circularly), up to 6 months
            left = min(range(1, 7), key=lambda k: smooth[(p - k) % 12])
            right = min(range(1, 7), key=lambda k: smooth[(p + k) % 12])
            thr_l = (smooth[p] + smooth[(p - left) % 12]) / 2
            thr_r = (smooth[p] + smooth[(p + right) % 12]) / 2
            start, end = p, p
            while (start - 1) % 12 != (p - left) % 12 and smooth[(start - 1) % 12] >= thr_l:
                start -= 1
            while (end + 1) % 12 != (p + right) % 12 and smooth[(end + 1) % 12] >= thr_r:
                end += 1
            seasons.append({
                "start_month": start % 12 + 1, "peak_month": p + 1, "end_month": end % 12 + 1,
                "peak_ndvi": round(smooth[p], 3),
            })
    return {
        "seasons": seasons,
        "seasons_per_year": len(seasons),
        "monthly_profile": [None if v is None else round(v, 3) for v in raw_profile],
        "smoothed_profile": [round(v, 3) for v in smooth],
        "bare_or_low_cover_months": [m + 1 for m, v in enumerate(filled) if v < LOW_COVER_NDVI],
        "looks_cropped": looks_cropped,
        "thresholds": {
            "low_cover_ndvi": LOW_COVER_NDVI, "min_peak_prominence": MIN_PROMINENCE, "flat_range": FLAT_RANGE,
            "note": "Heuristic thresholds, not calibrated for this region.",
        },
    }


def _months_label(start: int, end: int) -> str:
    return MONTHS[start - 1] if start == end else f"{MONTHS[start - 1]}–{MONTHS[end - 1]}"


def _peak_label(season: dict, smooth: list[float]) -> str:
    """Peak month, extended to an adjacent month that is within 0.02 NDVI of the peak."""
    p = season["peak_month"] - 1
    prev, nxt = (p - 1) % 12, (p + 1) % 12
    if smooth[p] - smooth[prev] <= 0.02:
        return f"{MONTHS[prev]}–{MONTHS[p]}"
    if smooth[p] - smooth[nxt] <= 0.02:
        return f"{MONTHS[p]}–{MONTHS[nxt]}"
    return MONTHS[p]


def compare_with_calendar(seasons: dict, rotation: dict) -> dict:
    """Does each crop window of ``rotation`` contain a detected NDVI peak? Supporting evidence only."""
    windows = rotation["windows"]
    if not seasons["looks_cropped"]:
        return {
            "status": "not_cropped",
            "per_crop": [],
            "text": "Satellite greenness at this pixel is flat or very low through the year, so it does not "
                    "look like a cropped field.",
            "warning": "The clicked point may not be farmland (or may be a fallow, urban or water pixel). "
                       "Check the location on the map.",
            "note": NOT_SPECIES_NOTE,
        }
    smooth = seasons["smoothed_profile"]
    peaks = seasons["seasons"]
    per_crop = []
    for w in windows:
        hit = [s for s in peaks if s["peak_month"] in w["months"]]
        per_crop.append({
            "crop": w["crop"], "label": w["label"],
            "window": _months_label(w["start_month"], w["end_month"]),
            "contains_peak": bool(hit),
            "peak_months": [s["peak_month"] for s in hit],
        })
    labels = [_peak_label(s, smooth) for s in peaks]
    peaks_text = " and ".join(labels) if labels else "no clear month"
    matched = sum(c["contains_peak"] for c in per_crop)
    if matched == len(per_crop) and len(peaks) == len(per_crop):
        status = "consistent"
        tail = (f"consistent with {len(per_crop)} crop{'s' if len(per_crop) > 1 else ''} a year as in this "
                f"rotation ({'; '.join(c['label'] + ' ' + c['window'] for c in per_crop)}).")
    elif matched:
        status = "partly_consistent"
        missing = [f"{c['label']} ({c['window']})" for c in per_crop if not c["contains_peak"]]
        tail = (f"partly consistent with this rotation: no greenness peak falls in the window of "
                f"{', '.join(missing)}" + (f"; {len(peaks)} peaks were detected" if len(peaks) != len(per_crop) else "") + ".")
    else:
        status = "inconsistent"
        tail = "which does not match this rotation's crop windows."
    return {
        "status": status,
        "per_crop": per_crop,
        "text": f"Satellite greenness at this pixel peaks in {peaks_text}, {tail}",
        "warning": None,
        "note": NOT_SPECIES_NOTE,
    }
