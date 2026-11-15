# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Turn raw POWER data into monthly and seasonal climate aggregates.

Input:  raw dict from power_client.fetch_monthly ({"meta", "monthly"}).
Output: dict with a 12-month climatology (temp, tmax, tmin, rain, ET0),
        per-year totals, decadal trends, rain variability and data gaps.

ET0 is FAO-56 Penman-Monteith (Allen et al. 1998, FAO Irrigation and
Drainage Paper 56). Mean daily Tmax / Tmin are estimated as
T2M ± T2M_RANGE / 2, because POWER's monthly T2M_MAX / T2M_MIN are monthly
extremes, not means of daily values.
"""

import calendar
import math
from statistics import mean, stdev

from scipy.stats import linregress

SOLAR_CONSTANT = 0.0820  # MJ m-2 min-1
STEFAN_BOLTZMANN = 4.903e-9  # MJ K-4 m-2 day-1
ALBEDO = 0.23  # FAO-56 hypothetical grass reference

# POWER unit string -> factor to MJ m-2 day-1
_RADIATION_TO_MJ = {"MJ/m^2/day": 1.0, "kW-hr/m^2/day": 3.6, "W/m^2": 0.0864}


def _sat_vp(t_c: float) -> float:
    """Saturation vapour pressure e°(T), kPa. FAO-56 Eq. 11."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def et0_penman_monteith(
    tmax_c: float,
    tmin_c: float,
    rh_mean_pct: float | None,
    u2_ms: float,
    rs_mj_m2_day: float,
    elevation_m: float,
    lat_deg: float,
    doy: int,
    g_mj_m2_day: float = 0.0,
    ea_kpa: float | None = None,
) -> float:
    """Reference evapotranspiration ET0 in mm/day, FAO-56 Penman-Monteith (Eq. 6).

    ``ea_kpa`` overrides the vapour pressure derived from ``rh_mean_pct``.
    For monthly data, ``doy`` is the mid-month day and ``g_mj_m2_day`` comes
    from Eq. 43/44.
    """
    t_mean = (tmax_c + tmin_c) / 2  # Eq. 9

    p = 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26  # Eq. 7, kPa
    gamma = 0.665e-3 * p  # Eq. 8, kPa °C-1

    es = (_sat_vp(tmax_c) + _sat_vp(tmin_c)) / 2  # Eq. 12
    if ea_kpa is None:
        ea_kpa = rh_mean_pct / 100 * es  # Eq. 19
    delta = 4098 * _sat_vp(t_mean) / (t_mean + 237.3) ** 2  # Eq. 13

    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)  # Eq. 23
    decl = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)  # Eq. 24
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(decl))))  # Eq. 25
    ra = (24 * 60 / math.pi) * SOLAR_CONSTANT * dr * (
        ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws)
    )  # Eq. 21
    rso = (0.75 + 2e-5 * elevation_m) * ra  # Eq. 37
    rns = (1 - ALBEDO) * rs_mj_m2_day  # Eq. 38
    rs_rso = min(rs_mj_m2_day / rso, 1.0) if rso > 0 else 1.0
    rnl = (
        STEFAN_BOLTZMANN
        * ((tmax_c + 273.16) ** 4 + (tmin_c + 273.16) ** 4) / 2
        * (0.34 - 0.14 * math.sqrt(ea_kpa))
        * (1.35 * rs_rso - 0.35)
    )  # Eq. 39
    rn = rns - rnl  # Eq. 40

    num = 0.408 * delta * (rn - g_mj_m2_day) + gamma * (900 / (t_mean + 273)) * u2_ms * (es - ea_kpa)
    den = delta + gamma * (1 + 0.34 * u2_ms)
    return max(num / den, 0.0)  # Eq. 6


def _mid_month_doy(month: int) -> int:
    """Day of year at mid-month, J = INT(30.4 M - 15) (FAO-56, Ch. 3)."""
    return int(30.4 * month - 15)


