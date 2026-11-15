# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Build candidate crop rotations for a location from suitable crops.

Input:  ranked crops (suitability.rank_crops: crop dicts with a "suitability"
        block), the farmer's current rotation (crop ids), how many alternatives
        to produce, and optionally the climate summary and regional pack (used by
        R7). Rules come from data/rotation_rules.json.
Output: list of rotation dicts: the current rotation first (always, with any
        broken rules flagged), then up to ``n_alternatives`` alternatives.

A rotation is one 12-month cycle: 2 or 3 crops, each in one of its windows.
Hard rules filter alternatives; soft rules plus mean suitability rank them.
Consecutive pairs are taken cyclically (the last crop is followed by the first
one of the next year). Calendars are monthly, so one shared "transition month"
(harvest of one crop, sowing of the next) is allowed by R1.
"""

import itertools
import json
from pathlib import Path
from statistics import mean

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "rotation_rules.json"
CONFIDENCE_ORDER = ["low", "medium", "high"]


def load_rules(path: Path = RULES_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _rule(rules: dict, rule_id: str) -> dict:
    return next(r for r in rules["rules"] if r["id"] == rule_id)


def _gap_months(end_month: int, next_start: int) -> int:
    """Whole months strictly between a window's end and the next start; -1 for a shared transition month."""
    return (next_start - end_month) % 12 - 1


def _illegal_overlap(windows: list[dict], allow_transition: bool) -> list[int]:
    """Months covered by more than one window, other than allowed transition months."""
    counts: dict[int, int] = {}
    for w in windows:
        for m in w["months"]:
            counts[m] = counts.get(m, 0) + 1
    shared = {m for m, n in counts.items() if n > 1}
    if allow_transition:
        transitions = {
            a["end_month"] for a in windows for b in windows
            if a is not b and a["end_month"] == b["start_month"]
        }
        shared = {m for m in shared if not (m in transitions and counts[m] == 2)}
    return sorted(shared)


def _cool_warm(climate_summary: dict | None) -> tuple[set[int], set[int]] | None:
    """The location's 6 coolest and 6 warmest months by mean temperature."""
    if not climate_summary:
        return None
    by_temp = sorted(climate_summary["monthly_climatology"], key=lambda m: m["temp_c"])
    return {m["month"] for m in by_temp[:6]}, {m["month"] for m in by_temp[6:]}


def _longest_fallow_run(fallow: list[int]) -> int:
    if len(fallow) == 12:
        return 12
    fset, best = set(fallow), 0
    for start in fallow:
        if (start - 2) % 12 + 1 in fset:
            continue  # not the start of a run
        run, m = 0, start
        while m in fset:
            run, m = run + 1, m % 12 + 1
        best = max(best, run)
    return best


