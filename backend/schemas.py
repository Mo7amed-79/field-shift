# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Request/response models: the API contract with the frontend (Pydantic v2)."""

from typing import Literal

from pydantic import BaseModel, Field


class Priorities(BaseModel):
    """Relative weights, each 0..1; the engine normalises them to sum to 1 (all zero = equal)."""

    water: float = Field(ge=0, le=1)
    soil: float = Field(ge=0, le=1)
    heat_risk: float = Field(ge=0, le=1)  # was "profit", then "resilience"


class Scenario(BaseModel):
    temp_delta_c: float = Field(0, ge=-5, le=5)
    water_cut_pct: float = Field(0, ge=0, le=100)


class Area(BaseModel):
    value: float = Field(gt=0)
    unit: Literal["feddan", "hectare", "acre"]


class CompareRequest(BaseModel):
    lat: float
    lon: float
    current_rotation: list[str]  # crop ids, e.g. ["rice", "wheat"]
    # ECOCROP rainfall limits only apply to rainfed farming; in irrigated
    # regions such as the Nile Delta rain is not the limiting factor.
    water_access: Literal["rainfed", "limited_irrigation", "full_irrigation"]
    # None = use the SoilGrids value; a farmer-entered value overrides it.
    soil_texture: Literal["clay", "loam", "sand"] | None = None
    priorities: Priorities
    scenario: Scenario = Field(default_factory=Scenario)
    field_name: str | None = None
    area: Area | None = None


class SoilInfo(BaseModel):
    source: str
    clay_pct: float
    sand_pct: float
    silt_pct: float
    ph: float | None
    organic_carbon_g_kg: float | None
    texture_class: str
    is_cached: bool
    note: str | None = None  # e.g. neighbouring cell used, resolution caveat


class SeasonInfo(BaseModel):
    start_month: int = Field(ge=1, le=12)
    peak_month: int = Field(ge=1, le=12)
    end_month: int = Field(ge=1, le=12)
    peak_ndvi: float | None = None
    source: str


NdviStatus = Literal["loaded", "not_loaded", "loading", "unavailable"]


class LocationInfo(BaseModel):
    lat: float
    lon: float
    label: str | None = None
    # None when SoilGrids is unavailable; the farmer can enter soil texture manually.
    soil: SoilInfo | None
    soil_message: str | None = None
    seasons_detected: list[SeasonInfo]  # from cached MODIS NDVI; [] until loaded via /api/ndvi
    ndvi_status: NdviStatus | None = None


class MonthlyClimate(BaseModel):
    month: int = Field(ge=1, le=12)
    rain_mm: float
    temp_c: float
    et0_mm: float


class ForecastMonth(BaseModel):
    month: int = Field(ge=1, le=12)
    temp_c: float
    et0_mm: float


class ClimateInfo(BaseModel):
    source: str
    period: str
    fetched_at: str
    is_cached: bool
    monthly: list[MonthlyClimate]
    forecast_next_year: list[ForecastMonth] | None = None
    forecast_method: str | None = None


class NdviPoint(BaseModel):
    date: str  # ISO date, YYYY-MM-DD
    ndvi: float


class Scores(BaseModel):
    water: float | None = Field(None, ge=0, le=100)  # None when a crop has no Kc values
    soil_practices: float = Field(ge=0, le=100)  # "soil-health practices (proxy)"
    heat_risk: float = Field(ge=0, le=100)  # "heat-stress exposure (proxy)"; higher = less exposure


class CropWindow(BaseModel):
    crop: str
    label: str
    start_month: int = Field(ge=1, le=12)
    end_month: int = Field(ge=1, le=12)
    months: list[int]
    window_source: str


class PhAdvisory(BaseModel):
    status: Literal["ok", "outside_optimal", "outside_absolute"]
    soil_ph: float
    ecocrop_optimal: list[float]
    ecocrop_absolute: list[float]
    note: str


class CropSuitability(BaseModel):
    crop: str
    score: float = Field(ge=0, le=100)
    limiting_factor: str
    window_source: str | None = None
    ph_advisory: PhAdvisory | None = None


class YearPlanMonth(BaseModel):
    month: int = Field(ge=1, le=12)
    crop: str | None
    activity: Literal["plant", "grow", "harvest", "fallow"]
    kc: float | None = None  # month-average crop coefficient (FAO-56 curve); None when fallow
    etc_mm: float | None  # crop water use (ET0 x Kc); None if not assessed
    rain_mm: float
    irrigation_mm: float | None
    irrigation_m3: float | None = None  # only when an area is given
    note: str | None = None


