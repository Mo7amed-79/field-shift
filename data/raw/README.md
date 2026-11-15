# data/raw

Raw downloads, kept unmodified.

| File | Origin |
|---|---|
| `EcoCrop_DB.csv` | FAO ECOCROP database, as copied in the OpenCLIM/ecocrop repository (commit f13577f, downloaded 2026-11-14). Windows-1252 encoded. |
| `Ecocrop_variable_lookup.csv` | Column meanings for the CSV, from the same repository. |

`scripts/build_crops.py` turns `EcoCrop_DB.csv` into `data/crops.json`.

**License:** UNVERIFIED. The OpenCLIM repo has no LICENSE file; its README states the Open Government Licence, but the data originates from FAO. The team must confirm FAO's terms and record them in `data/sources.json` before submission. See the "FAO ECOCROP" entry there.
