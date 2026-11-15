# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Build a month-by-month plan for one year of a rotation.

Input:  a rotation with "crops" ({"crop", "label", "start_month", "end_month",
        "months"}) and its "water" block (water_balance.rotation_water_need),
        the monthly climatology, and optionally the field area.
Output: 12 dicts (January to December): month, crop, activity
        (plant/grow/harvest/fallow), kc (month-average crop coefficient; in a
        shared transition month the ETc/ET0 mix of both crops), etc_mm,
        rain_mm, irrigation_mm, irrigation_m3 (None without area) and an
        optional note.
"""

from engine.scoring import mm_to_m3


def _activity(month: int, c: dict) -> str:
    if month == c["start_month"]:
        return "plant"
    if month == c["end_month"]:
        return "harvest"
    return "grow"


def build_year_plan(rotation: dict, climate_summary: dict, area: dict | None = None) -> list[dict]:
    """Return the 12-month plan; a shared transition month shows the incoming crop with a note."""
    rain_by_month = {m["month"]: m["rain_mm"] for m in climate_summary["monthly_climatology"]}
    water_by_crop = {
        (p["crop"], tuple(p["months"])): {m["month"]: m for m in p["monthly"]}
        for p in rotation["water"]["per_crop"] if p["water_assessed"]
    }

    plan = []
    for month in range(1, 13):
        here = [c for c in rotation["crops"] if month in c["months"]]
        etc = irr = et0 = 0.0
        assessed = True
        for c in here:
            w = water_by_crop.get((c["crop"], tuple(c["months"])))
            if w is None:
                assessed = False
                continue
            etc += w[month]["etc_mm"]
            et0 += w[month]["et0_mm"]
            irr += w[month]["irrigation_mm"]

        if not here:
            entry = {"crop": None, "activity": "fallow", "note": None}
        elif len(here) == 1:
            c = here[0]
            entry = {"crop": c["crop"], "activity": _activity(month, c), "note": None}
        else:
            outgoing = next((c for c in here if c["end_month"] == month), here[0])
            incoming = next((c for c in here if c["start_month"] == month and c is not outgoing), here[-1])
            entry = {
                "crop": incoming["crop"],
                "activity": "plant",
                "note": f"Harvest {outgoing['label']}, then plant {incoming['label']} (shared transition month).",
            }
        if here and not assessed:
            note = "Water need not estimated for a crop without Kc values."
            entry["note"] = f"{entry['note']} {note}" if entry["note"] else note

        irrigation = round(irr, 1) if assessed else None
        plan.append({
            "month": month,
            **entry,
            "kc": round(etc / et0, 2) if here and assessed and et0 > 0 else None,
            "etc_mm": round(etc, 1) if assessed else None,
            "rain_mm": round(rain_by_month[month], 1),
            "irrigation_mm": irrigation,
            "irrigation_m3": round(mm_to_m3(irr, area["value"], area["unit"])) if area and irrigation is not None else None,
        })
    return plan
