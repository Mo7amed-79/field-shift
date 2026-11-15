# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""FastAPI app for Field Shift. Run with: uvicorn backend.main:app --reload"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend import chat as chat_llm
from backend.schemas import (
    ChatRequest, ChatResponse, ClimateInfo, CompareRequest, CompareResponse, FieldHistory, LocationInfo,
    NdviResponse, SoilInfo,
)
from engine import climate as climate_engine
from engine import forecast as forecast_engine
from engine import cdl_client, chat_context, ndvi_client, pipeline, power_client, regional, soil_client, water_balance

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# Climate record: 1991 to the last complete year available from POWER.
CLIMATE_START_YEAR, CLIMATE_END_YEAR = 1991, 2025
# The ORNL MODIS API often takes 40-60 s per request, so NDVI is fetched in a background
# thread with a longer per-request timeout; /api/compare and /api/location only read the cache.
NDVI_BACKGROUND_TIMEOUT_S = 120
_ndvi_jobs: dict[tuple[float, float], dict] = {}
_ndvi_lock = threading.Lock()


def _load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


AI_DISCLOSURE = (
    "This project used AI (SI) tools: code was generated and refactored with Claude (Anthropic) "
    "via Claude Code, and Claude chat supported research and planning. All AI output was reviewed "
    "and tested by the Field Shift team. See AI_USAGE.md."
)

app = FastAPI(
    title="Field Shift API",
    description=f"Compares a farmer's crop rotation with known alternatives using NASA POWER climate data.\n\n**Use of AI:** {AI_DISCLOSURE}",
)

# Open CORS for local dev only; tighten before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/crops")
def crops():
    return _load_json(DATA_DIR / "crops.json")


@app.get("/api/sources")
def sources():
    return _load_json(DATA_DIR / "sources.json")


def _fetch_power(lat: float, lon: float) -> dict:
    try:
        return power_client.fetch_monthly(lat, lon, CLIMATE_START_YEAR, CLIMATE_END_YEAR)
    except power_client.PowerRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except power_client.PowerUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))


def _fetch_soil(lat: float, lon: float) -> tuple[dict | None, SoilInfo | None, str | None]:
    """(raw engine soil dict, SoilInfo, message); soil is None when SoilGrids is unavailable."""
    try:
        s = soil_client.fetch_soil(lat, lon)
    except (soil_client.SoilUnavailableError, ValueError) as e:  # ValueError: unexpected units
        return None, None, f"Soil data unavailable, please enter your soil texture manually. ({e})"
    meta = s["meta"]
    info = SoilInfo(
        source=meta["source"],
        clay_pct=round(s["clay_pct"], 1),
        sand_pct=round(s["sand_pct"], 1),
        silt_pct=round(s["silt_pct"], 1),
        ph=round(s["ph"], 1) if s["ph"] is not None else None,
        organic_carbon_g_kg=round(s["organic_carbon_g_kg"], 1) if s["organic_carbon_g_kg"] is not None else None,
        texture_class=s["texture_class"],
        is_cached=meta["is_cached"],
        note=" ".join(n for n in (meta["note"], meta["resolution_note"].capitalize() + ".") if n),
    )
    return s, info, None


def _climate_info(raw: dict, summary: dict, projection: dict | None = None) -> ClimateInfo:
    meta = raw["meta"]
    if projection is None:
        try:
            projection = forecast_engine.project_next_year(raw)
        except ValueError:
            projection = None
    return ClimateInfo(
        forecast_next_year=projection["months"] if projection else None,
        forecast_method=projection["method"] if projection else None,
        forecast_year=projection["year"] if projection else None,
        forecast_backtest=projection["backtest"] if projection else None,
        forecast_trend_per_decade=projection["trend_per_decade"] if projection else None,
        source=meta["source"],
        period=summary["period"],
        fetched_at=meta["fetched_at"],
        is_cached=meta["is_cached"],
        monthly=[
            {
                "month": m["month"],
                "rain_mm": round(m["rain_mm"], 1),
                "temp_c": round(m["temp_c"], 1),
                "et0_mm": round(m["et0_mm"], 1),
            }
            for m in summary["monthly_climatology"]
        ],
    )