class RevenueCrop(BaseModel):
    crop: str
    label: str
    year: int = 1
    yield_t_per_ha: float | None
    price_per_t: float | None
    revenue_per_ha: float | None
    revenue_per_feddan: float | None
    revenue_for_area: float | None = None
    yield_basis: str | None = None
    price_basis: str | None = None
    note: str | None = None
    missing: list[str]


class Revenue(BaseModel):
    """Indicative gross revenue (yield x price, no costs). Not a score unless scored is true."""

    available: bool  # False when the region has no market table
    complete: bool  # True only if every crop has a yield and a price
    scored: bool  # True only where revenue is a priority (pack market table complete)
    currency: str | None
    price_snapshot: str | None
    status: str | None = None
    per_crop: list[RevenueCrop]
    missing: list[dict]  # [{crop, label, missing: ["yield" | "price"]}]
    n_years: int | None = None
    total_per_ha: float | None  # whole rotation cycle
    total_per_feddan: float | None
    total_for_area: float | None = None
    annual_per_ha: float | None = None  # cycle total / n_years
    annual_per_feddan: float | None = None
    annual_for_area: float | None = None
    area: Area | None = None
    revenue_change_pct: float | None = None  # annual, versus the current rotation; both must be complete
    notes: list[str]


class RotationResult(BaseModel):
    id: str
    name: str
    crops: list[str]
    windows: list[CropWindow]
    is_current: bool
    source: Literal["farmer", "regional pack", "exploratory"]
    scores: Scores
    score_details: dict
    total: float
    rank: int
    water_mm_per_year: float | None
    water_m3_per_year: float | None = None
    water_saving_mm: float | None = None  # versus the current rotation; negative = needs more
    water_saving_pct: float | None = None
    water_saving_m3: float | None = None  # only when an area is given
    exceeds_water_budget: bool | None = None  # only when scenario.water_cut_pct > 0
    water_budget_mm: float | None = None
    why: str
    not_assessed: str
    confidence: Literal["low", "medium", "high"]
    caveats: list[str]
    suitability: list[CropSuitability]
    year_plan: list[YearPlanMonth]
    revenue: Revenue | None = None


class SensitivityCase(BaseModel):
    name: str
    weights: dict[str, float]
    ranking: list[str]  # rotation ids, best first


class Sensitivity(BaseModel):
    cases: list[SensitivityCase]
    winner_is_robust: bool
    note: str


class RankedOption(BaseModel):
    id: str  # pack rotation id
    name: str
    is_current: bool
    water_mm_per_year: float | None
    water_saving_pct: float | None
    scores: Scores
    total: float
    rank: int
    revenue: Revenue | None = None


class NdviCropCheck(BaseModel):
    crop: str
    label: str
    window: str
    contains_peak: bool
    peak_months: list[int]


class NdviCheck(BaseModel):
    """Supporting evidence only: never changes scores."""

    status: Literal["consistent", "partly_consistent", "inconsistent", "not_cropped"]
    per_crop: list[NdviCropCheck]
    text: str
    warning: str | None
    note: str


class NdviSummary(BaseModel):
    seasons: list[SeasonInfo]
    seasons_per_year: int
    monthly_profile: list[float | None]  # mean NDVI by calendar month, January first
    bare_or_low_cover_months: list[int]
    looks_cropped: bool
    thresholds: dict


class NdviResponse(BaseModel):
    status: NdviStatus
    lat: float
    lon: float
    series: list[NdviPoint]
    summary: NdviSummary | None
    message: str | None
    meta: dict | None


class PackInfo(BaseModel):
    id: str
    name: str


class RunInfo(BaseModel):
    generated_at: str
    note: str


class CompareResponse(BaseModel):
    location: LocationInfo
    climate: ClimateInfo
    ndvi: list[NdviPoint]  # cached MODIS NDVI series; [] when ndvi_status != "loaded"
    ndvi_status: NdviStatus
    ndvi_check: NdviCheck | None  # current rotation's windows vs greenness peaks; evidence only
    mode: Literal["regional_pack", "exploratory"]
    pack: PackInfo | None
    priorities_used: dict[str, float]
    scenario: Scenario
    rotations: list[RotationResult]  # current rotation first
    all_ranked: list[RankedOption]  # every pack rotation, best first; [] in exploratory mode
    current_is_strong: bool  # current rotation is in the top two of all pack rotations
    sensitivity: Sensitivity
    warnings: list[str]
    assumptions: list[str]
    run_info: RunInfo
