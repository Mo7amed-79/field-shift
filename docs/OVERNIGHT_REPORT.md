# Overnight report: branch `overnight-us`

Run on 2026-11-15, 02:18 to about 03:05 (UTC+2), unattended. Every decision and failure is in [`OVERNIGHT_LOG.md`](OVERNIGHT_LOG.md); every contract addition is in [`API_CHANGES.md`](API_CHANGES.md) under "Overnight additions". Tests: **175 passed** (139 at the start). `main` was not touched and nothing was pushed.

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 1. Indicative gross revenue (Egypt) | **Done** | `engine/market.py`; a `revenue` block on every rotation and in `all_ranked`; one trade-off sentence in `why`; three USDA FAS reports added to `sources.json`. Not scored. |
| 2. Multi-year rotations + rainfed logic | **Done** | `year` on crop entries, `n_years`, annual means, 12 × N-month plan, fallow years. Rainfed → `water_deficit_mm`, no m3, label "water-deficit exposure", drought-stress wording. One-year results unchanged. |
| 3. US packs (Iowa, Kansas) | **Done** | Both packs, oat Kc, two US demo points. The Kansas point was moved in Phase 4 (see below). |
| 4. Farmer's rotation from USDA CDL | **Done** | `engine/cdl_client.py`, `GET /api/field-history`, `current_rotation` optional in `/api/compare` for US points. Endpoint, response format, projection and legend codes all verified against the service and the official NASS legend file. Added `pyproj`. |
| 5. US yields/prices from NASS QuickStats | **Skipped** | No `.env`, so no `NASS_API_KEY`. Nothing attempted. `.env` is git-ignored. |
| 6. Next-year climate projection | **Done** | `engine/forecast.py`; `forecast_next_year` / `forecast_method` filled, plus back-test; `scenario.use_forecast`. |
| Final | **Done** | Precache run (NDVI capped at 20 min; see below), mock regenerated from a real Tanta run, this report. |

## Tanta regression check (rice → wheat, full irrigation, equal priorities)

Matches the baseline exactly (90.5 / 89.9 / 60.0); `engine/tests/test_regression.py` pins the whole table.

| Rank | Rotation | Irrigation mm/yr | Water | Soil practices | Heat | **Total** | Indicative gross revenue, EGP/feddan/yr | Revenue vs current |
|---|---|---|---|---|---|---|---|---|
| 1 | Maize → Faba bean | 1422.2 | 74.0 | 97.5 | 100.0 | **90.5** | incomplete: no faba bean price | — |
| 2 | Maize → Berseem | 1504.3 | 69.7 | 100.0 | 100.0 | **89.9** | incomplete: no berseem yield or price | — |
| 3 | Soybean → Wheat | 1549.7 | 67.2 | 97.5 | 100.0 | **88.2** | 44,418 | −50.1% |
| 4 | Cotton → Short berseem | 1606.1 | 64.2 | 100.0 | 100.0 | **88.1** | incomplete: no berseem | — |
| 5 | Rice → Berseem | 1753.7 | 56.3 | 100.0 | 100.0 | **85.4** | incomplete: no berseem | — |
| 6 | Rice → Faba bean | 1827.1 | 52.4 | 100.0 | 100.0 | **84.1** | incomplete: no faba bean price | — |
| 7 | Maize → Wheat | 1545.0 | 67.5 | 27.5 | 100.0 | **65.0** | 51,760 | −41.8% |
| 8 | Rice → Wheat (current) | 1872.4 | 50.0 | 30.0 | 100.0 | **60.0** | 89,008 (rice is an upper bound) | 0 |

Example of the new sentence (maize → wheat against rice → wheat): *"It saves 18% irrigation water but indicative gross revenue is about 42% lower — February 2024 procurement prices, national average yields, costs not included; rice revenue is an upper bound (milled-rice price)."*

## US results (rainfed, equal priorities, rotation detected from CDL)

`POST /api/compare` with `current_rotation` omitted.

**Iowa, near Ames (42.00, −93.75).** CDL 2020–2025: Soybeans, Corn, Soybeans, Corn, Soybeans, Corn → **maize → soybean**, confidence high, matches pack rotation `maize_soybean`.

| Rank | Rotation | Years | Water | Deficit mm/yr | Soil | Heat | **Total** |
|---|---|---|---|---|---|---|---|
| 1 | Maize → Soybean (current) | 2 | 50.0 | 228.7 | 85.0 | 100.0 | **78.3** |
| 2 | Maize → Soybean → Oat | 3 | 68.1 | 187.2 | 68.3 | 97.8 | **78.1** |
| 3 | Continuous maize | 1 | 45.8 | 238.3 | 15.0 | 100.0 | **53.6** |

**Kansas, near Hays (38.75, −99.15).** CDL 2020–2025: Sorghum, Fallow/Idle, Winter Wheat, Sorghum, Fallow/Idle, Winter Wheat → **wheat → sorghum → fallow**, confidence high, matches `wheat_sorghum_fallow`.

| Rank | Rotation | Years | Water | Deficit mm/yr | Soil | Heat | **Total** |
|---|---|---|---|---|---|---|---|
| 1 | Wheat + double-crop soybean | 1 | 0.0 | 742.7 | 100.0 | 100.0 | **66.7** |
| 2 | Wheat → Fallow | 2 | 74.3 | 181.4 | 11.2 | 100.0 | **61.8** |
| 3 | Wheat → Sorghum → Fallow (current) | 3 | 50.0 | 239.7 | 11.7 | 100.0 | **53.9** |
| 4 | Continuous wheat | 1 | 0.0 | 362.7 | 22.5 | 100.0 | **40.8** |

