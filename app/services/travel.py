"""Travel time between two points in Bengaluru.

Local: straight-line distance x road-winding factor / time-of-day speed (Bengaluru traffic profile).
Cloud (later): Google Routes API computeRouteMatrix with traffic, same function signature.
"""
import math

ROAD_FACTOR = 1.35  # road distance / straight-line distance, typical for Indian cities


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def avg_speed_kmh(local_hour):
    """Two-wheeler / small-van speeds in Bengaluru by hour (IST)."""
    if 8 <= local_hour < 11 or 17 <= local_hour < 21:
        return 14  # peak
    if 22 <= local_hour or local_hour < 6:
        return 28  # night
    return 20


def travel(a, b, when_ist_hour):
    km = haversine_km(a, b) * ROAD_FACTOR
    minutes = km / avg_speed_kmh(when_ist_hour) * 60
    return {"km": round(km, 1), "minutes": round(minutes), "method": "estimate"}


# ---------------- Google Routes API (live donations only; cached and capped for cost) ----------------
_route_cache = {}  # (ngo_id, donor_key) -> (fetched_at, {"km", "minutes", "method"})
ROUTE_CACHE_S = 1800


def _token():
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def route_matrix(origins, dest, hour):
    """origins: [(id, (lat, lon))]; returns {id: travel dict}. Falls back to the estimate per origin."""
    import time

    import httpx

    from .. import config
    from . import guard
    out, todo, t = {}, [], time.time()
    dkey = f"{dest[0]:.4f},{dest[1]:.4f}"
    for oid, pt in origins:
        hit = _route_cache.get((oid, dkey))
        if hit and t - hit[0] < ROUTE_CACHE_S:
            out[oid] = hit[1]
        else:
            todo.append((oid, pt))
    if todo and config.ROUTES_API:
        try:
            guard.take("route_elements", config.ROUTES_DAILY_LIMIT, len(todo))
            body = {
                "origins": [{"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}} for _, p in todo],
                "destinations": [{"waypoint": {"location": {"latLng": {"latitude": dest[0], "longitude": dest[1]}}}}],
                "travelMode": "TWO_WHEELER", "routingPreference": "TRAFFIC_AWARE",
            }
            r = httpx.post("https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", json=body, timeout=20,
                           headers={"Authorization": f"Bearer {_token()}", "X-Goog-User-Project": config.GCP_PROJECT,
                                    "X-Goog-FieldMask": "originIndex,duration,distanceMeters,condition"})
            r.raise_for_status()
            for el in r.json():
                if el.get("condition") != "ROUTE_EXISTS":
                    continue
                oid = todo[el["originIndex"]][0]
                res = {"km": round(el["distanceMeters"] / 1000, 1),
                       "minutes": round(int(el["duration"].rstrip("s")) / 60), "method": "routes_api"}
                out[oid] = res
                _route_cache[(oid, dkey)] = (t, res)
        except Exception as e:  # cap reached, quota, network: estimate instead
            print(f"[routes] falling back to estimate: {e}")
    for oid, pt in todo:
        out.setdefault(oid, travel(pt, dest, hour))
    return out
