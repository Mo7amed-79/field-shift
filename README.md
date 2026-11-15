# Field Shift

NASA Space Apps 2026 project.

Field Shift is a decision-support web tool for farmers: a **global engine, with the Nile Delta as our demo case**. The farmer clicks their field on a map, enters their current crop rotation, water access and priorities (water, soil-health practices, heat-stress exposure), and optionally a field area and a climate scenario. Field Shift **compares the farmer's rotation with known alternatives**; it does not invent rotations. Where a regional pack exists (Nile Delta, Punjab), the alternatives are that region's typical rotations; elsewhere the tool runs in a clearly labelled, low-confidence *exploratory mode*. Each rotation is scored on irrigation need (FAO-56 crop water use from NASA POWER climate), soil-health practices (a proxy) and heat-stress exposure (a proxy) under the scenario, with the numbers behind every score, a month-by-month plan and a plain-language explanation of what is and is not assessed.

## Architecture

```
  Frontend (React + Vite)
        │  HTTP / JSON  (contract: backend/schemas.py)
        ▼
  Backend (FastAPI, backend/main.py)
        │  plain Python calls
        ▼
  Engine (engine/*, pure functions)
    clients:   power_client · soil_client · ndvi_client · cdl_client (US)
    analysis:  climate · forecast · suitability · water_balance · scoring
               market · planner · explain · regional · rotation_builder (exploratory)
    pipeline:  compares the current rotation with pack rotations
        │
        ▼
  Data (data/crops.json, data/raw/, data/cache/)
        ▲
        │  point queries, cached on first fetch
  NASA POWER  ·  MODIS MOD13Q1 (ORNL DAAC)  ·  ISRIC SoilGrids*  ·  USDA CDL*
                                              (* non-NASA)
```

## Folder guide

| Folder | What's in it | Owner |
|---|---|---|
| `engine/` | Pure-Python analysis: data clients (POWER, SoilGrids, MODIS NDVI, USDA Cropland Data Layer), climate aggregates, next-year projection, indicative gross revenue, crop suitability, rotation builder, water balance, scoring, planner, explanations. No web code. | Data / AI |
| `backend/` | FastAPI app and the Pydantic request/response contract. | Full-stack dev |
| `frontend/` | React app (Vite, to be created). `frontend/mock/compare.json` is sample `/api/compare` output for UI work. | Full-stack dev |
| `data/crops.json` | Crop catalogue with requirement envelopes, generated from FAO ECOCROP by `scripts/build_crops.py`. Do not edit by hand; Kc values are still `null`. | Research |
| `data/rotation_rules.json` | Hard and soft rotation rules with weights; each needs a reference. | Research |
| `data/regional/` | Regional packs (local knowledge): bounding box, default water access and crop calendar. `egypt_nile_delta.json`, `india_punjab.json`, `us_iowa_corn_belt.json` and `us_kansas_plains.json` (rainfed, multi-year rotations) carry the candidate rotations; all entries are provisional until a researcher verifies them. | Research |
| `data/demo_points.json` | Demo locations (farmland) with their water access. | Research |
| `data/kc_fao56.json` | Crop coefficients (Kc ini/mid/end) per crop, provisional until checked against FAO-56 Table 12. | Research |
| `data/market_egypt.json` | Egypt yields and February 2024 prices (USDA FAS reports) for indicative gross revenue; provisional. | Research |
| `data/cdl_crop_map.json` | USDA CDL class codes (official legend, `data/raw/cdl_codes_names.xlsx`) mapped to crop ids; mapping unverified. | Research |
| `data/sources.json` | Every external dataset, its license and access date. | Research |
| `data/raw/` | Raw downloads (e.g. the ECOCROP CSV). See `data/raw/README.md`. | Research |
| `data/cache/` | Cached API responses (git-ignored). | — |
| `scripts/` | `precache_demo.py` fills `data/cache/` with POWER and SoilGrids data for the demo points; `build_crops.py` builds `data/crops.json` from ECOCROP; `demo_report.py` prints rankings and rotations for the demo points. | Data / AI |
| `docs/` | Write-ups and `API_CHANGES.md` (contract changes for the frontend). | Everyone |

## Running the backend

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

The API is then at http://127.0.0.1:8000 (interactive docs at `/docs`). Endpoints:

- `GET /api/health`
- `GET /api/crops`
- `GET /api/sources`
- `GET /api/location?lat=&lon=` (real SoilGrids soil; `seasons_detected` empty until NDVI is added)
- `GET /api/climate?lat=&lon=` (real NASA POWER, 1991–2025)
- `GET /api/field-history?lat=&lon=` (contiguous US only: yearly USDA Cropland Data Layer crops and the detected rotation)
- `GET /api/ndvi?lat=&lon=` (MODIS MOD13Q1 NDVI from ORNL DAAC; returns `loading` and fetches in the background if not cached — a fetch takes several minutes)
- `POST /api/compare` (real: POWER → climate → regional pack → water → scoring → plan → explanation; see `docs/API_CHANGES.md`). `current_rotation` may be omitted for US points (detected from the CDL).

Run the tests from the project root with `pytest`.

## Data sources & licenses

Every external dataset we use (NASA and non-NASA, flagged with `non_nasa`) is listed in [`data/sources.json`](data/sources.json), with its URL, what we use it for, its license and the access date. Add a source there before using it in the engine.

`frontend/mock/compare.json` is real engine output for the demo point near Tanta; its `run_info` gives the run date.

## Use of AI (SI) tools

This project used AI tools. Code was generated and refactored with Claude (Anthropic) via Claude Code, and Claude chat supported research and planning. The team framed the problem, chose the data sources, methods and agronomic rules, and reviews and tests all AI-generated code and checks results against published references.

See [AI_USAGE.md](AI_USAGE.md) for the full disclosure, including which folders are AI-generated, AI-assisted or human-written. The same statement is in the `ai_disclosure` field of `data/sources.json`, in the API description at `/docs`, and as a header comment in every Python file.

## License

Apache License 2.0. See [LICENSE](LICENSE).
