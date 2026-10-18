"""'10 PM rush' live demo scenario for the control room.

Everything except the people is real: real restaurants, real dish photos (Indian Food Images dataset),
real Gemini analysis, the real safety rules, matching and offer cascade. What is simulated:
  - the kitchen confirming a batch quantity (photos show a plate, the kitchen reports the batch size)
  - NGOs answering offers (accept / decline / no answer), unless a human answers on the NGO phone
  - pickup and delivery, shown as a time-lapse
Offers in the scenario expire after `offer_timeout_s` (default 20 s) instead of 10 min, so the
cascade to the next NGO is visible on stage.
"""
import asyncio
import random
from datetime import timedelta
from pathlib import Path

from .. import config
from ..store import DONORS, execute, now, one, parse, query
from . import donations, offers

# full Kaggle dataset locally if downloaded; otherwise the small committed demo set (used on Cloud Run)
_FULL = config.ROOT / "data/raw/food_images/Indian Food Images/Indian Food Images"
IMAGES = _FULL if _FULL.exists() else config.ROOT / "data/demo_photos"
VEG_DISHES = ["dal_tadka", "chana_masala", "palak_paneer", "kadai_paneer", "aloo_gobi", "dal_makhani", "poha",
              "bhindi_masala", "navrattan_korma", "dum_aloo", "aloo_matar", "paneer_butter_masala"]
NONVEG_DISHES = ["biryani", "butter_chicken", "chicken_tikka_masala", "chicken_razala", "maach_jhol"]

STATE = {"running": False, "auto_ngo": True, "started_at": None, "planned": 0, "created": 0, "errors": [],
         "task": None}
_decisions = {}  # offer_id -> (action, due_time)


def status():
    return {k: v for k, v in STATE.items() if k != "task"} | {
        "started_at": STATE["started_at"].isoformat() if STATE["started_at"] else None}


def _pick_photo(donor, rnd):
    pool = VEG_DISHES if donor.get("veg_only") else VEG_DISHES + NONVEG_DISHES * 2
    for _ in range(10):
        cls = rnd.choice(pool)
        photos = sorted((IMAGES / cls).glob("*.jpg"))
        if photos:
            return rnd.choice(photos)
    raise RuntimeError("No dish photos found - is data/raw/food_images downloaded?")


async def _donate(donor, rnd, offer_timeout_s):
    photo = _pick_photo(donor, rnd)
    created = await asyncio.to_thread(donations.create, donor["donor_id"], photo.read_bytes(), "image/jpeg",
                                      photo.name, 2)
    a = created["analysis"]
    if not a.get("is_food", True):
        return
    servings = max(10, round(float(donor["sim_avg_surplus_servings"]) * rnd.uniform(0.7, 1.3)))
    storage = rnd.choices(["hot_held", "room_temp"], [0.7, 0.3])[0]
    cooked = now() - timedelta(minutes=rnd.uniform(50, 130) if storage == "hot_held" else rnd.uniform(20, 70))
    await asyncio.to_thread(donations.confirm, created["donation_id"], servings, a["diet"], cooked, storage,
                            offer_timeout_s)


async def _ngo_bot(rnd):
    """Answers pending scenario offers like real NGOs would, and time-lapses pickup + delivery."""
    progress = {}  # donation_id -> next step due
    while STATE["running"] or query("SELECT 1 FROM donations WHERE simulated=2 AND status IN "
                                    "('offered','accepted','picked_up') LIMIT 1"):
        t = now()
        if STATE["auto_ngo"]:
            for o in query("""SELECT o.id FROM offers o JOIN donations d ON d.id=o.donation_id
                              WHERE o.status='pending' AND d.simulated=2"""):
                if o["id"] not in _decisions:
                    r = rnd.random()
                    action = "accept" if r < 0.55 else "decline" if r < 0.75 else "ignore"
                    _decisions[o["id"]] = (action, t + timedelta(seconds=rnd.uniform(3, 9)))
                action, due = _decisions[o["id"]]
                if action != "ignore" and t >= due:
                    offers.respond(o["id"], action == "accept")
        for d in query("SELECT id, status FROM donations WHERE simulated=2 AND status IN ('accepted','picked_up')"):
            key = (d["id"], d["status"])
            progress.setdefault(key, t + timedelta(seconds=rnd.uniform(6, 10)))
            if t >= progress[key]:
                (offers.mark_picked_up if d["status"] == "accepted" else offers.mark_delivered)(d["id"])
        await asyncio.sleep(1)


