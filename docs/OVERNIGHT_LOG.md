# Overnight log (branch `overnight-us`)

Times are local (UTC+2).

## 2026-11-15 02:18 — Start
- Branch `overnight-us` created from `main` (91f5562). Baseline: 139 tests pass.
- Tanta rice → wheat baseline confirmed: maize → faba bean 90.5, maize → berseem 89.9, current 60.0.
  Locked in `engine/tests/test_regression.py` (also pins the full `all_ranked` order and current water need 1872.4 mm).
- No `.env` file exists in the project, so `NASS_API_KEY` is not set: **Phase 5 will be skipped** (as instructed).

## 02:35 — Phase 1: indicative gross revenue (Egypt)
- `engine/market.py`: `load_market(pack)` reads the table named by the pack's new `market_table` key (added `"market_table": "market_egypt.json"` to the Nile Delta pack; Punjab has none → `revenue.available = false`).
- Decision: added `"basis_short"` to `data/market_egypt.json` (the wording used in explanations: "February 2024 procurement prices, national average yields, costs not included"). No numbers changed.
- Per feddan uses the file's `feddan_to_hectare` 0.42; acre = 0.4047 ha (unit definition).
- Revenue for Tanta (EGP per feddan per year): rice → wheat 89,008; maize → wheat 51,760 (−41.8%); soybean → wheat 44,418 (−50.1%). Every rotation with berseem or faba bean is incomplete (no price).
- Decision: `not_assessed` used to say "crop prices and income". "income" is now banned, so it reads "production costs and current market prices" where a market table exists and "crop prices and revenue" elsewhere. This is the only text change to existing Nile Delta/Punjab output; scores and totals are unchanged (regression test green).
- Added the three USDA FAS reports to `data/sources.json` (EG2024-0008 URL is the third-party mirror recorded in the market file; official GAIN link TODO).
- **Agronomically suspicious:** rice revenue uses milled-rice yield × milled-rice price, which includes the milling margin; it is an upper bound and makes rice → wheat look far ahead of every alternative on revenue. A paddy farm-gate price is needed.
- The two top-ranked Delta alternatives (maize → faba bean, maize → berseem) cannot get a revenue comparison, so the headline trade-off sentence is "no price for Faba bean / Berseem".

## 03:05 — Phase 2: multi-year rotations and rainfed water logic
- Model: each year of an N-year cycle is treated like the old one-year 12-month frame (windows may wrap, shared transition months are split 50/50 within that year). A year with no crop entries is a fallow year; packs give its length with `"n_years"`.
- Water: `water_balance.rotation_water_need_cycle` runs the old one-year balance per year; the rotation figure is the mean over the cycle (fallow year = 0 mm). One-year rotations go through the same code path and give identical numbers (regression test green).
- Heat — decision: the rotation heat score stays the window-length-weighted mean over every crop in the cycle. This equals the mean of the per-year scores weighted by cropped months; a fallow year has no crop to expose, so it is not averaged in as 100.
- Soil: legume and family diversity over every crop in the cycle; ground cover = cropped months / (12 × N). Fallow months are listed in `fallow_months`.
- Rainfed: same quantity (ETc − 0.8 × rain) reported as `water_deficit_mm`; irrigation, saving and m3 fields are null; the water budget (`water_cut_pct`) is ignored. The score formula is unchanged: clip(50 + % less deficit than the current rotation).
- Decision: the farmer can enter `"fallow"` in `current_rotation`. It matches a pack rotation by cycle signature (e.g. `["wheat", "fallow"]`). Multi-year matches ignore order only when the multiset of entries is the same. Exploratory mode ignores fallow entries and warns.
- **Agronomically questionable:** in rainfed mode a fallow year has 0 deficit, so wheat → fallow scores better on water-deficit exposure than continuous wheat simply because half the years have no crop. That is arithmetic, not stored soil moisture: there is no soil water balance, so the real benefit of fallow (stored moisture for the next wheat crop) is not modelled.

