"""Tonight's surplus forecast: where and when food will appear, which NGOs should be on standby.

Model (local): per-area weekday-aware average of past surplus (servings listed per night), blended with the
recent 14-night level, plus each area's hour-of-day profile. Backtested on the last 7 nights.
Cloud (later): the same features as a BigQuery ML / Vertex AI forecasting model once real pickups accumulate.
History is the simulated donation log, so the accuracy number describes the pipeline, not the real city.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..store import DONORS, NGOS, parse, query
from .travel import travel

IST = timezone(timedelta(hours=5, minutes=30))
CELL = 0.03  # ~3.3 km grid
STANDBY_MAX_MIN = 35  # NGO must reach the area within this many minutes at the forecast hour
_briefing_cache = {}


def _cell(lat, lon):
    return (round(lat / CELL), round(lon / CELL))


def _night(dt_ist):
    """Food listed after midnight belongs to the previous evening's 'night'."""
    return (dt_ist - timedelta(hours=6)).date()


def _history():
    rows = query("SELECT donor_id, created_at, servings FROM donations WHERE servings IS NOT NULL")
    out = []
    for r in rows:
        d = DONORS.get(r["donor_id"])
        if not d:
            continue
        t = parse(r["created_at"]).astimezone(IST)
        out.append({"cell": _cell(d["lat"], d["lon"]), "night": _night(t), "hour": t.hour,
                    "servings": r["servings"], "locality": d["locality"], "lat": d["lat"], "lon": d["lon"]})
    return out


def _predict(hist, cells, target_night, train_before):
    """Weekday-aware average per cell, using nights strictly before `train_before`."""
    nights = sorted({h["night"] for h in hist if h["night"] < train_before})
    if not nights:
        return {c: 0.0 for c in cells}
    totals = defaultdict(float)
    for h in hist:
        if h["night"] < train_before:
            totals[(h["cell"], h["night"])] += h["servings"]
    same_wd = [n for n in nights if n.weekday() == target_night.weekday()]
    recent = nights[-14:]
    pred = {}
    for c in cells:
        wd = sum(totals[(c, n)] for n in same_wd) / len(same_wd) if same_wd else 0
        rc = sum(totals[(c, n)] for n in recent) / len(recent)
        pred[c] = 0.6 * wd + 0.4 * rc if same_wd else rc
    return pred


def backtest(hist=None, days=7):
    hist = hist or _history()
    current = _night(datetime.now(IST))
    nights = sorted({h["night"] for h in hist if h["night"] < current})  # only completed nights
    if len(nights) < days + 14:
        return None
    cells = sorted({h["cell"] for h in hist})
    actual = defaultdict(float)
    for h in hist:
        actual[(h["cell"], h["night"])] += h["servings"]
    err_model, err_naive, city_err, city_actual = [], [], [], []
    for night in nights[-days:]:
        pm = _predict(hist, cells, night, night)
        train = [n for n in nights if n < night]
        naive = {c: sum(actual[(c, n)] for n in train) / len(train) for c in cells}
        for c in cells:
            err_model.append(abs(pm[c] - actual[(c, night)]))
            err_naive.append(abs(naive[c] - actual[(c, night)]))
        tot = sum(actual[(c, night)] for c in cells)
        city_err.append(abs(sum(pm.values()) - tot))
        city_actual.append(tot)
    mae, mae_naive = sum(err_model) / len(err_model), sum(err_naive) / len(err_naive)
    return {"nights": days, "areas": len(cells), "mae_meals_per_area": round(mae, 1),
            "naive_mae_meals_per_area": round(mae_naive, 1),
            "improvement_pct": round(100 * (1 - mae / mae_naive), 1) if mae_naive else None,
            "city_total_error_pct": round(100 * sum(city_err) / sum(city_actual), 1) if sum(city_actual) else None}


