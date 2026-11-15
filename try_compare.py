import json, requests

body = {
    "lat": 30.85, "lon": 31.05,
    "current_rotation": ["rice", "wheat"],
    "water_access": "full_irrigation",
    "priorities": {"water": 1, "soil": 1, "heat_risk": 1},
    "scenario": {"temp_delta_c": 0, "water_cut_pct": 0},
    "area": {"value": 2, "unit": "feddan"},
}
r = requests.post("http://127.0.0.1:8000/api/compare", json=body)
print(r.status_code)
data = r.json()
for rot in data["rotations"]:
    print(rot["rank"], rot["name"], rot["total"], "|", rot["why"])