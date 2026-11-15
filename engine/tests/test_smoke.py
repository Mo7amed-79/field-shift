# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Smoke tests: modules import, mock matches the contract, API responds."""

import importlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas import CompareResponse
from engine import power_client, soil_client

ROOT = Path(__file__).resolve().parents[2]

ENGINE_MODULES = [
    "power_client",
    "soil_client",
    "ndvi_client",
    "climate",
    "forecast",
    "suitability",
    "rotation_builder",
    "water_balance",
    "scoring",
    "planner",
    "explain",
]

client = TestClient(app)


@pytest.mark.parametrize("name", ENGINE_MODULES)
def test_engine_module_imports(name):
    importlib.import_module(f"engine.{name}")


def test_mock_compare_validates():
    raw = json.loads((ROOT / "frontend" / "mock" / "compare.json").read_text(encoding="utf-8"))
    resp = CompareResponse.model_validate(raw)
    assert len(resp.rotations) == 3
    assert all(len(r.year_plan) == 12 for r in resp.rotations)
    assert all(r.suitability for r in resp.rotations)
    assert resp.rotations[0].is_current
    assert "REAL engine output" in resp.run_info.note
    assert resp.ndvi_status == "loaded" and len(resp.ndvi) == 69  # mock run had Tanta NDVI cached
    assert resp.ndvi_check is not None


def test_post_compare_returns_200():
    # Offline: serve the real Tanta POWER and SoilGrids responses from the test cache.
    fixtures = Path(__file__).parent / "fixtures"
    shutil.copy(fixtures / "power_30.85_31.05_1991_2025.json", power_client.CACHE_DIR)
    shutil.copy(fixtures / "soil_30.850_31.050.json", soil_client.CACHE_DIR)
    body = {
        "lat": 30.85,
        "lon": 31.05,
        "current_rotation": ["rice", "wheat"],
        "water_access": "full_irrigation",
        "priorities": {"water": 0.6, "soil": 0.3, "heat_risk": 0.1},
        "scenario": {"temp_delta_c": 0, "water_cut_pct": 0},
    }
    r = client.post("/api/compare", json=body)
    assert r.status_code == 200
    assert r.json()["location"]["soil"]["texture_class"]


def test_get_location_returns_200():
    r = client.get("/api/location", params={"lat": 31.0, "lon": 31.0})
    assert r.status_code == 200
    assert "seasons_detected" in r.json()


def test_ai_disclosure_in_metadata():
    sources = client.get("/api/sources").json()
    assert "Claude" in sources["ai_disclosure"]
    assert sources["sources"]
    assert "AI_USAGE.md" in client.get("/openapi.json").json()["info"]["description"]
