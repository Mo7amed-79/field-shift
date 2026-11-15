# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Fetch and cache topsoil properties for a point from ISRIC SoilGrids v2.0.

Endpoint: https://rest.isric.org/soilgrids/v2.0/properties/query
Input:  latitude, longitude.
Output: depth-weighted 0-30 cm clay/sand/silt %, pH (H2O), organic carbon
        (g/kg), USDA texture class, and meta (source, url, fetched_at,
        is_cached, resolution note, optional note).

Values are converted with the unit info in each response: value in
``mapped_units`` divided by ``d_factor`` gives ``target_units``.
Raw responses are cached untouched under data/cache/.
"""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
TIMEOUT_S = 30
RETRIES = 2
NEIGHBOUR_OFFSET_DEG = 0.005
SECOND_RING_OFFSET_DEG = 0.02
MAX_REQUESTS = 9  # centre + first ring (4) + one full second ring (4)
PROPERTIES = ("clay", "sand", "silt", "phh2o", "soc")
DEPTHS = ("0-5cm", "5-15cm", "15-30cm")
RESOLUTION_NOTE = "250 m modelled estimate, not a field soil test"

# Our field name -> (SoilGrids property, expected target unit)
FIELDS = {
    "clay_pct": ("clay", "%"),
    "sand_pct": ("sand", "%"),
    "silt_pct": ("silt", "%"),
    "ph": ("phh2o", "-"),
    "organic_carbon_g_kg": ("soc", "g/kg"),
}


class SoilUnavailableError(RuntimeError):
    """No SoilGrids values could be obtained for the point (API, cache or neighbours)."""


def cache_path(lat: float, lon: float) -> Path:
    return CACHE_DIR / f"soil_{lat:.3f}_{lon:.3f}.json"


def build_url(lat: float, lon: float) -> str:
    params = [("lon", f"{lon:.3f}"), ("lat", f"{lat:.3f}")]
    params += [("property", p) for p in PROPERTIES]
    params += [("depth", d) for d in DEPTHS]
    params += [("value", "mean")]
    return requests.Request("GET", BASE_URL, params=params).prepare().url


def texture_class_usda(clay: float, sand: float, silt: float) -> str:
    """USDA soil texture class from clay/sand/silt percentages.

    Boundaries of the USDA texture triangle (Soil Science Division Staff, 2017,
    Soil Survey Manual, USDA Handbook 18, Ch. 3), written as the inequalities
    used by the USDA-NRCS soil texture calculator. Fractions are normalised
    to sum to 100 first.
    """
    total = clay + sand + silt
    if total <= 0:
        raise ValueError("clay + sand + silt must be positive")
    clay, sand, silt = (100 * v / total for v in (clay, sand, silt))

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    return "clay"


def parse_response(data: dict) -> dict:
    """Depth-weighted (by layer thickness) 0-30 cm means in target units.

    A field is None when SoilGrids has no value at any depth for it.
    """
    layers = {layer["name"]: layer for layer in data["properties"]["layers"]}
    out: dict = {"units": {}}
    for field, (prop, expected_unit) in FIELDS.items():
        layer = layers[prop]
        um = layer["unit_measure"]
        if um["target_units"] != expected_unit:
            raise ValueError(f"Unexpected SoilGrids unit for {prop}: {um['target_units']!r}")
        out["units"][field] = um["target_units"]
        total = weight = 0.0
        for depth in layer["depths"]:
            value = depth["values"].get("mean")
            if value is None:
                continue
            thickness = depth["range"]["bottom_depth"] - depth["range"]["top_depth"]
            total += thickness * value / um["d_factor"]
            weight += thickness
        out[field] = total / weight if weight else None

    texture = (out["clay_pct"], out["sand_pct"], out["silt_pct"])
    out["texture_class"] = None if None in texture else texture_class_usda(*texture)
    return out


def _request(url: str, timeout_s: float) -> dict:
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(url, timeout=timeout_s)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
        if attempt < RETRIES:
            time.sleep(2 ** (attempt + 1))
    raise ConnectionError(f"SoilGrids request failed after {RETRIES + 1} attempts: {last_err}")


def _get_point(lat: float, lon: float, timeout_s: float, online: bool = True) -> tuple[dict, str, bool]:
    """Raw response for a point: (data, fetched_at, is_cached). Raises ConnectionError."""
    path = cache_path(lat, lon)
    if path.exists():
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return data, mtime.isoformat(), True
    if not online:
        raise ConnectionError("not cached, and skipped the network after an earlier failure")
    data = _request(build_url(lat, lon), timeout_s)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return data, datetime.now(timezone.utc).isoformat(), False


def _has_texture(parsed: dict) -> bool:
    return parsed["texture_class"] is not None


def _ring(lat: float, lon: float, offset: float) -> list[tuple[float, float]]:
    return [
        (round(lat + dlat, 3), round(lon + dlon, 3))
        for dlat, dlon in ((offset, 0), (-offset, 0), (0, offset), (0, -offset))
    ]


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fetch_soil(lat: float, lon: float, timeout_s: float = TIMEOUT_S) -> dict:
    """Return depth-weighted 0-30 cm topsoil properties for (lat, lon).

    Coordinates are rounded to 0.001°. If the cell has no data (urban, water,
    desert), the 4 neighbours at ±0.005° are tried, then 4 at ±0.02°; at most
    MAX_REQUESTS network requests are made (cache reads are free). A "note"
    says how far the returned cell is from the requested point.
    Raises SoilUnavailableError if nothing usable is found online or in cache.
    """
    lat, lon = round(lat, 3), round(lon, 3)
    rings = [[(lat, lon)], _ring(lat, lon, NEIGHBOUR_OFFSET_DEG), _ring(lat, lon, SECOND_RING_OFFSET_DEG)]

    errors = []
    online = True
    requests_made = 0
    for ring in rings:
        for clat, clon in ring:
            if requests_made >= MAX_REQUESTS:
                online = False
            try:
                data, fetched_at, is_cached = _get_point(clat, clon, timeout_s, online)
            except ConnectionError as e:
                if online:
                    requests_made += 1
                errors.append(f"({clat}, {clon}): {e}")
                online = False  # after one network failure, only look at the cache
                continue
            if not is_cached:
                requests_made += 1
            parsed = parse_response(data)
            if not _has_texture(parsed):
                errors.append(f"({clat}, {clon}): SoilGrids returned no data")
                continue

            distance_km = _distance_km(lat, lon, clat, clon)
            note = None
            if (clat, clon) != (lat, lon):
                note = (
                    f"SoilGrids has no data at ({lat}, {lon}) (e.g. urban, water or desert cell); "
                    f"using the neighbouring point ({clat}, {clon}), {distance_km:.1f} km away."
                )
            return {
                **{k: parsed[k] for k in (*FIELDS, "texture_class")},
                "meta": {
                    "source": "ISRIC SoilGrids v2.0",
                    "url": build_url(clat, clon),
                    "lat_requested": lat,
                    "lon_requested": lon,
                    "lat_used": clat,
                    "lon_used": clon,
                    "distance_km": round(distance_km, 2),
                    "depth": "0-30 cm, weighted by layer thickness",
                    "units": parsed["units"],
                    "fetched_at": fetched_at,
                    "is_cached": is_cached,
                    "resolution_note": RESOLUTION_NOTE,
                    "note": note,
                    "network_requests": requests_made,
                },
            }

    raise SoilUnavailableError(
        f"No SoilGrids data for ({lat}, {lon}) or its neighbours within "
        f"±{SECOND_RING_OFFSET_DEG}° ({requests_made} network requests): " + "; ".join(errors)
    )