def _evaluate(sequence: list[dict], rules: dict, context: dict) -> dict:
    """Apply all rules to a sequence of {"crop": crop_dict, "window": {...}, "score": float}."""
    n = len(sequence)
    pairs = [(sequence[i], sequence[(i + 1) % n]) for i in range(n)] if n > 1 else []
    windows = [s["window"] for s in sequence if s["window"]]
    occupied = {m for w in windows for m in w["months"]}
    fallow = sorted(set(range(1, 13)) - occupied)
    results = []

    r1 = _rule(rules, "R1")
    overlap = _illegal_overlap(windows, r1.get("allow_transition_month", False))
    results.append({
        "id": "R1", "type": "hard", "passed": not overlap,
        "detail": f"overlapping months: {overlap}" if overlap else "no overlapping windows",
    })

    r2 = _rule(rules, "R2")
    low = [s["crop"]["id"] for s in sequence if s["score"] < r2["min_score"]]
    results.append({
        "id": "R2", "type": "hard", "passed": not low,
        "detail": f"below {r2['min_score']}: {low}" if low else f"all crops score >= {r2['min_score']}",
    })

    soft, soft_details = {}, {}
    r1b = _rule(rules, "R1b")
    if pairs and not overlap:
        gaps = [_gap_months(a["window"]["end_month"], b["window"]["start_month"]) for a, b in pairs]
        soft["R1b"] = sum(g >= r1b["preferred_gap_months"] for g in gaps) / len(gaps)
        soft_details["R1b"] = f"gaps between crops (months, -1 = shared transition month): {gaps}"
    else:
        soft["R1b"] = 0.0
        soft_details["R1b"] = "not assessed: windows overlap" if overlap else "single crop"

    soft["R3"] = 1.0 if any(s["crop"].get("is_legume") for s in sequence) else 0.0
    soft_details["R3"] = "legume present" if soft["R3"] else "no legume"

    if pairs:
        soft["R4"] = sum(a["crop"].get("family") != b["crop"].get("family") for a, b in pairs) / len(pairs)
        cereal_pairs = [(a, b) for a, b in pairs if a["crop"].get("category") == "cereal"]
        soft["R5"] = (
            sum(b["crop"].get("category") != "cereal" for _, b in cereal_pairs) / len(cereal_pairs)
            if cereal_pairs else 1.0
        )
    else:
        soft["R4"] = soft["R5"] = 1.0
    soft_details["R4"] = f"{soft['R4']:.0%} of consecutive pairs from different families"
    soft_details["R5"] = f"{soft['R5']:.0%} of cereals followed by a non-cereal"

    soft["R6"] = 1 - len(fallow) / 12
    soft_details["R6"] = f"{len(fallow)} fallow months"

    r7 = _rule(rules, "R7")
    seasons = context.get("seasons")
    run = _longest_fallow_run(fallow)
    if not context.get("two_season") or not seasons:
        soft["R7"] = 1.0
        soft_details["R7"] = "not applicable: location does not support two seasons"
    else:
        cool, warm = seasons
        in_cool = any(len(set(w["months"]) & cool) * 2 >= len(w["months"]) for w in windows)
        in_warm = any(len(set(w["months"]) & warm) * 2 >= len(w["months"]) for w in windows)
        long_fallow = run > r7["max_fallow_run_months"]
        soft["R7"] = (0.5 * (in_cool and in_warm)) + (0.5 * (not long_fallow))
        soft_details["R7"] = (
            f"cool-season crop: {'yes' if in_cool else 'no'}, warm-season crop: {'yes' if in_warm else 'no'}, "
            f"longest fallow run {run} months"
        )

    for rule_id, score in soft.items():
        results.append({
            "id": rule_id, "type": "soft", "passed": score >= 1.0,
            "score": round(score, 3), "weight": _rule(rules, rule_id)["weight"], "detail": soft_details[rule_id],
        })

    weights = {rid: _rule(rules, rid)["weight"] for rid in soft}
    rule_score = sum(weights[r] * soft[r] for r in soft) / sum(weights.values())
    mean_suit = mean(s["score"] for s in sequence) if sequence else 0.0
    sw = rules["suitability_weight"]
    total = (sw * mean_suit / 100 + (1 - sw) * rule_score) * 100

    return {
        "rules": results,
        "hard_rules_failed": [r["id"] for r in results if r["type"] == "hard" and not r["passed"]],
        "fallow_months": fallow,
        "longest_fallow_run": run,
        "mean_suitability": round(mean_suit, 1),
        "rule_score": round(rule_score * 100, 1),
        "total": round(total, 1),
    }


def _windows(crop: dict) -> list[dict]:
    return crop["suitability"]["alt_windows"]


def _confidence(sequence: list[dict]) -> str:
    levels = [s["crop"]["suitability"].get("confidence", "low") for s in sequence] or ["low"]
    return min(levels, key=CONFIDENCE_ORDER.index)


def _candidate(sequence: list[dict], rules: dict, context: dict, is_current: bool, rid: str) -> dict:
    ev = _evaluate(sequence, rules, context)
    return {
        "id": rid,
        "name": " → ".join(s["crop"]["name_en"] for s in sequence),
        "crops": [s["crop"]["id"] for s in sequence],
        "categories": [s["crop"].get("category") for s in sequence],
        "is_current": is_current,
        "confidence": _confidence(sequence),
        "sequence": [
            {
                "crop": s["crop"]["id"],
                "name_en": s["crop"]["name_en"],
                "window": s["window"],
                "window_source": s["crop"]["suitability"].get("window_source"),
                "suitability": s["score"],
            }
            for s in sequence
        ],
        **ev,
    }


def _by_start(sequence: list[dict]) -> list[dict]:
    return sorted(sequence, key=lambda s: s["window"]["start_month"])


def _window_only(w: dict) -> dict:
    return {k: w[k] for k in ("start_month", "end_month", "months")}


