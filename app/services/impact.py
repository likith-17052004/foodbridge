"""The four headline numbers + breakdowns for the impact dashboard (BigQuery when configured, else SQLite)."""
from datetime import timedelta, timezone

from ..store import DONORS, NGOS, parse, query

IST = timezone(timedelta(hours=5, minutes=30))


def _from_bigquery():
    """Headline numbers straight from the warehouse: one cached query (each query bills >= 10 MB)."""
    from . import cloud
    rows = cloud.bq_query(f"""
        SELECT FORMAT_DATE('%Y-%m-%d', DATE(delivered_at, 'Asia/Kolkata')) AS day, ngo_category AS cat,
               SUM(servings) AS meals, SUM(kg) AS kg, SUM(co2e_kg) AS co2e, COUNT(*) AS pickups
        FROM `{cloud.bq_table('pickups')}`
        GROUP BY GROUPING SETS ((day), (cat), ())""")
    if not rows:
        return None
    total = next(r for r in rows if r["day"] is None and r["cat"] is None)
    return {
        "meals_delivered": int(total["meals"] or 0), "kg_diverted": round(total["kg"] or 0, 1),
        "co2e_avoided_kg": round(total["co2e"] or 0, 1), "pickups": total["pickups"],
        "by_day": dict(sorted((r["day"], int(r["meals"])) for r in rows if r["day"])),
        "by_category": dict(sorted(((r["cat"], int(r["meals"])) for r in rows if r["cat"]), key=lambda kv: -kv[1])),
    }


def summary():
    # demo-scenario rows (simulated=2) are excluded everywhere, matching what is logged to BigQuery
    p = query("SELECT * FROM pickups WHERE simulated != 2 ORDER BY delivered_at")
    offered = query("""SELECT COUNT(DISTINCT o.donation_id) n FROM offers o JOIN donations d ON d.id = o.donation_id
                       WHERE d.simulated != 2""")[0]["n"]  # blocked-unsafe food is never offered
    accepted_in_window = query("""
        SELECT COUNT(DISTINCT o.donation_id) n FROM offers o JOIN donations d ON d.id = o.donation_id
        WHERE o.status='accepted' AND o.responded_at <= d.safe_until AND d.simulated != 2""")[0]["n"]
    status = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) n FROM donations WHERE simulated != 2 GROUP BY status")}
    blocked = query("""SELECT COUNT(*) n FROM donations WHERE status='expired' AND simulated != 2
                       AND id NOT IN (SELECT donation_id FROM offers)""")[0]["n"]

    by_day, by_cat = {}, {}
    for r in p:
        day = parse(r["delivered_at"]).astimezone(IST).strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + r["servings"]
        cat = NGOS.get(r["ngo_id"], {}).get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + r["servings"]

    routes = {}
    for r in p:
        k = (r["donor_id"], r["ngo_id"])
        routes[k] = routes.get(k, 0) + r["servings"]

    out = {
        "source": "sqlite",
        "meals_delivered": sum(r["servings"] for r in p),
        "kg_diverted": round(sum(r["kg"] for r in p), 1),
        "co2e_avoided_kg": round(sum(r["co2e_kg"] for r in p), 1),
        "accepted_within_window_pct": round(100 * accepted_in_window / offered, 1) if offered else None,
        "funnel": {
            "listed": sum(v for k, v in status.items() if k != "draft"),
            "blocked_unsafe": blocked,
            "no_ngo_in_reach": status.get("unmatched", 0),
            "expired_during_offers": status.get("expired", 0) - blocked,
            "in_progress": sum(status.get(k, 0) for k in ("offered", "accepted", "picked_up")),
            "delivered": status.get("delivered", 0),
        },
        "donations_offered": offered,
        "pickups": len(p),
        "avg_offers_per_pickup": round(sum(r["offers_made"] for r in p) / len(p), 2) if p else None,
        "median_distance_km": sorted(r["distance_km"] for r in p)[len(p) // 2] if p else None,
        "by_day": dict(sorted(by_day.items())),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "routes": [{"donor": [DONORS[d]["lat"], DONORS[d]["lon"]], "ngo": [NGOS[n]["lat"], NGOS[n]["lon"]],
                    "donor_name": DONORS[d]["name"], "ngo_name": NGOS[n]["name"], "meals": m}
                   for (d, n), m in routes.items() if d in DONORS and n in NGOS],
        "recent": [{"dish": r["dish"], "servings": r["servings"], "donor": DONORS.get(r["donor_id"], {}).get("name"),
                    "ngo": NGOS.get(r["ngo_id"], {}).get("name"), "delivered_at": r["delivered_at"],
                    "within_window": bool(r["within_window"]), "simulated": bool(r["simulated"])}
                   for r in reversed(p[-8:])],
    }
    bq = _from_bigquery()
    if bq:
        out |= bq | {"source": "bigquery"}
    return out
