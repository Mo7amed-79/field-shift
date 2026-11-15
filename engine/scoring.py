# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Transparent multi-criteria scoring of crop rotations.

Input:  rotations already enriched by the pipeline with "water" (water_balance
        results), "crop_info" (crops.json entries) and "heat" (heat_exposure per
        crop, current and scenario climate), plus the farmer's priorities, the
        scenario and the field area.
Output: the same rotations with "scores" (each 0-100 or None), "score_details"
        (the numbers behind each score), "total", "rank", water volumes and
        savings, and "exceeds_water_budget"; plus a sensitivity block.

Criteria:
- water (irrigated): saving versus the farmer's current rotation,
  score = clip(50 + saving_pct, 0, 100); the current rotation is 50 and a 25%
  saving is 75.
- water (rainfed): relative water stress. Per crop season, unmet fraction =
  water deficit / ETc; rotation value = mean over its crop seasons (fallow
  years are not seasons); score = clip(50 + 100 x (current - rotation), 0, 100).
  The deficit in mm is reported for information only.
- soil_practices: "soil-health practices (proxy)", three independent parts:
  legume (40), family diversity (30), ground cover (30). Not a measured soil
  outcome.
- heat_risk: "heat-stress exposure (proxy)", one-sided: monthly mean
  temperature above the crop's ECOCROP optimal maximum, harvest month excluded.
  Warming can only lower it.
"""

from statistics import mean

CRITERIA = ("water", "soil_practices", "heat_risk")
PRIORITY_KEYS = {"water": "water", "soil": "soil_practices", "heat_risk": "heat_risk"}
SOIL_LABEL = "soil-health practices (proxy)"
HEAT_LABEL = "heat-stress exposure (proxy)"
HEAT_PENALTY_PER_C = 15.0
HEAT_NOTE = (
    "Compares monthly mean temperature with each crop's ECOCROP optimal maximum (harvest month excluded). "
    "It does not model heat at flowering or daily maximum temperatures."
)
WATER_LABEL = "irrigation water saving"
WATER_LABEL_RAINFED = "water-deficit exposure"
FALLOW_MOISTURE_NOTE = (
    "Stored soil moisture from a fallow year is not modelled, so the benefit of fallow to the following "
    "crop is not captured."
)
M3_PER_MM = {"feddan": 4.2, "hectare": 10.0, "acre": 4.047}
SENSITIVITY_CASES = {
    "equal": {"water": 1, "soil": 1, "heat_risk": 1},
    "water_only": {"water": 1, "soil": 0, "heat_risk": 0},
    "soil_only": {"water": 0, "soil": 1, "heat_risk": 0},
    "heat_only": {"water": 0, "soil": 0, "heat_risk": 1},
}


def mm_to_m3(mm: float, area_value: float, area_unit: str) -> float:
    """Volume of ``mm`` of water over the area: 1 mm = 4.2 m3/feddan, 10 m3/hectare, 4.047 m3/acre."""
    return mm * area_value * M3_PER_MM[area_unit]


def clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def water_score(saving_pct: float) -> float:
    return clip(50 + saving_pct)


def normalise_priorities(priorities: dict) -> dict:
    """{water, soil, heat_risk} scaled to sum to 1; all zero means equal weights."""
    vals = {k: max(float(priorities.get(k, 0)), 0.0) for k in PRIORITY_KEYS}
    total = sum(vals.values())
    if total == 0:
        return {k: 1 / len(vals) for k in vals}
    return {k: v / total for k, v in vals.items()}


def heat_exposure(crop: dict, months: list[int], climate_summary: dict) -> dict:
    """Mean monthly excess of mean temperature over the crop's optimal max, harvest month excluded."""
    clim = {m["month"]: m for m in climate_summary["monthly_climatology"]}
    assessed = months[:-1] if len(months) > 1 else months
    excess = [max(0.0, clim[m]["temp_c"] - crop["temp_opt_max_c"]) for m in assessed]
    mean_excess = mean(excess) if excess else 0.0
    return {
        "months_assessed": assessed,
        "monthly_excess_c": [round(e, 2) for e in excess],
        "mean_excess_c": mean_excess,
        "score": clip(100 - HEAT_PENALTY_PER_C * mean_excess),
    }


