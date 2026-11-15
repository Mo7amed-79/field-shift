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
