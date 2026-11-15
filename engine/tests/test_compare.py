# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for water balance, scoring, planner, explanations and /api/compare."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app
from engine import power_client, regional, scoring, soil_client, water_balance
from engine.pipeline import EXPLORATORY_WARNING, run_compare

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
CROPS = json.loads((ROOT / "data" / "crops.json").read_text(encoding="utf-8"))
KC = water_balance.load_kc()
TANTA = (30.85, 31.05)
PUNJAB = (30.80, 75.60)
EQUAL = {"water": 1, "soil": 1, "heat_risk": 1}


def _raw(lat: float, lon: float) -> dict:
    name = f"power_{lat:.2f}_{lon:.2f}_1991_2025.json"
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return power_client.parse_response(data, "fixture", lat, lon, "fixture", is_cached=True)


def _soil(lat: float, lon: float) -> dict:
    data = json.loads((FIXTURES / f"soil_{lat:.3f}_{lon:.3f}.json").read_text(encoding="utf-8"))
    return soil_client.parse_response(data)


def _run(lat=TANTA[0], lon=TANTA[1], current=("rice", "wheat"), priorities=EQUAL, delta=0.0, cut=0.0, area=None, pack="auto"):
    req = {"current_rotation": list(current), "water_access": "full_irrigation", "priorities": priorities,
           "scenario": {"temp_delta_c": delta, "water_cut_pct": cut}, "area": area}
    p = regional.find_pack(lat, lon) if pack == "auto" else pack
    return run_compare(_raw(lat, lon), _soil(lat, lon), req, CROPS, KC, p)


def _cache(lat: float, lon: float) -> None:
    shutil.copy(FIXTURES / f"power_{lat:.2f}_{lon:.2f}_1991_2025.json", power_client.CACHE_DIR)
    shutil.copy(FIXTURES / f"soil_{lat:.3f}_{lon:.3f}.json", soil_client.CACHE_DIR)


def _body(lat, lon, current, **extra):
    return {"lat": lat, "lon": lon, "current_rotation": current, "water_access": "full_irrigation",
            "priorities": EQUAL, "scenario": {"temp_delta_c": 0, "water_cut_pct": 0}, **extra}


# --- water balance ----------------------------------------------------------

def _needs(out: dict) -> dict:
    return {c["id"]: c["water_mm_per_year"] for c in out["all_ranked"]}


def test_rice_wheat_needs_more_water_than_maize_faba_bean():
    needs = _needs(_run())
    assert needs["rice_wheat"] > needs["maize_faba_bean"]


def test_warming_raises_every_rotations_need():
    base, warm = _needs(_run()), _needs(_run(delta=2.0))
    assert base.keys() == warm.keys()
    assert all(warm[k] > base[k] for k in base)


def test_kc_curve_wheat_seven_months():
    kc = KC["wheat"]
    values = [k for k, _ in water_balance.monthly_kc(7, kc)]
    peak = values.index(max(values))
    assert all(a <= b + 1e-9 for a, b in zip(values[:peak], values[1:peak + 1]))  # rises
    assert sum(abs(v - kc["kc_mid"]) < 1e-9 for v in values) >= 2  # plateau at kc_mid
    assert all(a >= b - 1e-9 for a, b in zip(values[peak:], values[peak + 1:]))  # declines
    assert values[-1] < kc["kc_mid"]
    assert all(kc["kc_end"] - 1e-9 <= v <= kc["kc_mid"] + 1e-9 for v in values)


def test_kc_shown_in_year_plan():
    plan = _run()["rotations"][0]["year_plan"]
    assert plan[0]["kc"] is not None  # January: wheat
    assert all(p["kc"] is None for p in plan if p["activity"] == "fallow")


def test_paddy_extra_water_added(monkeypatch):
    pack = dict(regional.find_pack(*TANTA), paddy_extra_water_mm=300.0)
    base = _run()["rotations"][0]
    out = _run(pack=pack)
    current = out["rotations"][0]
    assert current["water_mm_per_year"] == pytest.approx(base["water_mm_per_year"] + 300, abs=0.2)
    assert not any("lower bound" in c for c in current["caveats"])
    assert any("300 mm" in c for c in current["caveats"])
    assert "percolation" not in current["not_assessed"]


