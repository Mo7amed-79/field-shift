# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Regional packs: local agronomic knowledge layered on the global engine.

Input:  latitude, longitude.
Output: the regional pack (data/regional/*.json) whose bounding box contains
        the point, or None. A pack can give a crop calendar (sowing and
        harvest months per crop) that overrides the global window search.
"""

import json
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "regional"
VERIFIED = "verified"


def load_packs(packs_dir: Path | None = None) -> list[dict]:
    packs = []
    for path in sorted((packs_dir or PACKS_DIR).glob("*.json")):
        with path.open(encoding="utf-8") as f:
            packs.append(json.load(f))
    return packs


def find_pack(lat: float, lon: float) -> dict | None:
    """Return the first pack whose bounding box contains (lat, lon), or None."""
    for pack in load_packs():
        b = pack["bbox"]
        if b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]:
            return pack
    return None


def calendar_entry(pack: dict | None, crop_id: str) -> dict | None:
    if not pack:
        return None
    return next((e for e in pack.get("crop_calendar", []) if e["crop"] == crop_id), None)


def is_verified(entry: dict) -> bool:
    return entry.get("status") == VERIFIED
