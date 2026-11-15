# API changes since the first mock

For the frontend developer. Lists every change to the API contract (`backend/schemas.py`) since the first `frontend/mock/compare.json`. The current contract is always `backend/schemas.py`. Interactive docs are at `/docs` when the backend runs.

`frontend/mock/compare.json` is now **real engine output** for the demo point near Tanta, not sample data. Its `run_info` says when it was generated and with which request.

## Latest round (2026-11-15): scoring frozen, MODIS NDVI added

- `scores.water` = `clip(50 + saving %, 0, 100)`: current rotation 50, a 25% saving 75 (was 50 + 2.5 × saving %).
- `scores.soil_practices` has three parts: legume 40, plant-family diversity 30, ground cover 30. `score_details.soil_practices` now has `parts` (`legume_in_rotation`, `family_diversity`, `ground_cover`), `distinct_families`, `n_crops`, `months_with_ground_cover`; the old `points` and `share_of_*` keys are gone.
- `ndvi` is now filled from the cache (16-day MODIS NDVI, `{date, ndvi}`), with new `ndvi_status`: `"loaded"` or `"not_loaded"`. `/api/compare` never waits for NDVI.
- New `ndvi_check`: `{status: consistent | partly_consistent | inconsistent | not_cropped, per_crop: [{crop, label, window, contains_peak, peak_months}], text, warning, note}`. Supporting evidence only; it never changes scores. When `warning` is set it is also in `warnings`.
- `location.seasons_detected` now comes from cached NDVI; each season gains `peak_ndvi`. New `location.ndvi_status`.
- New `GET /api/ndvi?lat=&lon=` → `{status: loaded | loading | unavailable, lat, lon, series, summary, message, meta}`. If not cached it starts a background fetch and returns `"loading"`; poll every 30–60 s. A fetch takes several minutes (the ORNL API is slow). `summary` = `{seasons, seasons_per_year, monthly_profile, bare_or_low_cover_months, looks_cropped, thresholds}`.

## Earlier round (2026-11-14, second update)

