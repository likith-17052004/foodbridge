"""Spoilage-aware NGO matching.

1. Filter out NGOs that cannot receive the food while it is still safe:
   NGO volunteer rides NGO -> donor -> NGO, plus loading time; arrival must be SERVE_BUFFER_MIN before safe_until.
2. Filter out diet mismatches (non-veg food to veg-only NGOs).
3. Rank the rest by need (ward vulnerability + meal gap), how much of the food they can use, and time margin.
"""
from datetime import timedelta, timezone

from .. import config
from ..store import NGOS
from .travel import route_matrix, travel

IST = timezone(timedelta(hours=5, minutes=30))
WEIGHTS = {"need": 0.45, "fit": 0.30, "margin": 0.25}


def match(donor: dict, servings: int, diet: str, safe_until, now, use_routes=False):
    """use_routes: real traffic-aware times from the Routes API (live donations); history uses the estimate."""
    donor_pt = (donor["lat"], donor["lon"])
    hour = now.astimezone(IST).hour
    times = route_matrix([(n["ngo_id"], (n["lat"], n["lon"])) for n in NGOS.values()], donor_pt, hour) if use_routes \
        else {n["ngo_id"]: travel((n["lat"], n["lon"]), donor_pt, hour) for n in NGOS.values()}
    eligible, excluded = [], []
    for ngo in NGOS.values():
        t = times[ngo["ngo_id"]]
        round_trip = 2 * t["minutes"] + config.LOADING_MIN
        arrive = now + timedelta(minutes=round_trip)
        margin = (safe_until - arrive).total_seconds() / 60 - config.SERVE_BUFFER_MIN
        row = {"ngo_id": ngo["ngo_id"], "name": ngo["name"], "category": ngo["category"], "ward": ngo.get("ward_name"),
               "lat": ngo["lat"], "lon": ngo["lon"], "km": t["km"], "travel_min": t["minutes"],
               "round_trip_min": round_trip, "arrive_by": arrive.isoformat(timespec="seconds"),
               "margin_min": round(margin), "travel_source": t.get("method", "estimate")}
        if margin < 0:
            excluded.append(row | {"reason": f"Can't arrive in time ({round_trip} min round trip)"})
            continue
        if diet != "veg" and not ngo["sim_accepts_nonveg"]:
            excluded.append(row | {"reason": "Serves vegetarian food only"})
            continue
        gap = max(int(ngo["sim_daily_meal_gap"] or 0), 1)
        fit = min(servings, gap) / max(servings, 1)  # share of the donation this NGO can actually use
        total_window = max((safe_until - now).total_seconds() / 60, 1)
        margin_score = min(margin / total_window, 1)
        need = float(ngo["need_score"]) / 100
        score = WEIGHTS["need"] * need + WEIGHTS["fit"] * fit + WEIGHTS["margin"] * margin_score
        eligible.append(row | {
            "score": round(100 * score, 1), "need_score": ngo["need_score"], "meal_gap": gap,
            "fit": round(fit, 2), "usable_meals": min(servings, gap), "why": _why(ngo, gap, servings, margin, t),
            "score_parts": {"need": round(100 * WEIGHTS["need"] * need, 1), "fit": round(100 * WEIGHTS["fit"] * fit, 1),
                            "margin": round(100 * WEIGHTS["margin"] * margin_score, 1)},
        })
    eligible.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(eligible, 1):
        r["rank"] = i
    return {"eligible": eligible, "excluded": sorted(excluded, key=lambda r: r["km"])}


def _why(ngo, gap, servings, margin, t):
    parts = [f"needs ~{gap} meals today" + (" (covers all)" if gap >= servings else f" of {servings}")]
    parts.append(f"{t['km']} km, ~{t['minutes']} min away")
    parts.append(f"{round(margin)} min safety margin")
    if ngo.get("ward_name"):
        parts.append(f"ward {ngo['ward_name']} need {ngo['need_score']:.0f}/100")
    return "; ".join(parts)
