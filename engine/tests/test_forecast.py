# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for the next-year projection (engine/forecast.py)."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import forecast
from engine.tests.test_compare import TANTA, _body, _cache, _raw, _run


def _recs(f):
    return [{"year": y, "month": m, "temp_c": f(y, m), "et0_mm": 10 * f(y, m)}
            for y in range(1991, 2026) for m in range(1, 13)]


def test_exact_trend_and_seasonality_is_recovered():
    recs = _recs(lambda y, m: 10 + m + 0.03 * (y + (m - 0.5) / 12 - 1991))
    model = forecast.fit(recs, "temp_c", 1991)
    assert model["trend_per_year"] == pytest.approx(0.03, abs=1e-9)
    assert forecast.predict(model, 2026, 7) == pytest.approx(10 + 7 + 0.03 * (2026 + 6.5 / 12 - 1991), abs=1e-6)
    bt = forecast.backtest(recs)
    assert bt["temp_c"]["beats_climatology"] and bt["temp_c"]["mae_model"] == pytest.approx(0, abs=1e-6)


def test_backtest_reports_when_model_loses():
    # warming to 2020, then back to the old level: the trend model over-predicts 2021-2025
    recs = _recs(lambda y, m: 10 + m + (0.1 * (y - 1991) if y <= 2020 else 0.0))
    bt = forecast.backtest(recs)
    assert bt["temp_c"]["beats_climatology"] is False
    assert "does NOT beat" in forecast._verdict(bt)


def test_tanta_projection():
    proj = forecast.project_next_year(_raw(*TANTA))
    assert proj["year"] == 2026 and [m["month"] for m in proj["months"]] == list(range(1, 13))
    assert proj["method"].startswith("Simple statistical projection, not a weather forecast")
    assert "Rain is not projected" in proj["method"]
    assert set(proj["backtest"]) >= {"temp_c", "et0_mm", "train_years", "test_years"}
    assert proj["backtest"]["train_years"] == [1991, 2020] and proj["backtest"]["test_years"] == [2021, 2025]


def test_use_forecast_changes_water_not_default():
    base = _run()
    req_out = _run_forecast()
    cur, cur_f = base["rotations"][0], req_out["rotations"][0]
    proj = req_out["projection"]
    clim = {m["month"]: m["et0_mm"] for m in base["climate_now"]["monthly_climatology"]}
    if sum(m["et0_mm"] for m in proj["months"]) > sum(clim.values()):
        assert cur_f["water_mm_per_year"] > cur["water_mm_per_year"]
    assert cur_f["score_details"]["heat_risk"]["projection_year"] == 2026
    assert any("projected 2026" in a for a in req_out["assumptions"])


def _run_forecast(delta=0.0):
    from engine import regional
    from engine.pipeline import run_compare
    from engine.tests.test_compare import CROPS, KC, _soil
    req = {"current_rotation": ["rice", "wheat"], "water_access": "full_irrigation",
           "priorities": {"water": 1, "soil": 1, "heat_risk": 1},
           "scenario": {"temp_delta_c": delta, "water_cut_pct": 0, "use_forecast": True}, "area": None}
    return run_compare(_raw(*TANTA), _soil(*TANTA), req, CROPS, KC, regional.find_pack(*TANTA))


def test_use_forecast_ignores_temp_delta_with_warning():
    out = _run_forecast(delta=2.0)
    assert any("was ignored" in w for w in out["warnings"])
    assert out["rotations"][0]["score_details"]["heat_risk"]["temp_delta_c"] == 0


def test_api_compare_climate_has_forecast():
    _cache(*TANTA)
    body = _body(*TANTA, ["rice", "wheat"])
    body["scenario"]["use_forecast"] = True
    j = TestClient(app).post("/api/compare", json=body).json()
    assert len(j["climate"]["forecast_next_year"]) == 12
    assert j["climate"]["forecast_backtest"]["temp_c"]["mae_model"] > 0
    assert j["scenario"]["use_forecast"] is True