def _check_units(units: dict) -> tuple[float, bool]:
    """Return (radiation factor to MJ m-2 day-1, precip_is_daily_rate)."""
    for name, expected in (("T2M", "C"), ("T2M_RANGE", "C"), ("RH2M", "%"), ("WS2M", "m/s")):
        if units.get(name) != expected:
            raise ValueError(f"Unexpected unit for {name}: {units.get(name)!r}, expected {expected!r}")
    rad_unit = units["ALLSKY_SFC_SW_DWN"]
    if rad_unit not in _RADIATION_TO_MJ:
        raise ValueError(f"Unsupported radiation unit: {rad_unit!r}")
    precip_unit = units["PRECTOTCORR"]
    if precip_unit == "mm/day":
        daily = True
    elif precip_unit in ("mm/month", "mm"):
        daily = False
    else:
        raise ValueError(f"Unsupported precipitation unit: {precip_unit!r}")
    return _RADIATION_TO_MJ[rad_unit], daily


def _monthly_records(raw: dict) -> list[dict]:
    """Per year-month values in summary units (°C, mm/month), with ET0."""
    meta = raw["meta"]
    rad_factor, precip_daily = _check_units(meta["units"])
    lat = meta["grid_cell"]["lat"]
    elevation = meta["elevation_m"]
    rows = sorted(raw["monthly"], key=lambda r: (r["year"], r["month"]))

    out = []
    for i, r in enumerate(rows):
        days = calendar.monthrange(r["year"], r["month"])[1]
        t, rng = r.get("t2m"), r.get("t2m_range")
        tmax = tmin = None
        if t is not None and rng is not None:
            tmax, tmin = t + rng / 2, t - rng / 2

        rain = r.get("precip")
        if rain is not None and precip_daily:
            rain *= days

        # Soil heat flux from neighbouring monthly means: Eq. 43, else Eq. 44, else 0.
        prev_t = rows[i - 1].get("t2m") if i > 0 else None
        next_t = rows[i + 1].get("t2m") if i + 1 < len(rows) else None
        if prev_t is not None and next_t is not None:
            g = 0.07 * (next_t - prev_t)  # Eq. 43
        elif prev_t is not None and t is not None:
            g = 0.14 * (t - prev_t)  # Eq. 44
        else:
            g = 0.0

        et0 = None
        inputs = (tmax, r.get("rh2m"), r.get("ws2m"), r.get("srad"))
        if all(v is not None for v in inputs):
            et0 = days * et0_penman_monteith(
                tmax, tmin, r["rh2m"], r["ws2m"], r["srad"] * rad_factor,
                elevation, lat, _mid_month_doy(r["month"]), g,
            )

        out.append({
            "year": r["year"], "month": r["month"],
            "temp_c": t, "tmax_c": tmax, "tmin_c": tmin,
            "rain_mm": rain, "et0_mm": et0,
            "missing": [k for k in ("t2m", "t2m_range", "rh2m", "ws2m", "srad", "precip") if r.get(k) is None],
        })
    return out


