# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Plain-language explanation of a ranking (template-based, no LLM).

Input:  scored rotations (scoring.score_rotations), the id of the farmer's
        current rotation, and optionally how the current rotation ranks among
        all local options.
Output: the same rotations with "why" (2-3 sentences naming the actual numbers
        and the main trade-off) and a "not assessed" sentence. No yield or
        income claims.
"""

ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}
LABELS = {"water": "water saving", "soil_practices": "soil-health practices (proxy)",
          "heat_risk": "heat-stress exposure (proxy)"}


def _ordinal(n: int) -> str:
    return ORDINALS.get(n, f"{n}th")


def _water_sentence(r: dict, current: dict) -> str:
    need = r["water_mm_per_year"]
    if need is None:
        return "Its irrigation need could not be estimated (a crop has no Kc values)."
    vol = f" (about {r['water_m3_per_year']:,} m3 for your field)" if r.get("water_m3_per_year") is not None else ""
    if r is current or r.get("water_saving_pct") is None:
        return f"It needs about {need:.0f} mm of irrigation per year{vol}."
    pct, mm = r["water_saving_pct"], r["water_saving_mm"]
    if abs(mm) < 1:
        return f"It needs about {need:.0f} mm of irrigation per year{vol}, the same as your current rotation."
    direction = "less" if mm > 0 else "more"
    return (f"It needs about {need:.0f} mm of irrigation per year{vol}, {abs(mm):.0f} mm ({abs(pct):.0f}%) "
            f"{direction} than your current rotation.")


def _soil_reason(r: dict) -> str:
    d = r["score_details"]["soil_practices"]
    parts = ["includes a legume" if d["legume_in_rotation"] else "has no legume"]
    if d["n_crops"] > 1 and d["distinct_families"] < d["n_crops"]:
        parts.append("uses crops from the same plant family")
    fallow = 12 - d["months_with_ground_cover"]
    if fallow:
        parts.append(f"leaves {fallow} month{'s' if fallow > 1 else ''} without a crop")
    return ", ".join(parts)


def _heat_sentence(r: dict) -> str | None:
    h = r["score_details"]["heat_risk"]
    delta = h["temp_delta_c"]
    if not delta and h["score_current_climate"] >= 100:
        return None
    text = f"Heat-stress exposure (proxy) scores {h['score_current_climate']:.0f}/100 today"
    if delta:
        text += f" and {h['score_scenario']:.0f}/100 at {delta:+g} °C"
    return text + "."


def _trade_off(r: dict, current: dict) -> str | None:
    """The criterion where this rotation is furthest behind the current one."""
    worst, gap = None, 0.0
    for k in LABELS:
        a, b = r["scores"][k], current["scores"][k]
        if a is None or b is None:
            continue
        if b - a > gap:
            worst, gap = k, b - a
    if worst is None or gap < 1:
        return None
    return f"Trade-off: it scores {gap:.0f} points lower than your current rotation on {LABELS[worst]}."


def _gains(best: dict, current: dict) -> str:
    gains = []
    pct = best.get("water_saving_pct")
    if pct is not None and pct >= 1:
        gains.append(f"{pct:.0f}% less irrigation")
    for k in ("soil_practices", "heat_risk"):
        a, b = best["scores"][k], current["scores"][k]
        if a is not None and b is not None and a - b >= 1:
            gains.append(f"{a - b:.0f} points on {LABELS[k]}")
    if not gains:
        return f"no alternative scores higher than it on any criterion"
    return f"the best alternative ({best['name']}) would gain " + " and ".join(gains)


def not_assessed(r: dict) -> str:
    items = ["crop prices and income", "pests and diseases", "a field-level soil test"]
    rice = [p for p in r["water"]["per_crop"] if p["crop"] == "rice"]
    if rice and not any(p.get("paddy_extra_water_mm") is not None for p in rice):
        items.append("water for rice land preparation and percolation")
    return "Not assessed: " + ", ".join(items[:-1]) + " and " + items[-1] + "."


def explain_ranking(
    scored: list[dict], current_id: str, current_is_strong: bool = False,
    current_overall_rank: int | None = None, n_options: int | None = None,
) -> list[dict]:
    """Attach "why" and "not_assessed" text to each scored rotation."""
    current = next(r for r in scored if r["id"] == current_id)
    alternatives = sorted((r for r in scored if r is not current), key=lambda r: r["rank"])
    n = len(scored)
    for r in scored:
        s = r["scores"]
        if r is current and current_is_strong:
            head = (f"Your current rotation is already among the best local options "
                    f"(ranked {_ordinal(current_overall_rank)} of {n_options}, total {r['total']:.0f}/100); "
                    f"{_gains(alternatives[0], current) if alternatives else 'there is no alternative to compare'}.")
        elif r is current:
            head = f"Your current rotation ranks {_ordinal(r['rank'])} of {n} (total {r['total']:.0f}/100)."
        else:
            head = f"Ranks {_ordinal(r['rank'])} of {n} (total {r['total']:.0f}/100)."
        sentences = [head + " " + _water_sentence(r, current)]
        soil = f"Soil-health practices (proxy) score {s['soil_practices']:.0f}: it {_soil_reason(r)}."
        heat = _heat_sentence(r)
        sentences.append(soil + (f" {heat}" if heat else ""))
        if r is not current:
            t = _trade_off(r, current)
            if t:
                sentences.append(t)
        r["why"] = " ".join(sentences)
        r["not_assessed"] = not_assessed(r)
    return scored
