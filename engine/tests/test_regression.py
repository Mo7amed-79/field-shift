# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Regression check: the Tanta rice -> wheat result must not change (overnight run baseline)."""

from engine.tests.test_compare import _run


def test_tanta_rice_wheat_totals_unchanged():
    out = _run()
    totals = {r["id"]: r["total"] for r in out["rotations"]}
    assert totals == {"maize_faba_bean": 90.5, "maize_berseem": 89.9, "current": 60.0}
    current = out["rotations"][0]
    assert current["scores"] == {"water": 50.0, "soil_practices": 30.0, "heat_risk": 100.0}
    assert current["water_mm_per_year"] == 1872.4
    ranked = [(r["id"], r["total"]) for r in out["all_ranked"]]
    assert ranked == [
        ("maize_faba_bean", 90.5), ("maize_berseem", 89.9), ("soybean_wheat", 88.2),
        ("cotton_short_berseem", 88.1), ("rice_berseem", 85.4), ("rice_faba_bean", 84.1),
        ("maize_wheat", 65.0), ("rice_wheat", 60.0),
    ]
