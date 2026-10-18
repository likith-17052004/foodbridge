# FoodBridge: AI surplus food matching (Bengaluru)

A restaurant photographs leftover food. Gemini identifies the dish, servings and safe hours. The offer then
goes to the NGO best able to collect and serve it before it spoils.

## Run locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # Vertex AI: gcloud auth application-default login
.venv/bin/python scripts/simulate_history.py --days 45 --reset   # dashboard history
.venv/bin/uvicorn app.main:app --reload
```
- Donor app: http://localhost:8000
- NGO inbox: http://localhost:8000/ngo
- **Control room** (demo screen for judges): http://localhost:8000/control. Press "Play 10 PM rush".
- Surplus forecast: http://localhost:8000/forecast
- Impact dashboard: http://localhost:8000/dashboard

## AI quality (measured)
`scripts/eval_vision.py --per-class 3`: 240 photos covering 80 Indian dishes (Indian Food Images dataset), `google/gemini-3.6-flash`:

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

## Data (real vs simulated)
| Data | Source | Real / simulated |
|---|---|---|
| 20 demo NGOs | OpenStreetMap care homes + NGO Darpan (NITI Aayog) registrations | real name, location, category |
| NGO capacity, meal gap, veg-only, fridge, vehicle | `sim_*` columns | simulated (deterministic) |
| Need score | 50% simulated meal gap + 25% ward density + 25% ward SC/ST share (Census 2011) | mixed |
| 40 demo donors | Zomato Bengaluru datasets (Kaggle): coordinates, cuisines, closing time | real |
| Typical surplus per donor | `sim_*` columns | simulated |
| Safety rules | USDA 2-hour rule, hot-holding at 60 °C or above, FSSAI surplus food regulations 2019 | real guidance |
| CO2e factor | FAO Food Wastage Footprint: about 2.5 kg CO2e per kg | real |
| Donation history | `scripts/simulate_history.py` (runs the app's own safety and matching code) | simulated |

Rebuild the data: `scripts/clean_darpan.py`, then `geocode.py`, then `build_ngos.py` and `build_donors.py`.

## Data sources and credits
- NGOs: OpenStreetMap contributors (ODbL); NGO Darpan, NITI Aayog (public registry).
- Restaurants: Zomato Bangalore datasets on Kaggle (vora1011, aestheteaman01).
- Wards and census: OpenCity.in (BBMP ward maps, Census 2011 ward data).
- PIN code centroids: All India Pincode Directory 2025 (India Post via Kaggle).
- Dish photos: Indian Food Images Dataset on Kaggle (iamsouravbanerjee). `data/demo_photos/` holds 85 of them for the demo.

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
- Gemini is capped at `GEMINI_DAILY_LIMIT` (150) calls per day for the whole app. Uploads are limited to 20 per IP per hour, and the demo rush to 3 per IP per hour (max 8 dishes each).
- Routes API is capped at `ROUTES_DAILY_LIMIT` (150) elements per day, about 4.5k per month, inside the free allowance.
- BigQuery: one cached dashboard query every 5 minutes or less, and history is seeded with a free load job.
- Artifact Registry keeps the last 3 images. A ₹300/month budget alert covers the project.

## Local stand-ins for cloud services (swapped in later)
| Local | Cloud |
|---|---|
| `data/uploads/` | Cloud Storage |
| Gemini on Vertex AI using your gcloud credentials | Gemini on Vertex AI using the Cloud Run service account |
| `events` table plus a 3-second tick loop | Pub/Sub plus a scheduler |
| SQLite `pickups` table | BigQuery |
| Distance-based travel estimate | Routes API |
| Weekday-average forecast | BigQuery ML / Vertex AI forecasting |