async def _run(n, interval_s, offer_timeout_s, seed):
    rnd = random.Random(seed)
    donors = [d for d in DONORS.values() if d.get("bulk_cooking")] or list(DONORS.values())
    bot = asyncio.create_task(_ngo_bot(random.Random(seed + 1)))
    try:
        for donor in rnd.sample(donors, min(n, len(donors))):
            if not STATE["running"]:
                break
            try:
                await _donate(donor, rnd, offer_timeout_s)
                STATE["created"] += 1
            except Exception as e:  # keep the show going
                STATE["errors"].append(f"{donor['name']}: {e}"[:200])
            await asyncio.sleep(interval_s)
    finally:
        STATE["running"] = False
    await bot


def start(n=6, interval_s=8, offer_timeout_s=20, auto_ngo=True, seed=None):
    if STATE["running"]:
        return status()
    STATE.update(running=True, auto_ngo=auto_ngo, started_at=now(), planned=n, created=0, errors=[])
    STATE["task"] = asyncio.create_task(_run(n, interval_s, offer_timeout_s,
                                             seed if seed is not None else random.randrange(10 ** 6)))
    return status()


def stop():
    STATE["running"] = False
    return status()


def set_auto_ngo(on: bool):
    STATE["auto_ngo"] = on
    return status()


def reset():
    """Remove scenario rows (keeps simulated history and real donations)."""
    stop()
    ids = [r["id"] for r in query("SELECT id, photo_path FROM donations WHERE simulated=2")]
    for r in query("SELECT photo_path FROM donations WHERE simulated=2"):
        if r["photo_path"]:
            Path(config.UPLOAD_DIR / Path(r["photo_path"]).name).unlink(missing_ok=True)
    for i in ids:
        execute("DELETE FROM offers WHERE donation_id=?", (i,))
    execute("DELETE FROM pickups WHERE simulated=2")
    execute("DELETE FROM donations WHERE simulated=2")
    _decisions.clear()
    return {"removed": len(ids)}


def live(limit=12):
    """Recent live + scenario donations for the control room."""
    cutoff = (now() - timedelta(hours=3)).isoformat(timespec="seconds")
    rows = query("SELECT id FROM donations WHERE simulated IN (0,2) AND status != 'draft' AND created_at >= ? "
                 "ORDER BY created_at DESC LIMIT ?", (cutoff, limit))
    out = []
    for r in rows:
        v = offers.donation_view(r["id"])
        m = v.get("matches") or {"eligible": [], "excluded": []}
        v["matches"] = {"eligible": m["eligible"][:6], "eligible_count": len(m["eligible"]),
                        "excluded": [{"ngo_id": x["ngo_id"], "reason": x["reason"]} for x in m["excluded"]]}
        out.append(v)
    return out


def tonight():
    """Headline numbers for live + scenario activity in the last 3 hours."""
    cutoff = (now() - timedelta(hours=3)).isoformat(timespec="seconds")
    p = query("SELECT servings, kg, co2e_kg FROM pickups WHERE simulated IN (0,2) AND delivered_at >= ?", (cutoff,))
    d = one("""SELECT COUNT(*) n, SUM(status='expired') blocked, SUM(status IN ('offered','accepted','picked_up')) active
               FROM donations WHERE simulated IN (0,2) AND status != 'draft' AND created_at >= ?""", (cutoff,))
    acc = query("""SELECT d.created_at c, MIN(o.responded_at) a FROM donations d JOIN offers o ON o.donation_id=d.id
                   WHERE d.simulated IN (0,2) AND o.status='accepted' AND d.created_at >= ? GROUP BY d.id""", (cutoff,))
    secs = sorted((parse(r["a"]) - parse(r["c"])).total_seconds() for r in acc if r["a"])
    return {"meals": sum(r["servings"] for r in p), "kg": round(sum(r["kg"] for r in p), 1),
            "co2e_kg": round(sum(r["co2e_kg"] for r in p), 1), "donations": d["n"] or 0,
            "active": d["active"] or 0, "blocked_or_expired": d["blocked"] or 0,
            "median_match_seconds": round(secs[len(secs) // 2]) if secs else None}