The Phase 3 Kansas point (38.85, −99.25) turned out to be alfalfa and then grass/pasture in the CDL, so the demo point was moved to a real wheat → sorghum → fallow field. No revenue for the US packs (Phase 5 skipped).

## Forecast back-test (fit 1991–2020, test 2021–2025, 60 months)

Mean absolute error, trend + seasonal model against the 1991–2020 monthly climatology:

| Point | Temperature, model | Temperature, climatology | ET0 model, mm/month | ET0 climatology, mm/month | Beats climatology? |
|---|---|---|---|---|---|
| Tanta | 0.86 °C | 1.13 °C | 6.42 | 7.05 | yes, both |
| Ludhiana (Punjab) | 1.27 | 1.48 | 13.59 | 19.72 | yes, both |
| Ames (Iowa) | 1.62 | 1.86 | 10.92 | 12.27 | yes, both |
| Hays (Kansas) | 1.45 | 1.61 | 11.84 | 12.98 | yes, both |

The gain is modest (9–31%). It comes mostly from the recent warming offset, not from predicting any particular year. `forecast_method` says "Simple statistical projection, not a weather forecast" and would say "does NOT beat" if it lost (tested). Rain is not projected. With `use_forecast` at Tanta, every rotation's water need rises about 2.3% and the ranking does not change.

## NDVI precache

NDVI_PLACEHOLDER

## Provisional values a human must verify

- **Kc (`data/kc_fao56.json`)**: every crop, from memory of FAO-56 Table 12. New: **oat 0.30 / 1.15 / 0.25** (source TODO).
- **Stage fractions** 0.15 / 0.25 / 0.40 / 0.20 for every crop; they need per-crop FAO-56 Table 11 lengths, especially winter wheat (see below).
- **Regional packs**: every calendar and rotation in `egypt_nile_delta`, `india_punjab`, and the new `us_iowa_corn_belt` (maize May–Oct, soybean May–Oct, oat Apr–Jul; 3 rotations) and `us_kansas_plains` (wheat Oct–Jun, sorghum Jun–Oct, soybean Jun–Oct; 4 rotations). Also both US bounding boxes.
- **`paddy_extra_water_mm`** is still `null` in the Delta and Punjab, so rice water is a lower bound.
- **`data/market_egypt.json`**: the whole file (secondary USDA FAS source, February 2024 pre-devaluation prices). Specifically:
  - Rice uses milled yield × milled price, an upper bound.
  - Faba bean yield is "about 3.4 t/ha, verify".
  - Sugar beet has no yield.
  - Faba bean and berseem have no price.
  - The EG2024-0008 URL is a third-party mirror.
- **`data/cdl_crop_map.json`**: the codes come from the official legend, but the mapping to crop ids is AI-written. Decisions: Cotton → upland_cotton; durum and spring wheat → wheat; cantaloupe and honeydew → melon.
- **CDL inference thresholds**: period ≤ 3 years; high = no mismatch and at least 2 cycles; medium = at most 1 mismatch.
- **Licenses still "TODO verify"**: NASA POWER, MOD13Q1, SoilGrids, FAO-56, GAEZ, the three USDA FAS reports and the CDL. ECOCROP is "UNVERIFIED". The CDL and MOD13Q1 product citations are TODO.
- **US demo points**: chosen overnight from the CDL; confirm they are suitable for the demo.

## What looks agronomically wrong

1. **Winter wheat Kc timing (Kansas)**: with the default stage fractions, wheat's Kc peak falls in Feb–Apr, when Kansas wheat is dormant. The real mid-season is May–June. Kansas wheat deficits are unreliable until stage lengths are per crop.
2. **Rainfed fallow arithmetic**: a fallow year counts 0 mm of deficit, so wheat → fallow beats continuous wheat on water simply by having fewer crop years. The real benefit of fallow (stored soil moisture for the next crop) is not modelled, because there is no soil water balance.
3. **Double-crop soybean ranks first at Hays** (soil 100) despite a 743 mm/yr deficit, four times that of wheat → fallow. Double-cropping is rare in semi-arid west-central Kansas; one bounding box covers both the dry west and the wetter east.
4. **The diversity formula penalises longer rotations**: maize → soybean → oat (soil 68) scores below maize → soybean (85), because (families − 1)/(crops − 1) drops when a third crop repeats a family (oat and maize are both Poaceae).
5. **Rice revenue is inflated** (milled basis), which makes rice → wheat look far ahead on revenue: maize → wheat −42%, soybean → wheat −50%.
6. **The two best Delta alternatives get no revenue comparison**: maize → faba bean and maize → berseem have no price.
7. **Heat scores 100 almost everywhere** (Delta, Iowa, Kansas), so the criterion barely discriminates. It only bites for oat in an Iowa July (97.8) and for rice under the 2026 projection.
8. **Punjab POWER shows cooling**: −0.35 °C per decade, and ET0 falling. That is plausible (irrigation, aerosols) but needs checking against station data.
9. **Rainfed rice** (allowed by the arithmetic when a user picks rainfed in the Delta) is meaningless; there is no guard.

## Merge or discard

To merge into `main` (local only; push when you are ready):

```bash
cd /home/mo-sayed/Documents/NACA
git checkout main
git merge --no-ff overnight-us -m "Merge overnight-us: revenue, multi-year/rainfed, US packs, CDL field history, forecast"
pytest
git push origin main        # only when you are happy with it
```

To discard the branch entirely:

```bash
cd /home/mo-sayed/Documents/NACA
git checkout main
git branch -D overnight-us
```

`pyproj` was installed into `.venv` for Phase 4. After discarding, you can remove it with `pip uninstall pyproj` (harmless if left).
