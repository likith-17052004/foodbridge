"""Build the donor (restaurant) table from the two Zomato Bengaluru datasets.

Real: name, address, coordinates, locality, cuisines, price for two, ratings, phone,
      veg-only flag, opening hours / closing time, popular dishes (when the second file matches).
Simulated (sim_ prefix, deterministic): sim_avg_surplus_servings, sim_surplus_days_per_week.

Outputs:
  data/processed/donors_master.csv   all usable restaurants
  data/processed/donors_demo.csv     40 donors spread across the city
"""
import hashlib
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data/raw/zomato", ROOT / "data/processed"
BBOX = {"lat": (12.70, 13.30), "lon": (77.30, 77.90)}

# outlets whose surplus is not cooked meals NGOs can serve
NON_MEAL = {"Beverages", "Ice Cream", "Desserts", "Juices", "Cafe", "Bakery", "Tea", "Coffee", "Shake",
            "Bubble Tea", "Paan", "Mithai", "Sandwich", "Burger", "Pizza", "Fast Food", "Momos", "Rolls"}
VEG_NAME = r"\b(?:udupi|brahmin\w*|sagar|bhavan|bhavana|upahar|upachar|darshini|pure veg|vegetarian|tiffin)\b"
# cuisines that typically cook in bulk (bigger surplus)
BULK = {"Biryani", "North Indian", "South Indian", "Andhra", "Kerala", "Chettinad", "Mughlai", "Chinese",
        "Mangalorean", "Hyderabadi", "Udupi", "Thali", "Street Food"}


def slug(url):
    m = re.search(r"zomato\.com/bangalore/([^/?#]+)", str(url))
    return m.group(1).replace("-bangalore", "") if m else None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def to_24h(t):
    """'11pm' / '11:30pm' / '12midnight' / '12noon' -> 23.0 / 23.5 / 24.0 / 12.0"""
    t = t.strip().lower()
    if "midnight" in t:
        return 24.0
    if "noon" in t:
        return 12.0
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t)
    if not m:
        return None
    h, mins = int(m.group(1)) % 12, int(m.group(2) or 0)
    return h + (12 if m.group(3) == "pm" else 0) + mins / 60

def closing_hour(timing):
    """Last closing time of the day; '24 Hours' -> 24. Early-morning closes (1:30am) -> 25.5."""
    s = str(timing)
    if "24 hours" in s.lower():
        return 24.0
    ranges = re.findall(r"–\s*([0-9:]+\s*(?:am|pm)|12midnight|12noon)", s, re.I)
    if not ranges:
        return None
    hours = [to_24h(r) for r in ranges]
    hours = [h + 24 if h is not None and h < 6 else h for h in hours]  # past midnight
    hours = [h for h in hours if h is not None]
    return max(hours) if hours else None


def seeded(key, lo, hi):
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return lo + h % (hi - lo + 1)