## 03:25 — Phase 3: US regional packs (rainfed)
- Added `data/regional/us_iowa_corn_belt.json` and `data/regional/us_kansas_plains.json` with your calendars and rotations. Every calendar entry and rotation is `"status": "provisional — unverified"`, `"source": "TODO"`. Fallow years use `"n_years"`.
- Added oat Kc 0.30 / 1.15 / 0.25 (provisional — unverified, source TODO). Oat was already in `crops.json` (ECOCROP *Avena sativa*).
- Decision on demo points (none existed for the US): **near Ames, Iowa (42.00, −93.75)** and **near Hays, Kansas (38.85, −99.25)**; Hays is in the semi-arid wheat–fallow / wheat–sorghum–fallow area of west-central Kansas. SoilGrids returned clay loam and silty clay loam (farmland, not urban). POWER has 420 months, no gaps. These responses are also saved as test fixtures.
- Iowa (equal weights, current maize → soybean): maize → soybean 78.3, maize → soybean → oat 78.1, continuous maize 53.6. Kansas (current wheat → fallow): wheat + double-crop soybean 66.7, wheat → fallow 53.7, wheat → sorghum → fallow 43.2, continuous wheat 40.8.
- **Agronomically wrong or questionable:**
  - Maize → soybean → oat scores *lower* on soil-health practices (68) than maize → soybean (85). The frozen diversity formula (distinct families − 1)/(crops − 1) penalises a third crop from a family already present (oat and maize are both Poaceae), although a 3-year rotation is normally seen as more diverse.
  - Winter wheat (Oct–Jun) uses the default stage fractions, so its Kc peak falls in Feb–Apr, when Kansas wheat is dormant (mean temperature −1 to 7 °C). The real mid-season is May–June. Wheat water figures for Kansas are therefore unreliable until per-crop stage lengths (FAO-56 Table 11, winter wheat) are added.
  - Wheat + double-crop soybean ranks first in Kansas on soil practices, despite a water deficit 4× that of wheat → fallow. At Hays (about 650 mm rain/yr, ET0 about 1,330 mm) double-cropping is rare; it is mostly practised in wetter eastern Kansas. The pack covers both zones with one bbox.
  - Heat score is 100 for almost everything in both US packs (oat 97.8), so it does not discriminate there either.
  - Rainfed rice in the Delta (tested with synthetic input only) is meaningless agronomically, but the arithmetic is allowed.

## 03:50 — Phase 4: farmer's rotation from the USDA Cropland Data Layer
- Verified the endpoint against the CropScape developer examples page (https://nassgeodata.gmu.edu/CropScape/devhelp/getexamples.html): `GetCDLValue?year=&x=&y=`, coordinates in "USA Contiguous Albers Equal Area Conic (USGS version)". The first attempt to load the help page timed out (60 s); a retry worked.
- Real response format: XML `<Result>{x: …, y: …, value: 1, category: "Corn", color: "#FFD400"}</Result>`, which is not JSON (unquoted keys), so it is parsed with a regex. Year 2026 returns the fault "The year must be between 1997 and 2025", which is used to find the latest year (2025).
- Projection: EPSG:5070 (NAD83 / Conus Albers: lat_0 23, lon_0 −96, parallels 29.5/45.5). Added `pyproj==3.8.0` to the venv and requirements. Check: the doc example x/y maps back to 38.87 N, −77.84 W (northern Virginia), and the service returned Corn there.
- Legend: the official NASS file `cdl_codes_names.xlsx` was downloaded from the CDL FAQ page and saved to `data/raw/`. `data/cdl_crop_map.json` maps 40 CDL classes to our crop ids (including double-crop classes as two crops in one year, and 61 Fallow/Idle → fallow) and lists 24 non-crop classes. Every code was asserted against the legend file. The mapping itself is marked unverified. Decisions: Cotton → `upland_cotton`; durum and spring wheat → `wheat` (noted); cantaloupe and honeydew → `melon`.
- Inference: shortest period p ∈ {1, 2, 3} with the fewest year-to-year mismatches over 6 years. The cycle is the most recent p years, rotated to match a pack rotation if one exists. Confidence: high = no mismatches and at least 2 cycles seen and every year modelled; medium = at most 1 mismatch; else low. Over half non-crop years → `not_cropland`.
- **Demo point changed:** the Phase 3 Kansas point (38.85, −99.25) is alfalfa then grass/pasture in the CDL, so it is not a wheat field. Searching a grid around Hays found **38.75, −99.15**, with Sorghum, Fallow, Winter Wheat, Sorghum, Fallow, Winter Wheat for 2020–2025: a textbook wheat → sorghum → fallow field. `demo_points.json`, the test fixtures and the US tests now use it. The two Phase 3 fixtures for the old point were removed; same POWER grid cell, so the climate numbers are identical. Iowa (42.00, −93.75): Soybeans/Corn alternating 2020–2025 → maize → soybean, high confidence.
- Timing: the first Iowa fetch took 43 s (7 requests; the service is sometimes slow); Kansas 11 s. One request timed out once during the exploration grid. Results are cached in `data/cache/cdl_*.json` (git-ignored); fixtures are in `engine/tests/fixtures/`.
- **Caveat:** CDL assigns winter wheat to its harvest year, so "fallow" years in a wheat → sorghum → fallow sequence match our frame model (wheat yr 1, sorghum yr 2, fallow yr 3), but sowing happens in the previous calendar year. Only one 30 m pixel is read; edge pixels can be misclassified.
