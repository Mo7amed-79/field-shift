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

from engine import climate, explain, market, planner, rotation_builder, scoring, suitability, water_balance

N_ALTERNATIVES = 2
SOURCE_PACK_ROTATION = "regional pack rotation (provisional)"
SOURCE_PACK_ROTATION_VERIFIED = "regional pack rotation (verified)"
EXPLORATORY_WARNING = (
    "Exploratory mode: there is no regional pack for this location, so the alternatives were generated "
    "from global ECOCROP estimates and have not been checked against local practice. Treat them as ideas "
    "to discuss with a local agronomist, not recommendations."
)
VERIFIED = "verified"


def _window(crop_id: str, label: str, start: int, end: int, source: str) -> dict:
    return {"crop": crop_id, "label": label, "start_month": start, "end_month": end,
            "months": suitability.months_between(start, end), "window_source": source}


def _from_pack_rotation(rot: dict, is_current: bool = False) -> dict:
    source = SOURCE_PACK_ROTATION_VERIFIED if rot.get("status") == VERIFIED else SOURCE_PACK_ROTATION
    return {
        "id": "current" if is_current else rot["id"],
        "name": rot["name"],
        "is_current": is_current,
        "source": "regional pack",
        "pack_rotation_id": rot["id"],
        "status": rot.get("status"),
        "crops": [_window(c["crop"], c["label"], c["start_month"], c["end_month"], source) for c in rot["crops"]],
        "caveats": [],
    }


def _current_in_pack(current: list[str], pack: dict, ranked_by_id: dict, crops_by_id: dict) -> dict:
    """Use a matching pack rotation's windows, else each crop's calendar window, else its global window."""
    for rot in pack.get("typical_rotations", []):
        if [c["crop"] for c in rot["crops"]] == current or (
            len(rot["crops"]) == len(current) and {c["crop"] for c in rot["crops"]} == set(current)
        ):
            r = _from_pack_rotation(rot, is_current=True)
            order = {c: i for i, c in enumerate(current)}
            r["crops"].sort(key=lambda c: order[c["crop"]])
            r["name"] = " → ".join(c["label"] for c in r["crops"])
            return r

    windows, caveats = [], []
    for crop_id in current:
        entry = next((e for e in pack.get("crop_calendar", []) if e["crop"] == crop_id), None)
        name = crops_by_id[crop_id]["name_en"]
        if entry:
            src = suitability.SOURCE_REGIONAL_VERIFIED if entry.get("status") == VERIFIED else suitability.SOURCE_REGIONAL
            windows.append(_window(crop_id, name, entry["start_month"], entry["end_month"], src))
        else:
            w = ranked_by_id[crop_id]["suitability"]["best_window"]
            windows.append(_window(crop_id, name, w["start_month"], w["end_month"], suitability.SOURCE_GLOBAL))
            caveats.append(f"{name} has no entry in the {pack['name']} calendar; its window is a global ECOCROP estimate.")
    return {
        "id": "current", "name": " → ".join(w["label"] for w in windows), "is_current": True,
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
    rot["water"] = water_balance.rotation_water_need(rot, clim_scen, kc_table, paddy_extra)
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
    overlap = rotation_builder._illegal_overlap(rot["crops"], allow_transition=True)
    if overlap:
        rot["caveats"].append(f"Crop windows overlap in months {overlap}; this sequence may not fit in one year.")
    rot["caveats"] += rot["water"]["caveats"] + rot["water"]["notes"]
    return rot


def run_compare(raw: dict, soil: dict | None, request: dict, crops: list[dict], kc_table: dict,
                pack: dict | None) -> dict:
    """Run the full comparison. Raises ValueError for crop ids not in the crop table."""
    crops_by_id = {c["id"]: c for c in crops}
    current_ids = list(request["current_rotation"])
    unknown = [c for c in current_ids if c not in crops_by_id]
    if unknown:
        raise ValueError(f"Unknown crop ids in current_rotation: {unknown}")

    scenario = request.get("scenario") or {}
    water_access = request["water_access"]
    area = request.get("area")
    clim_now = climate.summarize(raw)
    clim_scen = water_balance.scenario_climate(raw, scenario.get("temp_delta_c", 0) or 0)
    soil_ph = soil.get("ph") if soil else None
    ranked = suitability.rank_crops(crops, clim_now, soil, water_access, pack)
    ranked_by_id = {c["id"]: c for c in ranked}

    warnings = []
    exploratory = pack is None
    if exploratory:
        warnings.append(EXPLORATORY_WARNING)
        cands = rotation_builder.build_candidates(ranked, current_ids, N_ALTERNATIVES, climate_summary=clim_now)
        candidates = [_from_builder(c) for c in cands]
    else:
        current = _current_in_pack(current_ids, pack, ranked_by_id, crops_by_id)
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

    for rot in candidates:
        _enrich(rot, clim_now, clim_scen, crops_by_id, kc_table, water_access, soil_ph, exploratory,
                None if exploratory else pack.get("paddy_extra_water_mm"))
    scoring.score_rotations(candidates, request["priorities"], scenario, "current", area)

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
        r["year_plan"] = planner.build_year_plan(r, clim_scen, area)
        r["score_details"]["water"]["per_crop"] = [
            {"crop": p["crop"], "irrigation_mm": round(p["seasonal"]["irrigation_mm"], 1),
             "etc_mm": round(p["seasonal"]["etc_mm"], 1), "kc_status": p["kc_status"]}
            for p in r["water"]["per_crop"] if p["water_assessed"]
        ]
        r["windows"] = r["crops"]
        r["crops"] = [c["crop"] for c in r["windows"]]

    if clim_now.get("rain_record_warning"):
        warnings.append(clim_now["rain_record_warning"])
    assumptions = list(water_balance.ASSUMPTIONS) + [
        "Water score = clip(50 + % irrigation saved versus your current rotation, 0, 100): your current "
        "rotation scores 50 and a 25% saving scores 75.",
        scoring.HEAT_LABEL.capitalize() + ": " + scoring.HEAT_NOTE,
        "Soil-health practices (proxy) = legume in rotation (40) + plant-family diversity (30) + months with "
        "ground cover (30); it counts practices and is not a measured soil outcome.",
        "Kc values and regional calendars are provisional until verified against FAO-56 Table 12 and local sources.",
    ]
    if market_table:
        assumptions.append(
            "Indicative gross revenue = national average yield x " + market.basis_text(market_table).split(",")[0]
            + "; it is shown for information only and is not part of the scores."
        )
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
    }