def test_crop_without_kc_is_not_water_scored():
    clim = {"monthly_climatology": [{"month": m, "et0_mm": 100, "rain_mm": 0} for m in range(1, 13)]}
    out = water_balance.crop_water_need("pea", [1, 2, 3], clim, None)
    assert out["water_assessed"] is False and "No Kc" in out["note"]


def test_rice_caveat_present():
    current = _run()["rotations"][0]
    assert any("lower bound" in c for c in current["caveats"])


# --- scoring ----------------------------------------------------------------

@pytest.mark.parametrize("unit, m3", [("feddan", 4200.0), ("hectare", 10000.0), ("acre", 4047.0)])
def test_mm_to_m3(unit, m3):
    assert scoring.mm_to_m3(1000, 1, unit) == pytest.approx(m3)


def test_water_score_is_saving_versus_current():
    assert scoring.water_score(0) == 50
    assert scoring.water_score(25) == 75  # 25% less water
    assert scoring.water_score(10) == 60
    assert scoring.water_score(50) == 100 and scoring.water_score(70) == 100
    assert scoring.water_score(-60) == 0
    out = _run(area={"value": 1, "unit": "feddan"})
    assert out["rotations"][0]["scores"]["water"] == 50
    for r in out["rotations"][1:]:
        cur = out["rotations"][0]["water_mm_per_year"]
        assert r["water_saving_pct"] == pytest.approx(100 * (cur - r["water_mm_per_year"]) / cur, abs=0.1)
        assert r["scores"]["water"] == pytest.approx(scoring.water_score(r["water_saving_pct"]), abs=0.2)
        assert r["water_saving_m3"] == pytest.approx(r["water_saving_mm"] * 4.2, abs=1)
        assert f"{abs(r['water_saving_pct']):.0f}%" in r["why"]


@pytest.mark.parametrize("lat, lon", [TANTA, PUNJAB])
def test_warming_never_raises_heat_score(lat, lon):
    base = {r["id"]: r["scores"]["heat_risk"] for r in _run(lat, lon)["all_ranked"]}
    warm = {r["id"]: r["scores"]["heat_risk"] for r in _run(lat, lon, delta=2.0)["all_ranked"]}
    assert all(warm[k] <= base[k] for k in base)


def test_punjab_wheat_not_zeroed():
    current = _run(*PUNJAB)["rotations"][0]  # rice -> wheat
    wheat = next(s for s in current["suitability"] if s["crop"] == "wheat")
    assert wheat["score"] > 0
    assert any("absolute maximum" in c and "Wheat" in c for c in current["caveats"])
    heat = current["score_details"]["heat_risk"]["per_crop"]
    assert all(h["score_current"] > 0 for h in heat)


def test_current_is_strong():
    out = _run(current=("maize", "faba_bean"))
    assert out["current_is_strong"] is True
    assert len(out["rotations"]) == 3
    assert "already among the best" in out["rotations"][0]["why"]


def test_all_ranked_has_every_pack_rotation():
    out = _run()
    ids = {r["id"] for r in out["all_ranked"]}
    pack_ids = {r["id"] for r in regional.find_pack(*TANTA)["typical_rotations"]}
    assert ids == pack_ids and len(ids) == 8
    assert [r["rank"] for r in out["all_ranked"]] == list(range(1, 9))
    assert sum(r["is_current"] for r in out["all_ranked"]) == 1


def test_priorities_change_the_winner():
    def winner(p):
        return next(r["id"] for r in _run(priorities=p)["rotations"] if r["rank"] == 1)
    water_winner = winner({"water": 1, "soil": 0, "heat_risk": 0})
    soil_winner = winner({"water": 0, "soil": 1, "heat_risk": 0})
    # At Tanta the lowest-water pack rotation (maize -> faba bean) leaves a fallow month,
    # while maize -> berseem covers all 12 months, so the soil-only winner differs.
    assert water_winner != soil_winner


def test_water_budget_flag():
    out = _run(cut=20)
    current = out["rotations"][0]
    assert current["exceeds_water_budget"] is True  # 100% > 80% of itself
    for r in out["rotations"]:
        assert r["exceeds_water_budget"] == (r["water_mm_per_year"] > 0.8 * current["water_mm_per_year"])


def test_sensitivity_block():
    s = _run()["sensitivity"]
    assert [c["name"] for c in s["cases"]] == ["your_priorities", "equal", "water_only", "soil_only", "heat_only"]
    assert isinstance(s["winner_is_robust"], bool) and s["note"]


