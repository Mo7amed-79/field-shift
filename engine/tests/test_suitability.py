# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for crop suitability and rotation building."""

import copy
import itertools
import json
from pathlib import Path

import pytest

from engine import climate, power_client, regional, rotation_builder, soil_client
from engine.suitability import crop_suitability, cycle_months, envelope_score, rank_crops

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
CROPS = json.loads((ROOT / "data" / "crops.json").read_text(encoding="utf-8"))
COOL_SEASON = {10, 11, 12, 1, 2, 3, 4}
WARM_SEASON = {4, 5, 6, 7, 8, 9, 10}


def _climate(name: str, lat: float, lon: float, start: int, end: int) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw = power_client.parse_response(data, "fixture", lat, lon, "fixture", is_cached=True)
    return climate.summarize(raw)


def _soil(name: str) -> dict:
    return soil_client.parse_response(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def tanta():
    return _climate("power_30.85_31.05_2015_2025.json", 30.85, 31.05, 2015, 2025), _soil("soil_30.850_31.050.json")


@pytest.fixture(scope="module")
def ludhiana():
    return _climate("power_30.80_75.60_1991_2025.json", 30.80, 75.60, 1991, 2025), _soil("soil_30.800_75.600.json")


def _crop(crop_id: str) -> dict:
    return next(c for c in CROPS if c["id"] == crop_id)


# --- envelope -------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        (5, 0),  # at absolute min
        (4, 0),  # below absolute min
        (10, 50),  # halfway up the lower ramp
        (15, 100),  # at optimal min
        (20, 100),  # inside optimum
        (25, 100),  # at optimal max
        (30, 50),  # halfway down the upper ramp
        (35, 0),  # at absolute max
        (40, 0),  # above absolute max
    ],
)
def test_envelope_boundaries(value, expected):
    assert envelope_score(value, 5, 15, 25, 35) == pytest.approx(expected)


# --- suitability ------------------------------------------------------------

def test_tanta_full_irrigation_seasons(tanta):
    clim, soil = tanta
    wheat = crop_suitability(_crop("wheat"), clim, soil, "full_irrigation")
    assert set(wheat["best_window"]["months"]) <= COOL_SEASON
    warm = [crop_suitability(_crop(c), clim, soil, "full_irrigation") for c in ("rice", "maize")]
    assert any(set(s["best_window"]["months"]) <= WARM_SEASON for s in warm)


def test_full_irrigation_does_not_apply_rain(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("rice"), clim, soil, "full_irrigation")
    assert s["details"]["rainfall"]["applied"] is False
    assert "not applied" in s["details"]["rainfall"]["note"].lower()
    assert s["limiting_factor"] != "rainfall"


def test_tanta_rainfed_rice_fails_on_rain(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("rice"), clim, soil, "rainfed")
    assert s["score"] <= 5
    assert s["limiting_factor"] == "rainfall"


def test_limited_irrigation_averages_rain_score(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("rice"), clim, soil, "limited_irrigation")
    rain = s["details"]["rainfall"]
    assert rain["score"] == pytest.approx((rain["rainfed_score"] + 100) / 2, abs=0.1)


def test_ph_not_assessed_without_soil(tanta):
    clim, _ = tanta
    s = crop_suitability(_crop("wheat"), clim, None, "full_irrigation")
    assert "ph" in s["not_assessed"] and "ph" not in s["details"]


def test_killing_temperature_zeroes_score(tanta):
    clim, soil = tanta
    crop = copy.deepcopy(_crop("wheat"))
    crop["temp_kill_c"] = 50  # every month's minimum is below this
    s = crop_suitability(crop, clim, soil, "full_irrigation")
    assert s["score"] == 0 and s["details"]["temperature"]["killed"] is True


def test_top_windows_returned(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("maize"), clim, soil, "full_irrigation")
    assert len(s["alt_windows"]) == 3
    assert s["alt_windows"][0]["start_month"] == s["best_window"]["start_month"]
    scores = [w["score"] for w in s["alt_windows"]]
    assert scores == sorted(scores, reverse=True)


def test_rankings_differ_between_locations(tanta, ludhiana):
    order_t = [c["id"] for c in rank_crops(CROPS, *tanta, "full_irrigation")]
    order_l = [c["id"] for c in rank_crops(CROPS, *ludhiana, "full_irrigation")]
    assert order_t != order_l


# --- rotation builder -------------------------------------------------------

