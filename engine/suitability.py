# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Score how well each crop fits a location (ECOCROP-style envelopes).

Input:  crop entries from data/crops.json (temperature, rainfall and pH
        envelopes, cycle length, killing temperature), a climate summary
        (climate.summarize), soil properties (soil_client.fetch_soil, or None)
        and optionally a regional pack (regional.find_pack).
Output: per-crop suitability {"score": 0..100, "limiting_factor", "best_window",
        "alt_windows", "window_source", "confidence", "details", "ph_advisory",
        "caveats", "not_assessed"}, and a ranked list of crops.

Method: if the regional pack has a calendar entry for the crop, the crop is
scored in that window. Otherwise every start month is tried as a window of the
crop's typical cycle length. Each factor is scored 0 outside the absolute
range, 100 inside the optimal range and linearly in between. Temperature is the
mean of the monthly scores; with the default zero rule it is 0 if any month
other than the harvest month scores 0 or the killing temperature is crossed.
Pack rotations are scored without the zero rule (zero_rule="none"); months
above the absolute maximum are then only reported. Rainfall is applied
according to water access. The window score is
the minimum of the factors applied (limiting-factor principle). pH is advisory
only and never lowers the score.
"""

from statistics import mean

from engine import regional

DAYS_PER_MONTH = 30
CYCLE_MIN_MONTHS, CYCLE_MAX_MONTHS = 2, 8
N_TOP_WINDOWS = 3
WATER_ACCESS = ("rainfed", "limited_irrigation", "full_irrigation")
SOURCE_REGIONAL = "regional calendar (provisional)"
SOURCE_REGIONAL_VERIFIED = "regional calendar (verified)"
SOURCE_GLOBAL = "global ECOCROP estimate"
PH_CAVEAT = (
    "Soil pH is shown as advice only and does not affect the score: ECOCROP pH ranges are "
    "unreliable for alkaline alluvial soils (berseem, a staple of the Nile Delta, would score 0 "
    "on typical Delta clay)."
)


def envelope_score(value: float, abs_min: float, opt_min: float, opt_max: float, abs_max: float) -> float:
    """0 at/outside the absolute limits, 100 inside the optimal range, linear in between."""
    if opt_min <= value <= opt_max:
        return 100.0
    if value <= abs_min or value >= abs_max:
        return 0.0
    if value < opt_min:
        return 100.0 * (value - abs_min) / (opt_min - abs_min)
    return 100.0 * (abs_max - value) / (abs_max - opt_max)


def cycle_months(crop: dict) -> int:
    """Typical cycle: round((GMIN + 0.25 (GMAX - GMIN)) / 30) months, clamped to 2-8."""
    days = crop["cycle_days_min"] + 0.25 * (crop["cycle_days_max"] - crop["cycle_days_min"])
    return max(CYCLE_MIN_MONTHS, min(CYCLE_MAX_MONTHS, round(days / DAYS_PER_MONTH)))


def window_months(start_month: int, length: int) -> list[int]:
    return [(start_month - 1 + i) % 12 + 1 for i in range(length)]


def months_between(start_month: int, end_month: int) -> list[int]:
    """Inclusive months from start to end, wrapping over the year end."""
    return window_months(start_month, (end_month - start_month) % 12 + 1)


def ph_advisory(crop: dict, soil_ph: float | None) -> dict | None:
    if soil_ph is None:
        return None
    if crop["ph_opt_min"] <= soil_ph <= crop["ph_opt_max"]:
        status = "ok"
    elif crop["ph_abs_min"] <= soil_ph <= crop["ph_abs_max"]:
        status = "outside_optimal"
    else:
        status = "outside_absolute"
    return {
        "status": status,
        "soil_ph": round(soil_ph, 2),
        "ecocrop_optimal": [crop["ph_opt_min"], crop["ph_opt_max"]],
        "ecocrop_absolute": [crop["ph_abs_min"], crop["ph_abs_max"]],
        "note": {
            "ok": "Soil pH is within the ECOCROP optimal range.",
            "outside_optimal": "Soil pH is outside the ECOCROP optimal range but within its absolute range.",
            "outside_absolute": "Soil pH is outside the ECOCROP absolute range; check locally, "
                                "ECOCROP pH ranges are often too narrow for alkaline soils.",
        }[status],
    }


ZERO_RULES = ("exclude_harvest", "none")


def _score_window(
    crop: dict, clim: dict[int, dict], months: list[int], water_access: str, zero_rule: str = "exclude_harvest"
) -> dict:
    if zero_rule not in ZERO_RULES:
        raise ValueError(f"zero_rule must be one of {ZERO_RULES}")
    temp_env = (crop["temp_abs_min_c"], crop["temp_opt_min_c"], crop["temp_opt_max_c"], crop["temp_abs_max_c"])
    monthly_temp = [clim[m]["temp_c"] for m in months]
    monthly_temp_scores = [envelope_score(t, *temp_env) for t in monthly_temp]
    pre_harvest = monthly_temp_scores[:-1] if len(months) > 1 else monthly_temp_scores
    any_zero = min(pre_harvest) == 0
    above_abs_max = [m for m, t in zip(months, monthly_temp) if t >= crop["temp_abs_max_c"]]

    coldest_tmin = min(clim[m]["tmin_c"] for m in months)
    kill = crop.get("temp_kill_c")
    killed = kill is not None and coldest_tmin < kill
    if zero_rule == "none":
        temp_score = mean(monthly_temp_scores)
    else:
        temp_score = 0.0 if any_zero or killed else mean(monthly_temp_scores)

    factors = {"temperature": temp_score}
    details = {
        "temperature": {
            "method": (
                "mean of monthly scores" if zero_rule == "none" else
                "mean of monthly scores; 0 if any month before the harvest month scores 0 or the killing "
                "temperature is crossed"
            ),
            "zero_rule": zero_rule,
            "months_above_absolute_max": above_abs_max,
            "envelope_c": list(temp_env),
            "monthly_temp_c": [round(t, 1) for t in monthly_temp],
            "monthly_scores": [round(s, 1) for s in monthly_temp_scores],
            "any_month_zero_before_harvest": any_zero,
            "coldest_mean_tmin_c": round(coldest_tmin, 1),
            "killing_temp_c": kill,
            "killed": killed,
            "score": round(temp_score, 1),
        }
    }

    rain_env = (crop["rain_abs_min_mm"], crop["rain_opt_min_mm"], crop["rain_opt_max_mm"], crop["rain_abs_max_mm"])
    rain_mm = sum(clim[m]["rain_mm"] for m in months)
    rain_rainfed = envelope_score(rain_mm, *rain_env)
    rain_detail = {"envelope_mm": list(rain_env), "window_rain_mm": round(rain_mm, 1), "water_access": water_access}
    if water_access == "full_irrigation":
        rain_detail.update(applied=False, note="Not applied: under full irrigation rainfall is not the limiting factor.")
    else:
        rain_score = rain_rainfed if water_access == "rainfed" else (rain_rainfed + 100) / 2
        factors["rainfall"] = rain_score
        rain_detail.update(applied=True, rainfed_score=round(rain_rainfed, 1), score=round(rain_score, 1))
        if water_access == "limited_irrigation":
            rain_detail["note"] = "Limited irrigation: average of the rainfed score and 100."
    details["rainfall"] = rain_detail

    score = min(factors.values())
    limiting = "none" if score >= 100 else min(factors, key=factors.get)
    return {
        "start_month": months[0],
        "end_month": months[-1],
        "months": months,
        "score": round(score, 1),
        "limiting_factor": limiting,
        "temp_mean_score": mean(monthly_temp_scores),
        "details": details,
    }


def _confidence(window_source: str) -> str:
    return {SOURCE_REGIONAL_VERIFIED: "high", SOURCE_REGIONAL: "medium"}.get(window_source, "low")


def crop_suitability(
    crop: dict, climate_summary: dict, soil: dict | None, water_access: str, pack: dict | None = None
) -> dict:
    """Score ``crop`` at this location.

    With a regional calendar entry the crop is scored in that window only;
    otherwise "alt_windows" holds the top N_TOP_WINDOWS windows, best first.
    """
    if water_access not in WATER_ACCESS:
        raise ValueError(f"water_access must be one of {WATER_ACCESS}, got {water_access!r}")
    if not crop.get("complete", True):
        return {
            "score": 0.0, "limiting_factor": "missing ECOCROP data", "best_window": None, "alt_windows": [],
            "window_source": None, "confidence": "low", "cycle_months": None, "details": {},
            "ph_advisory": None, "caveats": [], "not_assessed": ["all"],
        }

    soil_ph = soil.get("ph") if soil else None
    not_assessed = [] if soil_ph is not None else ["ph"]
    clim = {m["month"]: m for m in climate_summary["monthly_climatology"]}

    entry = regional.calendar_entry(pack, crop["id"])
    if entry:
        months = months_between(entry["start_month"], entry["end_month"])
        windows = [_score_window(crop, clim, months, water_access)]
        window_source = SOURCE_REGIONAL_VERIFIED if regional.is_verified(entry) else SOURCE_REGIONAL
        cycle = {
            "months": len(months),
            "method": f"from regional pack {pack['id']!r}",
            "calendar_status": entry.get("status"),
            "calendar_source": entry.get("source"),
        }
    else:
        length = cycle_months(crop)
        windows = [_score_window(crop, clim, window_months(start, length), water_access) for start in range(1, 13)]
        # Ties are broken by the average monthly temperature score, then the earlier start month.
        windows.sort(key=lambda w: (-w["score"], -w["temp_mean_score"], w["start_month"]))
        window_source = SOURCE_GLOBAL
        cycle = {
            "months": length,
            "method": "round((GMIN + 0.25 * (GMAX - GMIN)) / 30), clamped to 2-8 months",
            "gmin_days": crop["cycle_days_min"],
            "gmax_days": crop["cycle_days_max"],
        }
    if "perennial" in (crop.get("life_span") or ""):
        cycle["perennial_note"] = f"ECOCROP lists life span {crop['life_span']!r}; treated as an annual crop with this cycle."

    best = windows[0]
    details = {**best["details"], "cycle": cycle}
    return {
        "score": best["score"],
        "limiting_factor": best["limiting_factor"],
        "best_window": {k: best[k] for k in ("start_month", "end_month", "months")},
        "alt_windows": [
            {k: w[k] for k in ("start_month", "end_month", "months", "score", "limiting_factor")}
            for w in windows[:N_TOP_WINDOWS]
        ],
        "window_source": window_source,
        "confidence": _confidence(window_source),
        "cycle_months": len(best["months"]),
        "details": details,
        "ph_advisory": ph_advisory(crop, soil_ph),
        "caveats": [PH_CAVEAT],
        "not_assessed": not_assessed,
    }


def rank_crops(
    crops: list[dict], climate_summary: dict, soil: dict | None, water_access: str, pack: dict | None = None
) -> list[dict]:
    """Return every crop with its suitability attached under "suitability", best first (ties by id)."""
    ranked = [
        {**crop, "suitability": crop_suitability(crop, climate_summary, soil, water_access, pack)}
        for crop in crops
    ]
    ranked.sort(key=lambda c: (-c["suitability"]["score"], c["id"]))
    return ranked


def score_in_window(
    crop: dict, climate_summary: dict, months: list[int], water_access: str, zero_rule: str = "exclude_harvest"
) -> dict:
    """Score ``crop`` in a fixed window: {"score", "limiting_factor", "temperature_score",
    "months_above_absolute_max", "details"}."""
    clim = {m["month"]: m for m in climate_summary["monthly_climatology"]}
    w = _score_window(crop, clim, months, water_access, zero_rule)
    return {
        "score": w["score"],
        "limiting_factor": w["limiting_factor"],
        "temperature_score": w["details"]["temperature"]["score"],
        "months_above_absolute_max": w["details"]["temperature"]["months_above_absolute_max"],
        "details": w["details"],
    }
