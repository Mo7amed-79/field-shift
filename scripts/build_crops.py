# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Build data/crops.json from the FAO ECOCROP CSV (OpenCLIM/ecocrop copy).

Input:  data/raw/EcoCrop_DB.csv (Windows-1252 encoded; one row per species).
Output: data/crops.json for the curated crop list below, matched by exact
        scientific name. Nothing is filled by hand: crops not found or with
        missing envelope fields are reported, not patched.

Run from the project root: python scripts/build_crops.py

ECOCROP columns used (meanings from Ecocrop_variable_lookup.csv):
  EcoPortCode, ScientificName, FAMNAME, CAT (reference only), LISPA,
  TMIN, TOPMN, TOPMX, TMAX, KTMP          temperature, °C
  RMIN, ROPMN, ROPMX, RMAX                rainfall, mm (over the crop cycle,
                                           as used by OpenCLIM/ecocrop)
  PHMIN, PHOPMN, PHOPMX, PHMAX            soil pH
  GMIN, GMAX                              crop cycle, days
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "EcoCrop_DB.csv"
OUT_PATH = ROOT / "data" / "crops.json"
SOURCE = "FAO ECOCROP via OpenCLIM/ecocrop"

# Curated list: (id, scientific name, English name, Arabic name).
# Arabic names were written with AI help and are not yet reviewed by a native speaker.
CURATED = [
    ("rice", "Oryza sativa", "Rice", "أرز"),
    ("wheat", "Triticum aestivum", "Wheat", "قمح"),
    ("maize", "Zea mays", "Maize", "ذرة شامية"),
    ("berseem", "Trifolium alexandrinum", "Berseem (Egyptian clover)", "برسيم"),
    ("cotton", "Gossypium barbadense", "Cotton (Egyptian / Pima)", "قطن"),
    ("upland_cotton", "Gossypium hirsutum", "Upland cotton", "قطن أبلند"),
    ("faba_bean", "Vicia faba", "Faba bean", "فول بلدي"),
    ("barley", "Hordeum vulgare", "Barley", "شعير"),
    ("sorghum", "Sorghum bicolor", "Sorghum", "ذرة رفيعة"),
    ("pearl_millet", "Pennisetum glaucum", "Pearl millet", "دخن"),
    ("soybean", "Glycine max", "Soybean", "فول الصويا"),
    ("groundnut", "Arachis hypogaea", "Groundnut (peanut)", "فول سوداني"),
    ("chickpea", "Cicer arietinum", "Chickpea", "حمص"),
    ("lentil", "Lens culinaris", "Lentil", "عدس"),
    ("common_bean", "Phaseolus vulgaris", "Common bean", "فاصوليا"),
    ("cowpea", "Vigna unguiculata", "Cowpea", "لوبيا"),
    ("pea", "Pisum sativum", "Pea", "بازلاء"),
    ("alfalfa", "Medicago sativa", "Alfalfa (lucerne)", "برسيم حجازي"),
    ("sunflower", "Helianthus annuus", "Sunflower", "عباد الشمس"),
    ("sesame", "Sesamum indicum", "Sesame", "سمسم"),
    ("rapeseed", "Brassica napus", "Rapeseed (canola)", "كانولا"),
    ("flax", "Linum usitatissimum", "Flax (linseed)", "كتان"),
    ("sugar_beet", "Beta vulgaris", "Sugar beet", "بنجر السكر"),
    ("potato", "Solanum tuberosum", "Potato", "بطاطس"),
    ("sweet_potato", "Ipomoea batatas", "Sweet potato", "بطاطا"),
    ("tomato", "Solanum lycopersicum", "Tomato", "طماطم"),
    ("onion", "Allium cepa", "Onion", "بصل"),
    ("garlic", "Allium sativum", "Garlic", "ثوم"),
    ("oat", "Avena sativa", "Oat", "شوفان"),
    ("melon", "Cucumis melo", "Melon", "شمام"),
]

# ECOCROP column -> our field. All are required except temp_kill_c (KTMP).
ENVELOPE = {
    "TMIN": "temp_abs_min_c", "TOPMN": "temp_opt_min_c", "TOPMX": "temp_opt_max_c", "TMAX": "temp_abs_max_c",
    "RMIN": "rain_abs_min_mm", "ROPMN": "rain_opt_min_mm", "ROPMX": "rain_opt_max_mm", "RMAX": "rain_abs_max_mm",
    "PHMIN": "ph_abs_min", "PHOPMN": "ph_opt_min", "PHOPMX": "ph_opt_max", "PHMAX": "ph_abs_max",
    "GMIN": "cycle_days_min", "GMAX": "cycle_days_max",
}
OPTIONAL = {"KTMP": "temp_kill_c"}

# ECOCROP uses traditional family names; these are the conserved alternative
# names for the same families (ICN Art. 18.5), so mapping them changes nothing.
FAMILY_SYNONYMS = {
    "Gramineae": "Poaceae", "Leguminosae": "Fabaceae", "Cruciferae": "Brassicaceae",
    "Compositae": "Asteraceae", "Umbelliferae": "Apiaceae", "Labiatae": "Lamiaceae",
    "Palmae": "Arecaceae", "Guttiferae": "Clusiaceae",
}
LEGUME_FAMILIES = {"Fabaceae", "Leguminosae"}