@pytest.fixture(scope="module")
def tanta_candidates(tanta):
    ranked = rank_crops(CROPS, *tanta, "full_irrigation")
    return ranked, rotation_builder.build_candidates(ranked, ["rice", "wheat"])


def test_current_rotation_always_first(tanta_candidates):
    _, cands = tanta_candidates
    assert cands[0]["is_current"] is True
    assert cands[0]["crops"] == ["rice", "wheat"]
    assert len(cands) == 3


def test_alternatives_pass_hard_rules(tanta_candidates):
    _, cands = tanta_candidates
    for alt in cands[1:]:
        assert alt["hard_rules_failed"] == []
        # R1, checked independently: a month may be shared only as a harvest->sowing transition.
        windows = [s["window"] for s in alt["sequence"]]
        months = [m for w in windows for m in w["months"]]
        shared = {m for m in months if months.count(m) > 1}
        assert all(months.count(m) == 2 for m in shared)
        assert shared <= {w["end_month"] for w in windows} & {w["start_month"] for w in windows}
        assert all(s["suitability"] >= 40 for s in alt["sequence"])  # R2


def test_alternatives_differ_from_current_and_each_other(tanta_candidates):
    _, cands = tanta_candidates
    for a, b in itertools.combinations(cands, 2):
        assert rotation_builder.is_distinct(a, b)


def test_current_rotation_flags_broken_rules(tanta):
    ranked = rank_crops(CROPS, *tanta, "rainfed")
    cands = rotation_builder.build_candidates(ranked, ["rice", "not_a_crop"])
    current = cands[0]
    assert current["is_current"] is True
    assert "R2" in current["hard_rules_failed"]  # rainfed rice scores ~0
    assert "unknown_crop" in current["hard_rules_failed"]


def test_rotation_rules_have_sources():
    rules = rotation_builder.load_rules()
    ids = {r["id"] for r in rules["rules"]}
    assert {"R1", "R1b", "R2", "R3", "R4", "R5", "R6", "R7"} <= ids
    for r in rules["rules"]:
        assert r["type"] in ("hard", "soft") and r["source"]
        if r["type"] == "soft":
            assert r["weight"] > 0


# --- crops.json ------------------------------------------------------------

def test_crops_built_from_ecocrop():
    ids = {c["id"] for c in CROPS}
    assert {"rice", "wheat", "maize", "berseem", "cotton", "faba_bean"} <= ids
    for c in CROPS:
        assert c["source"] == "FAO ECOCROP via OpenCLIM/ecocrop" and c["ecocrop_id"]
        assert c["is_legume"] == (c["family"] == "Fabaceae")
        assert c["kc_mid"] is None


# --- temperature, pH, cycle -------------------------------------------------

def test_temperature_is_mean_of_monthly_scores(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("wheat"), clim, soil, "full_irrigation")
    t = s["details"]["temperature"]
    assert t["score"] == pytest.approx(sum(t["monthly_scores"]) / len(t["monthly_scores"]), abs=0.1)


def test_temperature_zero_if_any_month_zero(tanta):
    clim, soil = tanta
    crop = copy.deepcopy(_crop("rice"))
    crop["temp_abs_max_c"] = 29.0  # July/August (~29.6 °C) now score 0
    crop["temp_opt_max_c"] = 28.0
    pack = regional.find_pack(30.85, 31.05)  # rice May-Oct includes July
    s = crop_suitability(crop, clim, soil, "full_irrigation", pack)
    assert s["details"]["temperature"]["any_month_zero_before_harvest"] is True
    assert s["score"] == 0


def test_ph_is_advisory_only(tanta):
    clim, soil = tanta
    berseem = _crop("berseem")
    alkaline = {**soil, "ph": 8.3}  # above berseem's ECOCROP absolute max (7.5)
    s_ok = crop_suitability(berseem, clim, soil, "full_irrigation")
    s_alk = crop_suitability(berseem, clim, alkaline, "full_irrigation")
    assert s_alk["score"] == s_ok["score"]
    assert s_alk["ph_advisory"]["status"] == "outside_absolute"
    assert s_alk["limiting_factor"] != "ph"
    assert any("pH" in c for c in s_alk["caveats"])


@pytest.mark.parametrize("ph, status", [(6.8, "ok"), (7.3, "outside_optimal"), (8.0, "outside_absolute")])
def test_ph_advisory_status(tanta, ph, status):
    clim, soil = tanta
    s = crop_suitability(_crop("berseem"), clim, {**soil, "ph": ph}, "full_irrigation")
    assert s["ph_advisory"]["status"] == status


