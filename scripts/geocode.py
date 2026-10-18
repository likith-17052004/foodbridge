"""Geocode NGO addresses (Bengaluru) into lat/lon.

Fallback chain, first hit inside the Bengaluru bounding box wins:
  1. Google Places Text Search  (name + address)   -> confidence "high"
     also returns place_id, phone, rating, business status
  2. Google Geocoding API       (address)          -> "high" / "medium"
  3. Nominatim / OpenStreetMap  (name or locality) -> "medium"
  4. India Post PIN-code centroid (2025 directory) -> "low"

Google steps run only when GOOGLE_MAPS_API_KEY is set. Results are cached in
data/cache/geocode_cache.json, so re-runs are free and resumable.

Usage:
  python3 scripts/geocode.py data/raw/govt/darpan_ngos.csv \
      --address-col address --name-col name -o data/processed/ngos_geocoded.csv
"""
import argparse
import csv
import json
import os
import re
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data/cache/geocode_cache.json"
PINCODE_CSV = ROOT / "data/raw/pincode/all_india_pincode_directory_2025.csv"

# Bengaluru Urban + Rural, generous margin
BBOX = {"lat": (12.70, 13.30), "lon": (77.30, 77.90)}
CITY_CENTER = (12.9716, 77.5946)
NOMINATIM_UA = "FoodBridge-AIBuilderCup-prototype"
PIN_RE = re.compile(r"\b(5[0-9]{2})\s?([0-9]{3})\b")


def in_bbox(lat, lon):
    return BBOX["lat"][0] <= lat <= BBOX["lat"][1] and BBOX["lon"][0] <= lon <= BBOX["lon"][1]


def clean(text):
    text = re.sub(r"\s+", " ", (text or "")).strip(" ,")
    return re.sub(r"\bBangalore\b", "Bengaluru", text, flags=re.I)


def extract_pin(text):
    m = PIN_RE.search(text or "")
    return m.group(1) + m.group(2) if m else None


def http_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ---------- step 1: Google Places Text Search (New) ----------
def google_places(name, address, key):
    body = json.dumps({
        "textQuery": f"{name}, {address}" if name else address,
        "regionCode": "IN",
        "locationBias": {"circle": {"center": {"latitude": CITY_CENTER[0], "longitude": CITY_CENTER[1]},
                                    "radius": 40000.0}},
        "maxResultCount": 1,
    }).encode()
    fields = ("places.id,places.displayName,places.formattedAddress,places.location,"
              "places.nationalPhoneNumber,places.rating,places.businessStatus,places.types")
    res = http_json("https://places.googleapis.com/v1/places:searchText", data=body, headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields})
    if not res.get("places"):
        return None
    p = res["places"][0]
    return {
        "lat": p["location"]["latitude"], "lon": p["location"]["longitude"],
        "geo_source": "google_places", "geo_confidence": "high",
        "geo_formatted": p.get("formattedAddress", ""), "place_id": p.get("id", ""),
        "place_name": p.get("displayName", {}).get("text", ""),
        "phone": p.get("nationalPhoneNumber", ""), "rating": p.get("rating", ""),
        "business_status": p.get("businessStatus", ""),
    }


# ---------- step 2: Google Geocoding API ----------
def google_geocode(address, key):
    q = urllib.parse.urlencode({"address": address, "components": "country:IN", "region": "in",
                                "bounds": f"{BBOX['lat'][0]},{BBOX['lon'][0]}|{BBOX['lat'][1]},{BBOX['lon'][1]}",
                                "key": key})
    res = http_json(f"https://maps.googleapis.com/maps/api/geocode/json?{q}")
    if res.get("status") != "OK":
        return None
    r = res["results"][0]
    loc_type = r["geometry"]["location_type"]  # ROOFTOP / RANGE_INTERPOLATED / GEOMETRIC_CENTER / APPROXIMATE
    return {
        "lat": r["geometry"]["location"]["lat"], "lon": r["geometry"]["location"]["lng"],
        "geo_source": f"google_geocode:{loc_type.lower()}",
        "geo_confidence": "high" if loc_type in ("ROOFTOP", "RANGE_INTERPOLATED") else "medium",
        "geo_formatted": r.get("formatted_address", ""),
    }


# ---------- step 3: Nominatim (free, 1 req/s) ----------
_last_nominatim = 0.0


def nominatim(query):
    global _last_nominatim
    wait = 1.1 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.time()
    q = urllib.parse.urlencode({"q": query, "format": "jsonv2", "countrycodes": "in", "limit": 1,
                                "viewbox": f"{BBOX['lon'][0]},{BBOX['lat'][1]},{BBOX['lon'][1]},{BBOX['lat'][0]}",
                                "bounded": 1})
    res = http_json(f"https://nominatim.openstreetmap.org/search?{q}", headers={"User-Agent": NOMINATIM_UA})
    if not res:
        return None
    return {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]),
            "geo_source": f"nominatim:{res[0].get('addresstype', '')}", "geo_confidence": "medium",
            "geo_formatted": res[0].get("display_name", "")}


