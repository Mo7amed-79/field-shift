# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for the rainfed US regional packs (Iowa Corn Belt, Kansas Plains)."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import regional, water_balance
from engine.pipeline import run_compare
from engine.tests.test_compare import CROPS, KC, _body, _cache, _raw, _soil

IOWA = (42.00, -93.75)
KANSAS = (38.75, -99.15)
EQUAL = {"water": 1, "soil": 1, "heat_risk": 1}


def _run_us(lat, lon, current, priorities=EQUAL, water="rainfed"):
    req = {"current_rotation": list(current), "water_access": water, "priorities": priorities,
           "scenario": {"temp_delta_c": 0, "water_cut_pct": 0}, "area": None}
    return run_compare(_raw(lat, lon), _soil(lat, lon), req, CROPS, KC, regional.find_pack(lat, lon))


def test_packs_found_and_rainfed():
    assert regional.find_pack(*IOWA)["id"] == "us_iowa_corn_belt"
    assert regional.find_pack(*KANSAS)["id"] == "us_kansas_plains"
    for p in (regional.find_pack(*IOWA), regional.find_pack(*KANSAS)):
        assert p["water_access_default"] == "rainfed"
        assert all(r["status"] == "provisional — unverified" for r in p["typical_rotations"])


def test_oat_kc_provisional():
    assert KC["oat"]["kc_ini"] == 0.30 and KC["oat"]["kc_mid"] == 1.15 and KC["oat"]["kc_end"] == 0.25
    assert KC["oat"]["status"] == "provisional — unverified"


def test_iowa_multi_year_rotations():
    out = _run_us(*IOWA, ["maize", "soybean"])
    ranked = {r["id"]: r for r in out["all_ranked"]}
    assert set(ranked) == {"maize_soybean", "continuous_maize", "maize_soybean_oat"}
    assert ranked["maize_soybean"]["is_current"] and ranked["maize_soybean"]["n_years"] == 2
    assert ranked["maize_soybean_oat"]["n_years"] == 3
    cur = out["rotations"][0]
    assert len(cur["year_plan"]) == 24 and cur["water_mm_per_year"] is None
    assert cur["water_deficit_mm"] > 0
    assert out["warnings"] == []  # rainfed matches the pack default


def test_kansas_fallow_rotations():
    out = _run_us(*KANSAS, ["wheat", "fallow"])
    cur = out["rotations"][0]
    assert cur["name"] == "Wheat → Fallow" and cur["n_years"] == 2
    d = cur["score_details"]["soil_practices"]
    assert d["cycle_months"] == 24 and d["cycle_months_with_ground_cover"] == 9
    assert len(d["fallow_months"]) == 15
    ranked = {r["id"]: r for r in out["all_ranked"]}
    assert ranked["wheat_sorghum_fallow"]["n_years"] == 3
    # double crop: wheat and soybean in the same year, sharing June and October
    dc = ranked["wheat_double_crop_soybean"]
    assert dc["n_years"] == 1 and dc["scores"]["soil_practices"] == 100.0
    # continuous wheat has twice the annual deficit of wheat -> fallow (fallow year = 0 mm)
    assert ranked["continuous_wheat"]["water_deficit_mm"] == pytest.approx(
        2 * ranked["wheat_fallow"]["water_deficit_mm"], abs=0.2)


def test_api_compare_iowa():
    _cache(*IOWA)
    body = _body(*IOWA, ["maize", "soybean"])
    body["water_access"] = "rainfed"
    r = TestClient(app).post("/api/compare", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["pack"]["id"] == "us_iowa_corn_belt"
    assert j["rotations"][0]["water_criterion_label"] == "water-deficit exposure"
    assert len(j["rotations"][0]["year_plan"]) == 24