# --- selection, planner, explain --------------------------------------------

def test_pack_alternatives_are_best_two_distinct():
    out = _run()
    alts = out["rotations"][1:]
    assert len(alts) == 2
    assert set(alts[0]["crops"]) != set(alts[1]["crops"])
    others = [r for r in out["all_ranked"] if not r["is_current"]]
    assert {a["id"] for a in alts} == {others[0]["id"], others[1]["id"]}  # top two non-current
    assert all(r["confidence"] == "medium" for r in out["rotations"])


def test_year_plan_and_m3():
    out = _run(area={"value": 2, "unit": "feddan"})
    for r in out["rotations"]:
        plan = r["year_plan"]
        assert [p["month"] for p in plan] == list(range(1, 13))
        assert sum(p["irrigation_mm"] for p in plan) == pytest.approx(r["water_mm_per_year"], abs=1)
        assert sum(p["irrigation_m3"] for p in plan) == pytest.approx(r["water_m3_per_year"], rel=0.01)
    may = out["rotations"][0]["year_plan"][4]  # rice -> wheat shares May
    assert may["activity"] == "plant" and "transition" in may["note"]


def test_explanation_names_numbers_and_limits():
    out = _run()
    alt = out["rotations"][1]
    assert "mm" in alt["why"] and "than your current rotation" in alt["why"]
    assert "prices" in alt["not_assessed"] and "pests" in alt["not_assessed"]
    assert "percolation" in out["rotations"][0]["not_assessed"]  # current includes rice
    for word in ("yield", "income will", "profit"):
        assert word not in alt["why"].lower()


def test_unknown_crop_rejected():
    with pytest.raises(ValueError):
        _run(current=("rice", "dragonfruit"))


# --- /api/compare -------------------------------------------------------------

def test_api_compare_tanta():
    _cache(*TANTA)
    r = TestClient(app).post("/api/compare", json=_body(*TANTA, ["rice", "wheat"], area={"value": 1, "unit": "feddan"}))
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "regional_pack" and body["pack"]["id"] == "egypt_nile_delta"
    assert len(body["rotations"]) == 3 and body["rotations"][0]["is_current"]
    assert sum(not x["is_current"] for x in body["rotations"]) == 2
    assert body["rotations"][1]["water_m3_per_year"] is not None


def test_api_compare_punjab():
    _cache(*PUNJAB)
    r = TestClient(app).post("/api/compare", json=_body(*PUNJAB, ["rice", "wheat"]))
    assert r.status_code == 200
    body = r.json()
    assert body["pack"]["id"] == "india_punjab"
    assert len(body["rotations"]) == 3 and body["rotations"][0]["is_current"]


def test_api_compare_exploratory_without_pack(monkeypatch):
    _cache(*TANTA)
    monkeypatch.setattr(main.regional, "find_pack", lambda lat, lon: None)
    r = TestClient(app).post("/api/compare", json=_body(*TANTA, ["rice", "wheat"]))
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "exploratory" and body["pack"] is None
    assert EXPLORATORY_WARNING in body["warnings"]
    assert all(x["confidence"] == "low" for x in body["rotations"])
    for x in body["rotations"]:
        for s in x["suitability"]:
            assert s["ph_advisory"] is None or s["ph_advisory"]["status"] == "outside_absolute"


def test_api_compare_unknown_crop_is_400():
    _cache(*TANTA)
    r = TestClient(app).post("/api/compare", json=_body(*TANTA, ["rice", "dragonfruit"]))
    assert r.status_code == 400


def test_soil_practices_three_parts():
    crops = {c["id"]: c for c in CROPS}
    rice_wheat, _ = scoring.soil_practices([crops["rice"], crops["wheat"]], 12)
    maize_berseem, d = scoring.soil_practices([crops["maize"], crops["berseem"]], 12)
    assert rice_wheat == 30.0  # no legume, one family (Poaceae), full cover
    assert maize_berseem == 100.0
    assert set(d["parts"]) == {"legume_in_rotation", "family_diversity", "ground_cover"}
    faba, d2 = scoring.soil_practices([crops["maize"], crops["faba_bean"]], 11)
    assert d2["parts"] == {"legume_in_rotation": 40.0, "family_diversity": 30.0, "ground_cover": 27.5}
