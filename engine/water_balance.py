# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Monthly crop water need versus rainfall (FAO-56 single crop coefficient).

Input:  a crop's growing window (months), monthly climatology (climate.summarize),
        and the crop's Kc values (data/kc_fao56.json).
Output: monthly and seasonal crop evapotranspiration (ETc), effective rain
        and irrigation need, in mm, with the assumptions stated.

Simplifications (stated in every result):
- Kc follows the FAO-56 single crop coefficient curve: initial (kc_ini),
  development (linear kc_ini -> kc_mid), mid-season (kc_mid), late season
  (linear kc_mid -> kc_end). Stage lengths are fractions of the window
  (default 0.15 / 0.25 / 0.40 / 0.20, provisional); monthly Kc is the mean of
  the curve over that month.
- ETc = ET0 x Kc per month; effective rain = 0.8 x rain; irrigation need =
  max(ETc - effective rain, 0).
- A month shared by two crops (harvest of one, sowing of the next) counts at
  half weight for each.
"""

import copy
import json
from pathlib import Path

from engine import climate

KC_PATH = Path(__file__).resolve().parent.parent / "data" / "kc_fao56.json"
EFFECTIVE_RAIN_FRACTION = 0.8

ASSUMPTIONS = [
    "Kc follows the FAO-56 four-stage curve (initial, development, mid-season, late season) with stage lengths "
    "as fractions of the growing window (default 0.15 / 0.25 / 0.40 / 0.20, provisional until replaced with "
    "FAO-56 Table 11 lengths); monthly Kc is the curve's mean over the month.",
    "ETc = ET0 x Kc per month, with ET0 from FAO-56 Penman-Monteith on the NASA POWER monthly climatology.",
    f"Effective rain = {EFFECTIVE_RAIN_FRACTION} x monthly rain (a fixed fraction, not a soil water balance).",
    "Irrigation need = max(ETc - effective rain, 0) per month; no soil water storage, leaching or "
    "conveyance losses.",
    "A month shared by two crops (harvest of one, sowing of the next) counts at half weight for each.",
]
STAGES = ("initial", "development", "mid", "late")
SAMPLES_PER_MONTH = 60
RICE_CAVEAT = (
    "Paddy rice also needs water for land preparation (puddling) and for percolation and seepage, which "
    "this ETc estimate does not include, so the rice water need shown is a lower bound."
)


def load_kc(path: Path = KC_PATH) -> dict:
    """Kc table by crop id; each entry gets "stage_fractions" (its own, else the default)."""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    default = {k: data["stage_fractions_default"][k] for k in STAGES}
    return {
        crop: {**entry, "stage_fractions": entry.get("stage_fractions", default)}
        for crop, entry in data["crops"].items()
    }


def apply_pack_overrides(kc_table: dict, pack: dict | None) -> dict:
    """Kc table with per-crop stage fractions from the regional pack's crop calendar, if any.

    A crop_calendar entry may carry "stage_fractions" ({initial, development, mid, late}) with its own
    "stage_fractions_status"; it replaces the default stage lengths for that crop in that pack only.
    """
    out = dict(kc_table)
    for entry in (pack or {}).get("crop_calendar", []):
        f, crop = entry.get("stage_fractions"), entry["crop"]
        if not f or crop not in out:
            continue
        out[crop] = {**out[crop], "stage_fractions": {k: f[k] for k in STAGES},
                     "stage_fractions_status": f.get("status"), "stage_fractions_source": f"pack {pack['id']}"}
    return out


def kc_at(t: float, kc: dict) -> float:
    """FAO-56 Kc curve at fraction ``t`` (0..1) of the growing window."""
    f = kc["stage_fractions"]
    ini, dev, mid = f["initial"], f["development"], f["mid"]
    if t <= ini:
        return kc["kc_ini"]
    if t <= ini + dev:
        return kc["kc_ini"] + (t - ini) / dev * (kc["kc_mid"] - kc["kc_ini"])
    if t <= ini + dev + mid:
        return kc["kc_mid"]
    late_start = ini + dev + mid
    return kc["kc_mid"] + (t - late_start) / (1 - late_start) * (kc["kc_end"] - kc["kc_mid"])


def stage_at(t: float, kc: dict) -> str:
    f, edge = kc["stage_fractions"], 0.0
    for stage in STAGES:
        edge += f[stage]
        if t <= edge:
            return stage
    return "late"


def monthly_kc(n_months: int, kc: dict) -> list[tuple[float, str]]:
    """(mean Kc over the month, stage at mid-month) for each month of an ``n_months`` window."""
    out = []
    for i in range(n_months):
        ts = [(i + (j + 0.5) / SAMPLES_PER_MONTH) / n_months for j in range(SAMPLES_PER_MONTH)]
        out.append((sum(kc_at(t, kc) for t in ts) / len(ts), stage_at((i + 0.5) / n_months, kc)))
    return out


def scenario_climate(raw: dict, temp_delta_c: float) -> dict:
    """Climate summary with every temperature shifted by ``temp_delta_c``; ET0 is recomputed (Penman-Monteith)."""
    if not temp_delta_c:
        return climate.summarize(raw)
    shifted = copy.deepcopy(raw)
    for row in shifted["monthly"]:
        for key in ("t2m", "t2m_max", "t2m_min"):
            if row.get(key) is not None:
                row[key] += temp_delta_c
    return climate.summarize(shifted)


def crop_water_need(
    crop_id: str,
    months: list[int],
    climate_summary: dict,
    kc: dict | None,
    month_weights: dict[int, float] | None = None,
) -> dict:
    """Monthly and seasonal ETc, effective rain and irrigation need (mm) for one crop in its window.

    Returns {"water_assessed": False, "note": ...} when the crop has no Kc values.
    """
    if not kc:
        return {
            "crop": crop_id,
            "water_assessed": False,
            "note": f"No Kc values for {crop_id!r} in data/kc_fao56.json; water need not estimated.",
        }
    clim = {m["month"]: m for m in climate_summary["monthly_climatology"]}
    weights = month_weights or {}

    monthly = []
    for month, (kc_month, stage) in zip(months, monthly_kc(len(months), kc)):
        w = weights.get(month, 1.0)
        et0 = clim[month]["et0_mm"] * w
        rain = clim[month]["rain_mm"] * w
        etc = et0 * kc_month
        eff = EFFECTIVE_RAIN_FRACTION * rain
        monthly.append({
            "month": month, "stage": stage, "kc": kc_month, "weight": w,
            "et0_mm": et0, "etc_mm": etc, "rain_mm": rain, "effective_rain_mm": eff,
            "irrigation_mm": max(etc - eff, 0.0),
        })
    seasonal = {k: sum(m[k] for m in monthly) for k in ("et0_mm", "etc_mm", "rain_mm", "effective_rain_mm", "irrigation_mm")}
    return {
        "crop": crop_id,
        "water_assessed": True,
        "months": months,
        "monthly": monthly,
        "seasonal": seasonal,
        "kc_status": kc.get("status"),
        "caveats": [],
    }


def rotation_water_need(
    rotation: dict, climate_summary: dict, kc_table: dict, paddy_extra_water_mm: float | None = None
) -> dict:
    """Water need of every crop in a rotation; shared transition months are split 50/50.

    ``rotation["crops"]`` holds {"crop", "months", ...}. The annual total is None if any crop lacks Kc.
    With ``paddy_extra_water_mm`` (from the regional pack) that amount is added to rice's irrigation
    need; without it rice need is flagged as a lower bound.
    """
    counts: dict[int, int] = {}
    for c in rotation["crops"]:
        for m in c["months"]:
            counts[m] = counts.get(m, 0) + 1
    weights = {m: 1 / n for m, n in counts.items() if n > 1}

    per_crop = [crop_water_need(c["crop"], c["months"], climate_summary, kc_table.get(c["crop"]), weights)
                for c in rotation["crops"]]
    for p in per_crop:
        if p["crop"] != "rice" or not p["water_assessed"]:
            continue
        if paddy_extra_water_mm is None:
            p["caveats"].append(RICE_CAVEAT)
        else:
            total_w = sum(m["weight"] for m in p["monthly"])
            for m in p["monthly"]:  # spread over the season in proportion to each month's weight
                m["irrigation_mm"] += paddy_extra_water_mm * m["weight"] / total_w
            p["seasonal"]["irrigation_mm"] += paddy_extra_water_mm
            p["paddy_extra_water_mm"] = paddy_extra_water_mm
            p["caveats"].append(
                f"Rice irrigation need includes {paddy_extra_water_mm:.0f} mm for land preparation, percolation "
                "and seepage from the regional pack, on top of crop water use (ETc)."
            )
    assessed = all(p["water_assessed"] for p in per_crop)
    return {
        "per_crop": per_crop,
        "water_assessed": assessed,
        "annual_irrigation_mm": sum(p["seasonal"]["irrigation_mm"] for p in per_crop) if assessed else None,
        "annual_etc_mm": sum(p["seasonal"]["etc_mm"] for p in per_crop) if assessed else None,
        "notes": [p["note"] for p in per_crop if not p["water_assessed"]],
        "caveats": sorted({c for p in per_crop for c in p.get("caveats", [])}),
    }


def rotation_years(rotation: dict) -> int:
    """Number of years in the rotation cycle (``n_years``, else the largest crop ``year``, else 1)."""
    return rotation.get("n_years") or max((c.get("year", 1) for c in rotation["crops"]), default=1)


def crops_in_year(rotation: dict, year: int) -> list[dict]:
    return [c for c in rotation["crops"] if c.get("year", 1) == year]


def rotation_water_need_cycle(
    rotation: dict, climate_summary: dict, kc_table: dict, paddy_extra_water_mm: float | None = None
) -> dict:
    """``rotation_water_need`` for each year of an N-year cycle, with annual means.

    Each year is treated like a one-year rotation (shared transition months split 50/50 within
    that year). A year without crops (fallow) needs 0 mm. For a one-year rotation the result
    equals ``rotation_water_need`` plus "n_years" and "per_year".
    """
    n = rotation_years(rotation)
    per_year = []
    for y in range(1, n + 1):
        sub = {**rotation, "crops": crops_in_year(rotation, y)}
        w = rotation_water_need(sub, climate_summary, kc_table, paddy_extra_water_mm)
        for p in w["per_crop"]:
            p["year"] = y
        w["year"] = y
        w["fallow_year"] = not sub["crops"]
        per_year.append(w)
    if n == 1:
        return {**per_year[0], "n_years": 1, "per_year": per_year}
    assessed = all(w["water_assessed"] for w in per_year)
    return {
        "per_crop": [p for w in per_year for p in w["per_crop"]],
        "water_assessed": assessed,
        "annual_irrigation_mm": sum(w["annual_irrigation_mm"] for w in per_year) / n if assessed else None,
        "annual_etc_mm": sum(w["annual_etc_mm"] for w in per_year) / n if assessed else None,
        "notes": [x for w in per_year for x in w["notes"]],
        "caveats": sorted({c for w in per_year for c in w["caveats"]}),
        "n_years": n,
        "per_year": per_year,
    }
