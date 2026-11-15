# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Compare the farmer's rotation with known alternatives (the /api/compare core).

Input:  raw NASA POWER data (power_client.fetch_monthly), soil (or None), the
        request (current_rotation, water_access, priorities, scenario, area),
        the crop table, the Kc table and the regional pack (or None).
Output: dict with mode, rotations (current + up to two alternatives, scored,
        explained, with a 12-month plan), sensitivity, warnings and assumptions.

Inside a regional pack the engine compares the pack's typical rotations; it
does not invent rotations. Without a pack it falls back to the global
rotation builder in "exploratory mode" (low confidence).
"""

from engine import climate, explain, forecast, market, planner, rotation_builder, scoring, suitability, water_balance

N_ALTERNATIVES = 2
SOURCE_PACK_ROTATION = "regional pack rotation (provisional)"
SOURCE_PACK_ROTATION_VERIFIED = "regional pack rotation (verified)"
EXPLORATORY_WARNING = (
    "Exploratory mode: there is no regional pack for this location, so the alternatives were generated "
    "from global ECOCROP estimates and have not been checked against local practice. Treat them as ideas "
    "to discuss with a local agronomist, not recommendations."
)
VERIFIED = "verified"


FALLOW = "fallow"


def _window(crop_id: str, label: str, start: int, end: int, source: str, year: int = 1) -> dict:
    return {"crop": crop_id, "label": label, "start_month": start, "end_month": end,
            "months": suitability.months_between(start, end), "window_source": source, "year": year}


def _pack_years(rot: dict) -> int:
    return rot.get("n_years") or max((c.get("year", 1) for c in rot["crops"]), default=1)


def cycle_signature(rot: dict) -> list[str]:
    """Crop ids in cycle order, with "fallow" for each year without a crop."""
    sig = []
    for y in range(1, _pack_years(rot) + 1):
        in_year = [c["crop"] for c in rot["crops"] if c.get("year", 1) == y]
        sig += in_year or [FALLOW]
    return sig


def _from_pack_rotation(rot: dict, is_current: bool = False) -> dict:
    source = SOURCE_PACK_ROTATION_VERIFIED if rot.get("status") == VERIFIED else SOURCE_PACK_ROTATION
    return {
        "id": "current" if is_current else rot["id"],
        "name": rot["name"],
        "is_current": is_current,
        "source": "regional pack",
        "pack_rotation_id": rot["id"],
        "status": rot.get("status"),
        "n_years": _pack_years(rot),
        "crops": [_window(c["crop"], c["label"], c["start_month"], c["end_month"], source, c.get("year", 1))
                  for c in rot["crops"]],
        "caveats": [],
    }


def _current_in_pack(current: list[str], pack: dict, ranked_by_id: dict, crops_by_id: dict,
                     years: list[int] | None = None) -> dict:
    """Use a matching pack rotation's windows, else each crop's calendar window, else its global window."""
    for rot in pack.get("typical_rotations", []):
        sig = cycle_signature(rot)
        if sig == current:
            return _from_pack_rotation(rot, is_current=True)
        if len(sig) == len(current) and set(sig) == set(current) and _pack_years(rot) == 1:
            r = _from_pack_rotation(rot, is_current=True)
            order = {c: i for i, c in enumerate(current)}
            r["crops"].sort(key=lambda c: order[c["crop"]])
            r["name"] = " → ".join(c["label"] for c in r["crops"])
            return r
        if len(sig) == len(current) and sorted(sig) == sorted(current):
            return _from_pack_rotation(rot, is_current=True)  # same multi-year cycle, other starting year

    windows, caveats = [], []
    n_fallow = current.count(FALLOW)
    for i, crop_id in enumerate(current):
        if crop_id == FALLOW:
            continue
        year = years[i] if years else 1
        entry = next((e for e in pack.get("crop_calendar", []) if e["crop"] == crop_id), None)
        name = crops_by_id[crop_id]["name_en"]
        if entry:
            src = suitability.SOURCE_REGIONAL_VERIFIED if entry.get("status") == VERIFIED else suitability.SOURCE_REGIONAL
            windows.append(_window(crop_id, name, entry["start_month"], entry["end_month"], src, year))
        else:
            w = ranked_by_id[crop_id]["suitability"]["best_window"]
            windows.append(_window(crop_id, name, w["start_month"], w["end_month"], suitability.SOURCE_GLOBAL,
                                   year))
            caveats.append(f"{name} has no entry in the {pack['name']} calendar; its window is a global ECOCROP estimate.")
    if years:
        n_years = max(years)
    elif n_fallow:
        n_years = 1 + n_fallow if windows else n_fallow
        caveats.append(f"Each 'fallow' entry is treated as a year without a crop after the listed crops "
                       f"({1 + n_fallow}-year cycle).")
    return {
        "id": "current",
        "name": " → ".join(crops_by_id[c]["name_en"] if c != FALLOW else "Fallow" for c in current),
        "is_current": True, "n_years": n_years if (years or n_fallow) else 1,
        "source": "farmer", "pack_rotation_id": None, "status": None, "crops": windows, "caveats": caveats,
    }


def _from_builder(cand: dict) -> dict:
    return {
        "id": cand["id"], "name": cand["name"], "is_current": cand["is_current"],
        "source": "exploratory" if not cand["is_current"] else "farmer",
        "pack_rotation_id": None, "status": None,
        "crops": [
            {**s["window"], "crop": s["crop"], "label": s["name_en"], "window_source": s["window_source"]}
            for s in cand["sequence"]
        ],
        "caveats": [],
    }


def _confidence(rot: dict, kc_table: dict, exploratory: bool) -> str:
    if exploratory or any(c["window_source"] == suitability.SOURCE_GLOBAL for c in rot["crops"]):
        return "low"
    windows_verified = all(c["window_source"].endswith("(verified)") for c in rot["crops"])
    kc_verified = all((kc_table.get(c["crop"]) or {}).get("status") == VERIFIED for c in rot["crops"])
    return "high" if windows_verified and kc_verified else "medium"


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _enrich(rot: dict, clim_now: dict, clim_scen: dict, crops_by_id: dict, kc_table: dict,
            water_access: str, soil_ph: float | None, exploratory: bool, paddy_extra: float | None) -> dict:
    rot["crop_info"] = [crops_by_id[c["crop"]] for c in rot["crops"]]
    rot["water"] = water_balance.rotation_water_need_cycle(rot, clim_scen, kc_table, paddy_extra)
    # Pack rotations are local practice: no "any month 0 -> window 0" rule, only a warning.
    zero_rule = "exclude_harvest" if exploratory else "none"
    rot["heat"], rot["suitability"] = [], []
    for c, info in zip(rot["crops"], rot["crop_info"]):
        now = suitability.score_in_window(info, clim_now, c["months"], water_access, zero_rule)
        rot["heat"].append({
            "crop": c["crop"],
            "current": scoring.heat_exposure(info, c["months"], clim_now),
            "scenario": scoring.heat_exposure(info, c["months"], clim_scen),
        })
        hot = suitability.score_in_window(info, clim_scen, c["months"], water_access, zero_rule)["months_above_absolute_max"]
        if hot:
            rot["caveats"].append(
                f"{c['label']}: monthly mean temperature is above the ECOCROP absolute maximum "
                f"({info['temp_abs_max_c']:g} °C) in {', '.join(MONTHS[m - 1] for m in hot)}"
                + (" (local practice, so not scored as unsuitable)." if not exploratory else ".")
            )
        ph = suitability.ph_advisory(info, soil_ph)
        if exploratory and ph and ph["status"] != "outside_absolute":
            ph = None
        rot["suitability"].append({
            "crop": c["crop"], "score": now["score"], "limiting_factor": now["limiting_factor"],
            "window_source": c["window_source"], "ph_advisory": ph,
        })
    for y in range(1, water_balance.rotation_years(rot) + 1):
        overlap = rotation_builder._illegal_overlap(water_balance.crops_in_year(rot, y), allow_transition=True)
        if overlap:
            where = f" of year {y}" if water_balance.rotation_years(rot) > 1 else ""
            rot["caveats"].append(
                f"Crop windows overlap in months {overlap}{where}; this sequence may not fit in one year.")
    rot["caveats"] += rot["water"]["caveats"] + rot["water"]["notes"]
    return rot


def run_compare(raw: dict, soil: dict | None, request: dict, crops: list[dict], kc_table: dict,
                pack: dict | None) -> dict:
    """Run the full comparison. Raises ValueError for crop ids not in the crop table."""
    crops_by_id = {c["id"]: c for c in crops}
    cycle = request.get("current_cycle")  # [[crop ids of year 1], [year 2], ...]; [] = fallow year
    if cycle:
        current_ids = [c for year in cycle for c in (year or [FALLOW])]
        current_years = [i for i, year in enumerate(cycle, 1) for _ in (year or [FALLOW])]
    else:
        current_ids = list(request["current_rotation"])
        current_years = None
    unknown = [c for c in current_ids if c not in crops_by_id and c != FALLOW]
    if unknown:
        raise ValueError(f"Unknown crop ids in current_rotation: {unknown}")

    scenario = dict(request.get("scenario") or {})
    water_access = request["water_access"]
    area = request.get("area")
    clim_now = climate.summarize(raw)
    projection = forecast.project_next_year(raw)
    scenario_warnings = []
    if scenario.get("use_forecast"):
        if scenario.get("temp_delta_c"):
            scenario_warnings.append(
                f"use_forecast is on, so the temperature change of {scenario['temp_delta_c']:+g} °C was ignored: "
                f"water and heat use the projected {projection['year']} temperature and ET0.")
            scenario["temp_delta_c"] = 0
        scenario["projection_year"] = projection["year"]
        clim_scen = forecast.forecast_climate(clim_now, projection)
    else:
        clim_scen = water_balance.scenario_climate(raw, scenario.get("temp_delta_c", 0) or 0)
    soil_ph = soil.get("ph") if soil else None
    ranked = suitability.rank_crops(crops, clim_now, soil, water_access, pack)
    ranked_by_id = {c["id"]: c for c in ranked}

    warnings = list(scenario_warnings)
    exploratory = pack is None
    if exploratory:
        warnings.append(EXPLORATORY_WARNING)
        if FALLOW in current_ids:
            warnings.append("Exploratory mode does not model fallow years; 'fallow' entries were ignored.")
        crop_ids = [c for c in current_ids if c != FALLOW]
        cands = rotation_builder.build_candidates(ranked, crop_ids, N_ALTERNATIVES, climate_summary=clim_now)
        candidates = [_from_builder(c) for c in cands]
    else:
        current = _current_in_pack(current_ids, pack, ranked_by_id, crops_by_id, current_years)
        others = [
            _from_pack_rotation(r) for r in pack.get("typical_rotations", [])
            if r["id"] != current["pack_rotation_id"]
        ]
        candidates = [current] + others
        if water_access != pack.get("water_access_default"):
            warnings.append(
                f"You selected {water_access!r}, but the {pack['name']} pack assumes "
                f"{pack.get('water_access_default')!r}; its calendar and rotations may not fit your situation."
            )

    if not exploratory:
        kc_table = water_balance.apply_pack_overrides(kc_table, pack)
    for rot in candidates:
        _enrich(rot, clim_now, clim_scen, crops_by_id, kc_table, water_access, soil_ph, exploratory,
                None if exploratory else pack.get("paddy_extra_water_mm"))
    scoring.score_rotations(candidates, request["priorities"], scenario, "current", area, water_access)

    current = candidates[0]
    market_table = None if exploratory else market.load_market(pack)
    for rot in candidates:
        rot["revenue"] = market.rotation_revenue(rot, market_table, area)
    market.add_change_vs_current(candidates, current)
    weights = scoring.normalise_priorities(request["priorities"])
    all_ranked, current_is_strong, current_overall_rank = [], False, None
    if exploratory:
        shown = candidates
    else:
        ordered = sorted(candidates, key=lambda r: scoring.rank_key(r, weights))
        all_ranked = [
            {
                "id": r["pack_rotation_id"] or r["id"], "name": r["name"], "is_current": r["is_current"],
                "water_mm_per_year": r["water_mm_per_year"], "water_saving_pct": r["water_saving_pct"],
                "water_deficit_mm": r["water_deficit_mm"],
                "water_deficit_reduction_pct": r["water_deficit_reduction_pct"],
                "water_unmet_fraction": r["water_unmet_fraction"],
                "n_years": r["water"]["n_years"],
                "scores": r["scores"], "total": r["total"], "rank": i, "revenue": r["revenue"],
            }
            for i, r in enumerate(ordered, 1)
        ]
        current_overall_rank = next(x["rank"] for x in all_ranked if x["is_current"])
        current_is_strong = current_overall_rank <= 2
        alternatives = []
        for r in ordered:
            if r is current:
                continue
            if all({c["crop"] for c in r["crops"]} != {c["crop"] for c in a["crops"]} for a in alternatives):
                alternatives.append(r)
            if len(alternatives) == N_ALTERNATIVES:
                break
        shown = [current] + alternatives

    for i, rid in enumerate(scoring._rank(shown, weights), 1):
        next(r for r in shown if r["id"] == rid)["rank"] = i
    explain.explain_ranking(shown, "current", current_is_strong, current_overall_rank, len(all_ranked) or None,
                            market.basis_text(market_table) if market_table else None)

    for r in shown:
        r["confidence"] = _confidence(r, kc_table, exploratory)
        r["year_plan"] = planner.build_cycle_plan(r, clim_scen, area, water_access == "rainfed")
        r["n_years"] = r["water"]["n_years"]
        key = "water_deficit_mm" if water_access == "rainfed" else "irrigation_mm"
        r["score_details"]["water"]["per_crop"] = [
            {"crop": p["crop"], "year": p.get("year", 1), key: round(p["seasonal"]["irrigation_mm"], 1),
             "etc_mm": round(p["seasonal"]["etc_mm"], 1), "kc_status": p["kc_status"]}
            for p in r["water"]["per_crop"] if p["water_assessed"]
        ]
        if water_access == "rainfed":
            for entry in r["score_details"]["water"]["per_crop"]:
                etc = entry["etc_mm"]
                entry["unmet_fraction"] = round(entry[key] / etc, 3) if etc > 0 else 0.0
        if scoring.has_fallow_year(r) and scoring.FALLOW_MOISTURE_NOTE not in r["caveats"]:
            r["caveats"].append(scoring.FALLOW_MOISTURE_NOTE)
        r["windows"] = r["crops"]
        r["crops"] = [c["crop"] for c in r["windows"]]

    if clim_now.get("rain_record_warning"):
        warnings.append(clim_now["rain_record_warning"])
    water_assumption = (
        "Water score (rainfed) = clip(50 + 100 x (unmet fraction of your current rotation - this rotation's), "
        "0, 100), where a crop's unmet fraction = water deficit / crop water demand (ETc) and a rotation's is "
        "the mean over its crop seasons; your current rotation scores 50. The deficit in mm is shown for "
        "information only." if water_access == "rainfed" else
        "Water score = clip(50 + % irrigation saved versus your current rotation, 0, 100): your current "
        "rotation scores 50 and a 25% saving scores 75.")
    assumptions = list(water_balance.ASSUMPTIONS) + [
        water_assumption,
        scoring.HEAT_LABEL.capitalize() + ": " + scoring.HEAT_NOTE,
        "Soil-health practices (proxy) = legume in rotation (40) + plant-family diversity (30) + months with "
        "ground cover (30); it counts practices and is not a measured soil outcome.",
        "Kc values and regional calendars are provisional until verified against FAO-56 Table 12 and local sources.",
    ]
    if scenario.get("use_forecast"):
        assumptions.append(
            f"Scenario uses the projected {projection['year']} temperature and ET0 ({forecast.METHOD}) with "
            "climatological rain; this is not a weather forecast.")
    if market_table:
        assumptions.append(
            "Indicative gross revenue = national average yield x " + market.basis_text(market_table).split(",")[0]
            + "; it is shown for information only and is not part of the scores."
        )
    if any(scoring.has_fallow_year(r) for r in candidates):
        assumptions.append(scoring.FALLOW_MOISTURE_NOTE)
    if any(e.get("stage_fractions") for e in (pack or {}).get("crop_calendar", [])):
        assumptions.append(
            "Some crops use pack-specific FAO-56 stage lengths (e.g. autumn-sown winter wheat, so peak water "
            "use falls in spring); these are provisional.")
    if request.get("soil_texture"):
        assumptions.append("The soil texture you entered is recorded but not yet used in the scores.")

    return {
        "mode": "exploratory" if exploratory else "regional_pack",
        "pack": None if exploratory else {"id": pack["id"], "name": pack["name"]},
        "climate_now": clim_now,
        "climate_scenario": clim_scen,
        "rotations": shown,
        "all_ranked": all_ranked,
        "current_is_strong": current_is_strong,
        "sensitivity": scoring.sensitivity(shown, request["priorities"]),
        "warnings": warnings,
        "assumptions": assumptions,
        "priorities_used": {k: round(v, 3) for k, v in weights.items()},
        "projection": projection,
    }
