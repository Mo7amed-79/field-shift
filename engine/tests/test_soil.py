# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for the SoilGrids client and the /api/location endpoint."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import soil_client
from engine.soil_client import texture_class_usda

FIXTURES = Path(__file__).parent / "fixtures"
TANTA_FIXTURE = FIXTURES / "soil_30.850_31.050.json"  # real response, 2026-11-14
NULL_FIXTURE = FIXTURES / "soil_31.800_31.000_null.json"  # real response for a sea point
TANTA = (30.85, 31.05)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- parser ---------------------------------------------------------------

def test_parser_reads_units_from_response():
    parsed = soil_client.parse_response(_load(TANTA_FIXTURE))
    assert parsed["units"] == {
        "clay_pct": "%", "sand_pct": "%", "silt_pct": "%", "ph": "-", "organic_carbon_g_kg": "g/kg",
    }


def test_parser_depth_weighting():
    # Raw clay means (g/kg, d_factor 10): 0-5 cm 394, 5-15 cm 415, 15-30 cm 385.
    parsed = soil_client.parse_response(_load(TANTA_FIXTURE))
    assert parsed["clay_pct"] == pytest.approx((5 * 39.4 + 10 * 41.5 + 15 * 38.5) / 30)
    # SOC dg/kg: 407, 262, 259 -> g/kg
    assert parsed["organic_carbon_g_kg"] == pytest.approx((5 * 40.7 + 10 * 26.2 + 15 * 25.9) / 30)
    assert parsed["ph"] == pytest.approx(7.2)
    total = parsed["clay_pct"] + parsed["sand_pct"] + parsed["silt_pct"]
    assert total == pytest.approx(100, abs=0.5)


def test_parser_rejects_unexpected_unit():
    data = _load(TANTA_FIXTURE)
    data["properties"]["layers"][0]["unit_measure"]["target_units"] = "g/kg"
    with pytest.raises(ValueError):
        soil_client.parse_response(data)


def test_parser_null_cell():
    parsed = soil_client.parse_response(_load(NULL_FIXTURE))
    assert parsed["clay_pct"] is None and parsed["texture_class"] is None


# --- USDA texture triangle ------------------------------------------------

@pytest.mark.parametrize(
    "clay, sand, silt, expected",
    [
        (5, 92, 3, "sand"),
        (8, 80, 12, "loamy sand"),
        (10, 65, 25, "sandy loam"),
        (20, 40, 40, "loam"),
        (15, 20, 65, "silt loam"),
        (5, 5, 90, "silt"),
        (25, 60, 15, "sandy clay loam"),
        (33, 33, 34, "clay loam"),
        (33, 10, 57, "silty clay loam"),
        (40, 50, 10, "sandy clay"),
        (45, 5, 50, "silty clay"),
        (60, 20, 20, "clay"),
    ],
)
def test_texture_triangle(clay, sand, silt, expected):
    assert texture_class_usda(clay, sand, silt) == expected


def test_texture_normalises_fractions():
    assert texture_class_usda(6, 46, 48) == texture_class_usda(60, 460, 480)


# --- nulls, cache and offline ---------------------------------------------

def test_null_cell_uses_neighbour(monkeypatch):
    calls = []

    def fake_request(url, timeout_s):
        calls.append(url)
        return _load(NULL_FIXTURE) if len(calls) == 1 else _load(TANTA_FIXTURE)

    monkeypatch.setattr(soil_client, "_request", fake_request)
    soil = soil_client.fetch_soil(31.8, 31.0)
    assert len(calls) == 2  # centre (null), then first neighbour
    assert soil["texture_class"] == "clay loam"
    assert soil["meta"]["lat_used"] == pytest.approx(31.805)
    assert "neighbouring point" in soil["meta"]["note"]


def test_all_null_raises(monkeypatch):
    monkeypatch.setattr(soil_client, "_request", lambda url, timeout_s: _load(NULL_FIXTURE))
    with pytest.raises(soil_client.SoilUnavailableError):
        soil_client.fetch_soil(31.8, 31.0)


def test_offline_uses_cache():
    shutil.copy(TANTA_FIXTURE, soil_client.cache_path(*TANTA))
    soil = soil_client.fetch_soil(*TANTA)
    assert soil["meta"]["is_cached"] is True
    assert soil["meta"]["note"] is None
    assert soil["meta"]["resolution_note"] == soil_client.RESOLUTION_NOTE


def test_offline_without_cache_raises():
    with pytest.raises(soil_client.SoilUnavailableError):
        soil_client.fetch_soil(*TANTA)


# --- /api/location --------------------------------------------------------

def test_api_location_real_soil_from_cache():
    shutil.copy(TANTA_FIXTURE, soil_client.cache_path(*TANTA))
    r = TestClient(app).get("/api/location", params={"lat": TANTA[0], "lon": TANTA[1]})
    assert r.status_code == 200
    body = r.json()
    assert body["soil"]["source"] == "ISRIC SoilGrids v2.0"
    assert body["soil"]["texture_class"] == "clay loam"
    assert body["seasons_detected"] == []


def test_api_location_soil_unavailable_returns_none(monkeypatch):
    def boom(lat, lon):
        raise soil_client.SoilUnavailableError("SoilGrids down")

    monkeypatch.setattr(soil_client, "fetch_soil", boom)
    r = TestClient(app).get("/api/location", params={"lat": 30.0, "lon": 31.0})
    assert r.status_code == 200
    body = r.json()
    assert body["soil"] is None
    assert "manually" in body["soil_message"]


def test_second_ring_used_with_distance(monkeypatch):
    calls = []

    def fake_request(url, timeout_s):
        calls.append(url)
        return _load(NULL_FIXTURE) if len(calls) <= 5 else _load(TANTA_FIXTURE)

    monkeypatch.setattr(soil_client, "_request", fake_request)
    soil = soil_client.fetch_soil(31.8, 31.0)
    assert len(calls) == 6  # centre + 4 first-ring (null), then first second-ring point
    assert soil["meta"]["lat_used"] == pytest.approx(31.82)
    assert soil["meta"]["distance_km"] == pytest.approx(2.22, abs=0.05)
    assert "km away" in soil["meta"]["note"]


def test_request_cap(monkeypatch):
    calls = []

    def fake_request(url, timeout_s):
        calls.append(url)
        return _load(NULL_FIXTURE)

    monkeypatch.setattr(soil_client, "_request", fake_request)
    with pytest.raises(soil_client.SoilUnavailableError):
        soil_client.fetch_soil(31.8, 31.0)
    assert len(calls) == soil_client.MAX_REQUESTS == 9  # centre + first ring + full second ring