def main():
    a = pd.read_csv(RAW / "Bangalore_Restaurants.csv")
    b = pd.read_csv(RAW / "BangaloreZomatoData.csv")

    a = a[a["Latitude"].between(*BBOX["lat"]) & a["Longitude"].between(*BBOX["lon"])]
    a = a.drop_duplicates(["Restaurant_Name", "Latitude", "Longitude"])
    a["slug"], b["slug"] = a["Website"].map(slug), b["URL"].map(slug)
    a["nk"] = a["Restaurant_Name"].map(norm) + "|" + a["Locality"].str.split(",").str[0].map(norm)
    b["nk"] = b["Name"].map(norm) + "|" + b["Area"].str.split(",").str[0].map(norm)

    bcols = ["Timing", "isVegOnly", "PopularDishes", "KnownFor", "isTakeaway"]
    by_slug = b.dropna(subset=["slug"]).drop_duplicates("slug").set_index("slug")[bcols]
    by_name = b.drop_duplicates("nk").set_index("nk")[bcols]
    extra = a["slug"].map(lambda s: by_slug.loc[s] if s in by_slug.index else None)
    rows = []
    for (_, r), e in zip(a.iterrows(), extra):
        if e is None and r["nk"] in by_name.index:
            e = by_name.loc[r["nk"]]
        rows.append(e if e is not None else pd.Series({c: None for c in bcols}))
    a = pd.concat([a.reset_index(drop=True), pd.DataFrame(rows).reset_index(drop=True)], axis=1)

    cuis = a["Category"].fillna("").str.split(r",\s*").apply(set)
    a = a[cuis.apply(lambda c: bool(c - NON_MEAL))]  # keep outlets that serve some real meals
    cuis = a["Category"].fillna("").str.split(r",\s*").apply(set)

    d = pd.DataFrame({
        "name": a["Restaurant_Name"].str.strip(),
        "address": a["Address"],
        "locality": a["Locality"].str.replace(", Bangalore", "").str.strip(),
        "lat": a["Latitude"].round(6), "lon": a["Longitude"].round(6),
        "cuisines": a["Category"],
        "price_for_two": pd.to_numeric(a["Pricing_for_2"], errors="coerce"),
        "dining_rating": pd.to_numeric(a["Dining_Rating"], errors="coerce"),
        "review_count": pd.to_numeric(a["Dining_Review_Count"], errors="coerce").fillna(0)
                        + pd.to_numeric(a["Delivery_Rating_Count"], errors="coerce").fillna(0),
        "phone": a["Phone_No"],
        # Zomato's flag misses many classic veg places, so also trust well-known veg naming
        "veg_only": a["isVegOnly"].map({1: True, 0: False, 1.0: True, 0.0: False}).astype("boolean")
                    | a["Restaurant_Name"].str.contains(VEG_NAME, case=False, regex=True),
        "timing": a["Timing"].str.replace(r"\s*\(Today\)", "", regex=True),
        "closing_hour": a["Timing"].map(closing_hour),
        "popular_dishes": a["PopularDishes"],
        "zomato_url": a["Website"],
        "bulk_cooking": cuis.apply(lambda c: bool(c & BULK)).values,
    }).reset_index(drop=True)

    # ---- simulated surplus profile: bigger, busier, bulk-cooking kitchens waste more ----
    key = d["name"] + d["lat"].astype(str)
    busy = d["review_count"].rank(pct=True)
    base = 8 + 30 * busy + 15 * d["bulk_cooking"].astype(int)
    d["sim_avg_surplus_servings"] = [round(b_ * seeded(k, 70, 130) / 100) for k, b_ in zip(key, base)]
    d["sim_surplus_days_per_week"] = [seeded(k + "d", 2, 6) for k in key]

    d.insert(0, "donor_id", [f"DNR{i:04d}" for i in range(1, len(d) + 1)])
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT / "donors_master.csv", index=False)

    # ---- demo: 40 bulk-cooking, well-reviewed donors with known closing time, spread out ----
    pool = d[d["bulk_cooking"] & d["closing_hour"].notna() & (d["review_count"] > 100)]
    pool = pool.sort_values("review_count", ascending=False)
    demo, used = [], []
    for min_km in (1.5, 0.8, 0.0):
        for _, r in pool.iterrows():
            if len(demo) >= 40 or r["donor_id"] in {x["donor_id"] for x in demo}:
                continue
            if all(((r.lat - u[0]) * 111) ** 2 + ((r.lon - u[1]) * 108) ** 2 >= min_km ** 2 for u in used):
                demo.append(r)
                used.append((r.lat, r.lon))
    demo = pd.DataFrame(demo)
    demo.to_csv(OUT / "donors_demo.csv", index=False)

    print(f"master: {len(d)} donors -> {OUT / 'donors_master.csv'}")
    print("  with timing/closing hour:", d["closing_hour"].notna().sum(), "| veg_only known:", d["veg_only"].notna().sum(),
          "| veg_only True:", int(d["veg_only"].fillna(False).sum()), "| bulk cooking:", int(d["bulk_cooking"].sum()))
    print("  closing hour distribution:", d["closing_hour"].dropna().round().value_counts().sort_index().to_dict())
    print(f"demo: {len(demo)} donors -> {OUT / 'donors_demo.csv'}")
    print(demo[["donor_id", "name", "locality", "veg_only", "closing_hour", "sim_avg_surplus_servings"]]
          .head(15).to_string(index=False))


if __name__ == "__main__":
    main()