def _current(ranked_by_id: dict, current_rotation: list[str], rules: dict, context: dict) -> dict:
    """Best placement of the farmer's crops in their windows; broken rules are flagged."""
    known = [ranked_by_id[c] for c in current_rotation if c in ranked_by_id]
    unknown = [c for c in current_rotation if c not in ranked_by_id]

    best = None
    for combo in itertools.product(*(_windows(c) for c in known)):
        seq = [{"crop": c, "window": _window_only(w), "score": w["score"]} for c, w in zip(known, combo)]
        ev = _evaluate(_by_start(seq), rules, context)
        key = (len(ev["hard_rules_failed"]), -ev["total"])
        if best is None or key < best[0]:
            best = (key, seq)

    seq = best[1] if best else []
    order = {c: i for i, c in enumerate(current_rotation)}  # keep the farmer's order
    seq.sort(key=lambda s: order[s["crop"]["id"]])
    cand = _candidate(seq, rules, context, is_current=True, rid="current")
    if unknown:
        cand["confidence"] = "low"
        cand["hard_rules_failed"].append("unknown_crop")
        cand["rules"].append({
            "id": "unknown_crop", "type": "hard", "passed": False,
            "detail": f"not in the crop database: {unknown}",
        })
    return cand


def is_distinct(a: dict, b: dict) -> bool:
    """Rotations differ by at least two crops, or by their category composition."""
    sa, sb = set(a["crops"]), set(b["crops"])
    if max(len(sa - sb), len(sb - sa)) >= 2:
        return True
    return sorted(a["categories"], key=str) != sorted(b["categories"], key=str)


def _supports_two_seasons(options: list[tuple], min_score: float, allow_transition: bool) -> bool:
    good = [(c, w) for c, w, s in options if s >= min_score]
    return any(
        ca["id"] != cb["id"] and not _illegal_overlap([wa, wb], allow_transition)
        for (ca, wa), (cb, wb) in itertools.combinations(good, 2)
    )


def build_candidates(
    ranked_crops: list[dict],
    current_rotation: list[str],
    n_alternatives: int = 2,
    rules: dict | None = None,
    climate_summary: dict | None = None,
    pack: dict | None = None,
) -> list[dict]:
    """Return the current rotation plus up to ``n_alternatives`` rule-compliant alternatives.

    Alternatives are 2- or 3-crop annual sequences of crops scoring >= the R2
    minimum that pass every hard rule. They are ranked by total score, then
    soft-rule score, fewer fallow months and crop ids, and must be distinct
    (``is_distinct``) from the current rotation and from each other.
    """
    rules = rules or load_rules()
    min_score = _rule(rules, "R2")["min_score"]
    allow_transition = _rule(rules, "R1").get("allow_transition_month", False)
    ranked_by_id = {c["id"]: c for c in ranked_crops}

    options = [
        (crop, _window_only(w), w["score"])
        for crop in ranked_crops
        for w in _windows(crop)
        if w["score"] >= min_score
    ]
    context = {
        "seasons": _cool_warm(climate_summary),
        "two_season": pack is not None
        or _supports_two_seasons(options, _rule(rules, "R7")["two_season_min_score"], allow_transition),
    }

    best_per_set: dict[frozenset, tuple] = {}
    for size in (2, 3):
        for combo in itertools.combinations(options, size):
            ids = [c["id"] for c, _, _ in combo]
            if len(set(ids)) < size or _illegal_overlap([w for _, w, _ in combo], allow_transition):
                continue
            seq = _by_start([{"crop": c, "window": w, "score": s} for c, w, s in combo])
            ev = _evaluate(seq, rules, context)
            if ev["hard_rules_failed"]:
                continue
            key = frozenset(ids)
            rank = (-ev["total"], -ev["rule_score"], len(ev["fallow_months"]), tuple(sorted(ids)))
            if key not in best_per_set or rank < best_per_set[key][0]:
                best_per_set[key] = (rank, seq)

    current = _current(ranked_by_id, current_rotation, rules, context)
    alternatives: list[dict] = []
    for rank, seq in sorted(best_per_set.values(), key=lambda v: v[0]):
        cand = _candidate(seq, rules, context, is_current=False, rid=f"alt_{len(alternatives) + 1}")
        if all(is_distinct(cand, other) for other in [current, *alternatives]):
            alternatives.append(cand)
            if len(alternatives) == n_alternatives:
                break

    margin = rules.get("tie_margin_points", 2)
    for alt in alternatives:
        close = [o["id"] for o in alternatives if o is not alt and abs(o["total"] - alt["total"]) <= margin]
        if close:
            alt["tie_note"] = (
                f"Within {margin} points of {', '.join(close)}: the ranking will be separated by water and "
                "soil scoring in the next step."
            )
    return [current] + alternatives
