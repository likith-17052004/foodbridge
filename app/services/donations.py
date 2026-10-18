"""Create and confirm donations: shared by the HTTP API and the live demo scenario."""
import json
import uuid

from .. import config
from ..store import DONORS, execute, iso, now, one
from . import cloud, guard, matching, offers, safety, vision


def create(donor_id, image_bytes, mime_type, filename="photo.jpg", origin=0):
    """Save the photo, run Gemini, store a draft. origin: 0 live, 2 demo scenario."""
    donor = DONORS[donor_id]
    if vision.gemini_enabled():
        guard.gemini_call()  # raises guard.LimitReached once the daily cap is used up
    did = "DON-" + uuid.uuid4().hex[:8]
    ext = filename.rsplit(".", 1)[-1].lower()[:5] if "." in filename else "jpg"
    path = config.UPLOAD_DIR / f"{did}.{ext}"
    path.write_bytes(image_bytes)  # local cache; the durable copy goes to Cloud Storage
    cloud.gcs_upload(f"photos/{path.name}", image_bytes, mime_type)
    analysis = vision.analyse(image_bytes, mime_type, f"{donor['name']}, a restaurant serving {donor['cuisines']}")
    execute("INSERT INTO donations (id, donor_id, photo_path, created_at, status, analysis, simulated) "
            "VALUES (?,?,?,?,?,?,?)",
            (did, donor_id, f"/uploads/{path.name}", iso(now()), "draft", json.dumps(analysis), origin))
    offers.publish_event("donation.analysed", {"donation_id": did, "donor_id": donor_id,
                                               "dish": analysis.get("dish_name"), "diet": analysis.get("diet")})
    return {"donation_id": did, "photo_url": f"/uploads/{path.name}", "analysis": analysis}


def confirm(donation_id, servings, diet, cooked_at, storage, offer_timeout_s=None):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    analysis = json.loads(d["analysis"]) | {"diet": diet}
    t = now()
    s = safety.safe_window(analysis, cooked_at, storage, t)
    m = {"eligible": [], "excluded": []} if s["blocked"] else matching.match(
        DONORS[d["donor_id"]], servings, diet, s["safe_until"], t, use_routes=True)
    s_json = s | {"safe_until": iso(s["safe_until"])}
    execute("""UPDATE donations SET servings=?, diet=?, cooked_at=?, storage=?, safe_until=?, safety=?, matches=?,
               status=?, offer_timeout_s=? WHERE id=?""",
            (servings, diet, iso(cooked_at), storage, iso(s["safe_until"]), json.dumps(s_json), json.dumps(m),
             "expired" if s["blocked"] else "draft", offer_timeout_s, donation_id))
    if s["blocked"]:
        offers.publish_event("donation.blocked", {"donation_id": donation_id, "reason": s["blocked_reason"]})
    else:
        offers.publish_event("donation.matched", {"donation_id": donation_id, "eligible": len(m["eligible"]),
                                                  "excluded": len(m["excluded"])})
        offers.start_cascade(donation_id)
    return offers.donation_view(donation_id)
