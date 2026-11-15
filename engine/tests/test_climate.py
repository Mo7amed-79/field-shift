# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for NASA POWER parsing and the climate summary."""

import copy
import json
import shutil
from pathlib import Path

import pytest
import requests

from engine import climate, power_client
from engine.climate import et0_penman_monteith

FIXTURE = Path(__file__).parent / "fixtures" / "power_30.85_31.05_2015_2025.json"
TANTA = (30.85, 31.05)


@pytest.fixture
def raw_tanta() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    url = power_client.build_url(*TANTA, 2015, 2025)
    return power_client.parse_response(data, url, *TANTA, "2026-11-14T00:00:00+00:00", is_cached=True)


@pytest.fixture
def offline_cache(tmp_path, monkeypatch):
    """Cache dir holding only the Tanta fixture; every network call fails."""
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    monkeypatch.setattr(power_client, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(power_client.time, "sleep", lambda s: None)

    def fail(*args, **kwargs):
        raise requests.ConnectionError("network disabled in test")

    monkeypatch.setattr(power_client.requests, "get", fail)
    return tmp_path


# --- parser ---------------------------------------------------------------

def test_parser_shape(raw_tanta):
    monthly = raw_tanta["monthly"]
    assert len(monthly) == 11 * 12  # 2015-2025, annual "13" entries dropped
    assert {r["month"] for r in monthly} == set(range(1, 13))
    assert monthly[0]["year"] == 2015 and monthly[-1]["year"] == 2025
    for field in power_client.FIELDS:
        assert field in monthly[0]


def test_parser_meta(raw_tanta):
    meta = raw_tanta["meta"]
    assert meta["source"] == "NASA POWER"
    assert meta["units"]["PRECTOTCORR"] == "mm/day"
    assert meta["units"]["ALLSKY_SFC_SW_DWN"] == "MJ/m^2/day"
    assert meta["fill_value"] == -999.0
    assert meta["elevation_m"] == pytest.approx(4.96)
    assert "T2M_RANGE" in meta["url"] and "community=AG" in meta["url"]


def test_parser_converts_fill_to_none():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["properties"]["parameter"]["T2M"]["201503"] = -999.0
    raw = power_client.parse_response(data, "u", *TANTA, "t", is_cached=False)
    row = next(r for r in raw["monthly"] if (r["year"], r["month"]) == (2015, 3))
    assert row["t2m"] is None
    assert "2015-03" in climate.summarize(raw)["data_gaps"]


# --- ET0 ------------------------------------------------------------------

def test_et0_fao56_example_17():
    """FAO-56 Chapter 4, Example 17: monthly ET0, Bangkok (13°44'N, 2 m), April.

    Tmax 34.8 °C, Tmin 25.6 °C, ea 2.85 kPa, u2 2 m/s, Rs 22.65 MJ m-2 day-1
    (from n = 8.5 h/day), G = 0.14 MJ m-2 day-1. FAO-56 result: 5.72 mm/day.
    """
    et0 = et0_penman_monteith(
        tmax_c=34.8, tmin_c=25.6, rh_mean_pct=None, u2_ms=2.0, rs_mj_m2_day=22.65,
        elevation_m=2, lat_deg=13 + 44 / 60, doy=105, g_mj_m2_day=0.14, ea_kpa=2.85,
    )
    assert et0 == pytest.approx(5.72, rel=0.05)


def test_et0_physical_bounds_tanta(raw_tanta):
    clim = climate.summarize(raw_tanta)["monthly_climatology"]
    days = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for m, d in zip(clim, days):
        assert 0 < m["et0_mm"] / d < 15
    assert clim[6]["et0_mm"] > clim[0]["et0_mm"]  # July > January


def test_warming_increases_annual_et0(raw_tanta):
    base = climate.summarize(raw_tanta)
    warmed_raw = copy.deepcopy(raw_tanta)
    for r in warmed_raw["monthly"]:
        r["t2m"] += 2
    warmed = climate.summarize(warmed_raw)
    for b, w in zip(base["yearly"], warmed["yearly"]):
        assert w["et0_mm"] > b["et0_mm"]


# --- summary --------------------------------------------------------------

def test_summary_structure(raw_tanta):
    s = climate.summarize(raw_tanta)
    assert len(s["monthly_climatology"]) == 12
    assert [y["year"] for y in s["yearly"]] == list(range(2015, 2026))
    assert s["period"] == "2015–2025"
    assert s["data_gaps"] == []
    assert s["trend"]["n_years_temp"] == 11
    assert s["variability"]["rain_annual_cv"] > 0
    # precip converted from mm/day to mm/month: Jan 2015 rate x 31 days
    jan_rate = next(r["precip"] for r in raw_tanta["monthly"] if (r["year"], r["month"]) == (2015, 1))
    jan_2015 = climate._monthly_records(raw_tanta)[0]
    assert jan_2015["rain_mm"] == pytest.approx(jan_rate * 31)


def test_rejects_unknown_precip_unit(raw_tanta):
    raw = copy.deepcopy(raw_tanta)
    raw["meta"]["units"]["PRECTOTCORR"] = "in/day"
    with pytest.raises(ValueError):
        climate.summarize(raw)


# --- cache / offline ------------------------------------------------------

def test_offline_exact_cache_hit(offline_cache):
    raw = power_client.fetch_monthly(*TANTA, 2015, 2025)
    assert raw["meta"]["is_cached"] is True
    assert "fallback_note" not in raw["meta"]
    assert len(raw["monthly"]) == 132


def test_offline_falls_back_to_nearest_cache(offline_cache):
    raw = power_client.fetch_monthly(30.95, 31.20, 2015, 2025)
    assert raw["meta"]["is_cached"] is True
    assert FIXTURE.name in raw["meta"]["fallback_note"]
    assert raw["meta"]["lat_requested"] == 30.95


def test_offline_no_nearby_cache_raises(offline_cache):
    with pytest.raises(power_client.PowerUnavailableError):
        power_client.fetch_monthly(30.90, 75.85, 2015, 2025)


def test_api_climate_uses_engine_offline(offline_cache, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.main import app

    # The fixture covers 2015-2025; the API default is 1991-2025.
    monkeypatch.setattr(main, "CLIMATE_START_YEAR", 2015)

    r = TestClient(app).get("/api/climate", params={"lat": TANTA[0], "lon": TANTA[1]})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "NASA POWER"
    assert body["is_cached"] is True
    assert len(body["monthly"]) == 12
    assert body["forecast_next_year"] is None


def _synthetic_long_record(raw_tanta: dict, warming_per_year: float) -> dict:
    """1991-2025 record built from the real 2015 months, with a linear temperature ramp."""
    base_rows = [r for r in raw_tanta["monthly"] if r["year"] == 2015]
    raw = copy.deepcopy(raw_tanta)
    raw["monthly"] = [
        {**r, "year": y, "t2m": r["t2m"] + warming_per_year * (y - 1991)}
        for y in range(1991, 2026) for r in base_rows
    ]
    return raw


def test_recent_vs_baseline_and_trend_pvalue(raw_tanta):
    s = climate.summarize(_synthetic_long_record(raw_tanta, 0.03))
    rvb = s["recent_vs_baseline"]
    assert rvb["recent"] == "2016–2025" and rvb["baseline"] == "1991–2005"
    # mean year 2020.5 vs 1998.0 -> 22.5 years x 0.03 °C
    assert rvb["temp_c"]["diff"] == pytest.approx(0.675)
    # Same daily rain rate every year; only leap-year Februaries differ.
    assert rvb["rain_mm"]["diff"] == pytest.approx(0, abs=0.1)
    assert rvb["et0_mm"]["diff"] > 0
    assert s["trend"]["temp_c_per_decade"] == pytest.approx(0.3)
    assert s["trend"]["temp_p_value"] < 1e-6
    assert s["trend"]["n_years_temp"] == 35


def test_recent_vs_baseline_none_when_baseline_missing(raw_tanta):
    rvb = climate.summarize(raw_tanta)["recent_vs_baseline"]  # fixture is 2015-2025 only
    assert rvb["temp_c"]["baseline"] is None and rvb["temp_c"]["diff"] is None
    assert rvb["temp_c"]["recent"] is not None


def test_meta_has_spatial_resolution_note(raw_tanta):
    note = raw_tanta["meta"]["spatial_resolution_note"]
    assert "power.larc.nasa.gov/docs/methodology" in note


def test_rain_record_warning(raw_tanta):
    raw = _synthetic_long_record(raw_tanta, 0.0)
    assert climate.summarize(raw)["rain_record_warning"] is None
    for r in raw["monthly"]:
        if r["year"] >= 2016:
            r["precip"] *= 3
    s = climate.summarize(raw)
    assert "must not be read as a climate trend" in s["rain_record_warning"]
    assert not any("rain" in k for k in s["trend"] if k != "method")
