"""FoodBridge API + static frontend.

Run locally:  .venv/bin/uvicorn app.main:app --reload
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .services import donations, forecast, impact, offers, safety, scenario, vision
from .store import DONORS, NGOS, iso, now, one, query

STATIC = config.ROOT / "app/static"


@asynccontextmanager
async def lifespan(_):
    async def loop():
        while True:
            try:
                offers.tick()
            except Exception as e:  # keep the loop alive
                print("tick error:", e)
            await asyncio.sleep(3)
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="FoodBridge", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")


@app.get("/")
def donor_page():
    return FileResponse(STATIC / "donor.html")


@app.get("/ngo")
def ngo_page():
    return FileResponse(STATIC / "ngo.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/control")
def control_page():
    return FileResponse(STATIC / "control.html")


@app.get("/forecast")
def forecast_page():
    return FileResponse(STATIC / "forecast.html")


# ---------------- reference data ----------------
@app.get("/api/config")
def get_config():
    return {"gemini": vision.gemini_enabled(), "model": vision.model_label(),
            "offer_timeout_seconds": config.OFFER_TIMEOUT_SECONDS, "serve_buffer_min": config.SERVE_BUFFER_MIN}


@app.get("/api/donors")
def list_donors():
    return list(DONORS.values())


@app.get("/api/ngos")
def list_ngos():
    return list(NGOS.values())


# ---------------- donor flow ----------------
@app.post("/api/donations")
async def create_donation(donor_id: str = Form(...), photo: UploadFile = File(...)):
    if donor_id not in DONORS:
        raise HTTPException(404, "Unknown donor")
    data = await photo.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "Photo too large (max 12 MB)")
    try:
        return await asyncio.to_thread(donations.create, donor_id, data, photo.content_type or "image/jpeg",
                                       photo.filename or "photo.jpg")
    except Exception as e:
        raise HTTPException(502, f"Food analysis failed: {e}")


class Confirm(BaseModel):
    servings: int
    diet: str
    cooked_at: datetime  # ISO with timezone offset from the browser
    storage: str         # room_temp / hot_held / refrigerated


@app.post("/api/donations/{donation_id}/confirm")
def confirm_donation(donation_id: str, body: Confirm):
    d = one("SELECT status FROM donations WHERE id=?", (donation_id,))
    if not d:
        raise HTTPException(404, "Unknown donation")
    if d["status"] != "draft":
        raise HTTPException(409, f"Donation already {d['status']}")
    if body.storage not in safety.STORAGE_CAPS or body.diet not in ("veg", "egg", "non_veg"):
        raise HTTPException(422, "Invalid storage or diet")
    if body.cooked_at > now() + timedelta(minutes=5):
        raise HTTPException(422, "Cooked time is in the future")
    if not 1 <= body.servings <= 2000:
        raise HTTPException(422, "Servings must be between 1 and 2000")
    return donations.confirm(donation_id, body.servings, body.diet, body.cooked_at, body.storage)


@app.get("/api/donations/{donation_id}")
def get_donation(donation_id: str):
    v = offers.donation_view(donation_id)
    if not v:
        raise HTTPException(404, "Unknown donation")
    return v


@app.get("/api/donations")
def recent_donations(donor_id: str | None = None, limit: int = 20):
    sql = "SELECT id FROM donations WHERE simulated=0" + (" AND donor_id=?" if donor_id else "")
    rows = query(sql + " ORDER BY created_at DESC LIMIT ?", ((donor_id,) if donor_id else ()) + (limit,))
    return [offers.donation_view(r["id"]) for r in rows]


# ---------------- NGO flow ----------------
@app.get("/api/ngos/{ngo_id}/offers")
def ngo_offers(ngo_id: str, include_demo: bool = False):
    if ngo_id not in NGOS:
        raise HTTPException(404, "Unknown NGO")
    rows = query("""SELECT o.*, d.status AS donation_status FROM offers o JOIN donations d ON d.id=o.donation_id
                    WHERE o.ngo_id=? AND d.simulated IN (0, ?) ORDER BY o.created_at DESC LIMIT 30""",
                 (ngo_id, 2 if include_demo else 0))
    out = []
    for o in rows:
        dv = offers.donation_view(o["donation_id"])
        mine = next((m for m in dv["matches"]["eligible"] if m["ngo_id"] == ngo_id), {})
        out.append(o | {"donation": {k: dv[k] for k in ("id", "servings", "diet", "safe_until", "photo_path",
                                                          "status", "storage", "cooked_at")},
                        "dish": dv["analysis"].get("dish_name"), "donor": dv["donor"], "match": mine})
    return {"ngo": NGOS[ngo_id], "offers": out, "server_time": iso(now())}


@app.post("/api/offers/{offer_id}/{action}")
def offer_action(offer_id: str, action: str):
    if action not in ("accept", "decline"):
        raise HTTPException(404)
    r = offers.respond(offer_id, action == "accept")
    if not r["ok"]:
        raise HTTPException(409, r["error"])
    return r


@app.post("/api/donations/{donation_id}/{action}")
def donation_action(donation_id: str, action: str):
    fn = {"picked_up": offers.mark_picked_up, "delivered": offers.mark_delivered}.get(action)
    if not fn:
        raise HTTPException(404)
    r = fn(donation_id)
    if not r["ok"]:
        raise HTTPException(409, r["error"])
    return r


# ---------------- impact ----------------
@app.get("/api/impact")
def get_impact():
    return impact.summary()


# ---------------- control room ----------------
@app.get("/api/live")
def live():
    return {"donations": scenario.live(), "tonight": scenario.tonight(), "scenario": scenario.status(),
            "server_time": iso(now())}


@app.get("/api/events")
def events(after: int = 0, limit: int = 40):
    rows = query("SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (after, limit))
    return [r | {"payload": json.loads(r["payload"])} for r in reversed(rows)]


class ScenarioStart(BaseModel):
    donations: int = 6
    interval_s: float = 8
    offer_timeout_s: int = 20
    auto_ngo: bool = True


@app.post("/api/scenario/start")
async def scenario_start(body: ScenarioStart):
    if not vision.gemini_enabled():
        raise HTTPException(409, "Configure a Gemini provider first (see .env.example)")
    return scenario.start(max(1, min(body.donations, 15)), body.interval_s, max(10, body.offer_timeout_s),
                          body.auto_ngo)


@app.post("/api/scenario/{action}")
def scenario_action(action: str, on: bool = True):
    fn = {"stop": scenario.stop, "reset": scenario.reset, "auto_ngo": lambda: scenario.set_auto_ngo(on)}.get(action)
    if not fn:
        raise HTTPException(404)
    return fn()


# ---------------- forecast + evaluation ----------------
@app.get("/api/forecast")
def get_forecast():
    return forecast.tonight()


@app.get("/api/forecast/briefing")
async def get_briefing():
    fc = forecast.tonight()
    return {"night": fc["night"], "text": await asyncio.to_thread(forecast.briefing, fc)}


@app.get("/api/eval")
def get_eval():
    p = config.DATA_DIR / "eval/vision_summary.json"
    return json.loads(p.read_text()) if p.exists() else None
