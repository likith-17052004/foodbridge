"""Food-safety window: how long the food can still be served.

Gemini estimates shelf life from the dish; hard caps from the storage method keep it conservative.
Basis: USDA "2-hour rule" for perishables in the danger zone (5-60 C), FSSAI Surplus Food Regulations 2019
(hand over "at a reasonable time before it gets spoiled"), hot-holding at >= 60 C.
"""
from datetime import timedelta

from .. import config

# hours after cooking (or after leaving hot-holding / the fridge) the food may stay out
STORAGE_CAPS = {
    "room_temp": {"high_risk": 2.0, "low_risk": 4.0},
    "hot_held": {"high_risk": 4.0, "low_risk": 6.0},       # kept >= 60 C in a bain-marie / closed handi
    "refrigerated": {"high_risk": 24.0, "low_risk": 36.0},  # chilled to <= 5 C soon after cooking
}
STORAGE_LABELS = {"room_temp": "Room temperature", "hot_held": "Kept hot (60 °C+)", "refrigerated": "Refrigerated"}


def safe_window(analysis: dict, cooked_at, storage: str, now):
    risk = "high_risk" if analysis.get("high_risk") else "low_risk"
    cap = STORAGE_CAPS[storage][risk]
    ai_hours = float(analysis.get("safe_hours_room_temp") or cap)
    # Gemini's estimate is for room temperature; cold/hot holding legitimately extends it up to the cap
    hours = cap if storage != "room_temp" else min(ai_hours, cap)
    safe_until = cooked_at + timedelta(hours=hours)
    minutes_left = (safe_until - now).total_seconds() / 60
    blocked_reason = None
    if analysis.get("visible_spoilage"):
        blocked_reason = "Visible spoilage in the photo"
    elif not analysis.get("is_food", True):
        blocked_reason = "Photo does not show food"
    elif minutes_left <= config.SERVE_BUFFER_MIN:
        blocked_reason = "Past the safe window - cannot be offered"
    return {
        "safe_until": safe_until, "hours_total": hours, "minutes_left": round(minutes_left),
        "rule": f"{STORAGE_LABELS[storage]}, {'high' if risk == 'high_risk' else 'low'}-risk food: max {cap:g} h",
        "ai_hours": ai_hours, "blocked": blocked_reason is not None, "blocked_reason": blocked_reason,
    }