def _ndvi_seasons(summary: dict | None) -> list[dict]:
    if not summary:
        return []
    return [{**s, "source": "MODIS MOD13Q1 NDVI (ORNL DAAC), mean annual profile"} for s in summary["seasons"]]


def _cached_ndvi(lat: float, lon: float) -> tuple[dict | None, dict | None]:
    """(series dict, detect_seasons summary) from the cache only; (None, None) if not cached."""
    data = ndvi_client.load_cached(lat, lon)
    if not data or not data["series"]:
        return None, None
    return data, ndvi_client.detect_seasons(data)


def _ndvi_job(lat: float, lon: float) -> None:
    key = (round(lat, 3), round(lon, 3))
    try:
        ndvi_client.fetch_ndvi_series(lat, lon, timeout_s=NDVI_BACKGROUND_TIMEOUT_S)
        state = {"state": "done"}
    except Exception as e:  # noqa: BLE001 - report any failure to the polling client
        state = {"state": "failed", "error": str(e)}
    with _ndvi_lock:
        _ndvi_jobs[key] = state


@app.get("/api/location", response_model=LocationInfo)
def location(
    lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)
) -> LocationInfo:
    _, soil, soil_message = _fetch_soil(lat, lon)
    pack = regional.find_pack(lat, lon)
    _, summary = _cached_ndvi(lat, lon)
    return LocationInfo(lat=lat, lon=lon, label=pack["name"] if pack else None,
                        soil=soil, soil_message=soil_message, seasons_detected=_ndvi_seasons(summary),
                        ndvi_status="loaded" if summary else "not_loaded")


@app.get("/api/ndvi", response_model=NdviResponse)
def ndvi(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)) -> NdviResponse:
    """Cached NDVI, or start a background fetch and return status "loading"; poll until "loaded"."""
    data, summary = _cached_ndvi(lat, lon)
    if data:
        return NdviResponse(status="loaded", lat=lat, lon=lon, series=data["series"],
                            summary={**summary, "seasons": _ndvi_seasons(summary)},
                            message=data["meta"]["quality_filtering"], meta=data["meta"])
    key = (round(lat, 3), round(lon, 3))
    with _ndvi_lock:
        job = _ndvi_jobs.get(key)
        if job and job["state"] == "failed":
            del _ndvi_jobs[key]  # the next poll starts a new attempt
            return NdviResponse(status="unavailable", lat=lat, lon=lon, series=[], summary=None, meta=None,
                                message=f"MODIS NDVI could not be loaded: {job['error']}")
        if not job or job["state"] == "done":
            _ndvi_jobs[key] = {"state": "loading"}
            threading.Thread(target=_ndvi_job, args=(lat, lon), daemon=True).start()
    return NdviResponse(status="loading", lat=lat, lon=lon, series=[], summary=None, meta=None,
                        message="Fetching MODIS NDVI from ORNL DAAC; this can take several minutes. Poll again.")


def _field_history(lat: float, lon: float) -> FieldHistory:
    """CDL field history for a contiguous-US point; status "not_available" elsewhere."""
    empty = dict(lat=lat, lon=lon, yearly=[], rotation=None, cycle=None, n_years=None, confidence="none",
                 matched_pack_rotation=None, source=cdl_client.SOURCE)
    if not cdl_client.in_conus(lat, lon):
        return FieldHistory(status="not_available", **{**empty, "source": None},
                            confidence_note="Field history from the USDA Cropland Data Layer is only "
                                            "available for the contiguous United States.")
    try:
        hist = cdl_client.fetch_history(lat, lon)
    except cdl_client.CdlUnavailableError as e:
        return FieldHistory(status="unavailable", **empty, confidence_note=f"CropScape could not be reached: {e}")
    inf = cdl_client.infer_rotation(hist, cdl_client.load_map(), regional.find_pack(lat, lon))
    return FieldHistory(
        status=inf["status"], lat=lat, lon=lon,
        yearly=[{k: y[k] for k in ("year", "cdl_value", "cdl_category", "legend_name", "kind", "crops")}
                for y in inf["yearly"]],
        rotation=inf["rotation"], cycle=inf["cycle"], n_years=inf["n_years"], confidence=inf["confidence"],
        confidence_note=inf["confidence_note"], matched_pack_rotation=inf["matched_pack_rotation"],
        source=inf["source"], resolution_note=inf["resolution_note"], is_cached=hist["meta"]["is_cached"],
    )