def _mean_or_none(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return mean(vals) if vals else None


RECENT_YEARS = (2016, 2025)
BASELINE_YEARS = (1991, 2005)


def _trend(points: list[tuple[int, float]]) -> tuple[float | None, float | None]:
    """(slope per decade, two-sided p-value) of an ordinary least-squares fit."""
    if len(points) < 3:
        return None, None
    x, y = zip(*points)
    fit = linregress(x, y)
    return float(fit.slope * 10), float(fit.pvalue)


def _recent_vs_baseline(yearly: list[dict]) -> dict:
    """Mean of RECENT_YEARS minus mean of BASELINE_YEARS, per variable.

    A period's mean is None unless every one of its years is present.
    """
    def period_mean(key: str, first: int, last: int) -> float | None:
        vals = [y[key] for y in yearly if first <= y["year"] <= last and y[key] is not None]
        return mean(vals) if len(vals) == last - first + 1 else None

    out = {"recent": f"{RECENT_YEARS[0]}–{RECENT_YEARS[1]}", "baseline": f"{BASELINE_YEARS[0]}–{BASELINE_YEARS[1]}"}
    for key in ("temp_c", "rain_mm", "et0_mm"):
        recent, baseline = period_mean(key, *RECENT_YEARS), period_mean(key, *BASELINE_YEARS)
        out[key] = {
            "recent": recent,
            "baseline": baseline,
            "diff": recent - baseline if recent is not None and baseline is not None else None,
        }
    return out


def _rain_record_warning(rvb: dict) -> str | None:
    """Warn when recent rain is more than double the baseline (likely a data artefact)."""
    recent, baseline = rvb["rain_mm"]["recent"], rvb["rain_mm"]["baseline"]
    if recent is None or baseline is None or recent <= 2 * baseline:
        return None
    return (
        f"Mean annual rain {rvb['recent']} ({recent:.0f} mm) is more than double the "
        f"{rvb['baseline']} mean ({baseline:.0f} mm). This may reflect a change in the NASA POWER "
        "precipitation source rather than real rainfall, and must not be read as a climate trend."
    )


def summarize(raw: dict) -> dict:
    """Compute monthly climatology, yearly totals, trends, variability and gaps from raw POWER data."""
    recs = _monthly_records(raw)
    years = sorted({r["year"] for r in recs})

    climatology = []
    for m in range(1, 13):
        month_recs = [r for r in recs if r["month"] == m]
        climatology.append({"month": m, **{
            k: _mean_or_none([r[k] for r in month_recs])
            for k in ("temp_c", "tmax_c", "tmin_c", "rain_mm", "et0_mm")
        }})

    yearly = []
    for y in years:
        yr = [r for r in recs if r["year"] == y]
        complete = len(yr) == 12
        temps, rains, et0s = ([r[k] for r in yr] for k in ("temp_c", "rain_mm", "et0_mm"))
        yearly.append({
            "year": y,
            "temp_c": mean(temps) if complete and None not in temps else None,
            "rain_mm": sum(rains) if complete and None not in rains else None,
            "et0_mm": sum(et0s) if complete and None not in et0s else None,
        })

    temp_pts = [(y["year"], y["temp_c"]) for y in yearly if y["temp_c"] is not None]
    et0_pts = [(y["year"], y["et0_mm"]) for y in yearly if y["et0_mm"] is not None]
    rain_annual = [y["rain_mm"] for y in yearly if y["rain_mm"] is not None]
    rain_cv = None
    if len(rain_annual) >= 2 and mean(rain_annual) > 0:
        rain_cv = stdev(rain_annual) / mean(rain_annual)

    rvb = _recent_vs_baseline(yearly)
    temp_slope, temp_p = _trend(temp_pts)
    et0_slope, et0_p = _trend(et0_pts)

    return {
        "monthly_climatology": climatology,
        "yearly": yearly,
        "trend": {
            "temp_c_per_decade": temp_slope,
            "temp_p_value": temp_p,
            "et0_mm_per_decade": et0_slope,
            "et0_p_value": et0_p,
            "n_years_temp": len(temp_pts),
            "n_years_et0": len(et0_pts),
            "method": "OLS on annual values over the full record (scipy.stats.linregress); "
                      "rain is deliberately not trended, see rain_record_warning",
        },
        "recent_vs_baseline": rvb,
        "rain_record_warning": _rain_record_warning(rvb),
        "variability": {"rain_annual_cv": rain_cv, "n_years": len(rain_annual)},
        "data_gaps": [f"{r['year']}-{r['month']:02d}" for r in recs if r["missing"]],
        "period": f"{years[0]}–{years[-1]}" if years else "",
        "units": {
            "temp_c": "°C", "tmax_c": "°C (mean daily max, T2M + T2M_RANGE/2)",
            "tmin_c": "°C (mean daily min, T2M - T2M_RANGE/2)",
            "rain_mm": "mm/month (yearly: mm/year)", "et0_mm": "mm/month (yearly: mm/year)",
            "temp_c_per_decade": "°C/decade", "et0_mm_per_decade": "mm/year per decade",
        },
    }
