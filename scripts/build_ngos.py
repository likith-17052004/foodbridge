"""Build the NGO master table and the 20-NGO demo shortlist.

Inputs (real):
  data/raw/osm_bengaluru_social.json        OpenStreetMap care homes / shelters
  data/processed/darpan_geocoded.csv        NGO Darpan (NITI Aayog) shortlist, geocoded
  data/raw/opencity/bbmp_wards_2015_198.kml 198 ward polygons
  data/raw/opencity/bengaluru_ward_census_2011.csv

Simulated (prefixed sim_ in the output, deterministic per NGO):
  sim_capacity_meals, sim_daily_meal_gap, sim_accepts_nonveg, sim_has_fridge, sim_has_vehicle

Outputs:
  data/processed/ngos_master.csv
  data/processed/ngos_demo.csv   (20 NGOs, spread across the city)
"""
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data/raw", ROOT / "data/processed"

# OSM entries that are not meal receivers (private hospitals called "nursing home", umbrella bodies, etc.)
OSM_EXCLUDE = {"Lakshmi Nursing Home", "Kruthika Nursing Home", "Pragathi Nursing Home",
               "Ramakrishna Nursing Home, Bengaluru Urban", "Aashirvad Nursing Home", "Pushpa Nursing Home",
               "Indira Canteen", "Indian Red Cross Society", "The Churches' Council for Child and Youth Care",
               "Athulya Senior Care Services", "Sree Ramaseva Mandali Foundation", "Sumanahalli Skill Development Center", "Old age home"}
OSM_RECEIVER_HINT = r"home|ashram|orphan|shelter|charity|mane|dhama|sisters|mission|hospice|care|sumanahalli|seva|vrud"

CATEGORY_RULES = [  # first match wins
    ("children", r"child|orphan|shishu|bosco|boys|girls|bala|kids"),
    ("elderly", r"aged|elder|senior|old age|vrud|vriddh|eventide|little sisters|hospice"),
    ("disabled", r"disab|cheshire|differently"),
    ("homeless", r"homeless|shelter|beena ville"),
    ("women", r"women|liza"),
]
# typical meals/day served, by category (used to simulate capacity)
CAPACITY_RANGE = {"children": (40, 150), "elderly": (25, 90), "disabled": (30, 100), "homeless": (40, 120),
                  "women": (20, 60), "community": (60, 250)}


# ---------------- wards ----------------
def load_wards():
    kml = (RAW / "opencity/bbmp_wards_2015_198.kml").read_text()
    census = pd.read_csv(RAW / "opencity/bengaluru_ward_census_2011.csv")
    census["Ward Name"] = census["Ward Name"].str.strip().str.lower()
    wards = []
    for i, pm in enumerate(re.findall(r"<Placemark>(.*?)</Placemark>", kml, re.S), 1):
        num = int(re.search(r"<name>Ward (\d+)</name>", pm).group(1)) if re.search(r"<name>Ward (\d+)</name>", pm) else i
        name = re.search(r"<Data name='Ward Name'>\s*<value>(.*?)</value>", pm, re.S).group(1).strip()
        coords = re.search(r"<coordinates>(.*?)</coordinates>", pm, re.S).group(1).split()
        poly = [(float(c.split(",")[1]), float(c.split(",")[0])) for c in coords]  # (lat, lon)
        wards.append({"ward_no": num, "ward_name": name, "poly": poly, "area_km2": polygon_area_km2(poly)})
    w = pd.DataFrame(wards)
    w = w.merge(census, left_on="ward_no", right_on="Ward Num", how="left")
    w["ward_population"] = w["Population"]
    w["ward_density_per_km2"] = (w["Population"] / w["area_km2"]).round()
    w["ward_scst_share"] = ((w["SC Population"] + w["ST Population"]) / w["Population"]).round(3)
    return w[["ward_no", "ward_name", "poly", "area_km2", "ward_population", "ward_density_per_km2", "ward_scst_share"]]


def polygon_area_km2(poly):
    kx, ky = 111.0 * 0.974, 111.0
    pts = [(lon * kx, lat * ky) for lat, lon in poly]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def point_in_poly(lat, lon, poly):
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def find_ward(lat, lon, wards):
    for _, w in wards.iterrows():
        if point_in_poly(lat, lon, w["poly"]):
            return w
    return None


# ---------------- sources ----------------
def load_osm():
    els = json.loads((RAW / "osm_bengaluru_social.json").read_text())["elements"]
    rows = []
    for e in els:
        t = e.get("tags", {})
        name = t.get("name")
        if not name or name in OSM_EXCLUDE:
            continue
        is_facility = t.get("amenity") in ("social_facility", "food_bank") or t.get("social_facility")
        is_ngo_home = t.get("office") == "ngo" and re.search(OSM_RECEIVER_HINT, name, re.I)
        if not (is_facility or is_ngo_home):
            continue
        lat, lon = (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
        addr = ", ".join(t[k] for k in ("addr:housenumber", "addr:street", "addr:suburb", "addr:postcode") if t.get(k))
        hint = " ".join(filter(None, [name, t.get("social_facility", ""), t.get("social_facility:for", "")]))
        rows.append({"source": "openstreetmap", "source_id": f"osm/{e['type']}/{e['id']}", "name": name,
                     "address": addr, "lat": lat, "lon": lon, "geo_confidence": "high", "geo_source": "osm",
                     "phone": t.get("phone", t.get("contact:phone", "")), "category_hint": hint})
    return pd.DataFrame(rows)


def load_darpan():
    d = pd.read_csv(OUT / "darpan_geocoded.csv", dtype={"pincode": str})
    d = d[d["lat"].notna()]
    return pd.DataFrame({
        "source": "ngo_darpan", "source_id": d["darpan_id"], "name": d["name"].str.title(),
        "address": d["address"], "lat": d["lat"], "lon": d["lon"],
        "geo_confidence": d["geo_confidence"], "geo_source": d["geo_source"], "phone": d.get("phone", ""),
        "category_hint": d["name"] + " " + d["beneficiary_sectors"].fillna(""),
    })


def categorize(hint):
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, hint, re.I):
            return cat
    return "community"


