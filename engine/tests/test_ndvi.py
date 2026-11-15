# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for the MODIS NDVI client, season detection and its use in the API."""

import json
import math
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import ndvi_client, power_client, soil_client

FIXTURES = Path(__file__).parent / "fixtures"
BANDS = json.loads((FIXTURES / "modis_MOD13Q1_bands.json").read_text(encoding="utf-8"))
SUBSET = json.loads((FIXTURES / "modis_MOD13Q1_subset_30.850_31.050_A2023001_A2023145.json").read_text(encoding="utf-8"))
TANTA = (30.85, 31.05)


def _series(values_by_month: dict[int, float], years=(2023, 2024, 2025)) -> dict:
    return {"series": [{"date": f"{y}-{m:02d}-15", "ndvi": v} for y in years for m, v in values_by_month.items()]}


# --- parser ---------------------------------------------------------------

def test_band_metadata_read_from_api():
    meta = ndvi_client.band_metadata(BANDS)
    assert meta["band"] == "250m_16_days_NDVI"
    assert meta["scale_factor"] == 0.0001 and meta["fill_value"] == -3000
    assert meta["valid_range"] == [-2000, 10000]


def test_parse_subset_applies_scale():
    meta = ndvi_client.band_metadata(BANDS)
    points = ndvi_client.parse_subset(SUBSET, meta)
    assert len(points) == 10
    assert points[0] == {"date": "2023-01-01", "modis_date": "A2023001", "ndvi": 0.7285}  # raw 7285


def test_parse_subset_drops_fill_values():
    meta = ndvi_client.band_metadata(BANDS)
    data = json.loads(json.dumps(SUBSET))
    data["subset"][3]["data"] = [-3000]
    assert len(ndvi_client.parse_subset(data, meta)) == 9


@pytest.mark.parametrize("n_dates", [1, 10, 11, 23, 69])
def test_paging_requests(monkeypatch, n_dates):
    dates = [{"modis_date": f"A2024{1 + 16 * i:03d}", "calendar_date": f"2024-01-{1 + i % 28:02d}"} for i in range(n_dates)]
    calls = []

    def fake_request(path, params, timeout_s, counter):
        counter["requests"] += 1
        calls.append(path)
        if path.endswith("/bands"):
            return BANDS
        if path.endswith("/dates"):
            return {"dates": dates}
        return {"subset": [], "scale": "0.0001"}

    monkeypatch.setattr(ndvi_client, "_request", fake_request)
    out = ndvi_client.fetch_ndvi_series(*TANTA, 2024, 2024)
    assert sum(p.endswith("/subset") for p in calls) == math.ceil(n_dates / 10)
    assert out["meta"]["request_count"] == 2 + math.ceil(n_dates / 10)
    assert "not applied" in out["meta"]["quality_filtering"]
    assert "10.3334/ORNLDAAC/1600" in out["meta"]["citation"]


def test_offline_without_cache_raises():
    with pytest.raises(ndvi_client.NdviUnavailableError):
        ndvi_client.fetch_ndvi_series(*TANTA, 2023, 2025)


# --- seasons --------------------------------------------------------------

def test_single_peak_series():
    profile = {m: 0.15 for m in range(1, 13)} | {6: 0.4, 7: 0.7, 8: 0.45}
    s = ndvi_client.detect_seasons(_series(profile))
    assert s["seasons_per_year"] == 1
    assert s["seasons"][0]["peak_month"] == 7
    assert s["looks_cropped"] is True
    assert 1 in s["bare_or_low_cover_months"] and 7 not in s["bare_or_low_cover_months"]


def test_two_peak_series():
    profile = {m: 0.2 for m in range(1, 13)} | {1: 0.55, 2: 0.7, 3: 0.5, 7: 0.5, 8: 0.65, 9: 0.45}
    s = ndvi_client.detect_seasons(_series(profile))
    assert [x["peak_month"] for x in s["seasons"]] == [2, 8]


def test_flat_series_not_farmland():
    s = ndvi_client.detect_seasons(_series({m: 0.12 for m in range(1, 13)}))
    assert s["looks_cropped"] is False and s["seasons"] == []
    rot = {"windows": [{"crop": "rice", "label": "Rice", "start_month": 5, "end_month": 10, "months": list(range(5, 11))}]}
    check = ndvi_client.compare_with_calendar(s, rot)
    assert check["status"] == "not_cropped"
    assert "may not be farmland" in check["warning"]


def test_compare_with_calendar_never_names_species():
    profile = {m: 0.2 for m in range(1, 13)} | {1: 0.55, 2: 0.7, 3: 0.5, 7: 0.5, 8: 0.65, 9: 0.45}
    s = ndvi_client.detect_seasons(_series(profile))
    rot = {"windows": [
        {"crop": "rice", "label": "Rice", "start_month": 5, "end_month": 10, "months": list(range(5, 11))},
        {"crop": "wheat", "label": "Wheat", "start_month": 11, "end_month": 5, "months": [11, 12, 1, 2, 3, 4, 5]},
    ]}
    check = ndvi_client.compare_with_calendar(s, rot)
    assert check["status"] == "consistent"
    assert all(c["contains_peak"] for c in check["per_crop"])
    assert "does not identify" in check["note"]