- `priorities.resilience` → **`priorities.heat_risk`**.
- `scores.climate_resilience` → **`scores.heat_risk`**, label "heat-stress exposure (proxy)". Higher = less exposed. It only counts months where the mean temperature is above the crop's optimal maximum, so warming can never raise it.
- `scores.water` changed meaning: it is now the saving versus the farmer's current rotation, `clip(50 + 2.5 × saving %, 0, 100)`. The current rotation is always 50; 20% less water = 100.
- New on each rotation: `water_saving_mm`, `water_saving_pct`, `water_saving_m3` (m3 only with `area`). Negative = needs more water than the current rotation.
- `score_details.water` keys changed: `candidate_set_min_mm` / `candidate_set_max_mm` removed; `current_rotation_mm`, `saving_mm`, `saving_pct` added. `score_details.climate_resilience` → `score_details.heat_risk` with `score_current_climate`, `score_scenario`, `change`, `per_crop`, `note`.
- New top-level `all_ranked` (every regional-pack rotation, best first, for a "see all options" table) and `current_is_strong` (true when the farmer's rotation is in the top two).
- New `year_plan[].kc`: month-average crop coefficient from the FAO-56 curve (`null` when fallow).
- `sensitivity.cases[].name`: `resilience_only` → `heat_only`.
- Alternatives are now simply the two best pack rotations that differ by at least one crop (the category-mix rule is gone).

## Endpoints

| Endpoint | Change |
|---|---|
| `POST /api/compare` | Real end to end (was a mock). Returns 400 for unknown crop ids, 503 if NASA POWER is unreachable and nothing is cached. Can take several seconds on a first request for a new location. |
| `GET /api/climate?lat=&lon=` | Real NASA POWER data, 1991–2025 (was a mock). |
| `GET /api/location?lat=&lon=` | New. Real SoilGrids soil, plus `label` = regional pack name if the point is in one. `seasons_detected` is always `[]` for now. |
| `GET /api/sources` | Shape changed from a list to `{"ai_disclosure": str, "sources": [...]}`. |
| `GET /api/crops` | Entries now have ECOCROP fields (envelopes, `category`, `family`, `is_legume`, `name_ar_reviewed`, `ecocrop_id`, ...). 30 crops. Crop id `beet` is now `sugar_beet`. |

## Request: `CompareRequest`

| Field | Change |
|---|---|
| `water_access` | **New, required.** `"rainfed" \| "limited_irrigation" \| "full_irrigation"`. |
| `priorities.profit` | **Renamed, now `priorities.heat_risk`** (was `resilience` in between; there is no price data). Weights are 0..1 each; the engine normalises them to sum to 1 (all zero = equal). |
| `soil_texture` | Now optional (`null` = use SoilGrids). Recorded but not yet used in the scores. |
| `scenario.temp_delta_c` | Now limited to -5..5. Shifts temperature and recomputes ET0. |
| `scenario.water_cut_pct` | Now limited to 0..100. Drives `exceeds_water_budget`. |
| `field_name` | New, optional string. |
| `area` | New, optional `{value: float > 0, unit: "feddan" \| "hectare" \| "acre"}`. Enables the `*_m3` fields. |

## Response: `CompareResponse`

| Field | Change |
|---|---|
| `region: str` | **Removed**, replaced by `location`. |
| `location` | New: `{lat, lon, label, soil, soil_message, seasons_detected}`. `soil` can be `null` (then `soil_message` asks the farmer to enter texture). `soil.ph` and `soil.organic_carbon_g_kg` can be `null`; `soil.note` added. |
| `climate.forecast_next_year`, `climate.forecast_method` | New, always `null` for now. |
| `ndvi` | New list of `{date, ndvi}` from cached MODIS NDVI; `[]` when `ndvi_status` is `"not_loaded"`. Load with `GET /api/ndvi`. |
| `mode` | New: `"regional_pack"` (rotations from local knowledge) or `"exploratory"` (no pack; generated alternatives, low confidence, with a warning). |
| `pack` | New: `{id, name}` or `null`. |
| `priorities_used` | New: the normalised weights actually applied. |
| `scenario` | New: echo of the request scenario. |
| `sensitivity` | New: `{cases: [{name, weights, ranking}], winner_is_robust, note}`. Cases: `your_priorities`, `equal`, `water_only`, `soil_only`, `heat_only`. `ranking` lists rotation ids, best first. |
| `all_ranked` | New: every regional-pack rotation `{id, name, is_current, water_mm_per_year, water_saving_pct, scores, total, rank}`, best first. `[]` in exploratory mode. |
| `current_is_strong` | New bool: the farmer's rotation is in the top two of `all_ranked`; its `why` then says so and what an alternative would gain. |
| `warnings` | New list of strings to show prominently (exploratory mode, data caveats, soil unavailable, water access mismatch). |
| `assumptions` | New list of strings (method simplifications). |
| `run_info` | New: `{generated_at, note}`. |
| `rotations` | Still a list, **current rotation first**, then up to two alternatives. See below. |

## Each rotation: `RotationResult`

| Field | Change |
|---|---|
| `windows` | New: `[{crop, label, start_month, end_month, months, window_source}]`. `label` may differ from the crop name (e.g. "Short berseem"). |
| `source` | New: `"farmer" \| "regional pack" \| "exploratory"`. |
| `scores` | **Changed keys**: was `{water, soil, climate_fit}`, now `{water, soil_practices, heat_risk}`, each 0–100. `water` = saving versus the current rotation (current = 50) and can be `null` (crop without Kc). Show `soil_practices` as **"soil-health practices (proxy)"** and `heat_risk` as **"heat-stress exposure (proxy)"** (higher = less exposed). |
| `score_details` | New: the numbers behind each score (mm, points, per-crop temperature scores). |
| `water_mm_per_year`, `water_m3_per_year` | New. m3 only when `area` is given. |
| `water_saving_mm`, `water_saving_pct`, `water_saving_m3` | New: versus the current rotation (0 for the current rotation itself; negative = needs more). |
| `exceeds_water_budget`, `water_budget_mm` | New; `null` unless `scenario.water_cut_pct > 0`. |
| `why` | Now real template text with actual numbers. |
| `not_assessed` | New sentence listing what is not assessed. |
| `confidence` | Now computed: `"medium"` while Kc and regional data are provisional, `"low"` in exploratory mode or for global windows, `"high"` only when everything is verified. |
| `suitability` | New: `[{crop, score, limiting_factor, window_source, ph_advisory}]`. `ph_advisory` is `{status: "ok" \| "outside_optimal" \| "outside_absolute", soil_ph, ecocrop_optimal, ecocrop_absolute, note}` or `null`; it is advice, not part of any score. |

## Each month: `year_plan[]` (`YearPlanMonth`)

| Field | Change |
|---|---|
| `water_need_mm` | **Removed**, replaced by `etc_mm` (crop water use) and `irrigation_mm` (need after effective rain). Both can be `null` for crops without Kc. |
| `irrigation_m3` | New; only when `area` is given. |
| `kc` | New: month-average crop coefficient (FAO-56 four-stage curve); in a shared transition month the mix of both crops; `null` when fallow. |
| `activity` | Unchanged values. In a shared transition month (harvest of one crop, sowing of the next) `crop` is the incoming crop, `activity` is `"plant"`, and `note` explains. |
| Order | Always 12 entries, January to December. |

# Overnight additions (branch `overnight-us`)

All additions are optional or new fields; no existing field was renamed or removed.

## Phase 1 — indicative gross revenue

| Where | Field | Meaning |
|---|---|---|
| `rotations[]`, `all_ranked[]` | `revenue` | New `Revenue` block (always present from the engine; `null`-able in the schema). |
| `revenue` | `available` | `false` when the region has no market table (e.g. Punjab, exploratory mode). |
| `revenue` | `complete` | `true` only if every crop has both a yield and a price. |
| `revenue` | `scored` | `false` everywhere except packs whose market table is complete (see Phase 5). |
| `revenue` | `currency`, `price_snapshot`, `status` | From the market table (Egypt: EGP, February 2024 procurement prices, provisional). |
| `revenue` | `per_crop[]` | `{crop, label, year, yield_t_per_ha, price_per_t, revenue_per_ha, revenue_per_feddan, revenue_for_area, yield_basis, price_basis, note, missing}`. |
| `revenue` | `missing[]` | `{crop, label, missing: ["yield" \| "price"]}`. |
| `revenue` | `total_per_ha`, `total_per_feddan`, `total_for_area` | Whole rotation cycle; `null` unless `complete`. `total_for_area` only with `area`. |
| `revenue` | `annual_per_ha`, `annual_per_feddan`, `annual_for_area`, `n_years` | Cycle total / number of years (same as total for one-year rotations). |
| `revenue` | `revenue_change_pct` | Annual revenue versus the current rotation, %; `null` unless both are complete. |
| `revenue` | `notes[]` | "gross revenue, no costs", and the rice upper-bound note when rice is present. |

Label it **"indicative gross revenue"** in the UI. Never call it profit or income. Revenue is not part of `scores` or `total` for Egypt.
`why` gains one sentence on revenue: the trade-off with numbers when both rotations are complete, otherwise which crop has no price.
`not_assessed` now starts with "production costs and current market prices" (regions with a market table) or "crop prices and revenue" (others); it no longer says "income".

## Phase 2 — multi-year rotations and rainfed water logic

| Where | Field | Meaning |
|---|---|---|
| `rotations[]`, `all_ranked[]` | `n_years` | Length of the rotation cycle (default 1). Water and revenue figures are **annual means** over the cycle. |
| `windows[]` | `year` | Year of the cycle the crop belongs to (1..n_years). |
| `year_plan[]` | `year` | The plan now has 12 × `n_years` entries (still 12 for one-year rotations), ordered year 1 Jan..Dec, year 2 Jan..Dec, … |
| `year_plan[]` | `water_deficit_mm` | Rainfed only: ETc − effective rain for the month. `irrigation_mm` and `irrigation_m3` are `null` when rainfed. |
| `rotations[]` | `water_deficit_mm`, `water_deficit_reduction_pct` | Rainfed only (also in `all_ranked[]`). In rainfed mode `water_mm_per_year`, `water_saving_*` and every `*_m3` field are `null`. |
| `rotations[]` | `water_criterion_label` | `"irrigation water saving"` or `"water-deficit exposure"` (rainfed). Use it as the label of `scores.water`. |
| `score_details.water` | `label`, `n_years`, `per_year_mm` | Plus, when rainfed, `water_deficit_mm`, `current_rotation_deficit_mm`, `deficit_reduction_mm`, `deficit_reduction_pct`. |
| `score_details.soil_practices` | `n_years`, `cycle_months`, `cycle_months_with_ground_cover`, `fallow_months` | `fallow_months` = `[{year, month}]`; each counts as zero ground cover. `months_with_ground_cover` is now the annual mean (unchanged for one-year rotations). |
| `score_details.water.per_crop[]` | `year`; `water_deficit_mm` replaces `irrigation_mm` when rainfed | |

Request: `current_rotation` may contain `"fallow"` for a year without a crop (e.g. `["wheat", "fallow"]`).

## Phase 4 — field history from the USDA Cropland Data Layer

New endpoint **`GET /api/field-history?lat=&lon=`** → `FieldHistory`:

| Field | Meaning |
|---|---|
| `status` | `"detected"`, `"not_detected"` (no repeating pattern / too few years), `"not_cropland"` (pasture, forest, built-up…), `"not_available"` (outside the contiguous US), `"unavailable"` (CropScape down). Never a 500. |
| `yearly[]` | `{year, cdl_value, cdl_category, legend_name, kind: "crop" \| "fallow" \| "non_crop" \| "other_crop", crops}`, oldest first. |
| `rotation` | Flat list with `"fallow"` for a year without a crop, e.g. `["wheat", "sorghum", "fallow"]`; can be sent as `current_rotation`. |
| `cycle` | Per year of the cycle, e.g. `[["wheat"], ["sorghum"], []]`. Double-crop years have two crops. |
| `n_years`, `confidence` (`high`/`medium`/`low`/`none`), `confidence_note` | Show `confidence_note` to the farmer. |
| `matched_pack_rotation` | Pack rotation id the cycle matches, or `null`. |
| `source`, `resolution_note`, `is_cached` | |

`POST /api/compare`:
- `current_rotation` is now **optional** (was required). If it is omitted for a contiguous-US point and a rotation is detected, the CDL rotation is used; otherwise the response is HTTP 400 with the reason.
- New response fields: `current_rotation_source` (`"farmer"` or `"cdl_detected"`) and `field_history` (the `FieldHistory` above, only when detected). A warning that starts "Your current rotation was detected from the USDA Cropland Data Layer…" is put first in `warnings`.
- Internal request key `current_cycle` (list of lists) is filled by the backend from the CDL; the frontend does not need to send it.

## Phase 5 — skipped
No `NASS_API_KEY` (no `.env` file), so there are no US market tables and no `revenue` priority. `Priorities` is unchanged; `revenue.scored` is always `false`.

## Phase 6 — next-year climate projection

| Where | Field | Meaning |
|---|---|---|
| `climate` (`/api/climate`, `/api/compare`) | `forecast_next_year` | Now filled: 12 × `{month, temp_c, et0_mm}` for `forecast_year`. |
| `climate` | `forecast_method` | Plain text: "Simple statistical projection, not a weather forecast…" plus the back-test verdict (says "does NOT beat" when it loses to climatology). |
| `climate` | `forecast_year`, `forecast_backtest`, `forecast_trend_per_decade` | New. `forecast_backtest` = `{available, train_years, test_years, n_test_months, temp_c: {mae_model, mae_climatology, skill_vs_climatology, beats_climatology}, et0_mm: {…}}`. |
| request `scenario` | `use_forecast` (bool, default `false`) | When `true`, water and heat use the projected year (rain stays climatological). `temp_delta_c` is then ignored, with a warning. |
| `score_details.heat_risk` | `projection_year` | Set when `use_forecast` is on. |

# Rainfed relative-stress scoring (branch `rainfed-relative-stress`, 2026-11-15)

Additions only; irrigated responses are unchanged (Tanta regression check passes).

**Behaviour change (rainfed only):** `scores.water` is now based on relative water stress, not millimetres of deficit. Per crop season, unmet fraction = `water_deficit_mm / etc_mm`; a rotation's value is the mean over its crop seasons (fallow years are not crop seasons and add nothing); score = clip(50 + 100 × (current unmet − rotation unmet), 0, 100), so the current rotation is 50. `water_deficit_mm` and `water_deficit_reduction_pct` are still returned, as information only.

| Where | Field | Meaning |
|---|---|---|
| `rotations[]`, `all_ranked[]` | `water_unmet_fraction` | Rainfed only: mean unmet fraction over crop seasons (0–1). This is what the water score uses. |
| `rotations[]` | `water_unmet_per_crop[]` | Rainfed only: `{crop, year, etc_mm, water_deficit_mm, unmet_fraction, rain_cover_fraction}` per crop season. |
| `score_details.water` | `unmet_fraction`, `current_rotation_unmet_fraction`, `unmet_per_crop` | Rainfed only. `method` text updated. |
| `score_details.water` | `fallow_note` | Rainfed rotations with a fallow year: "Stored soil moisture from a fallow year is not modelled, …". The same sentence is in `caveats` (any rotation with a fallow year) and in `assumptions`. |
| `score_details.water.per_crop[]` | `unmet_fraction` | Rainfed only. |
| `why` | — | Rainfed water sentence now reads "rain covers about 46% of wheat's … water demand"; the deficit in mm follows, marked as information. |

**Data:** `data/regional/us_kansas_plains.json`, wheat `crop_calendar` entry gains `stage_fractions` 0.48 / 0.22 / 0.22 / 0.08 (status "provisional — from memory of FAO-56 Table 11 winter wheat, October planting; verify"). `water_balance.apply_pack_overrides` applies a pack's per-crop `stage_fractions` for that pack only.

# Farmer Q&A (branch `farmer-chat`, 2026-11-15)

Purely additive: a new endpoint and new models. No existing endpoint or field changed.

**`POST /api/chat`**, request `ChatRequest`:

| Field | Type | Meaning |
|---|---|---|
| `question` | string (1–2000 chars) | The farmer's question. Untrusted: it only ever goes in a user turn. |
| `compare_request` | `CompareRequest` | Same body as `/api/compare`; the comparison is run again (NDVI from the cache only). A bad body gives the same 400 as `/api/compare`. |
| `language` | `"ar"` \| `"en"`, default `"ar"` | Answer language. |
| `history` | list of `{role: "user" \| "assistant", content}`, optional | Earlier turns; only the last 6 are used. |

Response `ChatResponse` (always 200 once the comparison has run):

| Field | Meaning |
|---|---|
| `answer` | The model's answer (ends with a "sources used" line), or `null` when unavailable. |
| `available` | `false` when no API key is configured, the provider errors, or it does not answer within 30 s. |
| `message` | Why the answer is unavailable (else `null`). |
| `sources_used` | Context section names parsed from the answer's "sources used" line. |
| `grounded` | `true` only if every number in the answer appears in the context (rounding allowed). |
| `numbers_not_in_context` | Numbers in the answer not found in the context (empty when grounded). |
| `disclosure` | `"AI-generated answer based on your field's analysis"`. |
| `model` | `"<provider>:<model>"`, e.g. `"openai:gpt-5-mini"`. |
| `elapsed_s` | Time of the model call. |
| `context_tokens` | Approximate size of the context sent (limit 6,000; year plans of lower-ranked rotations are dropped first). |
| `language_clean` | Arabic answers: `false` if Latin-script words remain after one automatic retry (`null` for English). |
| `latin_words` | The Latin-script words left in an Arabic answer. |
| `timeout_retries`, `language_retries` | How many automatic retries were made (each at most 1). |

Behaviour (2026-11-15, second pass): Arabic requests get an "ARABIC GLOSSARY" context section (crop names from `name_ar`, rotation names such as "ذرة شامية ثم برسيم", fixed terms and Arabic section names for the sources line), and answers are limited to 5 short sentences with no Latin letters. Monthly plans are sent only when the question is about timing, months, planting or harvest. A provider timeout is retried once. Default OpenAI model `gpt-6-luna` at reasoning effort `none` (a 400 on the effort falls back to `low`).