# Hand-reviewed categories (by scientific name), used by the rotation rules.
# ECOCROP's CAT lists uses, not crop types (it has no oilseed or fiber class),
# so it is kept only as "ecocrop_category" for reference.
REVIEWED_CATEGORIES = {
    "cereal": ["Oryza sativa", "Triticum aestivum", "Zea mays", "Hordeum vulgare", "Sorghum bicolor",
               "Pennisetum glaucum", "Avena sativa"],
    "legume": ["Vicia faba", "Glycine max", "Arachis hypogaea", "Cicer arietinum", "Lens culinaris",
               "Phaseolus vulgaris", "Vigna unguiculata", "Pisum sativum"],
    "forage_legume": ["Trifolium alexandrinum", "Medicago sativa"],
    "oilseed": ["Helianthus annuus", "Sesamum indicum", "Brassica napus", "Linum usitatissimum"],
    "fiber": ["Gossypium barbadense", "Gossypium hirsutum"],
    "root_tuber": ["Beta vulgaris", "Solanum tuberosum", "Ipomoea batatas"],
    "vegetable": ["Solanum lycopersicum", "Allium cepa", "Allium sativum", "Cucumis melo"],
}
CATEGORY_BY_NAME = {name: cat for cat, names in REVIEWED_CATEGORIES.items() for name in names}

# Current scientific name -> name used in ECOCROP, for species ECOCROP lists
# under an older name.
ALIASES = {"Solanum lycopersicum": "Lycopersicon esculentum"}


def _num(value: str) -> float | None:
    value = value.strip()
    if value in ("", "NA"):
        return None
    return float(value)


def build() -> tuple[list[dict], list[str], list[str], list[str]]:
    with CSV_PATH.open(encoding="cp1252", newline="") as f:
        rows = {r["ScientificName"]: r for r in csv.DictReader(f)}

    crops, missing, problems, aliases_used = [], [], [], []
    for crop_id, sci, name_en, name_ar in CURATED:
        ecocrop_name = sci
        row = rows.get(sci)
        if row is None and sci in ALIASES:
            ecocrop_name = ALIASES[sci]
            row = rows.get(ecocrop_name)
            if row is not None:
                aliases_used.append(f"{crop_id}: {sci} -> {ecocrop_name} (ECOCROP id {row['EcoPortCode']})")
        if row is None:
            missing.append(f"{crop_id} ({sci}): not found by exact scientific name or alias")
            continue

        family_ecocrop = row["FAMNAME"].split(":")[-1].strip()
        family = FAMILY_SYNONYMS.get(family_ecocrop, family_ecocrop)
        entry = {
            "id": crop_id,
            "name_en": name_en,
            "name_ar": name_ar,
            "name_ar_reviewed": False,
            "scientific_name": sci,
            "ecocrop_name": ecocrop_name,
            "family": family,
            "family_ecocrop": family_ecocrop,
            "is_legume": family in LEGUME_FAMILIES,
            "category": CATEGORY_BY_NAME.get(sci),
            "ecocrop_category": row["CAT"] or None,
            "life_span": row["LISPA"] or None,
        }
        gaps = []
        for col, field in ENVELOPE.items():
            value = _num(row[col])
            if col == "GMIN" and value is not None and value <= 0:
                value = None
            entry[field] = value
            if value is None:
                gaps.append(col)
        for col, field in OPTIONAL.items():
            entry[field] = _num(row[col])
        entry.update({"kc_ini": None, "kc_mid": None, "kc_end": None})
        entry["complete"] = not gaps
        entry["source"] = SOURCE
        entry["ecocrop_id"] = int(row["EcoPortCode"])

        if gaps:
            problems.append(f"{crop_id} ({sci}): missing {', '.join(gaps)}")
        for lo, mid_lo, mid_hi, hi, label in (
            ("TMIN", "TOPMN", "TOPMX", "TMAX", "temperature"),
            ("RMIN", "ROPMN", "ROPMX", "RMAX", "rainfall"),
            ("PHMIN", "PHOPMN", "PHOPMX", "PHMAX", "pH"),
        ):
            vals = [_num(row[c]) for c in (lo, mid_lo, mid_hi, hi)]
            if None not in vals and vals != sorted(vals):
                problems.append(f"{crop_id}: {label} envelope not ordered {vals}")
        if entry["category"] is None:
            problems.append(f"{crop_id}: not in the reviewed category map")
        crops.append(entry)
    return crops, missing, problems, aliases_used


def main() -> int:
    crops, missing, problems, aliases_used = build()
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(crops, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Built {len(crops)} crops -> {OUT_PATH.relative_to(ROOT)}")
    print("  " + ", ".join(c["id"] for c in crops))
    print(f"Aliases used ({len(aliases_used)}):")
    for a in aliases_used:
        print("  " + a)
    print(f"Missing ({len(missing)}):")
    for m in missing:
        print("  " + m)
    print(f"Data notes ({len(problems)}):")
    for p in problems:
        print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
