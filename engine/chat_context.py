# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Grounding context for the farmer Q&A (POST /api/chat). Deterministic, no LLM.

Input:  a /api/compare response (as a dict), the crop table (data/crops.json),
        the Kc table (data/kc_fao56.json, raw), the regional pack (or None)
        and data/sources.json.
Output: {"text": the context as labelled plain-text sections, "sections": the
        section names in order, "approx_tokens", "dropped": what was left out to
        stay under the size limit}.

Each section starts with "## <NAME>" so the model can name the sections it drew
on. Only numbers already computed by the engine appear here; nothing new is
calculated except unit-free formatting (rounding, month names).
"""

import math

MAX_TOKENS = 6000
CHARS_PER_TOKEN = 3.5  # conservative for English text with many numbers
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
PLAN_ROTATIONS = 3

SCORING_TEXT = """\
- Each rotation gets three scores from 0 to 100 and a total = weighted mean of the three, using the farmer's priorities (shown under FIELD AND REQUEST).
- Water score, irrigated farming: 50 + percent of irrigation water saved compared with the farmer's current rotation, limited to 0..100. The current rotation is always 50; a 25% saving gives 75; needing more water gives less than 50.
- Water score, rainfed farming: for each crop season, unmet fraction = water deficit / crop water demand (ETc); a rotation's value is the mean over its crop seasons (fallow years are not seasons). Score = 50 + 100 x (current unmet fraction - this rotation's), limited to 0..100. The deficit in mm is information only.
- Irrigation need per month = crop water demand (ETc = reference evapotranspiration ET0 x crop coefficient Kc) minus effective rain, never below 0. Effective rain = 0.8 x monthly rain. There is no soil water storage, leaching or canal loss in this calculation.
- Soil-health practices score (a proxy, not a soil measurement): up to 40 points if the rotation includes a legume, up to 30 for plant-family diversity, up to 30 for months of the year with a crop in the field.
- Heat-stress exposure score (a proxy): 100 minus 15 points per degree C that the monthly mean temperature is above the crop's optimal maximum (harvest month excluded). It does not model heat at flowering or daily maximum temperatures.
- Indicative gross revenue = national average yield x a price snapshot. Production costs are not included, it is not profit, and it is NOT part of any score.
- Not assessed by this tool: production costs and profit, pests and diseases, pesticides and fertiliser, a field-level soil test, market access, labour."""


def approx_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _n(x, nd: int = 1) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        x = round(x, nd)
        if x == int(x):
            return f"{int(x):,}"
        return f"{x:,.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def _months(ms: list[int]) -> str:
    return f"{MONTHS[ms[0] - 1]}-{MONTHS[ms[-1] - 1]}" if ms else "none"


def _field(c: dict, crops_by_id: dict) -> str:
    loc, pack = c["location"], c.get("pack") or {}
    rot0 = c["rotations"][0]
    area_line = "not given"
    rev = rot0.get("revenue") or {}
    if rev.get("area"):
        area_line = f"{_n(rev['area']['value'])} {rev['area']['unit']}"
    else:
        for m in rot0.get("year_plan", []):
            if m.get("irrigation_m3") is not None:
                area_line = "given (m3 figures are for the farmer's field)"
                break
    water_access = "rainfed" if rot0.get("water_criterion_label") == "water-deficit exposure" else "irrigated"
    sc = c.get("scenario") or {}
    lines = [
        f"Location: lat {loc['lat']}, lon {loc['lon']}; region: {pack.get('name') or 'no regional pack (exploratory mode)'}.",
        f"Mode: {c['mode']}. Water access: {water_access}. Field area: {area_line}.",
        f"Farmer's current rotation: {rot0['name']} (source: {c.get('current_rotation_source', 'farmer')}).",
        "Priorities used (weights): " + ", ".join(f"{k} {_n(v, 3)}" for k, v in c["priorities_used"].items()) + ".",
        f"Scenario: temperature change {sc.get('temp_delta_c', 0)} C, water cut {sc.get('water_cut_pct', 0)}%, "
        f"use forecast {bool(sc.get('use_forecast'))}.",
        f"Climate: {c['climate']['source']}, period {c['climate']['period']}.",
    ]
    return "\n".join(lines)


def _revenue(rev: dict | None) -> str:
    if not rev or not rev.get("available"):
        return "Revenue: not available for this region."
    cur = rev.get("currency")
    parts = []
    for p in rev["per_crop"]:
        if p["missing"]:
            parts.append(f"{p['label']}: missing {' and '.join(p['missing'])}")
        else:
            parts.append(f"{p['label']}: yield {_n(p['yield_t_per_ha'], 2)} t/ha x price {_n(p['price_per_t'])} "
                         f"{cur}/t = {_n(p['revenue_per_feddan'])} {cur}/feddan"
                         + (f", {_n(p['revenue_for_area'])} {cur} for the field" if p.get("revenue_for_area") else ""))
    if rev["complete"]:
        total = (f"total {_n(rev['annual_per_feddan'])} {cur}/feddan per year"
                 + (f", {_n(rev['annual_for_area'])} {cur} per year for the field" if rev.get("annual_for_area") else ""))
    else:
        total = "total not computable (missing data)"
    change = rev.get("revenue_change_pct")
    change_txt = f"; change vs current rotation {_n(change)}%" if change is not None else ""
    return (f"Indicative gross revenue (no costs, not scored; {rev.get('price_snapshot')}; status: {rev.get('status')}): "
            + "; ".join(parts) + f"; {total}{change_txt}. Notes: " + " ".join(rev.get("notes", [])))


def _rotation(r: dict, current: dict) -> str:
    sd = r["score_details"]
    windows = ", ".join(
        f"{w['label']} {_months(w['months'])}" + (f" (year {w['year']})" if r.get("n_years", 1) > 1 else "")
        for w in r["windows"])
    lines = [f"### {r['name']}{' (CURRENT ROTATION)' if r['is_current'] else ''}",
             f"Rank {r['rank']} among the rotations shown; total {_n(r['total'])}/100; confidence {r['confidence']}; "
             f"cycle {r.get('n_years', 1)} year(s).",
             f"Crops and months: {windows}."]
    if r.get("water_deficit_mm") is not None or r.get("water_criterion_label") == "water-deficit exposure":
        per = "; ".join(f"{p['crop']} rain covers {_n(100 * p['rain_cover_fraction'], 0)}% of demand "
                        f"(deficit {_n(p['water_deficit_mm'])} mm of ETc {_n(p['etc_mm'])} mm)"
                        for p in r.get("water_unmet_per_crop") or [])
        lines.append(f"Water (rainfed): unmet fraction {_n(r.get('water_unmet_fraction'), 3)}; deficit "
                     f"{_n(r['water_deficit_mm'])} mm/year (information only); {per}.")
    else:
        per = "; ".join(f"{p['crop']} {_n(p['irrigation_mm'])} mm (ETc {_n(p['etc_mm'])} mm)"
                        for p in sd["water"].get("per_crop", []))
        lines.append(
            f"Water (irrigated): irrigation need {_n(r['water_mm_per_year'])} mm/year"
            + (f" = {_n(r['water_m3_per_year'])} m3/year for the field" if r.get("water_m3_per_year") is not None else "")
            + (f"; saving vs current {_n(r['water_saving_mm'])} mm = {_n(r['water_saving_pct'])}%"
               if not r["is_current"] and r.get("water_saving_pct") is not None else "")
            + (f" ({_n(r['water_saving_m3'])} m3)" if not r["is_current"] and r.get("water_saving_m3") is not None else "")
            + f". Per crop: {per}.")
        if r.get("exceeds_water_budget") is not None:
            lines.append(f"Water budget {_n(r['water_budget_mm'])} mm: exceeded = {r['exceeds_water_budget']}.")
    s = r["scores"]
    soil = sd["soil_practices"]
    parts = ", ".join(f"{k.replace('_', ' ')} {_n(v)}/{_n(soil['max_points'][k])}" for k, v in soil["parts"].items())
    heat = "; ".join(f"{h['crop']} {_n(h['score_scenario'])} (mean excess {_n(h['mean_excess_c_scenario'], 2)} C)"
                     for h in sd["heat_risk"]["per_crop"])
    lines.append(f"Scores: water {_n(s['water'])}, soil-health practices {_n(s['soil_practices'])} ({parts}; "
                 f"{soil['distinct_families']} plant families in {soil['n_crops']} crops; legume: "
                 f"{'yes' if soil['legume_in_rotation'] else 'no'}), heat-stress exposure {_n(s['heat_risk'])} ({heat}).")
    lines.append(_revenue(r.get("revenue")))
    lines.append(f"Engine explanation: {r['why']}")
    lines.append(r["not_assessed"])
    if r.get("caveats"):
        lines.append("Caveats: " + " | ".join(r["caveats"]))
    return "\n".join(lines)


def _all_ranked(c: dict) -> str:
    if not c.get("all_ranked"):
        return "Only the rotations above were compared (exploratory mode)."
    rows = []
    for x in c["all_ranked"]:
        rev = x.get("revenue") or {}
        rev_txt = (f"revenue {_n(rev.get('annual_per_feddan'))} {rev.get('currency')}/feddan/yr"
                   + (f" ({_n(rev['revenue_change_pct'])}% vs current)" if rev.get("revenue_change_pct") is not None else "")
                   if rev.get("complete") else "revenue not computable")
        water = (f"irrigation {_n(x['water_mm_per_year'])} mm/yr, saving {_n(x['water_saving_pct'])}%"
                 if x.get("water_mm_per_year") is not None else
                 f"deficit {_n(x.get('water_deficit_mm'))} mm/yr, unmet fraction {_n(x.get('water_unmet_fraction'), 3)}")
        sc = x["scores"]
        rows.append(f"{x['rank']}. {x['name']}{' (CURRENT)' if x['is_current'] else ''}: total {_n(x['total'])}; "
                    f"water {_n(sc['water'])}, soil {_n(sc['soil_practices'])}, heat {_n(sc['heat_risk'])}; {water}; {rev_txt}")
    return f"All {len(rows)} local options in the regional pack, best first:\n" + "\n".join(rows)


def _sensitivity(c: dict) -> str:
    s = c["sensitivity"]
    cases = "; ".join(f"{k['name']}: {' > '.join(k['ranking'])}" for k in s["cases"])
    return f"{s['note']} Rankings by weighting: {cases}."


def _plan(r: dict) -> str:
    rows = []
    for m in r["year_plan"]:
        y = f"Y{m.get('year', 1)} " if r.get("n_years", 1) > 1 else ""
        water = (f"irrigation {_n(m['irrigation_mm'])} mm" + (f" ({_n(m['irrigation_m3'])} m3)" if m.get("irrigation_m3") is not None else "")
                 if m.get("irrigation_mm") is not None else f"deficit {_n(m.get('water_deficit_mm'))} mm")
        rows.append(f"{y}{MONTHS[m['month'] - 1]}: {m.get('crop') or 'no crop'} {m.get('activity') or ''}, Kc {_n(m.get('kc'), 2)}, "
                    f"ETc {_n(m.get('etc_mm'))} mm, rain {_n(m.get('rain_mm'))} mm, {water}"
                    + (f" - {m['note']}" if m.get("note") else ""))
    return f"### Monthly plan: {r['name']}\n" + "\n".join(rows)


def _climate(c: dict) -> str:
    rows = [f"{MONTHS[m['month'] - 1]}: mean {_n(m.get('temp_c'))} C (max {_n(m.get('tmax_c'))}, min {_n(m.get('tmin_c'))}), "
            f"rain {_n(m.get('rain_mm'))} mm, ET0 {_n(m.get('et0_mm'))} mm"
            for m in c["climate"]["monthly"]]
    return f"Monthly climatology ({c['climate']['source']}, {c['climate']['period']}):\n" + "\n".join(rows)


def _crops(c: dict, crops_by_id: dict, kc: dict, pack: dict | None) -> str:
    ids = []
    for r in c["rotations"]:
        for w in r["windows"]:
            if w["crop"] not in ids:
                ids.append(w["crop"])
    calendar = {e["crop"]: e for e in (pack or {}).get("crop_calendar", [])}
    rows = []
    for cid in ids:
        info = crops_by_id.get(cid, {})
        k = kc.get("crops", {}).get(cid)
        cal = calendar.get(cid)
        cal_txt = (f"regional calendar {MONTHS[cal['start_month'] - 1]}-{MONTHS[cal['end_month'] - 1]} ({cal.get('status')})"
                   if cal else "no regional calendar entry")
        kc_txt = (f"Kc initial {_n(k['kc_ini'], 2)} / mid {_n(k['kc_mid'], 2)} / end {_n(k['kc_end'], 2)} ({k.get('status')})"
                  if k else "no Kc values")
        rows.append(
            f"{info.get('name_en', cid)} ({info.get('name_ar', '')}; {info.get('scientific_name', '')}): family "
            f"{info.get('family')}, legume {'yes' if info.get('is_legume') else 'no'}; {cal_txt}; {kc_txt}; ECOCROP "
            f"temperature optimum {_n(info.get('temp_opt_min_c'))}-{_n(info.get('temp_opt_max_c'))} C, absolute "
            f"{_n(info.get('temp_abs_min_c'))}-{_n(info.get('temp_abs_max_c'))} C.")
    return "\n".join(rows)


def _sources(sources: dict) -> str:
    rows = []
    for s in sources.get("sources", []):
        flags = []
        if s.get("license") and "TODO" in str(s["license"]):
            flags.append("license not yet verified")
        if s.get("status"):
            flags.append(str(s["status"]))
        rows.append(f"- {s['name']}" + (f" [{'; '.join(flags)}]" if flags else ""))
    rows.append("- Kc values (FAO-56 Table 12), regional calendars and rotations: provisional, not yet verified.")
    return "\n".join(rows)


def build_context(compare: dict, crops: list[dict], kc: dict, pack: dict | None, sources: dict,
                  max_tokens: int = MAX_TOKENS) -> dict:
    """Context sections for the chat model; year plans of lower-ranked rotations are dropped first."""
    crops_by_id = {c["id"]: c for c in crops}
    current = next(r for r in compare["rotations"] if r["is_current"])
    by_rank = sorted(compare["rotations"], key=lambda r: r["rank"])
    sections = [
        ("FIELD AND REQUEST", _field(compare, crops_by_id)),
        ("ROTATIONS COMPARED", "\n\n".join(_rotation(r, current) for r in by_rank)),
        ("ALL LOCAL OPTIONS", _all_ranked(compare)),
        ("SENSITIVITY", _sensitivity(compare)),
        ("WARNINGS", "\n".join(f"- {w}" for w in compare["warnings"]) or "None."),
        ("ASSUMPTIONS", "\n".join(f"- {a}" for a in compare["assumptions"])),
        ("SCORING DEFINITIONS", SCORING_TEXT),
        ("CROP FACTS", _crops(compare, crops_by_id, kc, pack)),
        ("CLIMATE (NASA POWER)", _climate(compare)),
        ("DATA SOURCES", _sources(sources)),
    ]
    plans = [(f"MONTHLY PLAN {i}", _plan(r)) for i, r in enumerate(by_rank[:PLAN_ROTATIONS], 1)]

    def render(secs):
        return "\n\n".join(f"## {name}\n{body}" for name, body in secs)

    dropped = []
    while True:
        secs = sections[:-1] + plans + sections[-1:]
        text = render(secs)
        if approx_tokens(text) <= max_tokens or not plans:
            break
        name, body = plans.pop()  # lowest-ranked plan first
        dropped.append(body.splitlines()[0].removeprefix("### "))
    if approx_tokens(text) > max_tokens:
        secs = [s for s in secs if s[0] != "CLIMATE (NASA POWER)"]
        dropped.append("monthly climatology")
        text = render(secs)
    return {"text": text, "sections": [s[0] for s in secs], "approx_tokens": approx_tokens(text), "dropped": dropped}
