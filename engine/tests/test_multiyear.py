# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for multi-year rotations and rainfed (water-deficit) logic, on the Tanta climate."""

import copy

import pytest

from engine import regional
from engine.pipeline import cycle_signature
from engine.tests.test_compare import TANTA, _run

DELTA = regional.find_pack(*TANTA)


def _c(crop, label, start, end, year=1):
    return {"crop": crop, "label": label, "start_month": start, "end_month": end, "year": year}


def _pack(*extra_rotations):
    pack = copy.deepcopy(DELTA)
    pack["typical_rotations"] += list(extra_rotations)
    return pack


TWO_YEAR = {
    "id": "rw_mb", "name": "Rice → Wheat → Maize → Berseem", "status": "test",
    "crops": [_c("rice", "Rice", 5, 10), _c("wheat", "Wheat", 11, 5),
              _c("maize", "Maize", 5, 9, 2), _c("berseem", "Berseem", 10, 5, 2)],
}
WHEAT_FALLOW = {"id": "wheat_fallow", "name": "Wheat → Fallow", "status": "test", "n_years": 2,
                "crops": [_c("wheat", "Wheat", 11, 5)]}


def _by_id(out):
    return {r["id"]: r for r in out["all_ranked"]}


def test_single_year_plan_unchanged_shape():
    plan = _run()["rotations"][0]["year_plan"]
    assert len(plan) == 12 and all(p["year"] == 1 for p in plan)
    assert all(p["water_deficit_mm"] is None for p in plan)


def test_cycle_signature():
    assert cycle_signature(WHEAT_FALLOW) == ["wheat", "fallow"]
    assert cycle_signature(TWO_YEAR) == ["rice", "wheat", "maize", "berseem"]


def test_two_year_water_is_annual_mean():
    out = _run(pack=_pack(TWO_YEAR))
    r = _by_id(out)
    expected = (r["rice_wheat"]["water_mm_per_year"] + r["maize_berseem"]["water_mm_per_year"]) / 2
    assert r["rw_mb"]["water_mm_per_year"] == pytest.approx(expected, abs=0.2)
    assert r["rw_mb"]["n_years"] == 2


def test_two_year_soil_diversity_over_cycle_and_24_month_plan():
    out = _run(current=("rice", "wheat", "maize", "berseem"), pack=_pack(TWO_YEAR))
    cur = out["rotations"][0]
    d = cur["score_details"]["soil_practices"]
    assert d["n_crops"] == 4 and d["distinct_families"] == 2 and d["legume_in_rotation"]
    assert d["cycle_months"] == 24
    assert len(cur["year_plan"]) == 24 and [p["year"] for p in cur["year_plan"]] == [1] * 12 + [2] * 12
    total_plan = sum(p["irrigation_mm"] for p in cur["year_plan"])
    assert total_plan == pytest.approx(2 * cur["water_mm_per_year"], abs=1)


def test_fallow_year_counts_as_no_cover():
    out = _run(current=("wheat", "fallow"), pack=_pack(WHEAT_FALLOW))
    cur = out["rotations"][0]
    assert cur["name"] == "Wheat → Fallow" and cur["n_years"] == 2
    d = cur["score_details"]["soil_practices"]
    wheat_months = 7  # Nov-May
    assert d["cycle_months_with_ground_cover"] == wheat_months
    assert len(d["fallow_months"]) == 24 - wheat_months
    assert {"year": 2, "month": 6} in d["fallow_months"]
    assert d["parts"]["ground_cover"] == pytest.approx(30 * wheat_months / 24, abs=0.1)
    assert "17 of 24 months without a crop" in cur["why"]
    plan = cur["year_plan"]
    assert all(p["activity"] == "fallow" for p in plan if p["year"] == 2)


def test_rainfed_reports_deficit_not_irrigation():
    irrigated = _run(area={"value": 1, "unit": "feddan"})
    from engine.tests.test_compare import CROPS, KC, _raw, _soil
    from engine.pipeline import run_compare
    req = {"current_rotation": ["rice", "wheat"], "water_access": "rainfed",
           "priorities": {"water": 1, "soil": 1, "heat_risk": 1},
           "scenario": {"temp_delta_c": 0, "water_cut_pct": 20}, "area": {"value": 1, "unit": "feddan"}}
    out = run_compare(_raw(*TANTA), _soil(*TANTA), req, CROPS, KC, DELTA)
    cur, cur_irr = out["rotations"][0], irrigated["rotations"][0]
    assert cur["water_mm_per_year"] is None and cur["water_m3_per_year"] is None
    assert cur["water_deficit_mm"] == cur_irr["water_mm_per_year"]
    assert cur["water_criterion_label"] == "water-deficit exposure"
    assert cur["exceeds_water_budget"] is None
    assert "drought-stress" in cur["why"] and "irrigation" not in cur["why"]
    for r in out["rotations"][1:]:
        assert r["water_saving_pct"] is None and r["water_deficit_reduction_pct"] is not None
        assert "less than your current rotation" in r["why"] or "more than your current rotation" in r["why"]
    plan = cur["year_plan"]
    assert all(p["irrigation_mm"] is None and p["irrigation_m3"] is None for p in plan)
    assert sum(p["water_deficit_mm"] for p in plan) == pytest.approx(cur["water_deficit_mm"], abs=1)
    assert cur["score_details"]["water"]["label"] == "water-deficit exposure"
    assert any("rainfed" in w for w in out["warnings"])  # pack default is full irrigation