SOIL_POINTS = {"legume_in_rotation": 40.0, "family_diversity": 30.0, "ground_cover": 30.0}


def soil_practices(crop_infos: list[dict], months_covered: int, n_years: int = 1,
                   fallow_months: list[dict] | None = None) -> tuple[float, dict]:
    """Three independent parts: legume (40), family diversity (30), ground cover (30).

    For an N-year cycle ``months_covered`` counts cropped months over the whole cycle (out of
    12 x N); legume and family diversity are assessed over every crop in the cycle.
    """
    n = len(crop_infos)
    legume = any(c.get("is_legume") for c in crop_infos)
    families = {c["family"] for c in crop_infos}
    diversity = (len(families) - 1) / (n - 1) if n > 1 else 0.0
    cycle_months = 12 * n_years
    cycle_cover = min(months_covered, cycle_months)
    cover_months = cycle_cover / n_years
    if n_years == 1:
        cover_months = int(cover_months)
    parts = {
        "legume_in_rotation": SOIL_POINTS["legume_in_rotation"] * legume,
        "family_diversity": SOIL_POINTS["family_diversity"] * diversity,
        "ground_cover": SOIL_POINTS["ground_cover"] * cycle_cover / cycle_months,
    }
    return sum(parts.values()), {
        "label": SOIL_LABEL,
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "max_points": SOIL_POINTS,
        "legume_in_rotation": legume,
        "distinct_families": len(families),
        "n_crops": n,
        "months_with_ground_cover": cover_months,  # per year (annual mean for multi-year cycles)
        "n_years": n_years,
        "cycle_months": cycle_months,
        "cycle_months_with_ground_cover": cycle_cover,
        "fallow_months": fallow_months or [],  # [{year, month}]; each counts as zero ground cover
        "method": "legume 40 + 30 x (distinct families - 1) / (crops - 1) + 30 x months with a crop / months "
                  "in the cycle; diversity is assessed over the whole cycle",
        "note": "Counts practices known to support soil health; it is not a measured soil outcome.",
    }


def _weighted_total(scores: dict, weights: dict) -> float:
    """Weighted sum over the criteria that have a score; weights are re-normalised over those."""
    used = {k: weights[k] for k in PRIORITY_KEYS if scores[PRIORITY_KEYS[k]] is not None}
    wsum = sum(used.values())
    if wsum == 0:
        return 0.0
    return sum(w * scores[PRIORITY_KEYS[k]] for k, w in used.items()) / wsum


def _water_quantity(r: dict) -> float | None:
    """Irrigation need (mm per year), or for rainfed farming the mean unmet fraction of crop water demand."""
    if r.get("water_mm_per_year") is not None:
        return r["water_mm_per_year"]
    return r.get("water_unmet_fraction")


def unmet_fractions(water: dict) -> tuple[list[dict], float | None]:
    """Per crop season: unmet fraction = water deficit / ETc; and their mean (None if any crop lacks Kc).

    ``water`` is a rotation_water_need_cycle result; fallow years have no crop seasons, so they add nothing.
    """
    per_crop = []
    for p in water["per_crop"]:
        if not p["water_assessed"]:
            return [], None
        etc, deficit = p["seasonal"]["etc_mm"], p["seasonal"]["irrigation_mm"]
        frac = deficit / etc if etc > 0 else 0.0
        per_crop.append({"crop": p["crop"], "year": p.get("year", 1), "etc_mm": round(etc, 1),
                         "water_deficit_mm": round(deficit, 1), "unmet_fraction": round(frac, 3),
                         "rain_cover_fraction": round(1 - frac, 3)})
    if not per_crop:
        return [], None
    return per_crop, mean(p["unmet_fraction"] for p in per_crop)


def has_fallow_year(r: dict) -> bool:
    return any(w.get("fallow_year") for w in r["water"].get("per_year", []))


def rank_key(r: dict, weights: dict) -> tuple:
    """Total (desc), then water need or deficit (asc), then id: deterministic."""
    return (-_weighted_total(r["scores"], weights), _water_quantity(r) or float("inf"), r["id"])


