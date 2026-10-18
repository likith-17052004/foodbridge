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
