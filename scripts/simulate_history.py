"""Simulate past donations for the impact dashboard (the spec: "donation history is simulated for the demo").

Uses the real demo donors/NGOs, real closing times, and the app's own safety + matching code,
so the history behaves like the live system. Only the events (what was donated, who answered) are random.
Rows are written with simulated=1 and can be wiped with --reset.

Usage: .venv/bin/python scripts/simulate_history.py --days 45 [--reset]
"""
import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402
from app.services import matching, safety  # noqa: E402
from app.store import DONORS, execute, iso, query  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
# (dish, diet, high_risk, gemini-style room-temp safe hours)
DISHES = [
    ("Vegetable Biryani", "veg", True, 4), ("Chicken Biryani", "non_veg", True, 3), ("Dal Tadka & Jeera Rice", "veg", True, 4),
    ("Chapati & Mixed Veg Curry", "veg", False, 5), ("Paneer Butter Masala & Naan", "veg", True, 3),
    ("Sambar Rice", "veg", True, 4), ("Idli & Chutney", "veg", False, 5), ("Egg Curry & Rice", "egg", True, 3),
    ("Chicken Curry & Parotta", "non_veg", True, 3), ("Pulao & Raita", "veg", True, 3), ("Bisi Bele Bath", "veg", True, 4),
    ("Chole & Puri", "veg", False, 5), ("Fried Rice & Gobi Manchurian", "veg", True, 4), ("Lemon Rice", "veg", False, 5),
]


def simulate(days, seed):
    rnd = random.Random(seed)
    donors = list(DONORS.values())
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {"donations": 0, "delivered": 0, "expired": 0, "unmatched": 0}
    # each kitchen has regular surplus nights (e.g. weekend buffets, slow Mondays), as real kitchens do
    usual_days = {d["donor_id"]: set(random.Random(d["donor_id"]).sample(range(7), int(d["sim_surplus_days_per_week"])))
                  for d in donors}
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 4  # Fri-Sun
        tonight_donors = [d for d in donors if rnd.random() < (0.6 if day.weekday() in usual_days[d["donor_id"]] else 0.05)]
        for donor in tonight_donors:
            close = float(donor["closing_hour"] or 23)
            created = day + timedelta(hours=min(close, 26) - rnd.uniform(0.3, 1.5))
            dish, diet, high_risk, ai_hours = rnd.choice(DISHES)
            if donor.get("veg_only") and diet != "veg":
                dish, diet, high_risk, ai_hours = DISHES[0]
            servings = max(5, round(float(donor["sim_avg_surplus_servings"]) * (1.3 if weekend else 1.0)
                                    * rnd.uniform(0.75, 1.25)))
            storage = rnd.choices(["hot_held", "room_temp", "refrigerated"], [0.6, 0.3, 0.1])[0]
            cooked = created - timedelta(hours=rnd.uniform(1.0, 3.5))
            analysis = {"is_food": True, "dish_name": dish, "diet": diet, "high_risk": high_risk,
                        "estimated_servings": servings, "safe_hours_room_temp": ai_hours, "visible_spoilage": False,
                        "source": "simulated"}
            s = safety.safe_window(analysis, cooked.astimezone(timezone.utc), storage, created.astimezone(timezone.utc))
            did = "SIM-" + uuid.uuid4().hex[:8]
            t = created.astimezone(timezone.utc)
            m = {"eligible": [], "excluded": []} if s["blocked"] else matching.match(donor, servings, diet, s["safe_until"], t)
            status, offers, accepted = ("expired" if s["blocked"] else "unmatched"), [], None
            for cand in m["eligible"]:
                deadline = s["safe_until"] - timedelta(minutes=config.SERVE_BUFFER_MIN + cand["round_trip_min"])
                if t >= deadline:
                    continue
                expires = min(t + timedelta(seconds=config.OFFER_TIMEOUT_SECONDS), deadline)
                r = rnd.random()
                outcome = "accepted" if r < 0.55 else "declined" if r < 0.75 else "expired"
                resp = t + (timedelta(minutes=rnd.uniform(0.5, 8)) if outcome != "expired" else expires - t)
                if resp > expires:
                    outcome, resp = "expired", expires
                offers.append(("OFR-" + uuid.uuid4().hex[:8], cand, outcome, t, expires, resp))
                t = resp
                if outcome == "accepted":
                    accepted = cand
                    break
            for oid, cand, outcome, ct, ex, rp in offers:
                execute("INSERT INTO offers VALUES (?,?,?,?,?,?,?,?)",
                        (oid, did, cand["ngo_id"], cand["rank"], outcome, iso(ct), iso(ex), iso(rp)))
            if accepted:
                picked = t + timedelta(minutes=accepted["travel_min"] + rnd.uniform(0, 10))
                delivered = picked + timedelta(minutes=config.LOADING_MIN + accepted["travel_min"] * rnd.uniform(0.9, 1.3))
                kg = round(servings * config.KG_PER_SERVING, 1)
                execute("INSERT INTO pickups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    did, donor["donor_id"], accepted["ngo_id"], dish, diet, servings, kg, round(kg * config.CO2E_PER_KG, 1),
                    iso(offers[0][3]), iso(t), iso(picked), iso(delivered), iso(s["safe_until"]),
                    int(delivered <= s["safe_until"]), len(offers), accepted["km"], 1))
                status = "delivered"
            elif offers:
                status = "expired"
            stats[status] += 1
            stats["donations"] += 1
            execute("""INSERT INTO donations (id, donor_id, photo_path, created_at, status, analysis, servings, diet,
                       cooked_at, storage, safe_until, safety, matches, simulated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (did, donor["donor_id"], None, iso(created), status, json.dumps(analysis), servings, diet, iso(cooked),
                     storage, iso(s["safe_until"]), json.dumps(s | {"safe_until": iso(s["safe_until"])}), json.dumps(m)))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true", help="delete previously simulated rows first")
    a = ap.parse_args()
    if a.reset:
        execute("DELETE FROM offers WHERE donation_id LIKE 'SIM-%'")
        execute("DELETE FROM pickups WHERE simulated=1")
        execute("DELETE FROM donations WHERE simulated=1")
    print(simulate(a.days, a.seed))
    print(query("SELECT COUNT(*) pickups, SUM(servings) meals FROM pickups WHERE simulated=1")[0])


if __name__ == "__main__":
    main()
