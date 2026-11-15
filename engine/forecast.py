# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Project next year's monthly temperature and ET0 with a simple statistical model.

Input:  raw dict from power_client.fetch_monthly (multi-year monthly history).
Output: a 12-month projection of temp_c and et0_mm for the year after the
        record, the method in plain words, and a back-test against a
        climatology baseline (train to 2020, test 2021-2025).

Model (per variable, ordinary least squares):
    value(year, month) = s[month] + b x (year + (month - 0.5) / 12 - t0)
i.e. one mean per calendar month plus one linear trend. This is a simple
statistical projection, not a weather or seasonal forecast: it knows nothing
about El Nino, soil moisture or next year's weather. Rain is not projected.
"""

from statistics import mean

import numpy as np

from engine import climate

VARIABLES = ("temp_c", "et0_mm")
TRAIN_END = 2020
TEST_YEARS = (2021, 2025)
METHOD = (
    "Simple statistical projection, not a weather forecast: for each variable, a least-squares fit of one "
    "mean per calendar month plus a single linear trend over the NASA POWER monthly record, extended 12 "
    "months ahead. Rain is not projected (the climatology is used)."
)


def _records(raw: dict) -> list[dict]:
    return [r for r in climate._monthly_records(raw) if all(r[v] is not None for v in VARIABLES)]


def _design(recs: list[dict], t0: float) -> np.ndarray:
    x = np.zeros((len(recs), 13))
    for i, r in enumerate(recs):
        x[i, r["month"] - 1] = 1.0
        x[i, 12] = r["year"] + (r["month"] - 0.5) / 12 - t0
    return x


def fit(recs: list[dict], var: str, t0: float) -> dict:
    x = _design(recs, t0)
    y = np.array([r[var] for r in recs])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    return {"seasonal": coef[:12].tolist(), "trend_per_year": float(coef[12]), "t0": t0,
            "residual_sd": float(np.std(resid, ddof=13)) if len(y) > 13 else None}


def predict(model: dict, year: int, month: int) -> float:
    return model["seasonal"][month - 1] + model["trend_per_year"] * (year + (month - 0.5) / 12 - model["t0"])


def backtest(recs: list[dict], train_end: int = TRAIN_END, test_years: tuple[int, int] = TEST_YEARS) -> dict:
    """MAE of the trend model and of the monthly climatology (both fitted on years <= train_end)."""
    train = [r for r in recs if r["year"] <= train_end]
    test = [r for r in recs if test_years[0] <= r["year"] <= test_years[1]]
    if len(train) < 24 or not test:
        return {"available": False, "note": "Not enough data for a back-test."}
    t0 = train[0]["year"]
    out = {"available": True, "train_years": [train[0]["year"], train_end], "test_years": list(test_years),
           "n_test_months": len(test)}
    for var in VARIABLES:
        model = fit(train, var, t0)
        clim = {m: mean(r[var] for r in train if r["month"] == m) for m in range(1, 13)}
        mae_model = mean(abs(predict(model, r["year"], r["month"]) - r[var]) for r in test)
        mae_clim = mean(abs(clim[r["month"]] - r[var]) for r in test)
        out[var] = {
            "mae_model": round(mae_model, 3), "mae_climatology": round(mae_clim, 3),
            "skill_vs_climatology": round(1 - mae_model / mae_clim, 3) if mae_clim else None,
            "beats_climatology": mae_model < mae_clim,
        }
    return out


def _verdict(bt: dict) -> str:
    if not bt.get("available"):
        return bt.get("note", "No back-test.")
    parts = []
    for var, name, unit in (("temp_c", "temperature", "°C"), ("et0_mm", "ET0", "mm/month")):
        b = bt[var]
        word = "beats" if b["beats_climatology"] else "does NOT beat"
        parts.append(f"{name}: MAE {b['mae_model']:.2f} {unit} vs {b['mae_climatology']:.2f} for the climatology "
                     f"baseline, so the trend model {word} it")
    return (f"Back-test (fit {bt['train_years'][0]}–{bt['train_years'][1]}, test {bt['test_years'][0]}–"
            f"{bt['test_years'][1]}): " + "; ".join(parts) + ".")


def project_next_year(raw: dict) -> dict:
    """12-month projection of temperature and ET0 for the year after the record, with a back-test."""
    recs = _records(raw)
    if len(recs) < 36:
        raise ValueError("At least three years of monthly data are needed for a projection.")
    last_year = max(r["year"] for r in recs)
    target = last_year + 1
    t0 = recs[0]["year"]
    models = {v: fit(recs, v, t0) for v in VARIABLES}
    months = [
        {"month": m, "temp_c": round(predict(models["temp_c"], target, m), 2),
         "et0_mm": round(max(predict(models["et0_mm"], target, m), 0.0), 1)}
        for m in range(1, 13)
    ]
    bt = backtest(recs)
    return {
        "year": target,
        "months": months,
        "trend_per_decade": {v: round(10 * models[v]["trend_per_year"], 3) for v in VARIABLES},
        "residual_sd": {v: None if models[v]["residual_sd"] is None else round(models[v]["residual_sd"], 3)
                        for v in VARIABLES},
        "fitted_years": [t0, last_year],
        "backtest": bt,
        "method": f"{METHOD} {_verdict(bt)}",
    }


def forecast_climate(climate_summary: dict, projection: dict) -> dict:
    """A climate summary for the projected year: projected temperature (Tmax/Tmin shifted by the same
    amount) and ET0, climatological rain. Used when the scenario asks for use_forecast."""
    proj = {m["month"]: m for m in projection["months"]}
    out = dict(climate_summary)
    out["monthly_climatology"] = []
    for m in climate_summary["monthly_climatology"]:
        p = proj[m["month"]]
        shift = p["temp_c"] - m["temp_c"]
        out["monthly_climatology"].append({
            **m, "temp_c": p["temp_c"], "et0_mm": p["et0_mm"],
            "tmax_c": None if m.get("tmax_c") is None else m["tmax_c"] + shift,
            "tmin_c": None if m.get("tmin_c") is None else m["tmin_c"] + shift,
        })
    out["period"] = f"projected {projection['year']} (rain: {climate_summary.get('period')} climatology)"
    return out