def test_cycle_months_formula():
    # maize: GMIN 65, GMAX 365 -> 65 + 75 = 140 days -> 5 months (was 7)
    assert cycle_months(_crop("maize")) == 5
    # clamp: short and long cycles
    assert cycle_months({"cycle_days_min": 30, "cycle_days_max": 40}) == 2
    assert cycle_months({"cycle_days_min": 300, "cycle_days_max": 365}) == 8


def test_perennial_treated_as_annual(tanta):
    clim, soil = tanta
    s = crop_suitability(_crop("alfalfa"), clim, soil, "full_irrigation")
    assert "treated as an annual" in s["details"]["cycle"]["perennial_note"]


# --- regional packs ---------------------------------------------------------

def test_find_pack():
    assert regional.find_pack(30.85, 31.05)["id"] == "egypt_nile_delta"
    assert regional.find_pack(30.80, 75.60)["id"] == "india_punjab"
    assert regional.find_pack(52.0, 5.6) is None


def test_pack_window_used_with_medium_confidence(tanta):
    clim, soil = tanta
    pack = regional.find_pack(30.85, 31.05)
    wheat = crop_suitability(_crop("wheat"), clim, soil, "full_irrigation", pack)
    assert wheat["best_window"]["months"] == [11, 12, 1, 2, 3, 4, 5]
    assert wheat["window_source"] == "regional calendar (provisional)"
    assert wheat["confidence"] == "medium"
    assert wheat["details"]["temperature"]["monthly_scores"]  # still computed and shown
    pea = crop_suitability(_crop("pea"), clim, soil, "full_irrigation", pack)  # no calendar entry
    assert pea["window_source"] == "global ECOCROP estimate" and pea["confidence"] == "low"


def test_pack_rice_wheat_passes_hard_rules(tanta):
    clim, soil = tanta
    pack = regional.find_pack(30.85, 31.05)
    ranked = rank_crops(CROPS, clim, soil, "full_irrigation", pack)
    cands = rotation_builder.build_candidates(ranked, ["rice", "wheat"], climate_summary=clim, pack=pack)
    current = cands[0]
    assert current["hard_rules_failed"] == []  # May is a shared transition month
    assert current["confidence"] == "medium"
    for c in cands:
        r7 = next(r for r in c["rules"] if r["id"] == "R7")
        assert "not applicable" not in r7["detail"]
    for alt in cands[1:]:
        assert alt["confidence"] in ("low", "medium")


def test_r7_penalises_long_fallow(tanta):
    clim, soil = tanta
    ranked = {c["id"]: c for c in rank_crops(CROPS, clim, soil, "full_irrigation")}
    rules = rotation_builder.load_rules()
    context = {"seasons": rotation_builder._cool_warm(clim), "two_season": True}

    def seq(*items):
        return [{"crop": ranked[c], "window": {"start_month": m[0], "end_month": m[-1], "months": m}, "score": 100}
                for c, m in items]

    summer_only = rotation_builder._evaluate(seq(("rice", [5, 6, 7, 8]), ("sesame", [9, 10])), rules, context)
    two_season = rotation_builder._evaluate(seq(("rice", [5, 6, 7, 8, 9, 10]), ("wheat", [11, 12, 1, 2, 3, 4])), rules, context)
    r7 = lambda ev: next(r for r in ev["rules"] if r["id"] == "R7")["score"]
    assert r7(two_season) == 1.0
    assert r7(summer_only) == 0.0  # no cool-season crop and a 6-month fallow run


def test_tie_note_and_determinism(tanta):
    clim, soil = tanta
    ranked = rank_crops(CROPS, clim, soil, "full_irrigation")
    a = rotation_builder.build_candidates(ranked, ["rice", "wheat"], climate_summary=clim)
    b = rotation_builder.build_candidates(ranked, ["rice", "wheat"], climate_summary=clim)
    assert [c["crops"] for c in a] == [c["crops"] for c in b]
    alts = a[1:]
    if len(alts) == 2 and abs(alts[0]["total"] - alts[1]["total"]) <= 2:
        assert all("tie_note" in alt for alt in alts)


def test_reviewed_categories_and_alias():
    assert all(c["category"] for c in CROPS)
    tomato = _crop("tomato")
    assert tomato["ecocrop_name"] == "Lycopersicon esculentum" and tomato["category"] == "vegetable"
    assert _crop("berseem")["category"] == "forage_legume" and _crop("cotton")["category"] == "fiber"