def dedupe(df):
    """Drop entries < 200 m apart that share a distinctive word (OSM often maps the same home twice)."""
    keep, seen = [], []
    for _, r in df.iterrows():
        words = set(re.findall(r"[a-z]{4,}", r["name"].lower())) - {"home", "trust", "foundation", "society", "care"}
        dup = any(abs(r.lat - s.lat) * 111 < 0.2 and abs(r.lon - s.lon) * 108 < 0.2 and words & sw for s, sw in seen)
        if not dup:
            keep.append(r)
            seen.append((r, words))
    return pd.DataFrame(keep)


def seeded(key, lo, hi):
    """Deterministic pseudo-random int in [lo, hi] from a string key."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return lo + h % (hi - lo + 1)


def main():
    wards = load_wards()
    ngos = pd.concat([load_osm(), load_darpan()], ignore_index=True)
    # prefer OSM (exact coordinates) when the same NGO appears in both
    ngos = dedupe(ngos.sort_values("source", key=lambda s: s.map({"openstreetmap": 0, "ngo_darpan": 1})))
    ngos["category"] = ngos["category_hint"].apply(categorize)

    ward_cols = ["ward_no", "ward_name", "ward_population", "ward_density_per_km2", "ward_scst_share"]
    matched = [find_ward(r.lat, r.lon, wards) for r in ngos.itertuples()]
    for c in ward_cols:
        ngos[c] = [m[c] if m is not None else None for m in matched]

    # ---- simulated operational fields (labelled sim_) ----
    sid = ngos["source_id"].astype(str)
    ngos["sim_capacity_meals"] = [seeded(k, *CAPACITY_RANGE[c]) for k, c in zip(sid, ngos["category"])]
    ngos["sim_daily_meal_gap"] = [round(cap * seeded(k + "gap", 10, 45) / 100)
                                  for k, cap in zip(sid, ngos["sim_capacity_meals"])]
    ngos["sim_accepts_nonveg"] = [seeded(k + "nv", 0, 9) >= 3 for k in sid]  # ~70% accept non-veg
    ngos["sim_has_fridge"] = [seeded(k + "fr", 0, 9) >= 4 for k in sid]
    ngos["sim_has_vehicle"] = [seeded(k + "vh", 0, 9) >= 6 for k in sid]

    # ---- need score 0-100: real ward vulnerability + simulated meal gap ----
    dens = ngos["ward_density_per_km2"].rank(pct=True).fillna(0.5)
    scst = ngos["ward_scst_share"].rank(pct=True).fillna(0.5)
    gap = (ngos["sim_daily_meal_gap"] / ngos["sim_capacity_meals"]).rank(pct=True)
    ngos["need_score"] = (100 * (0.5 * gap + 0.25 * dens + 0.25 * scst)).round(1)

    ngos.insert(0, "ngo_id", [f"NGO{i:03d}" for i in range(1, len(ngos) + 1)])
    ngos = ngos.drop(columns=["category_hint"])
    OUT.mkdir(parents=True, exist_ok=True)
    ngos.to_csv(OUT / "ngos_master.csv", index=False)

    # ---- demo shortlist: 20 NGOs, best geo confidence first, spread over the city ----
    pool = ngos[ngos["geo_confidence"].isin(["high", "medium"]) & ngos["ward_no"].notna()].copy()
    pool["rank"] = pool["geo_confidence"].map({"high": 0, "medium": 1})
    pool = pool.sort_values(["rank", "need_score"], ascending=[True, False])
    demo, used = [], []
    for min_km in (2.0, 1.0, 0.0):  # relax spacing until we have 20
        for _, r in pool.iterrows():
            if len(demo) >= 20 or r["ngo_id"] in [d["ngo_id"] for d in demo]:
                continue
            if all(((r.lat - u[0]) * 111) ** 2 + ((r.lon - u[1]) * 108) ** 2 >= min_km ** 2 for u in used):
                demo.append(r)
                used.append((r.lat, r.lon))
    demo = pd.DataFrame(demo).drop(columns=["rank"])
    demo.to_csv(OUT / "ngos_demo.csv", index=False)

    print(f"master: {len(ngos)} NGOs  ->  {OUT / 'ngos_master.csv'}")
    print("  by source:", ngos["source"].value_counts().to_dict())
    print("  by category:", ngos["category"].value_counts().to_dict())
    print("  geo confidence:", ngos["geo_confidence"].value_counts().to_dict())
    print("  matched to a ward:", ngos["ward_no"].notna().sum())
    print(f"\ndemo: {len(demo)} NGOs  ->  {OUT / 'ngos_demo.csv'}")
    print(demo[["ngo_id", "name", "source", "category", "ward_name", "geo_confidence", "sim_capacity_meals",
                "need_score"]].to_string(index=False))


if __name__ == "__main__":
    main()