# --- API --------------------------------------------------------------------

def _cache_tanta():
    shutil.copy(FIXTURES / "power_30.85_31.05_1991_2025.json", power_client.CACHE_DIR)
    shutil.copy(FIXTURES / "soil_30.850_31.050.json", soil_client.CACHE_DIR)


BODY = {"lat": TANTA[0], "lon": TANTA[1], "current_rotation": ["rice", "wheat"], "water_access": "full_irrigation",
        "priorities": {"water": 1, "soil": 1, "heat_risk": 1}, "scenario": {"temp_delta_c": 0, "water_cut_pct": 0}}


def test_compare_works_without_ndvi():
    _cache_tanta()
    r = TestClient(app).post("/api/compare", json=BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["ndvi_status"] == "not_loaded" and body["ndvi"] == [] and body["ndvi_check"] is None


def test_ndvi_endpoint_never_500_offline():
    r = TestClient(app).get("/api/ndvi", params={"lat": 10.0, "lon": 10.0})
    assert r.status_code == 200
    assert r.json()["status"] in ("loading", "unavailable")


def test_failed_fetch_resumes_from_saved_chunks(monkeypatch):
    dates = [{"modis_date": f"A2024{1 + 16 * i:03d}", "calendar_date": "2024-01-01"} for i in range(23)]
    calls, fail_on = [], {"A2024321"}  # third chunk fails the first time

    def fake_request(path, params, timeout_s, counter):
        counter["requests"] += 1
        calls.append((path, params and params.get("startDate")))
        if path.endswith("/bands"):
            return BANDS
        if path.endswith("/dates"):
            return {"dates": dates}
        if params["startDate"] in fail_on:
            fail_on.clear()
            raise ConnectionError("502")
        return {"subset": [], "scale": "0.0001"}

    monkeypatch.setattr(ndvi_client, "_request", fake_request)
    with pytest.raises(ndvi_client.NdviUnavailableError, match="2/3 chunks saved"):
        ndvi_client.fetch_ndvi_series(*TANTA, 2024, 2024)
    calls.clear()
    out = ndvi_client.fetch_ndvi_series(*TANTA, 2024, 2024)
    assert calls == [("MOD13Q1/subset", "A2024321")]  # only the missing chunk
    assert out["meta"]["request_count"] == 6  # 2 metadata + 3 chunks + 1 failed attempt
    assert not ndvi_client.partial_path(*TANTA, 2024, 2024).exists()


def test_tanta_fixture_two_seasons():
    data = json.loads((FIXTURES / "ndvi_30.850_31.050_2023_2025.json").read_text(encoding="utf-8"))
    assert data["meta"]["points_kept"] == 69 and data["meta"]["band"] == "250m_16_days_NDVI"
    s = ndvi_client.detect_seasons(data)
    assert s["looks_cropped"] is True
    assert s["seasons_per_year"] == 2
    peaks = [x["peak_month"] for x in s["seasons"]]
    assert any(m in (12, 1, 2, 3) for m in peaks)  # winter crop
    assert any(m in (6, 7, 8, 9) for m in peaks)  # summer crop


def test_compare_uses_cached_ndvi_without_changing_scores():
    _cache_tanta()
    client = TestClient(app)
    without = client.post("/api/compare", json=BODY).json()
    shutil.copy(FIXTURES / "ndvi_30.850_31.050_2023_2025.json", ndvi_client.CACHE_DIR)
    with_ndvi = client.post("/api/compare", json=BODY).json()
    assert with_ndvi["ndvi_status"] == "loaded" and len(with_ndvi["ndvi"]) == 69
    assert with_ndvi["ndvi_check"]["status"] == "consistent"
    assert len(with_ndvi["location"]["seasons_detected"]) == 2
    strip = lambda b: [(r["id"], r["scores"], r["total"], r["rank"]) for r in b["rotations"]]
    assert strip(with_ndvi) == strip(without)
    loc = client.get("/api/location", params={"lat": TANTA[0], "lon": TANTA[1]}).json()
    assert loc["ndvi_status"] == "loaded"
    assert client.get("/api/ndvi", params={"lat": TANTA[0], "lon": TANTA[1]}).json()["status"] == "loaded"


def test_compare_with_calendar_multi_year_counts_crops_per_year():
    # Iowa-like: one summer peak per year; maize in year 1 and soybean in year 2 of the cycle.
    smooth = [0.2, 0.2, 0.2, 0.25, 0.4, 0.6, 0.85, 0.7, 0.5, 0.3, 0.2, 0.2]
    seasons = {"looks_cropped": True, "smoothed_profile": smooth,
               "seasons": [{"start_month": 5, "peak_month": 7, "end_month": 9}]}
    rot = {"windows": [
        {"crop": "maize", "label": "Maize", "start_month": 5, "end_month": 10, "months": [5, 6, 7, 8, 9, 10], "year": 1},
        {"crop": "soybean", "label": "Soybean", "start_month": 5, "end_month": 10, "months": [5, 6, 7, 8, 9, 10], "year": 2},
    ]}
    check = ndvi_client.compare_with_calendar(seasons, rot)
    assert check["status"] == "consistent" and "1 crop a year" in check["text"]
