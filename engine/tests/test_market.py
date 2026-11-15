# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Offline tests for indicative gross revenue (engine/market.py) and its use in /api/compare."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from engine import market, regional
from engine.tests.test_compare import PUNJAB, TANTA, _body, _cache, _run

EGYPT = market.load_market(regional.find_pack(*TANTA))


def _rot(*crops):
    return {"crops": [{"crop": c, "label": c.replace("_", " ").capitalize()} for c in crops]}


def test_egypt_market_linked_from_pack():
    assert EGYPT["currency"] == "EGP"
    assert market.load_market(regional.find_pack(*PUNJAB)) is None


def test_rice_wheat_revenue_is_yield_times_price():
    rev = market.rotation_revenue(_rot("rice", "wheat"), EGYPT, {"value": 2, "unit": "feddan"})
    assert rev["complete"] and rev["missing"] == []
    rice, wheat = rev["per_crop"]
    assert rice["revenue_per_ha"] == round(5.8209 * 27000)
    assert wheat["revenue_per_ha"] == round(6.5714 * 8333)
    assert rev["total_per_ha"] == rice["revenue_per_ha"] + wheat["revenue_per_ha"]
    assert rev["total_per_feddan"] == pytest.approx(rev["total_per_ha"] * 0.42, abs=1)
    assert rev["total_for_area"] == pytest.approx(rev["total_per_ha"] * 0.84, abs=1)
    assert any("upper bound" in n for n in rev["notes"])
    assert rev["scored"] is False


def test_missing_price_makes_revenue_incomplete():
    rev = market.rotation_revenue(_rot("maize", "berseem"), EGYPT)
    assert not rev["complete"] and rev["total_per_ha"] is None
    assert rev["missing"] == [{"crop": "berseem", "label": "Berseem", "missing": ["yield", "price"]}]


def test_compare_revenue_blocks_and_trade_off_sentence():
    out = _run()
    by_id = {r["id"]: r for r in out["all_ranked"]}
    assert all("revenue" in r for r in out["rotations"])
    mw = by_id["maize_wheat"]["revenue"]
    assert mw["revenue_change_pct"] == pytest.approx(-41.8, abs=0.1)
    assert by_id["maize_faba_bean"]["revenue"]["revenue_change_pct"] is None
    alt = out["rotations"][1]  # maize -> faba bean: no faba bean price
    assert "no price for Faba bean" in alt["why"]
    for r in out["rotations"]:
        assert "profit" not in r["why"].lower() and "income" not in r["why"].lower()
        assert "income" not in r["not_assessed"].lower()


def test_trade_off_sentence_when_both_complete():
    out = _run(current=("maize", "wheat"))
    soy = next(r for r in out["all_ranked"] if r["id"] == "rice_wheat")
    assert soy["revenue"]["revenue_change_pct"] > 0
    from engine import explain
    cur = out["rotations"][0]
    r = {"revenue": soy["revenue"], "water_saving_pct": soy["water_saving_pct"]}
    s = explain.revenue_sentence(r, cur, market.basis_text(EGYPT))
    assert "more irrigation water but indicative gross revenue is about" in s and "% higher" in s
    assert "costs not included" in s and "upper bound" in s


def test_punjab_revenue_unavailable():
    out = _run(*PUNJAB)
    assert all(r["revenue"]["available"] is False for r in out["rotations"])
    assert "crop prices" in out["rotations"][0]["not_assessed"]


def test_api_compare_has_revenue():
    _cache(*TANTA)
    body = TestClient(app).post("/api/compare", json=_body(*TANTA, ["rice", "wheat"])).json()
    assert body["rotations"][0]["revenue"]["currency"] == "EGP"
    assert all("revenue" in r for r in body["all_ranked"])