def tonight(target=None):
    hist = _history()
    target_night = target or _night(datetime.now(IST))
    cells = sorted({h["cell"] for h in hist})
    pred = _predict(hist, cells, target_night, target_night)

    meta = defaultdict(lambda: {"localities": defaultdict(int), "hours": defaultdict(float), "lat": [], "lon": []})
    for h in hist:
        m = meta[h["cell"]]
        m["localities"][h["locality"]] += 1
        m["hours"][h["hour"]] += h["servings"]
        m["lat"].append(h["lat"])
        m["lon"].append(h["lon"])

    areas = []
    for c in cells:
        if pred[c] < 5:
            continue
        m = meta[c]
        lat, lon = sum(m["lat"]) / len(m["lat"]), sum(m["lon"]) / len(m["lon"])
        hours = m["hours"]
        peak = max(hours, key=hours.get)
        share = {h: v / sum(hours.values()) for h, v in hours.items()}
        reach = []
        for n in NGOS.values():
            t = travel((n["lat"], n["lon"]), (lat, lon), peak)
            if t["minutes"] <= STANDBY_MAX_MIN:
                reach.append({"ngo_id": n["ngo_id"], "name": n["name"], "minutes": t["minutes"],
                              "meal_gap": n["sim_daily_meal_gap"], "need_score": n["need_score"]})
        locs = sorted(m["localities"], key=m["localities"].get, reverse=True)
        areas.append({
            "area": locs[0], "alt_area": locs[1] if len(locs) > 1 else None, "lat": round(lat, 5), "lon": round(lon, 5),
            "expected_meals": round(pred[c]), "peak_hour": peak,
            "window": f"{peak % 24:02d}:00-{(peak + 1) % 24:02d}:00",
            "hour_share": {str(h % 24): round(v, 2) for h, v in sorted(share.items())}, "reach": reach,
        })
    areas.sort(key=lambda a: -a["expected_meals"])

    # standby plan: biggest areas first; each NGO's meal gap is used up as it is assigned, so load spreads out
    remaining = {n["ngo_id"]: int(n["sim_daily_meal_gap"] or 0) for n in NGOS.values()}
    seen_names = {}
    for a in areas:
        need, plan = a["expected_meals"], []
        for s_ in sorted(a.pop("reach"), key=lambda s_: (s_["minutes"] > 20, -s_["need_score"], s_["minutes"])):
            if need <= 0 or len(plan) == 3:
                break
            take = min(remaining[s_["ngo_id"]], need)
            if take <= 0:
                continue
            remaining[s_["ngo_id"]] -= take
            need -= take
            plan.append(s_ | {"meals": take})
        a["standby"], a["uncovered_meals"] = plan, max(need, 0)
        a["reachable_gap"] = sum(p_["meals"] for p_ in plan)
        seen_names[a["area"]] = seen_names.get(a["area"], 0) + 1
        if seen_names[a["area"]] > 1:  # two grid cells share a locality name
            a["area"] = f"{a['area']} / {a['alt_area']}" if a["alt_area"] else f"{a['area']} ({seen_names[a['area']]})"

    by_hour = defaultdict(float)
    for a in areas:
        for h, s in a["hour_share"].items():
            by_hour[int(h)] += a["expected_meals"] * s
    order = sorted(by_hour, key=lambda h: (h - 12) % 24)  # evening first, after-midnight last
    demand = sum(int(n["sim_daily_meal_gap"] or 0) for n in NGOS.values())
    return {
        "night": target_night.isoformat(), "weekday": target_night.strftime("%A"),
        "expected_meals": sum(a["expected_meals"] for a in areas), "ngo_meal_gap": demand,
        "areas": areas, "by_hour": [{"hour": f"{h % 24:02d}:00", "meals": round(by_hour[h])} for h in order],
        "ngos": [{"ngo_id": n["ngo_id"], "name": n["name"], "lat": n["lat"], "lon": n["lon"],
                  "meal_gap": n["sim_daily_meal_gap"], "category": n["category"]} for n in NGOS.values()],
        "backtest": backtest(hist), "history_nights": len({h["night"] for h in hist}),
    }


def briefing(fc):
    """Short dispatch briefing written by Gemini from the forecast numbers (cached per night)."""
    if fc["night"] in _briefing_cache:
        return _briefing_cache[fc["night"]]
    from . import guard
    from .vision import generate_text
    facts = {"night": f"{fc['weekday']} {fc['night']}", "expected_meals_city": fc["expected_meals"],
             "ngo_meal_gap_city": fc["ngo_meal_gap"],
             "top_areas": [{k: a[k] for k in ("area", "expected_meals", "window")} |
                           {"standby": [s["name"] for s in a["standby"][:2]]} for a in fc["areas"][:5]]}
    prompt = ("You are the night dispatcher of FoodBridge, a surplus-food rescue service in Bengaluru. "
              "Write tonight's briefing for NGO coordinators: 4 short bullet points, plain text, no markdown "
              "headings, under 90 words total. Mention the busiest areas and time windows, which NGOs should be on "
              "standby, and whether expected surplus exceeds or falls short of NGO demand. Use only these facts:\n"
              + json.dumps(facts))
    try:
        guard.gemini_call()
        text = generate_text(prompt)
    except Exception as e:
        text = f"(Briefing unavailable: {e})"[:200]
    _briefing_cache[fc["night"]] = text
    return text
