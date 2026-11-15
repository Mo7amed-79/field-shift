# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Rainfed water criterion scored on relative stress (deficit / ETc), fallow note, winter wheat stages."""

import pytest

from engine import regional, scoring, water_balance
from engine.tests.test_compare import KC, TANTA
from engine.tests.test_us_packs import IOWA, KANSAS, _run_us

WHEAT_STAGES = {"initial": 0.48, "development": 0.22, "mid": 0.22, "late": 0.08}


@pytest.fixture(scope="module")
def kansas():
    return _run_us(*KANSAS, ["wheat", "sorghum", "fallow"])


def test_double_crop_not_first_on_water(kansas):
    water = {r["id"]: r["scores"]["water"] for r in kansas["all_ranked"]}
    best = max(water.values())
    assert water["wheat_double_crop_soybean"] < best
    assert water["wheat_double_crop_soybean"] == min(water.values())


def test_rainfed_score_formula(kansas):
    ranked = {r["id"]: r for r in kansas["all_ranked"]}
    cur = ranked["wheat_sorghum_fallow"]
    assert cur["is_current"] and cur["scores"]["water"] == 50.0
    for r in ranked.values():
        expected = scoring.clip(50 + 100 * (cur["water_unmet_fraction"] - r["water_unmet_fraction"]))
        assert r["scores"]["water"] == pytest.approx(expected, abs=0.2)
        assert r["water_deficit_mm"] > 0  # still reported, as information


def test_unmet_fraction_is_mean_over_crop_seasons(kansas):
    cur = kansas["rotations"][0]
    per_crop = cur["water_unmet_per_crop"]
    assert [p["crop"] for p in per_crop] == ["wheat", "sorghum"]  # the fallow year is not a crop season
    for p in per_crop:
        assert p["unmet_fraction"] == pytest.approx(p["water_deficit_mm"] / p["etc_mm"], abs=1e-3)
        assert p["rain_cover_fraction"] == pytest.approx(1 - p["unmet_fraction"], abs=1e-3)
    assert cur["water_unmet_fraction"] == pytest.approx(
        sum(p["unmet_fraction"] for p in per_crop) / 2, abs=1e-3)
    assert all("unmet_fraction" in p for p in cur["score_details"]["water"]["per_crop"])


def test_rain_cover_in_words(kansas):
    why = kansas["rotations"][0]["why"]
    assert "rain covers about" in why and "of wheat's" in why and "water demand" in why
    assert "information" in why


def test_fallow_moisture_note(kansas):
    cur = kansas["rotations"][0]
    assert scoring.FALLOW_MOISTURE_NOTE in cur["caveats"]
    assert cur["score_details"]["water"]["fallow_note"] == scoring.FALLOW_MOISTURE_NOTE
    assert scoring.FALLOW_MOISTURE_NOTE in kansas["assumptions"]
    dc = next(r for r in kansas["rotations"] if r["pack_rotation_id"] == "wheat_double_crop_soybean")
    assert scoring.FALLOW_MOISTURE_NOTE not in dc["caveats"]


def test_kansas_winter_wheat_stage_override():
    pack = regional.find_pack(*KANSAS)
    entry = next(e for e in pack["crop_calendar"] if e["crop"] == "wheat")
    assert {k: entry["stage_fractions"][k] for k in WHEAT_STAGES} == WHEAT_STAGES
    assert entry["stage_fractions"]["status"].startswith("provisional")
    kc = water_balance.apply_pack_overrides(KC, pack)["wheat"]
    months = [10, 11, 12, 1, 2, 3, 4, 5, 6]
    monthly = dict(zip(months, (k for k, _ in water_balance.monthly_kc(len(months), kc))))
    peak = max(monthly, key=monthly.get)
    assert peak in (4, 5, 6)  # spring, not mid-winter
    assert monthly[1] == pytest.approx(KC["wheat"]["kc_ini"])  # January is still the initial stage
    assert KC["wheat"]["stage_fractions"] != kc["stage_fractions"]  # the shared table is unchanged


def test_override_only_in_kansas():
    for lat_lon in (TANTA, IOWA):
        pack = regional.find_pack(*lat_lon)
        assert water_balance.apply_pack_overrides(KC, pack)["wheat"] == KC["wheat"]


def test_iowa_rainfed_scores(kansas):
    out = _run_us(*IOWA, ["maize", "soybean"])
    ranked = {r["id"]: r for r in out["all_ranked"]}
    assert ranked["maize_soybean"]["scores"]["water"] == 50.0
    # oat adds a season with a smaller unmet share, so the 3-year rotation scores above 50 on water
    assert ranked["maize_soybean_oat"]["scores"]["water"] > 50.0
    assert ranked["continuous_maize"]["scores"]["water"] < 50.0