@app.get("/api/field-history", response_model=FieldHistory)
def field_history(
    lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)
) -> FieldHistory:
    return _field_history(lat, lon)


@app.get("/api/climate", response_model=ClimateInfo)
def climate(
    lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)
) -> ClimateInfo:
    raw = _fetch_power(lat, lon)
    return _climate_info(raw, climate_engine.summarize(raw))


@app.post("/api/compare", response_model=CompareResponse)
def compare(req: CompareRequest) -> CompareResponse:
    raw = _fetch_power(req.lat, req.lon)
    soil, soil_info, soil_message = _fetch_soil(req.lat, req.lon)
    pack = regional.find_pack(req.lat, req.lon)
    request = req.model_dump()
    history = None
    if not req.current_rotation:
        history = _field_history(req.lat, req.lon)
        if history.status != "detected":
            raise HTTPException(
                status_code=400,
                detail="current_rotation is required here: it could not be detected from field history "
                       f"({history.status}: {history.confidence_note})",
            )
        request["current_cycle"] = history.cycle
        request["current_rotation"] = history.rotation
    try:
        result = pipeline.run_compare(
            raw, soil, request, _load_json(DATA_DIR / "crops.json"), water_balance.load_kc(), pack
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    warnings = list(result["warnings"])
    if history:
        warnings.insert(0, "Your current rotation was detected from the USDA Cropland Data Layer, not entered "
                           f"by you: {history.confidence_note} Correct it if it is wrong.")
    if soil_message:
        warnings.append(soil_message)
    # NDVI is supporting evidence only: read from the cache, never fetched here, never scored.
    ndvi_data, ndvi_summary = _cached_ndvi(req.lat, req.lon)
    ndvi_check = None
    if ndvi_summary:
        ndvi_check = ndvi_client.compare_with_calendar(ndvi_summary, result["rotations"][0])
        if ndvi_check["warning"]:
            warnings.append(ndvi_check["warning"])
    return CompareResponse(
        location=LocationInfo(
            lat=req.lat, lon=req.lon, label=pack["name"] if pack else None,
            soil=soil_info, soil_message=soil_message, seasons_detected=_ndvi_seasons(ndvi_summary),
            ndvi_status="loaded" if ndvi_summary else "not_loaded",
        ),
        climate=_climate_info(raw, result["climate_now"], result["projection"]),
        ndvi=ndvi_data["series"] if ndvi_data else [],
        ndvi_status="loaded" if ndvi_summary else "not_loaded",
        ndvi_check=ndvi_check,
        mode=result["mode"],
        pack=result["pack"],
        priorities_used=result["priorities_used"],
        scenario=req.scenario,
        rotations=result["rotations"],
        all_ranked=result["all_ranked"],
        current_is_strong=result["current_is_strong"],
        sensitivity=result["sensitivity"],
        warnings=warnings,
        assumptions=result["assumptions"],
        current_rotation_source="cdl_detected" if history else "farmer",
        field_history=history,
        run_info={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "Engine output. Climate from NASA POWER "
                    f"{'(cached)' if raw['meta']['is_cached'] else '(fresh)'}; Kc values and regional "
                    "calendars are provisional.",
        },
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Answer a farmer's question using only this field's /api/compare result (grounded Q&A).

    Runs the normal comparison (NDVI from the cache only), builds a deterministic context from it
    and asks the configured LLM. No key, a provider error or a timeout gives available=false (200).
    """
    result = compare(req.compare_request)  # a 400 for a bad compare request passes through
    pack = regional.find_pack(req.compare_request.lat, req.compare_request.lon)
    ctx = chat_context.build_context(
        result.model_dump(mode="json"), _load_json(DATA_DIR / "crops.json"),
        _load_json(water_balance.KC_PATH), pack, _load_json(DATA_DIR / "sources.json"),
        language=req.language, question=req.question,
    )
    history = [h.model_dump() for h in req.history] if req.history else None
    out = chat_llm.ask(req.question, ctx, req.language, history)
    return ChatResponse(**out, context_tokens=ctx["approx_tokens"])
