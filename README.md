# FoodBridge: AI surplus food matching (Bengaluru)

A restaurant photographs its leftover food. Gemini identifies the dish, veg/non-veg, risks and how long it stays
safe. FoodBridge then offers it to the NGO best able to collect and serve it before it spoils.

## Live demo
Hosted on Google Cloud Run: **[https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app)**

| Page | Link | What it shows |
|---|---|---|
| Control room | [/control](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app/control) | The demo screen: press **Play 10 PM rush** to watch real dish photos analysed by Gemini, matched and offered to NGOs live |
| Donate | [/](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app/) | Restaurant app: photo → Gemini analysis → confirm → live matching table |
| NGO inbox | [/ngo](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app/ngo) | NGO app: incoming offers with countdown, accept / decline, pickup and delivery |
| Forecast | [/forecast](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app/forecast) | Tonight's expected surplus by area and hour, NGO standby plan, Gemini dispatch briefing |
| Impact | [/dashboard](https://foodbridge-ai-builder-cup-524673786174.asia-south1.run.app/dashboard) | Meals delivered, kg diverted, CO₂e avoided, share accepted within the safe window (from BigQuery) |

The service scales to zero when idle, so the first page load can take a few seconds.

## How it works
1. **Upload**: the donor photographs the food; the photo is stored in Cloud Storage.
2. **Analyse**: Gemini 3.6 Flash on Vertex AI returns the dish, veg/egg/non-veg, risk factors, servings and safe
   hours. The kitchen confirms the quantity, cooking time and storage.
3. **Safety**: the safe window combines Gemini's estimate with USDA/FSSAI holding rules. Food past its window is
   blocked.
4. **Match**: the Routes API gives traffic-aware travel times. NGOs that can't receive the food in time are filtered out, and
   the rest are ranked by need (45%), how much of the food they can use (30%) and safety margin (25%).
5. **Offer**: the top NGO gets the offer; if it doesn't accept within 10 minutes, it moves to the next one. Every
   event is published to Pub/Sub and lands in BigQuery.
6. **Measure**: completed pickups stream into BigQuery and drive the impact dashboard and the surplus forecast.

## Run locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # Gemini on Vertex AI; see the comments in the file
gcloud auth application-default login     # local credentials for Vertex AI
.venv/bin/python -m app.bootstrap         # creates the local database with 45 nights of simulated history
.venv/bin/uvicorn app.main:app --reload   # serves the same pages as the live demo, on port 8000
```

## AI quality (measured)
`scripts/eval_vision.py --per-class 3`: 240 photos covering 80 Indian dishes (Indian Food Images dataset), Gemini 3.6 Flash:

| Metric | Result |
|---|---|
| Veg / non-veg correct | 99.6% |
| Non-veg dishes flagged as non-veg | 93.3% (14 of 15) |
| Veg dishes wrongly called non-veg | 0% |
| Dish identified exactly | 75.8% (most misses are near-twins, e.g. butter chicken vs chicken tikka masala) |
| Median latency | 6.9 s |

Gemini's own confidence did not separate right from wrong answers. So veg/non-veg uses two keys: Gemini proposes and the kitchen confirms before anything reaches a veg-only NGO.

## Forecast (measured on simulated history)
Per ~3 km area, a weekday-aware average blended with the last 14 nights, plus each area's hour profile.
Backtest on the last 7 completed nights: 16.7% lower error than a naive average per area, with the city total within about 18%.
NGO standby is a capacity-aware plan: each NGO's meal gap is used up as it is assigned. Gemini writes the nightly dispatch briefing.

## Data sources

All datasets were collected on **18 Oct 2026** for Bengaluru. Raw files are kept out of git (`data/raw/` is
ignored) because they can be rebuilt from the sources below. The repo only contains the processed files the
app needs: `data/processed/ngos_*.csv`, `data/processed/donors_demo.csv`, `data/demo_photos/` and
`data/eval/vision_summary.json`.

### Datasets used

| # | Dataset | Publisher / origin | Licence | Used for | Local file (not in git) | Read by |
|---|---|---|---|---|---|---|
| 1 | [Zomato Restaurants in Metropolitan Areas of India](https://www.kaggle.com/datasets/aestheteaman01/zomato-restaurant-caf-dataset-pune-bangalore) | Kaggle, aestheteaman01 (scraped from Zomato) | CC0-1.0 | Donor restaurants: name, address, **latitude/longitude**, cuisines, price, ratings, phone | `data/raw/zomato/Bangalore_Restaurants.csv` | `scripts/build_donors.py` |
| 2 | [Zomato Bangalore Restaurants 2022](https://www.kaggle.com/datasets/vora1011/zomato-bangalore-restaurants-2022) | Kaggle, vora1011 (scraped from Zomato) | ODbL-1.0 | Donor details joined to #1: opening hours (gives **closing time**), veg-only flag, popular dishes | `data/raw/zomato/BangaloreZomatoData.csv` | `scripts/build_donors.py` |
| 3 | [OpenStreetMap](https://www.openstreetmap.org/copyright) via the [Overpass API](https://overpass-api.de) | OpenStreetMap contributors | ODbL-1.0 | NGO receivers: care homes, old-age homes, orphanages, shelters (`amenity=social_facility`, `office=ngo`, …) with exact coordinates | `data/raw/osm_bengaluru_social.json` | `scripts/build_ngos.py` |
| 4 | [NGO Darpan](https://ngodarpan.gov.in) | NITI Aayog, Government of India (public NGO registry) | Public government registry | Registered NGOs (Karnataka, Bengaluru Urban): name, registration ID, address, PIN, sectors. Collected by the project author from the public search page; 160 records. | `data/raw/govt/ngodarpan_bengaluru_urban.csv` | `scripts/clean_darpan.py` → `geocode.py` → `build_ngos.py` |
| 5 | [Bengaluru Ward-wise Census Data 2011](https://data.opencity.in/dataset/bengaluru-census-2011) | Office of the Registrar General & Census Commissioner, India, via OpenCity | Public domain | Ward population and SC/ST share, used in the NGO **need score** | `data/raw/opencity/bengaluru_ward_census_2011.csv` | `scripts/build_ngos.py` |
| 6 | [BBMP Ward Map 2015 (198 wards)](https://data.opencity.in/dataset/bbmp-ward-information) | Bruhat Bengaluru Mahanagara Palike, via OpenCity | Not stated on OpenCity | Ward polygons: matches each NGO to its census ward and computes ward density | `data/raw/opencity/bbmp_wards_2015_198.kml` | `scripts/build_ngos.py` |
| 7 | [All India Pincode Directory 2025](https://www.kaggle.com/datasets/shibin007/all-india-pincode-directory2025) | India Post directory, compiled on Kaggle by shibin007 | CC-BY-SA-4.0 | Post-office coordinates: fallback location (PIN centroid) when an NGO address can't be geocoded | `data/raw/pincode/all_india_pincode_directory_2025.csv` | `scripts/geocode.py` |
| 8 | [Nominatim](https://nominatim.openstreetmap.org) geocoder | OpenStreetMap Foundation | ODbL-1.0 (data) | Turning NGO Darpan addresses into coordinates (1 request/s, results cached) | `data/cache/geocode_cache.json` | `scripts/geocode.py` |
| 9 | [Indian Food Images Dataset](https://www.kaggle.com/datasets/iamsouravbanerjee/indian-food-images-dataset) | Kaggle, iamsouravbanerjee | "Other" (see dataset page) | Testing Gemini (240 photos, 80 dishes) and dish photos for the live demo rush. 85 of them are committed in `data/demo_photos/`. | `data/raw/food_images/` | `scripts/eval_vision.py`, `app/services/scenario.py` |

### Reference figures and rules

| Figure / rule | Value used | Source |
|---|---|---|
| CO₂e per kg of food wasted | 2.5 kg CO₂e/kg (3.3 Gt CO₂e ÷ 1.3 Gt food) | FAO (2013), [*Food Wastage Footprint: Impacts on Natural Resources*](https://www.fao.org/4/i3347e/i3347e.pdf) |
| Perishable food at room temperature | max 2 h (high-risk) / 4 h (low-risk) | USDA FSIS, ["Danger Zone" 40–140 °F guidance](https://www.fsis.usda.gov/food-safety/safe-food-handling-and-preparation/food-safety-basics/danger-zone-40f-140f) |
| Hot-holding and chilled storage limits | ≥ 60 °C: 4–6 h; ≤ 5 °C: 24–36 h | Standard hot/cold holding practice (USDA FSIS); project's conservative caps in `app/services/safety.py` |
| Surplus food handover rules | Hand over "at a reasonable time before it gets spoiled"; no distribution past shelf life | FSSAI, *Food Safety and Standards (Recovery and Distribution of Surplus Food) Regulations, 2019* ([fssai.gov.in](https://www.fssai.gov.in)) |
| Weight of one serving | 0.35 kg | Project assumption (typical cooked adult meal portion) |
| Headline problem statistic | 78.2 million tonnes of household food waste a year in India | UNEP *Food Waste Index Report*, as cited in the challenge brief (via Outlook Business) |

### Live data from Google APIs (production)

| Data | API |
|---|---|
| Dish, veg/non-veg, servings, safe hours, risks from each photo; nightly dispatch briefing | Gemini 3.6 Flash on Vertex AI |
| Two-wheeler, traffic-aware travel time and distance from each NGO to the restaurant | Google Maps Platform Routes API (`computeRouteMatrix`), capped daily; falls back to an estimate (straight-line × 1.35, Bengaluru peak/off-peak speeds) |

### Simulated data (clearly marked)

The prototype has no real NGO capacity figures or donation history yet. These are simulated **deterministically**
(the same input always gives the same value), and every simulated column starts with `sim_`:

| Field | Where | How it's generated |
|---|---|---|
| `sim_capacity_meals`, `sim_daily_meal_gap` | NGOs | Typical meals/day for the NGO's category (children / elderly / disabled / homeless / women / community), seeded per NGO |
| `sim_accepts_nonveg`, `sim_has_fridge`, `sim_has_vehicle` | NGOs | Seeded per NGO (about 70% accept non-veg) |
| `sim_avg_surplus_servings`, `sim_surplus_days_per_week` | Restaurants | Scaled by real review count (footfall proxy) and bulk-cooking cuisines |
| Donation history (45 nights) | `scripts/simulate_history.py` | Real restaurants, NGOs, closing times, safety rules and matching code; only the events (what was donated, who answered) are random. Rows have `simulated = 1`. |
| Demo "10 PM rush" | `app/services/scenario.py` | Real photos and real Gemini analysis. Kitchen batch size, NGO replies and pickup timing are simulated (`simulated = 2`) and kept out of BigQuery. |

The **need score** mixes both kinds: 50% simulated meal gap, 25% real ward population density, 25% real ward SC/ST share.

### Downloaded but not used
- [USDA FSIS FoodKeeper](https://catalog.data.gov/dataset/fsis-foodkeeper-data) (public domain). Reviewed for storage times; the safety rules above were used instead.
- BBMP Ward Map 2022 (243 wards, OpenCity). The census uses the 2015 ward boundaries, so the 2015 map is used.
- `data/raw/govt/darpan_pass1_keywords.csv`, an additional NGO Darpan keyword search, not processed yet.

### Rebuilding the data
```
# 1. Download (Kaggle API token in ~/.kaggle)
kaggle datasets download -d aestheteaman01/zomato-restaurant-caf-dataset-pune-bangalore -p data/raw/zomato --unzip
kaggle datasets download -d vora1011/zomato-bangalore-restaurants-2022 -p data/raw/zomato --unzip
kaggle datasets download -d shibin007/all-india-pincode-directory2025 -p data/raw/pincode --unzip
kaggle datasets download -d iamsouravbanerjee/indian-food-images-dataset -p data/raw/food_images --unzip
#    OpenCity: census CSV + 2015 ward KML from the dataset pages above -> data/raw/opencity/
#    OpenStreetMap: Overpass query for social facilities / NGOs in the Bengaluru bounding box -> data/raw/osm_bengaluru_social.json
#    NGO Darpan: export of the public search (Karnataka, Bengaluru Urban) -> data/raw/govt/ngodarpan_bengaluru_urban.csv
# 2. Process
python3 scripts/clean_darpan.py data/raw/govt/ngodarpan_bengaluru_urban.csv
python3 scripts/geocode.py data/processed/darpan_shortlist.csv -o data/processed/darpan_geocoded.csv
python3 scripts/build_ngos.py && python3 scripts/build_donors.py
```

## Google Cloud architecture
| Piece | Service | Notes |
|---|---|---|
| Web app + API | Cloud Run (asia-south1) | Deploys automatically on every push to `main` |
| Food photo analysis + dispatch briefing | Gemini 3.6 Flash on Vertex AI (global endpoint) | Uses the Cloud Run service account; no API key |
| Photos, state snapshots | Cloud Storage `socialone-468019-foodbridge` | Private bucket; photos are deleted after 30 days |
| Event stream | Pub/Sub `foodbridge-events` with a BigQuery subscription | Every event lands in `foodbridge.events` |
| Pickups and impact KPIs | BigQuery `foodbridge.pickups` | Streamed on delivery; the dashboard query is cached for 5 minutes |
| Travel times | Routes API, `computeRouteMatrix` (two-wheeler, traffic-aware) | Cached for 30 minutes; falls back to an estimate |

Each service is optional. Without its env var (`GCS_BUCKET`, `PUBSUB_TOPIC`, `BQ_DATASET`, `ROUTES_API`), the app uses its local stand-in.

## Cost guardrails (personal project with no budget)
- Cloud Run scales to zero with max 1 instance and request-based CPU. Housekeeping runs on each request.
- Gemini is capped at `GEMINI_DAILY_LIMIT` calls per day for the whole app (60 in production, about ₹15/day worst case). Uploads are limited to 20 per IP per hour, and the demo rush to 3 per IP per hour (max 8 dishes each).
- Routes API is capped at `ROUTES_DAILY_LIMIT` (150) elements per day, about 4.5k per month, inside the free allowance. Each matched donation uses about 20 (one per NGO).
- BigQuery: one cached dashboard query every 5 minutes or less, and history is seeded with a free load job.
- Artifact Registry keeps the last 3 images. A ₹300/month budget alert covers the project.

## Running without Google Cloud
Every Google service is optional. When its setting is empty, the app falls back to a local version, so it also runs
fully offline (Gemini then returns a clearly-labelled demo result).

| Setting | With Google Cloud | Without it |
|---|---|---|
| `VISION_PROVIDER=vertex` | Gemini on Vertex AI | `demo` result (or `gemini` with `GEMINI_API_KEY`) |
| `GCS_BUCKET` | Photos and state snapshots in Cloud Storage | Photos in `data/uploads/`, state only in the local SQLite file |
| `PUBSUB_TOPIC` | Events published to Pub/Sub, then BigQuery | Events only in the local `events` table (which also drives the live UI) |
| `BQ_DATASET` | Pickups streamed to BigQuery; dashboard reads from it | Dashboard reads the local `pickups` table |
| `ROUTES_API=on` | Routes API travel times | Distance-based estimate |

The forecast is a statistical model (weekday-aware averages); it would move to BigQuery ML / Vertex AI forecasting
once real pickups accumulate.