def nominatim_queries(name, address):
    """Nominatim can't parse full Indian addresses; try name, then trailing locality chunks."""
    parts = [p.strip() for p in address.split(",") if p.strip() and not PIN_RE.fullmatch(p.strip())]
    parts = [p for p in parts if p.lower() not in ("bengaluru", "karnataka", "india", "bengaluru urban")]
    qs = []
    if name:
        qs.append(f"{name}, Bengaluru")
    for p in reversed(parts[-3:]):  # localities usually sit near the end
        if not re.search(r"\d", p):
            qs.append(f"{p}, Bengaluru")
    return qs


# ---------- step 4: PIN-code centroid ----------
def load_pin_centroids():
    if not PINCODE_CSV.exists():
        return {}
    pts = {}
    with open(PINCODE_CSV, encoding="latin1") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            if in_bbox(lat, lon):
                pts.setdefault(str(row["pincode"]).strip(), []).append((lat, lon))
    return {pin: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v)) for pin, v in pts.items()}


def km(a, b):
    # equirectangular approximation, fine at city scale
    dlat = (a[0] - b[0]) * 111.0
    dlon = (a[1] - b[1]) * 111.0 * 0.974  # cos(13 deg)
    return (dlat ** 2 + dlon ** 2) ** 0.5


GENERIC_WORDS = {"the", "of", "and", "trust", "foundation", "society", "seva", "samithi", "samiti", "home",
                 "charitable", "welfare", "association", "india", "bengaluru", "bangalore", "sangha", "for"}


def name_matches(name, found):
    """At least one distinctive word of the NGO name appears in the matched place."""
    words = {w for w in re.findall(r"[a-z]{3,}", name.lower()) if w not in GENERIC_WORDS}
    return not words or any(w in found.lower() for w in words)


MAX_KM_FROM_PIN = 6.0


def geocode_one(name, address, key, pins, pin=None):
    tried = []
    steps = []
    if key:
        steps += [("google_places", True, lambda: google_places(name, address, key)),
                  ("google_geocode", False, lambda: google_geocode(address, key))]
    for q in nominatim_queries(name, address):
        steps.append(("nominatim", bool(name) and q.startswith(name), lambda q=q: nominatim(q)))
    pin = extract_pin(address) or pin
    pin_center = pins.get(pin) if pin else None
    for label, by_name, fn in steps:
        try:
            r = fn()
        except Exception as e:  # network / quota errors: fall through to next step
            tried.append(f"{label}:error:{type(e).__name__}")
            continue
        if not r:
            tried.append(f"{label}:no_result")
        elif not in_bbox(r["lat"], r["lon"]):
            tried.append(f"{label}:outside_city")
        elif pin_center and km(pin_center, (r["lat"], r["lon"])) > MAX_KM_FROM_PIN:
            tried.append(f"{label}:far_from_pin")
        elif by_name and not name_matches(name, r.get("place_name") or r["geo_formatted"]):
            tried.append(f"{label}:name_mismatch")
        else:
            return r
    if pin_center:
        lat, lon = pin_center
        return {"lat": lat, "lon": lon, "geo_source": "pincode_centroid", "geo_confidence": "low",
                "geo_formatted": f"PIN {pin} centroid"}
    return {"lat": "", "lon": "", "geo_source": "failed", "geo_confidence": "none",
            "geo_formatted": "; ".join(tried)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--address-col", default="address")
    ap.add_argument("--name-col", default="name")
    ap.add_argument("--pincode-col", default="pincode", help="used when the address has no PIN")
    ap.add_argument("-o", "--output", default=str(ROOT / "data/processed/ngos_geocoded.csv"))
    ap.add_argument("--limit", type=int, help="only geocode the first N rows (testing)")
    args = ap.parse_args()

    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    print(f"Google: {'ON' if key else 'OFF (set GOOGLE_MAPS_API_KEY)'}")
    pins = load_pin_centroids()
    print(f"PIN centroids loaded: {len(pins)}")

    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    with open(args.input, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[:args.limit]

    out_cols = ["lat", "lon", "geo_source", "geo_confidence", "geo_formatted", "place_id", "place_name",
                "phone", "rating", "business_status"]
    stats = {}
    for i, row in enumerate(rows, 1):
        name, address = clean(row.get(args.name_col)), clean(row.get(args.address_col))
        ck = f"{name}|{address}|{'g' if key else 'ng'}"
        if ck not in cache or cache[ck]["geo_source"] == "failed":
            pin = re.sub(r"\.0$", "", str(row.get(args.pincode_col) or "").strip()) or None
            cache[ck] = geocode_one(name, address, key, pins, pin)
            if i % 10 == 0:
                CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                CACHE_PATH.write_text(json.dumps(cache, indent=1))
        res = cache[ck]
        row.update({c: res.get(c, "") for c in out_cols})
        stats[res["geo_confidence"]] = stats.get(res["geo_confidence"], 0) + 1
        print(f"[{i}/{len(rows)}] {res['geo_confidence']:<6} {res['geo_source']:<28} {name[:50]}")

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {args.output}")
    print("Confidence:", stats)


if __name__ == "__main__":
    main()