def _rank(rotations: list[dict], weights: dict) -> list[str]:
    return [r["id"] for r in sorted(rotations, key=lambda r: rank_key(r, weights))]


def _rotation_heat(r: dict) -> tuple[float, float, list[dict]]:
    """Window-length-weighted mean heat score, current and scenario."""
    per_crop, w_total, now, scen = [], 0, 0.0, 0.0
    for c, h in zip(r["crops"], r["heat"]):
        w = len(c["months"])
        w_total += w
        now += w * h["current"]["score"]
        scen += w * h["scenario"]["score"]
        per_crop.append({
            "crop": c["crop"], "window_months": w,
            "mean_excess_c_current": round(h["current"]["mean_excess_c"], 2),
            "mean_excess_c_scenario": round(h["scenario"]["mean_excess_c"], 2),
            "score_current": round(h["current"]["score"], 1),
            "score_scenario": round(h["scenario"]["score"], 1),
        })
    return now / w_total, scen / w_total, per_crop


def score_rotations(
    rotations: list[dict],
    priorities: dict,
    scenario: dict,
    current_id: str,
    area: dict | None = None,
    water_access: str | None = None,
) -> list[dict]:
    """Score ``rotations`` in place on the three criteria and rank them by the weighted total.

    With ``water_access == "rainfed"`` the water quantity (ETc - effective rain) is a water
    deficit, not an irrigation need: it goes to "water_deficit_mm"; irrigation and m3 fields are None.
    """
    rainfed = water_access == "rainfed"
    if rainfed:
        area = None  # no m3 figures for a water deficit
    current = next(r for r in rotations if r["id"] == current_id)
    current_need = current["water"]["annual_irrigation_mm"]
    cut = 0.0 if rainfed else float(scenario.get("water_cut_pct", 0) or 0)
    current_unmet = unmet_fractions(current["water"])[1] if rainfed else None

    for r in rotations:
        need = r["water"]["annual_irrigation_mm"]
        r["water_mm_per_year"] = None if need is None else round(need, 1)
        r["water_m3_per_year"] = (
            round(mm_to_m3(need, area["value"], area["unit"])) if need is not None and area else None
        )
        if need is None or not current_need:
            saving_mm = saving_pct = w_score = None
        else:
            saving_mm = current_need - need
            saving_pct = 100 * saving_mm / current_need
            w_score = water_score(saving_pct)
        r["water_saving_mm"] = None if saving_mm is None else round(saving_mm, 1)
        r["water_saving_pct"] = None if saving_pct is None else round(saving_pct, 1)
        r["water_saving_m3"] = (
            round(mm_to_m3(saving_mm, area["value"], area["unit"])) if saving_mm is not None and area else None
        )
        if cut > 0 and need is not None and current_need is not None:
            budget = (100 - cut) / 100 * current_need
            r["exceeds_water_budget"] = need > budget
            r["water_budget_mm"] = round(budget, 1)
        else:
            r["exceeds_water_budget"] = None
            r["water_budget_mm"] = None

        n_years = r["water"].get("n_years", 1)
        covered = {(c.get("year", 1), m) for c in r["crops"] for m in c["months"]}
        fallow = [{"year": y, "month": m} for y in range(1, n_years + 1) for m in range(1, 13)
                  if (y, m) not in covered]
        soil_score, soil_detail = soil_practices(r["crop_info"], len(covered), n_years, fallow)
        heat_now, heat_scen, heat_per_crop = _rotation_heat(r)

        r["scores"] = {
            "water": None if w_score is None else round(w_score, 1),
            "soil_practices": round(soil_score, 1),
            "heat_risk": round(heat_scen, 1),
        }
        r["score_details"] = {
            "water": {
                "annual_irrigation_mm": r["water_mm_per_year"],
                "current_rotation_mm": None if current_need is None else round(current_need, 1),
                "saving_mm": r["water_saving_mm"],
                "saving_pct": r["water_saving_pct"],
                "method": "score = clip(50 + saving %, 0, 100): your current rotation = 50, a 25% saving = 75",
                "notes": r["water"]["notes"],
                "label": WATER_LABEL_RAINFED if rainfed else WATER_LABEL,
                "n_years": n_years,
                "per_year_mm": [
                    None if w["annual_irrigation_mm"] is None else round(w["annual_irrigation_mm"], 1)
                    for w in r["water"].get("per_year", [r["water"]])
                ],
            },
            "soil_practices": soil_detail,
            "heat_risk": {
                "label": HEAT_LABEL,
                "per_crop": heat_per_crop,
                "score_current_climate": round(heat_now, 1),
                "score_scenario": round(heat_scen, 1),
                "change": round(heat_scen - heat_now, 1),
                "temp_delta_c": scenario.get("temp_delta_c", 0),
                "projection_year": scenario.get("projection_year"),  # set when the scenario uses the forecast
                "method": f"per crop: clip(100 - {HEAT_PENALTY_PER_C:g} x mean excess °C, 0, 100); "
                          "rotation: mean over crops weighted by window length",
                "note": HEAT_NOTE,
            },
        }

        if rainfed:
            water = r["score_details"]["water"]
            per_crop_unmet, unmet = unmet_fractions(r["water"])
            if unmet is None or current_unmet is None:
                score = None
            else:
                score = round(clip(50 + 100 * (current_unmet - unmet)), 1)
            r["scores"]["water"] = score
            r["water_deficit_mm"] = r["water_mm_per_year"]
            r["water_deficit_reduction_pct"] = r["water_saving_pct"]
            r["water_unmet_fraction"] = None if unmet is None else round(unmet, 3)
            r["water_unmet_per_crop"] = per_crop_unmet
            water.update({
                "unmet_fraction": r["water_unmet_fraction"],
                "current_rotation_unmet_fraction": None if current_unmet is None else round(current_unmet, 3),
                "unmet_per_crop": per_crop_unmet,
                "water_deficit_mm": r["water_mm_per_year"],
                "current_rotation_deficit_mm": water["current_rotation_mm"],
                "deficit_reduction_mm": water["saving_mm"],
                "deficit_reduction_pct": water["saving_pct"],
                "annual_irrigation_mm": None, "current_rotation_mm": None, "saving_mm": None, "saving_pct": None,
                "method": "per crop season: unmet fraction = water deficit / ETc, with water deficit = sum of "
                          "max(ETc - effective rain, 0) per month; rotation = mean over its crop seasons (fallow "
                          "years are not crop seasons); score = clip(50 + 100 x (current unmet fraction - this "
                          "rotation's), 0, 100). The deficit in mm is information only, not scored.",
            })
            if has_fallow_year(r):
                water["fallow_note"] = FALLOW_MOISTURE_NOTE
            r["water_mm_per_year"] = r["water_saving_mm"] = r["water_saving_pct"] = None
            r["water_m3_per_year"] = r["water_saving_m3"] = None
        else:
            r["water_deficit_mm"] = None
            r["water_deficit_reduction_pct"] = None
            r["water_unmet_fraction"] = None
        r["water_criterion_label"] = WATER_LABEL_RAINFED if rainfed else WATER_LABEL

    weights = normalise_priorities(priorities)
    for r in rotations:
        r["total"] = round(_weighted_total(r["scores"], weights), 1)
    for i, rid in enumerate(_rank(rotations, weights), 1):
        next(r for r in rotations if r["id"] == rid)["rank"] = i
    return rotations


def sensitivity(rotations: list[dict], user_priorities: dict) -> dict:
    """Ranking under the farmer's weights and under equal / single-criterion weights."""
    cases = {"your_priorities": user_priorities, **SENSITIVITY_CASES}
    out = []
    for name, pri in cases.items():
        weights = normalise_priorities(pri)
        out.append({"name": name, "weights": {k: round(v, 3) for k, v in weights.items()},
                    "ranking": _rank(rotations, weights)})
    winners = {c["ranking"][0] for c in out}
    robust = len(winners) == 1
    return {
        "cases": out,
        "winner_is_robust": robust,
        "note": (
            f"{out[0]['ranking'][0]} ranks first under every weighting."
            if robust else
            "The top rotation changes with the weighting: " + "; ".join(f"{c['name']}: {c['ranking'][0]}" for c in out) + "."
        ),
    }
