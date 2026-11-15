# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for the USDA Cropland Data Layer client and /api/field-history (saved real responses)."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import cdl_client, regional
from engine.tests.test_compare import TANTA, _body, _cache

FIXTURES = Path(__file__).parent / "fixtures"
IOWA = (42.0, -93.75)
KANSAS = (38.75, -99.15)
CMAP = cdl_client.load_map()


def _hist(lat, lon):
    return json.loads((FIXTURES / f"cdl_{lat:.4f}_{lon:.4f}.json").read_text(encoding="utf-8"))


def _cache_cdl(lat, lon):
    shutil.copy(FIXTURES / f"cdl_{lat:.4f}_{lon:.4f}.json", cdl_client.CACHE_DIR)


def _synthetic(*values):
    return {"years": [{"year": 2020 + i, "value": v, "category": "x"} for i, v in enumerate(values)]}


def test_parse_real_response():
    raw = _hist(*IOWA)["years"][0]["raw"]
    assert raw.startswith("<ns1:GetCDLValueResponse")
    assert cdl_client.parse_value(raw) == (5, "Soybeans")
    fault = "<faultstring>Error: The year must be between 1997 and 2025.</faultstring>"
    assert cdl_client.parse_year_range(fault) == (1997, 2025)
    with pytest.raises(cdl_client.CdlUnavailableError):
        cdl_client.parse_value(fault)


def test_albers_projection_matches_service_example():
    # CropScape doc example x/y (EPSG:5070) lies in northern Virginia
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(5070, 4326, always_xy=True).transform(1551565.363, 1909363.537)
    assert 38.5 < lat < 39.2 and -78.2 < lon < -77.5
    x, y = cdl_client.to_albers(lat, lon)
    assert x == pytest.approx(1551565.363, abs=0.01) and y == pytest.approx(1909363.537, abs=0.01)


def test_legend_codes_used():
    m = CMAP["crop_map"]
    assert m["1"]["legend_name"] == "Corn" and m["1"]["crops"] == ["maize"]
    assert m["5"]["crops"] == ["soybean"] and m["24"]["crops"] == ["wheat"]
    assert m["61"]["crops"] == ["fallow"] and m["26"]["crops"] == ["wheat", "soybean"]
    assert CMAP["non_crop_codes"]["176"] == "Grassland/Pasture"


def test_iowa_detects_maize_soybean():
    inf = cdl_client.infer_rotation(_hist(*IOWA), CMAP, regional.find_pack(*IOWA))
    assert inf["status"] == "detected" and inf["confidence"] == "high"
    assert inf["rotation"] == ["maize", "soybean"] and inf["matched_pack_rotation"] == "maize_soybean"


def test_kansas_detects_wheat_sorghum_fallow():
    inf = cdl_client.infer_rotation(_hist(*KANSAS), CMAP, regional.find_pack(*KANSAS))
    assert inf["rotation"] == ["wheat", "sorghum", "fallow"] and inf["cycle"] == [["wheat"], ["sorghum"], []]
    assert inf["n_years"] == 3 and inf["matched_pack_rotation"] == "wheat_sorghum_fallow"


def test_inference_edge_cases():
    assert cdl_client.infer_rotation(_synthetic(24, 24, 24, 24, 24, 24), CMAP)["rotation"] == ["wheat"]
    assert cdl_client.infer_rotation(_synthetic(26, 26, 26, 26), CMAP)["rotation"] == ["wheat", "soybean"]
    noisy = cdl_client.infer_rotation(_synthetic(1, 5, 1, 5, 1, 1), CMAP)
    assert noisy["n_years"] == 2 and noisy["confidence"] == "medium"
    assert cdl_client.infer_rotation(_synthetic(176, 176, 176, 1, 176, 176), CMAP)["status"] == "not_cropland"
    assert cdl_client.infer_rotation(_synthetic(1, 5), CMAP)["status"] == "not_detected"


def test_field_history_endpoint():
    _cache_cdl(*KANSAS)
    j = TestClient(app).get("/api/field-history", params={"lat": KANSAS[0], "lon": KANSAS[1]}).json()
    assert j["status"] == "detected" and j["rotation"] == ["wheat", "sorghum", "fallow"]
    assert [y["year"] for y in j["yearly"]] == list(range(2020, 2026)) and j["is_cached"] is True
    j = TestClient(app).get("/api/field-history", params={"lat": TANTA[0], "lon": TANTA[1]}).json()
    assert j["status"] == "not_available"


def test_field_history_offline_is_unavailable_not_500():
    j = TestClient(app).get("/api/field-history", params={"lat": IOWA[0], "lon": IOWA[1]}).json()
    assert j["status"] == "unavailable"  # network disabled in tests, nothing cached


def test_compare_detects_rotation_when_omitted():
    _cache(*KANSAS)
    _cache_cdl(*KANSAS)
    body = _body(*KANSAS, None)
    body["water_access"] = "rainfed"
    r = TestClient(app).post("/api/compare", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["current_rotation_source"] == "cdl_detected"
    assert j["rotations"][0]["name"] == "Wheat → Sorghum → Fallow" and j["rotations"][0]["n_years"] == 3
    assert "Cropland Data Layer" in j["warnings"][0]
    assert j["field_history"]["matched_pack_rotation"] == "wheat_sorghum_fallow"


def test_compare_without_rotation_outside_us_is_400():
    _cache(*TANTA)
    r = TestClient(app).post("/api/compare", json=_body(*TANTA, None))
    assert r.status_code == 400 and "not_available" in r.json()["detail"]
