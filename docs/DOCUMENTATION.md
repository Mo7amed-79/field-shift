# Field Shift: technical documentation

NASA Space Apps 2026. Every number in this document was produced by the code in this repository on 2026-11-15 from the cached data in `data/cache/`, unless it is quoted from a data file. Values the data files mark as provisional or unverified are marked the same way here.

## Contents

1. [Overview](#1-overview)
2. [How it works, end to end](#2-how-it-works-end-to-end)
3. [Data sources](#3-data-sources)
4. [Methods](#4-methods)
5. [Regional packs](#5-regional-packs)
6. [API reference](#6-api-reference)
7. [Farmer chat](#7-farmer-chat)
8. [Running it](#8-running-it)
9. [Validation](#9-validation)
10. [Known limitations and assumptions](#10-known-limitations-and-assumptions)
11. [Use of AI tools](#11-use-of-ai-tools)
12. [Roadmap](#12-roadmap)

## 1. Overview

Field Shift answers the NASA Space Apps 2026 challenge *Field Shift: Adapting Farms with NASA Data*. It is a decision-support tool for farmers, and for the extension officers who advise them, who want to know whether a different crop rotation would use less water or cope better with heat on their own field. The farmer gives a location, their current rotation, their water access and their priorities; Field Shift fetches NASA POWER climate for that point, computes FAO-56 crop water use, and compares the farmer's rotation with the rotations that are actually practised in that region. Each rotation gets three transparent 0–100 scores (water, soil-health practices, heat-stress exposure), a weighted total, a month-by-month plan and a plain-language explanation that states what was not assessed. It never invents rotations inside a known region, and outside one it says clearly that it is in a low-confidence exploratory mode.

The backend and engine are complete and tested; the React frontend is not yet built (`frontend/mock/compare.json` is real engine output for UI work).

## 2. How it works, end to end

```
 Farmer inputs: point (lat, lon), current rotation (or none, for US points), water access,
                priorities (water / soil / heat), optional field area and climate scenario
        │
        ▼
 NASA POWER monthly point data 1991–2025  ──────────────┐   (US only) USDA CDL field history
        │  engine/power_client.py                        │   engine/cdl_client.py → detected rotation
        ▼                                                │
 Climate summary + FAO-56 Penman-Monteith ET0            │   ISRIC SoilGrids topsoil (shown, not scored)
        │  engine/climate.py  (+ engine/forecast.py)     │   engine/soil_client.py
        ▼                                                │
 Regional pack: typical rotations and calendar  ◄────────┘   MODIS NDVI seasons (supporting evidence)
        │  engine/regional.py, data/regional/*.json            engine/ndvi_client.py
        ▼
 Water balance per crop and month (Kc curve, ETc, effective rain)
        │  engine/water_balance.py
        ▼
 Three scores: water · soil-health practices (proxy) · heat-stress exposure (proxy)
        │  engine/scoring.py   (+ indicative gross revenue, unscored: engine/market.py)
        ▼
 Ranking, weighted total, sensitivity block, water-budget flag
        │  engine/scoring.py, engine/pipeline.py
        ▼
 Month-by-month year plan  ── engine/planner.py
        ▼
 Plain-language explanation ── engine/explain.py   (optional grounded chat: backend/chat.py)
```

**Inputs and API** (`backend/main.py`, `backend/schemas.py`). FastAPI validates the request against the Pydantic contract and calls the engine. `engine/pipeline.py::run_compare` is the core and is a pure function of its inputs.

**NASA POWER** (`engine/power_client.py`). Monthly point data from `https://power.larc.nasa.gov/api/temporal/monthly/point` (community `AG`), parameters T2M, T2M_MAX, T2M_MIN, T2M_RANGE, RH2M, WS2M, ALLSKY_SFC_SW_DWN and PRECTOTCORR, 1991–2025. Coordinates are rounded to 0.01°. The raw response is cached untouched in `data/cache/`; if the network fails, the nearest cached point within 0.5° is used and a `fallback_note` is added.

**Climate and ET0** (`engine/climate.py`). Monthly records get FAO-56 Penman-Monteith ET0; the 12-month climatology, yearly totals, OLS trends (temperature and ET0 only), recent-versus-baseline differences, rain variability and data gaps are computed. `engine/forecast.py` adds a next-year projection with a back-test.

**Regional pack** (`engine/regional.py`). The pack whose bounding box contains the point supplies the candidate rotations and their calendars. With no pack, `engine/rotation_builder.py` and `engine/suitability.py` generate candidates from global ECOCROP envelopes (exploratory mode).

**Water balance** (`engine/water_balance.py`). For every crop and month: crop coefficient Kc from the four-stage FAO-56 curve, ETc = ET0 × Kc, effective rain, and irrigation need (irrigated) or water deficit (rainfed). Multi-year rotations are handled year by year.

**Scores, ranking, sensitivity** (`engine/scoring.py`). Water, soil-health practices and heat-stress exposure scores, the weighted total, a deterministic ranking, the sensitivity of the winner to the weights, and the water-budget flag. `engine/market.py` adds indicative gross revenue (yield × price) where a market table exists; it is never part of a score.

**Year plan** (`engine/planner.py`). Twelve entries per rotation year (12 × N for an N-year rotation): crop, activity (plant / grow / harvest / fallow), Kc, ETc, rain, irrigation (mm and m³ when an area is given) or deficit.

**Explanation** (`engine/explain.py`). Template text, no LLM: two or three sentences naming the actual numbers, the main trade-off against the current rotation, a revenue trade-off sentence when both revenues are complete, and a "Not assessed" sentence. The optional chat (`backend/chat.py`, section 7) answers follow-up questions from the same result.

## 3. Data sources

License and citation status are copied from `data/sources.json`. "NASA" is the inverse of that file's `non_nasa` flag.

### NASA data

| Name | Provider | What we take | Spatial / temporal resolution | Access | License / citation status | NASA |
|---|---|---|---|---|---|---|
| NASA POWER (Prediction Of Worldwide Energy Resources) | NASA Langley Research Center | Monthly T2M, T2M_MAX, T2M_MIN, T2M_RANGE, RH2M, WS2M, ALLSKY_SFC_SW_DWN, PRECTOTCORR at the clicked point → climatology and FAO-56 ET0 | Grid-cell averages: 1.0° × 1.0° for radiation, ½° × ⅝° for meteorology (POWER methodology docs, quoted in `power_client.py`); monthly, 1991–2025 | REST API `https://power.larc.nasa.gov/api/temporal/monthly/point`, cached | License: "TODO verify" | Yes |
| MODIS MOD13Q1 (Terra Vegetation Indices 16-Day L3 Global 250 m SIN Grid) via ORNL DAAC TESViS | NASA (Terra MODIS), served by ORNL DAAC | Band `250m_16_days_NDVI` at the clicked pixel, last three full years (2023–2025), to show greenness seasons next to the crop calendar; not used in any score | 250 m (single pixel, `kmAboveBelow=0`, `kmLeftRight=0`); 16-day composites | TESViS REST API `https://modis.ornl.gov/rst/api/v1/` (`/MOD13Q1/bands`, `/dates`, `/subset`; at most ten dates and one band per request), cached | License: "TODO verify". Tool citation: ORNL DAAC 2018, TESViS, https://doi.org/10.3334/ORNLDAAC/1600. Product citation: "TODO — add the MOD13Q1 product citation". Version: "TODO verify" | Yes |

### Other data

| Name | Provider | What we take | Spatial / temporal resolution | Access | License / citation status | NASA |
|---|---|---|---|---|---|---|
| USDA NASS Cropland Data Layer (CDL) via CropScape | USDA NASS | Yearly crop class at the clicked field, to detect the current rotation for US points | 30 m pixel; annual, last six available years (2020–2025 on 2026-11-15) | `GetCDLValue?year=&x=&y=` at `https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLValue`, x/y in EPSG:5070 (pyproj); legend from the official `cdl_codes_names.xlsx`; mapping to crop ids in `data/cdl_crop_map.json` is unverified | License: "TODO verify". Citation: "TODO — add the NASS CDL citation" | No |
| ISRIC SoilGrids v2.0 | ISRIC — World Soil Information | Topsoil clay, sand, silt %, pH (H₂O) and organic carbon, depth-weighted over 0–5, 5–15 and 15–30 cm; USDA texture class | 250 m modelled estimate ("not a field soil test") | REST `https://rest.isric.org/soilgrids/v2.0/properties/query`, cached; neighbouring cells tried when the centre cell is empty | License: "TODO verify" | No |
| FAO Irrigation and Drainage Paper 56 (Crop Evapotranspiration) | FAO (Allen et al. 1998) | Penman-Monteith ET0 (Ch. 2–4), Kc ini/mid/end (Table 12, in `data/kc_fao56.json`, provisional), ETc = Kc × ET0 | Method and coefficients | `https://www.fao.org/4/x0490e/x0490e0b.htm` | License: "TODO verify" | No |
| FAO ECOCROP | FAO, via the OpenCLIM/ecocrop repository (commit f13577f4, 2026-01-30) | Temperature, rainfall and pH envelopes, killing temperature, cycle length and botanical family for `data/crops.json` | Crop-level parameters | `https://github.com/OpenCLIM/ecocrop/blob/main/EcoCrop_DB.csv` (original: `https://gaez.fao.org/pages/ecocrop`) | License: "UNVERIFIED — team must confirm before submission". Citation: Brown, M. J., Redhead, J., Pywell, R., Price, J. EcoCrop Climatic Crop Suitability Scoring. doi:10.5281/zenodo.10843625 | No |
| USDA FAS GAIN EG2024-0011, Sugar Annual (Cairo, Egypt) | USDA Foreign Agricultural Service | February 2024 government procurement / guaranteed prices (EGP/t) for wheat, maize, rice, soybean, sunflower, cotton, sugar beet; implied sunflower and cotton yields | National; February 2024 snapshot | Report PDF (URL in `sources.json`) | License: "TODO verify". Status: "provisional — secondary source, not FAOSTAT" | No |
| USDA FAS GAIN EG2024-0023, Grain and Feed Update (Cairo, 25 Sep 2024) | USDA FAS | MY2024/25 area and production → national average yields for wheat, maize, rice | National; MY2024/25 | Report PDF | License: "TODO verify". Status: "provisional — secondary source, not FAOSTAT" | No |
| USDA FAS GAIN EG2024-0008, Oilseeds and Products Annual (Cairo, 4 Apr 2024) | USDA FAS | Soybean national average yield | National | Third-party mirror; "the official GAIN download URL is TODO" | License: "TODO verify". Status: "provisional — secondary source, not FAOSTAT" | No |

`data/sources.json` also lists FAO GAEZ, with "what we use: TODO: reference for cross-checking crop suitability results"; it is not used by any code yet.

## 4. Methods

### 4.1 Reference evapotranspiration (FAO-56 Penman-Monteith)

`climate.et0_penman_monteith` implements FAO-56 Eq. 6 with the supporting equations cited in the code (Eq. 7, 8, 9, 11, 12, 13, 19, 21, 23, 24, 25, 37, 38, 39, 40):

```
ET0 = [0.408 Δ (Rn − G) + γ (900 / (T + 273)) u2 (es − ea)] / [Δ + γ (1 + 0.34 u2)]     (mm/day)
```

Constants: solar constant 0.0820 MJ m⁻² min⁻¹, Stefan-Boltzmann 4.903 × 10⁻⁹ MJ K⁻⁴ m⁻² day⁻¹, albedo 0.23. Atmospheric pressure from the POWER grid-cell elevation (Eq. 7); clear-sky radiation Rso = (0.75 + 2 × 10⁻⁵ z) Ra (Eq. 37).

How the monthly inputs are derived from POWER (`climate._monthly_records`):

- **Tmax, Tmin** = T2M ± T2M_RANGE / 2, because POWER's monthly T2M_MAX / T2M_MIN are monthly extremes, not means of daily values.
- **ea** = RH2M / 100 × es (Eq. 19); **u2** = WS2M (already at 2 m); **Rs** = ALLSKY_SFC_SW_DWN converted to MJ m⁻² day⁻¹ using the unit string in the response (units are checked; an unexpected unit raises an error).
- **Day of year** = INT(30.4 M − 15), the FAO-56 mid-month day.
- **Soil heat flux G** = 0.07 (T(i+1) − T(i−1)) (Eq. 43); 0.14 (T(i) − T(i−1)) at the end of the record (Eq. 44); else 0.
- **Monthly ET0** = days in month × daily ET0. **Monthly rain** = PRECTOTCORR × days when the unit is mm/day.

The climatology is the mean of each calendar month over 1991–2025. Trends are OLS slopes per decade with p-values for temperature and ET0 only; rain is deliberately not trended (see 10.4). `rain_record_warning` is raised when the 2016–2025 mean annual rain is more than double the 1991–2005 mean.

### 4.2 Crop coefficient curve and stage fractions

`water_balance.kc_at` follows the FAO-56 single-coefficient curve over the growing window: initial stage at `kc_ini`, development linear from `kc_ini` to `kc_mid`, mid-season at `kc_mid`, late season linear from `kc_mid` to `kc_end`. Stage lengths are fractions of the window. The default is **0.15 / 0.25 / 0.40 / 0.20** (initial / development / mid / late), status "provisional — replace with FAO-56 Table 11 stage lengths per crop". The monthly Kc is the mean of the curve over the month (60 samples per month).

One per-crop override exists: Kansas winter wheat (`data/regional/us_kansas_plains.json`) uses **0.48 / 0.22 / 0.22 / 0.08**, status "provisional — from memory of FAO-56 Table 11 winter wheat, October planting; verify", so peak water use falls in April–June rather than mid-winter. It applies in that pack only (`water_balance.apply_pack_overrides`).

Kc values (`data/kc_fao56.json`): "Values supplied by the team from memory of FAO-56 Table 12; not checked against the table." Every crop has status "provisional — verify against FAO-56 Table 12", except oat (0.30 / 1.15 / 0.25), which is "provisional — unverified", source "TODO".

### 4.3 Effective rain, irrigation need and water deficit

Per month: `ETc = ET0 × Kc`, **effective rain = 0.8 × monthly rain** (a fixed fraction, not a soil water balance), and

```
irrigation need (or water deficit) = max(ETc − effective rain, 0)
```

A month shared by two crops (harvest of one, sowing of the next) counts at half weight for each. An N-year rotation is computed year by year; a year without crops (fallow) needs 0 mm, and the reported figures are annual means over the cycle.

- **Irrigated** (`full_irrigation`, `limited_irrigation`): the annual sum is the irrigation need in mm/year, converted to m³ for the field (1 mm = 4.2 m³/feddan, 10 m³/hectare, 4.047 m³/acre). For rice, the regional pack's `paddy_extra_water_mm` would be added; it is `null` in every pack, so rice need carries the caveat that it is a lower bound.
- **Rainfed**: the same quantity is a water deficit (`water_deficit_mm`), an indicator of drought-stress risk, not an irrigation need. For each crop season, **unmet fraction = water deficit / ETc**; a rotation's unmet fraction is the mean over its crop seasons (fallow years are not crop seasons).

### 4.4 Water score

- Irrigated: `score = clip(50 + saving %, 0, 100)`, where saving % = 100 × (current need − this need) / current need. The current rotation scores 50; a 25% saving scores 75.
- Rainfed: `score = clip(50 + 100 × (current unmet fraction − this rotation's unmet fraction), 0, 100)`. The deficit in mm is reported for information only.

### 4.5 Soil-health practices (proxy)

Three independent parts (`scoring.soil_practices`), labelled "soil-health practices (proxy)" because it counts practices, not a measured soil outcome:

```
score = 40 × [rotation includes a legume]
      + 30 × (distinct plant families − 1) / (crops − 1)
      + 30 × months with a crop / (12 × years in the cycle)
```

Legume and family diversity are assessed over every crop in the cycle; families come from ECOCROP.

### 4.6 Heat-stress exposure (proxy)

Per crop (`scoring.heat_exposure`): the mean, over the growing months excluding the harvest month, of `max(0, monthly mean temperature − ECOCROP optimal maximum)`; `score = clip(100 − 15 × mean excess °C, 0, 100)`. The rotation score is the mean over crops weighted by window length. It is computed under the scenario climate, so warming can only lower it. It does not model heat at flowering or daily maximum temperatures.

### 4.7 Weighted total, ranking and sensitivity

Priorities `{water, soil, heat_risk}` are clipped at 0 and normalised to sum to 1 (all zero → equal weights). The total is the weighted mean over the criteria that have a score (weights re-normalised if a score is missing). Ranking is by total (descending), then water need or unmet fraction (ascending), then id, so it is deterministic. The response shows the current rotation plus up to two alternatives with different crop sets (`N_ALTERNATIVES = 2`); `all_ranked` lists every pack rotation. `current_is_strong` is true when the current rotation ranks 1st or 2nd among all local options.

The sensitivity block re-ranks under five weightings: the farmer's, equal, water only, soil only, heat only. `winner_is_robust` is true when the same rotation wins all five. At Tanta (equal priorities) maize → faba bean wins four of the five; maize → berseem wins with soil only.

### 4.8 Water-budget flag

With `scenario.water_cut_pct > 0` (irrigated only): `budget = (100 − cut) / 100 × current need`, and each rotation gets `exceeds_water_budget`. It is a flag, not a score. At Tanta with a 20% cut the budget is 1,497.9 mm/year: maize → faba bean (1,422.2 mm) fits; maize → berseem (1,504.3 mm) and rice → wheat exceed it.

### 4.9 Indicative gross revenue (not scored)

`engine/market.py`: per crop, revenue per hectare = national average yield (t/ha) × price (per t), converted to per feddan (0.42 ha/feddan from the market table) and to the field area. The rotation total per cycle is divided by the cycle length for an annual figure. `revenue_change_pct` is computed on the annual figure only when both this rotation and the current one have a yield and a price for every crop. Fallow years add zero. Revenue is never part of a score or the total.

Only Egypt has a market table (`data/market_egypt.json`, status "provisional — secondary source (USDA FAS Cairo), NOT FAOSTAT; FAOSTAT could not be reached when this file was built"). Its caveats, quoted: prices are "a single snapshot from February 2024, before the March 2024 devaluation of the Egyptian pound"; they are "government procurement or guaranteed minimum prices, not prices observed on a specific farm"; "Yields are national averages"; "It is gross revenue, not profit"; "No price is available for berseem … or faba bean". Rice uses milled yield × milled price, which the file notes "overstates farm revenue … Treat as an upper bound."

### 4.10 NDVI season detection

`engine/ndvi_client.py`, MOD13Q1 NDVI at one 250 m pixel for 2023–2025 (69 dates), no quality filtering.

1. Mean NDVI per calendar month over all years; missing months filled by circular linear interpolation; circular 1-2-1 smoothing.
2. `looks_cropped` if the smoothed range ≥ **0.12** (`FLAT_RANGE`) and the maximum ≥ **0.25** (`LOW_COVER_NDVI`).
3. Peaks: `scipy.signal.find_peaks` on the profile tiled three times, prominence ≥ **0.08** (`MIN_PROMINENCE`). A season runs from the peak outwards while the smoothed NDVI stays at or above the midpoint between the peak and the lowest value within six months on that side.
4. Months below 0.25 are reported as bare or low cover.

The code labels these thresholds "Heuristic thresholds, not calibrated for this region." `compare_with_calendar` checks whether each crop window contains a peak and whether the number of peaks equals the number of crops per year: `consistent`, `partly_consistent`, `inconsistent` or `not_cropped`. NDVI is supporting evidence only: it never changes a score, and the text always says it does not identify crops.

### 4.11 Next-year projection (forecast) and back-test

`engine/forecast.py`, for temperature and ET0 separately, ordinary least squares:

```
value(year, month) = s[month] + b × (year + (month − 0.5)/12 − t0)
```

that is, twelve monthly means plus one linear trend, fitted on the whole 1991–2025 record and extended to 2026. ET0 is floored at 0. Rain is not projected (the climatology is used). The method text says it is a "Simple statistical projection, not a weather forecast". With `scenario.use_forecast`, water and heat use the projected temperature (Tmax/Tmin shifted by the same amount) and ET0 with climatological rain, and `temp_delta_c` is ignored with a warning.

Back-test: fit on 1991–2020, test on 2021–2025 (60 months), mean absolute error against the 1991–2020 monthly climatology:

| Point | Temperature MAE, model / climatology (°C) | ET0 MAE, model / climatology (mm/month) | Trend per decade (temperature, ET0) |
|---|---|---|---|
| Tanta (30.85, 31.05) | 0.863 / 1.128 | 6.424 / 7.047 | +0.362 °C, +1.923 mm |
| Ludhiana (30.8, 75.6) | 1.272 / 1.478 | 13.594 / 19.715 | −0.351 °C, −7.715 mm |
| Ames (42.0, −93.75) | 1.622 / 1.863 | 10.920 / 12.269 | +0.460 °C, +2.912 mm |
| Hays (38.75, −99.15) | 1.446 / 1.614 | 11.844 / 12.979 | +0.357 °C, +2.852 mm |

The model beats climatology at all four points, by 9–31%. If it did not, `forecast_method` would say "does NOT beat" (tested).

### 4.12 CDL rotation detection (US)

`engine/cdl_client.py`:

1. Reproject the point to EPSG:5070 with pyproj. Ask for next year's CDL; the service's fault message ("The year must be between 1997 and 2025") gives the latest year. Read the last six years (2020–2025), one 30 m pixel each. The response is XML wrapping `{x, y, value, category, color}` and is parsed with a regular expression.
2. Classify each year with `data/cdl_crop_map.json` (40 mapped codes, 24 non-crop codes; "Fallow/Idle Cropland" → fallow).
3. Fewer than three readable years → `not_detected`; non-crop in more than half the years → `not_cropland`.
4. Period: for periods 1–3 years, count the year pairs that break the repetition; take the period with the fewest breaks (shortest on ties). The cycle is the most recent full period, rotated to match a pack rotation if one exists.
5. Confidence: **high** = no breaks, at least two cycles seen and no unmodelled crop; **medium** = at most one break and at least two cycles; otherwise **low**.

Results: Ames 2020–2025 Soybeans, Corn, Soybeans, Corn, Soybeans, Corn → maize → soybean, high. Hays 2020–2025 Sorghum, Fallow/Idle Cropland, Winter Wheat, Sorghum, Fallow/Idle Cropland, Winter Wheat → wheat → sorghum → fallow, high. When `current_rotation` is omitted for a US point, `/api/compare` uses the detected rotation and puts a warning first.

### 4.13 Exploratory mode (no regional pack)

`engine/suitability.py` scores each ECOCROP crop in every 12-month window of its cycle length: each factor is 0 outside the absolute range, 100 inside the optimal range and linear in between; temperature is the mean of monthly scores (0 if a month before harvest scores 0 or the killing temperature is crossed); rainfall is applied for rainfed farming, averaged with 100 for limited irrigation and not applied for full irrigation; the window score is the minimum factor; pH is advisory only. `engine/rotation_builder.py` then builds 2–3-crop alternatives under the rules in `data/rotation_rules.json` (R1 hard: no overlapping windows, one shared transition month allowed; R2 hard: minimum suitability; soft R1b, R3 legume, R4 no same family back-to-back, R5 cereal then non-cereal, R6 fewer fallow months, R7 two seasons where the climate supports them). The response carries the warning: "Exploratory mode: there is no regional pack for this location … Treat them as ideas to discuss with a local agronomist, not recommendations." Confidence is always low.

## 5. Regional packs

A pack (`data/regional/<id>.json`) holds local knowledge layered on the global engine:

- `id`, `name`, `bbox` (lat/lon box used by `regional.find_pack`), `water_access_default`;
- `crop_calendar`: per crop, `start_month`, `end_month` (inclusive), `status`, `source`, and optionally `stage_fractions`;
- `typical_rotations`: each with `id`, `name`, `crops` (`crop`, `label`, `start_month`, `end_month`, optional `year` in the cycle) and optional `n_years` (a year without crops is a fallow year), `status`, `source`;
- `paddy_extra_water_mm` (and its source), optional `market_table`.

Every calendar entry and rotation in all four packs has status "provisional — unverified".

**Egypt — Nile Delta** (`egypt_nile_delta`; lat 30.0–31.6, lon 29.8–32.4; full irrigation; market table `market_egypt.json`). Calendar: wheat Nov–May, berseem Oct–May, faba bean, barley, lentil, chickpea, onion Nov–Apr, sugar beet Oct–May, rice May–Oct, maize, sorghum, soybean, groundnut May–Sep, cotton Mar–Oct, sesame and sunflower May–Aug. Calendar source: "TODO — researcher to confirm against FAO Crop Calendar / Egyptian Ministry of Agriculture". Rotations: rice → wheat, rice → berseem, rice → faba bean, maize → wheat, maize → berseem (berseem Oct–May), maize → faba bean, soybean → wheat, cotton → short berseem (berseem Nov–Feb).

**India — Punjab** (`india_punjab`; lat 29.5–32.5, lon 73.9–76.9; full irrigation; no market table). Calendar: rice and maize Jun–Oct, upland cotton May–Oct, wheat Nov–Apr, chickpea and rapeseed Nov–Mar. Rotations: rice → wheat, maize → wheat, upland cotton → wheat, maize → chickpea, maize → rapeseed.

**United States — Iowa (Corn Belt)** (`us_iowa_corn_belt`; lat 40.4–43.5, lon −96.6 to −90.1; rainfed). Calendar: maize and soybean May–Oct, oat Apr–Jul. Rotations: maize → soybean (2 years), continuous maize, maize → soybean → oat (3 years). The pack notes the entries were "supplied by the team from memory".

**United States — Kansas (Great Plains)** (`us_kansas_plains`; lat 37.0–40.0, lon −102.0 to −94.6; rainfed). Calendar: wheat Oct–Jun (with the winter-wheat stage fractions of 4.2), sorghum and soybean Jun–Oct. Rotations: wheat → fallow (2 years), wheat → sorghum → fallow (3 years), wheat + double-crop soybean (1 year), continuous wheat.

### Adding a new region

1. Create `data/regional/<country>_<region>.json` with the keys above. Copy an existing pack as a template. Use crop ids that exist in `data/crops.json` (and, for water, in `data/kc_fao56.json`).
2. Set `bbox` so it does not overlap another pack (`find_pack` returns the first match).
3. Fill `crop_calendar` for every crop in the rotations, with `status` and `source`. Use `"status": "verified"` only after a researcher has checked an entry; this is what raises a rotation's confidence from medium to high (together with verified Kc values).
4. List `typical_rotations` as practised locally; set `year` and `n_years` for multi-year cycles.
5. Set `water_access_default`, and `paddy_extra_water_mm` with its source if rice is grown (otherwise leave `null`; rice need is then flagged as a lower bound).
6. Optionally add `data/market_<country>.json` and link it with `market_table`.
7. Add any new crop's Kc to `data/kc_fao56.json` with status and source, and any new source to `data/sources.json`.
8. Add a demo point to `data/demo_points.json`, run `python scripts/precache_demo.py`, and add a test (see `engine/tests/test_us_packs.py` for a pattern).

## 6. API reference

Base URL `http://127.0.0.1:8000`; interactive docs at `/docs`. The contract is `backend/schemas.py`; contract changes are logged in [`API_CHANGES.md`](API_CHANGES.md).

The examples below were produced by calling the app through FastAPI's test client against the cached data on 2026-11-15, and trimmed by a script: lists cut to their first two or three items (with "… N more"), long strings cut at 240 characters, and only the most useful keys kept for `/api/compare`. Nothing was typed by hand.

Errors: a bad body or unknown crop id → 400; NASA POWER unreachable with no cached point within 0.5° → 503; `current_rotation` omitted where no rotation can be detected → 400. SoilGrids, NDVI, CDL and chat failures never cause an error status; they return `null`/`"unavailable"`/`available: false` with a message.

**`CompareRequest` fields.**
- `lat`, `lon`.
- `current_rotation`: a list of crop ids; `"fallow"` marks a year without a crop. It may be omitted in the contiguous US.
- `water_access`: one of `rainfed`, `limited_irrigation`, `full_irrigation`.
- `priorities`: `{water, soil, heat_risk}`.
- `scenario`: `{temp_delta_c, water_cut_pct, use_forecast}`.
- `area`: `{value, unit}`, where `unit` is one of `feddan`, `hectare`, `acre`.
- Optional: `soil_texture` (recorded, not used) and `field_name`.

### `GET /api/health`

Liveness check.

Request:

```bash
curl 'http://127.0.0.1:8000/api/health'
```

Response (HTTP 200, trimmed):

```json
{
  "status": "ok"
}
```

### `GET /api/crops`

The crop table (`data/crops.json`, 30 crops built from FAO ECOCROP). First entry shown.

Request:

```bash
curl 'http://127.0.0.1:8000/api/crops'
```

Response (HTTP 200, trimmed):

```json
[
  {
    "id": "rice",
    "name_en": "Rice",
    "name_ar": "أرز",
    "name_ar_reviewed": false,
    "scientific_name": "Oryza sativa",
    "ecocrop_name": "Oryza sativa",
    "family": "Poaceae",
    "family_ecocrop": "Gramineae",
    "is_legume": false,
    "category": "cereal",
    "ecocrop_category": "cereals & pseudocereals",
    "life_span": "annual",
    "temp_abs_min_c": 10.0,
    "temp_opt_min_c": 20.0,
    "temp_opt_max_c": 30.0,
    "temp_abs_max_c": 36.0,
    "rain_abs_min_mm": 1000.0,
    "rain_opt_min_mm": 1500.0,
    "rain_opt_max_mm": 2000.0,
    "rain_abs_max_mm": 4000.0,
    "ph_abs_min": 4.5,
    "ph_opt_min": 5.5,
    "ph_opt_max": 7.0,
    "ph_abs_max": 9.0,
    "cycle_days_min": 80.0,
    "cycle_days_max": 180.0,
    "temp_kill_c": null,
    "kc_ini": null,
    "kc_mid": null,
    "kc_end": null,
    "complete": true,
    "source": "FAO ECOCROP via OpenCLIM/ecocrop",
    "ecocrop_id": 1574
  },
  "… 29 more"
]
```

### `GET /api/sources`

`data/sources.json`: the AI disclosure and 10 data sources with URL, use, license status and access date.

Request:

```bash
curl 'http://127.0.0.1:8000/api/sources'
```

Response (HTTP 200, trimmed):

```json
{
  "ai_disclosure": "This project used AI (SI) tools: code was generated and refactored with Claude (Anthropic) via Claude Code, and Claude chat supported research and planning. All AI output was reviewed and tested by the Field Shift team. See AI_USAGE.md.",
  "sources": [
    {
      "name": "NASA POWER (Prediction Of Worldwide Energy Resources)",
      "url": "https://power.larc.nasa.gov/",
      "what_we_use": "Monthly point data for the selected location (T2M, T2M_MAX, T2M_MIN, T2M_RANGE, RH2M, WS2M, ALLSKY_SFC_SW_DWN, PRECTOTCORR): climatology and FAO-56 reference evapotranspiration (ET0).",
      "license": "TODO verify",
      "accessed": "2026-11-14",
      "non_nasa": false
    },
    "… 9 more"
  ]
}
```

### `GET /api/location`

Regional pack label, SoilGrids topsoil (0–30 cm) and cached NDVI seasons for a point.

Request:

```bash
curl 'http://127.0.0.1:8000/api/location?lat=30.85&lon=31.05'
```

Response (HTTP 200, trimmed):

```json
{
  "lat": 30.85,
  "lon": 31.05,
  "label": "Egypt — Nile Delta",
  "soil": {
    "source": "ISRIC SoilGrids v2.0",
    "clay_pct": 39.6,
    "sand_pct": 35.4,
    "silt_pct": 24.9,
    "ph": 7.2,
    "organic_carbon_g_kg": 28.5,
    "texture_class": "clay loam",
    "is_cached": true,
    "note": "250 m modelled estimate, not a field soil test."
  },
  "soil_message": null,
  "seasons_detected": [
    {
      "start_month": 12,
      "peak_month": 2,
      "end_month": 3,
      "peak_ndvi": 0.692,
      "source": "MODIS MOD13Q1 NDVI (ORNL DAAC), mean annual profile"
    },
    {
      "start_month": 7,
      "peak_month": 7,
      "end_month": 8,
      "peak_ndvi": 0.696,
      "source": "MODIS MOD13Q1 NDVI (ORNL DAAC), mean annual profile"
    }
  ],
  "ndvi_status": "loaded"
}
```

### `GET /api/climate`

NASA POWER monthly climatology 1991–2025 with ET0, plus the next-year projection and its back-test.

Request:

```bash
curl 'http://127.0.0.1:8000/api/climate?lat=30.85&lon=31.05'
```

Response (HTTP 200, trimmed):

```json
{
  "source": "NASA POWER",
  "period": "1991–2025",
  "fetched_at": "2026-11-14T16:41:13.621258+00:00",
  "is_cached": true,
  "monthly": [
    {
      "month": 1,
      "rain_mm": 14.8,
      "temp_c": 13.3,
      "et0_mm": 66.0
    },
    {
      "month": 2,
      "rain_mm": 11.8,
      "temp_c": 14.0,
      "et0_mm": 75.1
    },
    "… 10 more"
  ],
  "forecast_year": 2026,
  "forecast_next_year": [
    {
      "month": 1,
      "temp_c": 13.99,
      "et0_mm": 69.5
    },
    {
      "month": 2,
      "temp_c": 14.64,
      "et0_mm": 78.6
    },
    "… 10 more"
  ],
  "forecast_trend_per_decade": {
    "temp_c": 0.362,
    "et0_mm": 1.923
  },
  "forecast_backtest": {
    "available": true,
    "train_years": [
      1991,
      2020
    ],
    "test_years": [
      2021,
      2025
    ],
    "n_test_months": 60,
    "temp_c": {
      "mae_model": 0.863,
      "mae_climatology": 1.128,
      "skill_vs_climatology": 0.235,
      "beats_climatology": true
    },
    "et0_mm": {
      "mae_model": 6.424,
      "mae_climatology": 7.047,
      "skill_vs_climatology": 0.088,
      "beats_climatology": true
    }
  },
  "forecast_method": "Simple statistical projection, not a weather forecast: for each variable, a least-squares fit of one mean per calendar month plus a single linear trend over the NASA POWER monthly record, extended 12 months ahead. Rain is not projected (the…"
}
```

### `GET /api/ndvi`

Cached MODIS MOD13Q1 NDVI series and detected seasons; when not cached, returns `status: "loading"` and starts a background fetch (poll again).

Request:

```bash
curl 'http://127.0.0.1:8000/api/ndvi?lat=30.85&lon=31.05'
```

Response (HTTP 200, trimmed):

```json
{
  "status": "loaded",
  "lat": 30.85,
  "lon": 31.05,
  "series": [
    {
      "date": "2023-01-01",
      "ndvi": 0.7285
    },
    {
      "date": "2023-01-17",
      "ndvi": 0.7456
    },
    "… 67 more"
  ],
  "summary": {
    "seasons": [
      {
        "start_month": 12,
        "peak_month": 2,
        "end_month": 3,
        "peak_ndvi": 0.692,
        "source": "MODIS MOD13Q1 NDVI (ORNL DAAC), mean annual profile"
      },
      {
        "start_month": 7,
        "peak_month": 7,
        "end_month": 8,
        "peak_ndvi": 0.696,
        "source": "MODIS MOD13Q1 NDVI (ORNL DAAC), mean annual profile"
      }
    ],
    "seasons_per_year": 2,
    "looks_cropped": true,
    "thresholds": {
      "low_cover_ndvi": 0.25,
      "min_peak_prominence": 0.08,
      "flat_range": 0.12,
      "note": "Heuristic thresholds, not calibrated for this region."
    }
  },
  "message": "Quality filtering not applied: the API returns one band per request, so the pixel-reliability band would double the number of requests. Cloudy composites may lower some NDVI values."
}
```

### `GET /api/field-history`

Contiguous US only: the USDA CDL class at the pixel for the last six available years and the detected rotation.

Request:

```bash
curl 'http://127.0.0.1:8000/api/field-history?lat=42.0&lon=-93.75'
```

Response (HTTP 200, trimmed):

```json
{
  "status": "detected",
  "lat": 42.0,
  "lon": -93.75,
  "yearly": [
    {
      "year": 2020,
      "cdl_value": 5,
      "cdl_category": "Soybeans",
      "legend_name": "Soybeans",
      "kind": "crop",
      "crops": [
        "soybean"
      ]
    },
    {
      "year": 2021,
      "cdl_value": 1,
      "cdl_category": "Corn",
      "legend_name": "Corn",
      "kind": "crop",
      "crops": [
        "maize"
      ]
    },
    {
      "year": 2022,
      "cdl_value": 5,
      "cdl_category": "Soybeans",
      "legend_name": "Soybeans",
      "kind": "crop",
      "crops": [
        "soybean"
      ]
    },
    "… 3 more"
  ],
  "rotation": [
    "maize",
    "soybean"
  ],
  "cycle": [
    [
      "maize"
    ],
    [
      "soybean"
    ]
  ],
  "n_years": 2,
  "confidence": "high",
  "confidence_note": "Detected from the USDA Cropland Data Layer 2020–2025: a 2-year cycle (maize → soybean) seen 3.0 times with 0 year(s) that break the pattern. Confidence high. The CDL is a 30 m classification of satellite imagery; a single pixel can be miscl…",
  "matched_pack_rotation": "maize_soybean",
  "source": "USDA NASS Cropland Data Layer (CropScape GetCDLValue)",
  "resolution_note": "The CDL is a 30 m classification of satellite imagery; a single pixel can be misclassified, especially at field edges, and accuracy varies by crop and state.",
  "is_cached": true
}
```

### `POST /api/compare`

The core comparison. Tanta, rice → wheat, full irrigation, equal priorities, 2 feddan. Shown: the top alternative (rank 1) and the trimmed top-level blocks.

Request:

```bash
curl -X POST http://127.0.0.1:8000/api/compare -H 'Content-Type: application/json' -d @request.json
```
`request.json`:
```json
{
  "lat": 30.85,
  "lon": 31.05,
  "current_rotation": [
    "rice",
    "wheat"
  ],
  "water_access": "full_irrigation",
  "priorities": {
    "water": 1,
    "soil": 1,
    "heat_risk": 1
  },
  "scenario": {
    "temp_delta_c": 0,
    "water_cut_pct": 0
  },
  "area": {
    "value": 2,
    "unit": "feddan"
  }
}
```

Response (HTTP 200, trimmed):

```json
{
  "mode": "regional_pack",
  "pack": {
    "id": "egypt_nile_delta",
    "name": "Egypt — Nile Delta"
  },
  "priorities_used": {
    "water": 0.333,
    "soil": 0.333,
    "heat_risk": 0.333
  },
  "rotations": [
    {
      "id": "maize_faba_bean",
      "name": "Maize → Faba bean",
      "crops": [
        "maize",
        "faba_bean"
      ],
      "is_current": false,
      "scores": {
        "water": 74.0,
        "soil_practices": 97.5,
        "heat_risk": 100.0
      },
      "total": 90.5,
      "rank": 1,
      "water_mm_per_year": 1422.2,
      "water_m3_per_year": 11947.0,
      "water_saving_mm": 450.2,
      "water_saving_pct": 24.0,
      "water_saving_m3": 3781.0,
      "n_years": 1,
      "water_criterion_label": "irrigation water saving",
      "confidence": "medium",
      "why": "Ranks 1st of 3 (total 90/100). It needs about 1422 mm of irrigation per year (about 11,947 m3 for your field), 450 mm (24%) less than your current rotation. Soil-health practices (proxy) score 98: it includes a legume, leaves 1 month withou…",
      "not_assessed": "Not assessed: production costs and current market prices, pests and diseases and a field-level soil test.",
      "windows": [
        {
          "crop": "maize",
          "label": "Maize",
          "start_month": 5,
          "end_month": 9,
          "months": [
            5,
            6,
            7,
            "… 2 more"
          ],
          "window_source": "regional pack rotation (provisional)",
          "year": 1
        },
        {
          "crop": "faba_bean",
          "label": "Faba bean",
          "start_month": 11,
          "end_month": 4,
          "months": [
            11,
            12,
            1,
            "… 3 more"
          ],
          "window_source": "regional pack rotation (provisional)",
          "year": 1
        }
      ],
      "score_details": {
        "water": {
          "annual_irrigation_mm": 1422.2,
          "saving_pct": 24.0,
          "method": "score = clip(50 + saving %, 0, 100): your current rotation = 50, a 25% saving = 75",
          "per_crop": [
            {
              "crop": "maize",
              "year": 1,
              "irrigation_mm": 973.6,
              "etc_mm": 975.7,
              "kc_status": "provisional — verify against FAO-56 Table 12"
            },
            {
              "crop": "faba_bean",
              "year": 1,
              "irrigation_mm": 448.7,
              "etc_mm": 501.0,
              "kc_status": "provisional — verify against FAO-56 Table 12"
            }
          ]
        },
        "soil_practices": {
          "parts": {
            "legume_in_rotation": 40.0,
            "family_diversity": 30.0,
            "ground_cover": 27.5
          },
          "method": "legume 40 + 30 x (distinct families - 1) / (crops - 1) + 30 x months with a crop / months in the cycle; diversity is assessed over the whole cycle"
        },
        "heat_risk": {
          "score_current_climate": 100.0,
          "score_scenario": 100.0,
          "method": "per crop: clip(100 - 15 x mean excess °C, 0, 100); rotation: mean over crops weighted by window length"
        }
      },
      "revenue": {
        "available": true,
        "complete": false,
        "currency": "EGP",
        "annual_per_feddan": null,
        "annual_for_area": null,
        "revenue_change_pct": null,
        "missing": [
          {
            "crop": "faba_bean",
            "label": "Faba bean",
            "missing": [
              "price"
            ]
          }
        ]
      },
      "year_plan": [
        {
          "month": 1,
          "crop": "faba_bean",
          "activity": "grow",
          "kc": 1.12,
          "etc_mm": 73.7,
          "rain_mm": 14.8,
          "irrigation_mm": 61.8,
          "irrigation_m3": 519.0,
          "note": null,
          "year": 1,
          "water_deficit_mm": null
        },
        {
          "month": 2,
          "crop": "faba_bean",
          "activity": "grow",
          "kc": 1.15,
          "etc_mm": 86.4,
          "rain_mm": 11.8,
          "irrigation_mm": 77.0,
          "irrigation_m3": 646.0,
          "note": null,
          "year": 1,
          "water_deficit_mm": null
        },
        {
          "month": 3,
          "crop": "faba_bean",
          "activity": "grow",
          "kc": 1.14,
          "etc_mm": 135.7,
          "rain_mm": 10.7,
          "irrigation_mm": 127.1,
          "irrigation_m3": 1067.0,
          "note": null,
          "year": 1,
          "water_deficit_mm": null
        },
        "… 9 more"
      ],
      "caveats": []
    },
    "… 2 more"
  ],
  "all_ranked": [
    {
      "id": "maize_faba_bean",
      "name": "Maize → Faba bean",
      "is_current": false,
      "water_mm_per_year": 1422.2,
      "water_saving_pct": 24.0,
      "scores": {
        "water": 74.0,
        "soil_practices": 97.5,
        "heat_risk": 100.0
      },
      "total": 90.5,
      "rank": 1
    },
    {
      "id": "maize_berseem",
      "name": "Maize → Berseem",
      "is_current": false,
      "water_mm_per_year": 1504.3,
      "water_saving_pct": 19.7,
      "scores": {
        "water": 69.7,
        "soil_practices": 100.0,
        "heat_risk": 100.0
      },
      "total": 89.9,
      "rank": 2
    },
    {
      "id": "soybean_wheat",
      "name": "Soybean → Wheat",
      "is_current": false,
      "water_mm_per_year": 1549.7,
      "water_saving_pct": 17.2,
      "scores": {
        "water": 67.2,
        "soil_practices": 97.5,
        "heat_risk": 100.0
      },
      "total": 88.2,
      "rank": 3
    },
    "… 5 more"
  ],
  "ndvi_check": {
    "status": "consistent",
    "text": "Satellite greenness at this pixel peaks in Feb and Jul, consistent with 2 crops a year as in this rotation (Rice May–Oct; Wheat Nov–May)."
  },
  "sensitivity": {
    "winner_is_robust": false,
    "note": "The top rotation changes with the weighting: your_priorities: maize_faba_bean; equal: maize_faba_bean; water_only: maize_faba_bean; soil_only: maize_berseem; heat_only: maize_faba_bean."
  },
  "warnings": [
    "Mean annual rain 2016–2025 (120 mm) is more than double the 1991–2005 mean (52 mm). This may reflect a change in the NASA POWER precipitation source rather than real rainfall, and must not be read as a climate trend."
  ],
  "assumptions": [
    "Kc follows the FAO-56 four-stage curve (initial, development, mid-season, late season) with stage lengths as fractions of the growing window (default 0.15 / 0.25 / 0.40 / 0.20, provisional until replaced with FAO-56 Table 11 lengths); month…",
    "ETc = ET0 x Kc per month, with ET0 from FAO-56 Penman-Monteith on the NASA POWER monthly climatology.",
    "Effective rain = 0.8 x monthly rain (a fixed fraction, not a soil water balance).",
    "… 7 more"
  ],
  "current_rotation_source": "farmer"
}
```

### `POST /api/compare` without `current_rotation` (US point)

Near Ames, Iowa, rainfed: the current rotation is detected from the USDA CDL.

Request:

```bash
curl -X POST http://127.0.0.1:8000/api/compare -H 'Content-Type: application/json' -d @request.json
```
`request.json`:
```json
{
  "lat": 42.0,
  "lon": -93.75,
  "water_access": "rainfed",
  "priorities": {
    "water": 1,
    "soil": 1,
    "heat_risk": 1
  }
}
```

Response (HTTP 200, trimmed):

```json
{
  "current_rotation_source": "cdl_detected",
  "field_history": {
    "status": "detected",
    "rotation": [
      "maize",
      "soybean"
    ],
    "confidence": "high",
    "matched_pack_rotation": "maize_soybean"
  },
  "all_ranked": [
    {
      "id": "maize_soybean",
      "name": "Maize → Soybean",
      "is_current": true,
      "water_unmet_fraction": 0.351,
      "water_deficit_mm": 228.7,
      "scores": {
        "water": 50.0,
        "soil_practices": 85.0,
        "heat_risk": 100.0
      },
      "total": 78.3,
      "rank": 1
    },
    {
      "id": "maize_soybean_oat",
      "name": "Maize → Soybean → Oat",
      "is_current": false,
      "water_unmet_fraction": 0.318,
      "water_deficit_mm": 187.2,
      "scores": {
        "water": 53.3,
        "soil_practices": 68.3,
        "heat_risk": 97.8
      },
      "total": 73.1,
      "rank": 2
    },
    {
      "id": "continuous_maize",
      "name": "Continuous maize",
      "is_current": false,
      "water_unmet_fraction": 0.37,
      "water_deficit_mm": 238.3,
      "scores": {
        "water": 48.1,
        "soil_practices": 15.0,
        "heat_risk": 100.0
      },
      "total": 54.4,
      "rank": 3
    }
  ],
  "ndvi_check": {
    "status": "consistent",
    "text": "Satellite greenness at this pixel peaks in Jul, consistent with 1 crop a year as in this rotation (Maize May–Oct; Soybean May–Oct)."
  },
  "warnings": [
    "Your current rotation was detected from the USDA Cropland Data Layer, not entered by you: Detected from the USDA Cropland Data Layer 2020–2025: a 2-year cycle (maize → soybean) seen 3.0 times with 0 year(s) that break the pattern. Confidenc…"
  ]
}
```

### `POST /api/chat`

Grounded farmer Q&A (needs an LLM key on the server; without one the response is 200 with `available: false`).

Request:

```bash
curl -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' -d @request.json
```
`request.json`:
```json
{
  "question": "لو زرعت ذرة وفول هوفر مياه قد إيه؟",
  "language": "ar",
  "compare_request": {
    "lat": 30.85,
    "lon": 31.05,
    "current_rotation": [
      "rice",
      "wheat"
    ],
    "water_access": "full_irrigation",
    "priorities": {
      "water": 1,
      "soil": 1,
      "heat_risk": 1
    },
    "scenario": {
      "temp_delta_c": 0,
      "water_cut_pct": 0
    },
    "area": {
      "value": 2,
      "unit": "feddan"
    }
  }
}
```

Response (HTTP 200, trimmed):

```json
{
  "answer": "دورة الذرة الشامية ثم الفول البلدي احتياج الري فيها ١١٬٩٤٧ متر مكعب في السنة لحقل مساحته فدانان، وده أقل من دورتك الحالية بمقدار ٣٬٧٨١ متر مكعب، أو ٢٤٪. الأرقام تقديرية، واحتياج ري الأرز في الدورة الحالية حد أدنى، لأنه لا يشمل مياه تجهيز الأرض والتسرب. مواعيد الزراعة والحصاد في التحليل مبدئية وغير مؤكدة محليًا.  \nالمصادر المستخدمة: بيانات الحقل، مقارنة الدورات، بيانات المحاصيل (معاملات الفاو-٥٦ والتقويم الإقليمي)",
  "available": true,
  "message": null,
  "sources_used": [
    "بيانات الحقل",
    "مقارنة الدورات",
    "بيانات المحاصيل (معاملات الفاو-٥٦ والتقويم الإقليمي)"
  ],
  "grounded": true,
  "numbers_not_in_context": [],
  "disclosure": "AI-generated answer based on your field's analysis",
  "model": "openai:gpt-6-luna",
  "elapsed_s": 4.7,
  "context_tokens": 4385,
  "language_clean": true,
  "latin_words": [],
  "timeout_retries": 0,
  "language_retries": 0
}
```

## 7. Farmer chat

`POST /api/chat` (`backend/chat.py`, context built by `engine/chat_context.py`) answers a farmer's question using only that field's comparison. It is optional: without an LLM key it returns 200 with `available: false` and the rest of the tool is unaffected.

**Context.** The endpoint runs the normal comparison (NDVI from the cache only) and builds a deterministic, plain-text context with labelled sections:
- FIELD AND REQUEST;
- ROTATIONS COMPARED: crops and windows, water need or deficit, saving, the three scores with their parts, total, rank, the revenue block, the engine's explanation and the caveats;
- ALL LOCAL OPTIONS, SENSITIVITY, WARNINGS, ASSUMPTIONS;
- SCORING DEFINITIONS, in plain words;
- CROP FACTS: family, legume, ECOCROP temperatures, Kc with status and regional calendar for each crop;
- CLIMATE (NASA POWER) and DATA SOURCES, with flags where a license is unverified;
- the MONTHLY PLAN of the top three rotations, sent only when the question is about timing, months, planting or harvest.

For Arabic questions the context also carries an ARABIC GLOSSARY:
- crop names from `name_ar`;
- rotation names written with "ثم";
- fixed terms such as فدان, جنيه, مم, متر مكعب, أرز شعير, حد أعلى;
- Arabic section names for the sources line.

The context is limited to about 6,000 tokens; the plans of lower-ranked rotations are dropped first. For Tanta it is about 4,385 tokens for a non-timing question and about 5,321 for a timing question.

**Rules given to the model** (system prompt; the farmer's question and up to six history turns go only in user/assistant turns and cannot change the rules):
- Answer only from the context.
- Every number must appear in the context. Never compute new agronomic or money numbers, and never estimate yields, prices, income or profit.
- If the answer is not in the context, say so and suggest the local agricultural extension officer.
- Discuss only crop rotation, water and timing. Never recommend pesticides, chemicals, fertilisers or doses.
- Say when a figure is provisional, a lower bound or an upper bound. Yields and prices are national averages from a provisional table.
- Arabic answers: at most five short sentences in simple, Egyptian-friendly Arabic, using the glossary and no Latin letters at all.
- End with a "sources used" line naming the context sections.

**What it refuses.** Pesticide, disease and pest questions (for example "أرش إيه للدودة؟" is answered with "this tool does not cover it, ask the agricultural extension officer"). It also refuses profit or income estimates; it may quote the indicative gross revenue figures with their caveats. Instructions embedded in the question are ignored.

**Checks on the answer.**
- `numbers_not_in_context` lists any number in the answer that is not in the context, allowing rounding; `grounded` is true only if the list is empty. The check confirms only that each number appears somewhere in the context, not that it is attached to the right thing.
- For Arabic answers, `language_clean` is false if Latin-script words remain after one automatic retry with a correction note.
- A provider timeout is retried once.
- The key is never logged or returned.
- The disclosure "AI-generated answer based on your field's analysis" is always included.

**Provider and model.** Chosen by environment:
- `OPENAI_API_KEY` → OpenAI Responses API, default model `gpt-6-luna` at reasoning effort `none` (falls back to `low` if the model rejects it);
- `ANTHROPIC_API_KEY` → Anthropic Messages API, default model `claude-haiku-5-5` with thinking off.

`LLM_PROVIDER` chooses when both keys are set; `LLM_MODEL` (or `OPENAI_MODEL` / `ANTHROPIC_MODEL`) overrides the model. Settings: timeout 30 s, answer budget 400 tokens, temperature 0.2 only where the model accepts it. In a side-by-side run of six test questions on 2026-11-15, `gpt-6-luna` gave clean Arabic in 2.0–4.2 s for five of the six; one call took 32 s. The Anthropic path is tested with mocks only.

## 8. Running it

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
python scripts/precache_demo.py        # POWER, SoilGrids, NDVI and (US) CDL for the demo points
uvicorn backend.main:app --reload      # API at http://127.0.0.1:8000, docs at /docs
pytest                                 # 203 tests, offline
```

These commands were run in a fresh virtual environment on 2026-11-15:
- the install succeeded;
- the precache reported every demo point cached;
- `pytest` passed 203 tests;
- the server answered `/api/health` and `/api/compare`.

A first precache from an empty cache is slow, because the ORNL MODIS service often takes 40–60 s per request. NDVI for one point took 373–818 s in our runs.

**Offline behaviour.**
- **NASA POWER:** read from `data/cache/`. If the API cannot be reached, the nearest cached point within 0.5° is used (with a note); with no such point the response is 503.
- **SoilGrids:** cached. Empty cells are retried at neighbouring offsets. If no data can be had, `soil` is `null` with a message and the comparison still runs.
- **NDVI:** `/api/compare` and `/api/location` only ever read the cache. `/api/ndvi` starts a background fetch and returns `"loading"`.
- **CDL:** cached; if CropScape is unreachable the field history has status `unavailable`.
- **Chat:** without a key it returns `available: false`.
- **Tests:** they never touch the network. `engine/tests/conftest.py` blocks `requests.get`, redirects every cache to a temporary folder and hides any LLM key.

**Environment variables** (names only; all optional, used only by the chat): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `LLM_REASONING_EFFORT`. They may be set in the shell or in `.env` at the project root. `.env` is git-ignored.

Other scripts: `scripts/demo_report.py` prints rankings for the demo points; `scripts/build_crops.py` rebuilds `data/crops.json` from the ECOCROP CSV.

## 9. Validation

**Test suite.** 203 collected tests (`pytest`; some test functions are parametrised), all offline, built on real, unmodified API responses stored in `engine/tests/fixtures/`. By file:

| Area | Tests | What is checked |
|---|---|---|
| Climate and ET0 (`test_climate.py`) | 16 | POWER parsing and fill values, the FAO-56 worked example, physical bounds, warming raises ET0, units, offline cache and nearest-point fallback, trends, rain warning |
| Comparison (`test_compare.py`) | 28 | water balance, Kc curve, rice caveat, m³, water score, heat score never rises with warming, priorities, budget flag, sensitivity, year plan, explanations, API errors, exploratory mode |
| Crop suitability and rotation rules (`test_suitability.py`) | 37 | ECOCROP envelopes, windows, rainfall by water access, pH advisory, hard/soft rules, determinism |
| Soil (`test_soil.py`) | 25 | SoilGrids units and depth weighting, USDA texture triangle, neighbour cells, offline |
| NDVI (`test_ndvi.py`) | 19 | band metadata, scaling and fill values, paging, one/two/flat profiles, calendar check never names a species, resume after failure, never 500 offline |
| CDL (`test_cdl.py`) | 10 | real response parsing, Albers projection against the service example, legend codes, Iowa and Kansas detection, edge cases, endpoint |
| Multi-year and rainfed (`test_multiyear.py`, `test_rainfed_stress.py`, `test_us_packs.py`) | 19 | annual means, fallow years, deficit reporting, unmet-fraction score, fallow moisture note, Kansas winter-wheat stages |
| Revenue (`test_market.py`) | 7 | yield × price, incomplete tables, trade-off sentence, no revenue where no table |
| Forecast (`test_forecast.py`) | 6 | recovers an exact synthetic trend, reports when it loses to climatology, `use_forecast` |
| Chat (`test_chat.py`) | 20 | context contents and size, roles, history truncation, no key / timeout / error, Arabic glossary, Latin-letter retry, number check |
| Regression and smoke (`test_regression.py`, `test_smoke.py`) | 16 | Tanta totals pinned; imports, mock validates against the schema, AI disclosure present |

**FAO-56 worked example.** `test_et0_fao56_example_17` reproduces FAO-56 Chapter 4, Example 17 (Bangkok, April: Tmax 34.8 °C, Tmin 25.6 °C, ea 2.85 kPa, u2 2 m/s, Rs 22.65 MJ m⁻² day⁻¹, G 0.14): expected 5.72 mm/day, tolerance 5%.

**Tanta regression** (rice → wheat, full irrigation, equal priorities; `test_regression.py` pins the whole table):

| Rank | Rotation | Irrigation mm/yr | Saving | Water | Soil | Heat | Total |
|---|---|---|---|---|---|---|---|
| 1 | Maize → Faba bean | 1,422.2 | 24.0% | 74.0 | 97.5 | 100.0 | 90.5 |
| 2 | Maize → Berseem | 1,504.3 | 19.7% | 69.7 | 100.0 | 100.0 | 89.9 |
| 3 | Soybean → Wheat | 1,549.7 | 17.2% | 67.2 | 97.5 | 100.0 | 88.2 |
| 4 | Cotton → Short berseem | 1,606.1 | 14.2% | 64.2 | 100.0 | 100.0 | 88.1 |
| 5 | Rice → Berseem | 1,753.7 | 6.3% | 56.3 | 100.0 | 100.0 | 85.4 |
| 6 | Rice → Faba bean | 1,827.1 | 2.4% | 52.4 | 100.0 | 100.0 | 84.1 |
| 7 | Maize → Wheat | 1,545.0 | 17.5% | 67.5 | 27.5 | 100.0 | 65.0 |
| 8 | Rice → Wheat (current) | 1,872.4 | 0 | 50.0 | 30.0 | 100.0 | 60.0 |

For a 2-feddan field, maize → faba bean needs 11,947 m³/year against 15,728 m³ for rice → wheat, a saving of 3,781 m³.

**Forecast back-test.** See 4.11: the trend model beats the climatology baseline for both variables at all four demo points.

**NDVI against the regional calendar.**
- **Tanta:** two greenness seasons, Dec–Mar peaking in Feb (NDVI 0.692) and Jul–Aug peaking in Jul (0.696). The check reports `consistent`: "Satellite greenness at this pixel peaks in Feb and Jul, consistent with 2 crops a year as in this rotation (Rice May–Oct; Wheat Nov–May)."
- **Iowa (Ames):** one season, Jun–Aug peaking in Jul (0.842). The check reports `consistent`: "peaks in Jul, consistent with 1 crop a year as in this rotation (Maize May–Oct; Soybean May–Oct)."
- **Punjab (Ludhiana):** two peaks, Feb and Aug, `consistent` with rice → wheat.
- **Kansas (Hays):** one peak in Apr. The check reports `partly_consistent`: no peak falls in the sorghum window (Jun–Oct). Over 2023–2025 the pixel had sorghum only in 2023.

## 10. Known limitations and assumptions

### 10.1 Provisional and unverified values

- **Kc values:** all crop coefficients come "from memory of FAO-56 Table 12; not checked against the table". Oat's source is "TODO".
- **Stage fractions:** the default 0.15 / 0.25 / 0.40 / 0.20 is provisional, and the same for every crop. The Kansas winter-wheat override is "from memory … verify".
- **Regional packs:** every calendar entry and rotation in all four packs is "provisional — unverified", as are all four bounding boxes. The Iowa and Kansas packs were "supplied by the team from memory".
- **Market table:** `market_egypt.json` is provisional (secondary source, February 2024, before the devaluation).
  - Rice revenue is an upper bound (milled basis).
  - Faba bean yield needs verifying.
  - Sugar beet has no yield.
  - Faba bean and berseem have no price.
  - The EG2024-0008 URL is a third-party mirror.
- **Crop table:** Arabic crop names in `data/crops.json` are marked `name_ar_reviewed: false`.
- **CDL mapping:** the CDL-to-crop mapping in `data/cdl_crop_map.json` is unverified, as are the inference thresholds (period ≤ 3 years; the high and medium rules).
- **NDVI thresholds** are heuristic, "not calibrated for this region".
- **Licenses and citations still open:**
  - "TODO verify": NASA POWER, MOD13Q1, SoilGrids, FAO-56, GAEZ, the three USDA FAS reports, the CDL.
  - ECOCROP: "UNVERIFIED — team must confirm before submission".
  - MOD13Q1 product citation and CDL citation: TODO.
- **Rotation rules:** the references in `data/rotation_rules.json` are still TODO.

### 10.2 Proxies

- **Soil-health practices** counts practices (legume, family diversity, ground cover); it is not a measured soil outcome. It penalises a third crop from a family already in the rotation: in Iowa, maize → soybean → oat scores 68.3 against 85.0 for maize → soybean.
- **Heat-stress exposure** compares monthly means with an ECOCROP optimal maximum. It ignores daily extremes and heat at flowering. It is 100 for almost every rotation in the Delta, Iowa and Kansas under current climate, so it barely discriminates there. Exceptions: rice → wheat at Tanta drops to 95.4 at +2 °C, and Punjab rice → wheat scores 81.6.
- **Water deficit (rainfed)** is an indicator of drought-stress risk, not a yield loss.

### 10.3 Not modelled

- **Economics:** production costs, profit and net returns. Revenue is gross, indicative and unscored, and exists only for Egypt.
- **Pests, diseases, pesticides and fertiliser.**
- **Soil moisture storage:** there is no soil water balance. Effective rain is a fixed 0.8 × rain. Stored moisture from a fallow year is not modelled, so fallow's benefit to the next crop is not captured.
- **Rice paddy water:** land preparation, percolation and seepage are not included (`paddy_extra_water_mm` is `null`), so rice irrigation need is a lower bound.
- **Field-level climate:** POWER gives grid-cell averages (½° × ⅝° meteorology, 1° radiation), not field measurements.
- **NDVI** is one 250 m pixel without quality filtering. It shows greenness, not which crop is grown, and can mix fields.
- **Soil is not used in any score.** SoilGrids is shown, pH is advisory only (exploratory mode), and a farmer-entered `soil_texture` is recorded but not used.
- **Rain is not projected** by the forecast.
- **Transport:** canal and conveyance losses, leaching requirement, salinity.

### 10.4 Known data issues and results that need care

- **POWER rainfall discontinuity:** at Tanta, mean annual rain 2016–2025 is 120 mm against 52 mm for 1991–2005. The tool warns that this "may reflect a change in the NASA POWER precipitation source rather than real rainfall". At Ludhiana the jump (928 mm against 521 mm) is just under the 2× warning threshold, so no warning is shown. Rain is never trended.
- **Punjab cooling:** POWER shows a cooling trend (−0.351 °C and −7.7 mm ET0 per decade). It is not verified against station data.
- **Kansas results:**
  - Wheat + double-crop soybean ranks first overall (total 82.1) on its soil score of 100, although it has the lowest water score (46.3, rain covering about 42% of crop demand on average). Double-cropping is rare in semi-arid west-central Kansas, and one bounding box covers both the dry west and the wetter east.
  - The water scores at Hays span only 46.3–50.4, because wheat and sorghum both have about 55% of their demand unmet.
  - Fallow now has no water benefit at all (see 10.3).
- **Rainfed rice:** the tool lets a user pick rainfed in the Delta, which gives meaningless rice figures. There is no guard.
- **Exploratory mode** (outside the four packs): alternatives come from global ECOCROP envelopes and generic rules, have not been checked against local practice, and are always low confidence.
- **Chat:** the 30 s timeout is not a hard wall-clock limit; single calls of 32–34 s were observed. The number check confirms only that numbers appear in the context, not that they are correctly attributed.

## 11. Use of AI tools

From [`AI_USAGE.md`](../AI_USAGE.md):
- **Code:** generated and refactored with Claude (Anthropic) via Claude Code. This covers the engine, backend, scripts, tests and documentation drafts.
- **Research and planning:** Claude chat helped find datasets and methods, outline the architecture and draft the task breakdown.
- **At runtime,** the optional farmer chat calls an LLM (OpenAI `gpt-6-luna` by default, or Anthropic `claude-haiku-5-5`), only to phrase answers from a context the engine built. Nothing it says is scored or ranked.
- **The next-year projection is not an AI model.** It is an ordinary least-squares statistical model fitted by our code to NASA POWER data.

The human team framed the problem, chose the data sources, methods and agronomic rules, and reviews and tests all AI-generated code. It checks results against published references such as the FAO-56 worked example. The data files with team-supplied values (packs, Kc, market table) are marked "TODO: team to confirm" in the AI_USAGE table. The raw downloads and test fixtures are unmodified API responses, not AI-generated.

## 12. Roadmap

- Build the React frontend: map click, rotation input, score cards, year plan, NDVI chart and chat.
- Have a researcher verify Kc values (FAO-56 Table 12), per-crop stage lengths (Table 11), every regional calendar and rotation, and the open licenses and citations.
- Add a simple soil water balance, so that fallow and stored moisture are represented, and paddy water from a sourced value.
- Replace the Egypt market table with FAOSTAT prices and yields, and add US yields and prices from NASS QuickStats.
- Apply MODIS pixel-reliability filtering, and compare NDVI over several pixels or the field polygon.
- Split broad packs such as Kansas east/west, and add more regions.
- Use SoilGrids texture in the water balance and in suitability.
- Put a hard wall-clock limit on chat calls, and run a reviewed evaluation of Arabic answers with native speakers.
