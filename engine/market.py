# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Indicative gross revenue of a rotation (yield x price), from a regional market table.

Input:  a rotation ({"crops": [{"crop", "label", ...}], optional "n_years"}),
        a market table (data/market_*.json, linked from a regional pack by
        "market_table") and optionally the field area.
Output: per-crop yield, price and revenue per hectare, per feddan and for the
        given area; the rotation total per cycle and per year; "complete" (every
        crop has a yield and a price) and "missing".

This is GROSS revenue: no production costs. It is shown next to the scores but
is not a score and not a priority unless a pack's market table is complete
for every crop in the pack (see revenue_is_scoreable).
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HA_PER_UNIT_DEFAULT = {"hectare": 1.0, "feddan": 0.42, "acre": 0.4047}
GROSS_NOTE = "Indicative gross revenue = yield x price; production costs are not included."


def load_market(pack: dict | None, data_dir: Path = DATA_DIR) -> dict | None:
    """The market table linked from ``pack["market_table"]``, or None."""
    name = (pack or {}).get("market_table")
    if not name:
        return None
    path = data_dir / name
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _ha_per_unit(market: dict) -> dict:
    conv = market.get("unit_conversions", {})
    out = dict(HA_PER_UNIT_DEFAULT)
    if conv.get("feddan_to_hectare"):
        out["feddan"] = conv["feddan_to_hectare"]
    return out


def _price_key(market: dict) -> str:
    return f"price_{market['currency'].lower()}_per_t"


def basis_text(market: dict) -> str:
    """Short statement of what the numbers are, for explanations."""
    return market.get("basis_short") or (
        f"{market.get('price_snapshot', 'price snapshot')}, national average yields, costs not included"
    )


def unavailable(reason: str) -> dict:
    return {
        "available": False, "complete": False, "scored": False, "currency": None, "price_snapshot": None,
        "per_crop": [], "missing": [], "total_per_ha": None, "total_per_feddan": None,
        "total_for_area": None, "annual_per_ha": None, "annual_per_feddan": None, "annual_for_area": None,
        "n_years": None, "area": None, "revenue_change_pct": None, "notes": [reason], "status": None,
    }


def rotation_revenue(rotation: dict, market: dict | None, area: dict | None = None) -> dict:
    """Per-crop and total indicative gross revenue for one rotation cycle (and per year)."""
    if market is None:
        return unavailable("No market table for this region; revenue not estimated.")
    ha = _ha_per_unit(market)
    pkey = _price_key(market)
    n_years = rotation.get("n_years") or 1
    per_crop, missing, notes = [], [], [GROSS_NOTE]
    for c in rotation["crops"]:
        entry = market["crops"].get(c["crop"]) or {}
        y, p = entry.get("yield_t_per_ha"), entry.get(pkey)
        gaps = [k for k, v in (("yield", y), ("price", p)) if v is None]
        if c["crop"] == "fallow":
            y, p, gaps = 0.0, 0.0, []
        rev_ha = y * p if not gaps else None
        if gaps:
            missing.append({"crop": c["crop"], "label": c.get("label", c["crop"]), "missing": gaps})
        if entry.get("note") and c["crop"] == "rice":
            notes.append(f"{c.get('label', 'Rice')}: {entry['note']}")
        per_crop.append({
            "crop": c["crop"], "label": c.get("label", c["crop"]), "year": c.get("year", 1),
            "yield_t_per_ha": y, "price_per_t": p,
            "revenue_per_ha": None if rev_ha is None else round(rev_ha),
            "revenue_per_feddan": None if rev_ha is None else round(rev_ha * ha["feddan"]),
            "revenue_for_area": (None if rev_ha is None or not area
                                 else round(rev_ha * area["value"] * ha[area["unit"]])),
            "yield_basis": entry.get("yield_basis"), "price_basis": entry.get("price_basis"),
            "note": entry.get("note"), "missing": gaps,
        })
    complete = not missing
    total_ha = sum(p["revenue_per_ha"] for p in per_crop) if complete else None

    def scaled(factor: float) -> float | None:
        return None if total_ha is None else round(total_ha * factor)

    area_factor = area["value"] * ha[area["unit"]] if area else None
    return {
        "available": True,
        "complete": complete,
        "scored": False,
        "currency": market["currency"],
        "price_snapshot": market.get("price_snapshot"),
        "status": market.get("status"),
        "per_crop": per_crop,
        "missing": missing,
        "n_years": n_years,
        "total_per_ha": scaled(1.0),
        "total_per_feddan": scaled(ha["feddan"]),
        "total_for_area": scaled(area_factor) if area_factor else None,
        "annual_per_ha": scaled(1.0 / n_years),
        "annual_per_feddan": scaled(ha["feddan"] / n_years),
        "annual_for_area": scaled(area_factor / n_years) if area_factor else None,
        "area": area,
        "revenue_change_pct": None,
        "notes": notes,
    }


def add_change_vs_current(rotations: list[dict], current: dict) -> None:
    """Set revenue_change_pct (annual, versus the current rotation) where both are complete."""
    base = current["revenue"]
    for r in rotations:
        rev = r["revenue"]
        if rev["complete"] and base["complete"] and base["annual_per_ha"]:
            rev["revenue_change_pct"] = round(100 * (rev["annual_per_ha"] - base["annual_per_ha"]) / base["annual_per_ha"], 1)
        else:
            rev["revenue_change_pct"] = None


def revenue_is_scoreable(pack: dict | None, market: dict | None) -> bool:
    """True only when the market table has yield and price for every crop in every pack rotation."""
    if not pack or not market or not market.get("revenue_scoreable", False):
        return False
    pkey = _price_key(market)
    for rot in pack.get("typical_rotations", []):
        for c in rot["crops"]:
            if c["crop"] == "fallow":
                continue
            e = market["crops"].get(c["crop"]) or {}
            if e.get("yield_t_per_ha") is None or e.get(pkey) is None:
                return False
    return True
