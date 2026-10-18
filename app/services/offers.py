"""Offer cascade: offer to the top NGO; if it doesn't accept within OFFER_TIMEOUT_SECONDS, move to the next.

Every state change is published as an event (local: events table; cloud: Pub/Sub topic `offers`).
A background loop (`tick`) expires offers and blocks donations whose safe window has passed.
"""
import json
import uuid
from datetime import timedelta

from .. import config
from ..store import DONORS, NGOS, execute, iso, now, one, parse, publish, query


publish_event = publish  # re-exported for other services


def start_cascade(donation_id):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    eligible = json.loads(d["matches"])["eligible"]
    if not eligible:
        execute("UPDATE donations SET status='unmatched' WHERE id=?", (donation_id,))
        publish("donation.unmatched", {"donation_id": donation_id})
        return None
    return _offer_next(donation_id)


def _offer_next(donation_id):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    tried = {o["ngo_id"] for o in query("SELECT ngo_id FROM offers WHERE donation_id=?", (donation_id,))}
    t = now()
    if parse(d["safe_until"]) - timedelta(minutes=config.SERVE_BUFFER_MIN) <= t:
        return _expire_donation(donation_id, "Safe window passed before any NGO accepted")
    for m in json.loads(d["matches"])["eligible"]:
        if m["ngo_id"] in tried:
            continue
        # re-check this NGO can still make it in time (time has passed since matching)
        arrive = t + timedelta(minutes=m["round_trip_min"])
        if arrive > parse(d["safe_until"]) - timedelta(minutes=config.SERVE_BUFFER_MIN):
            continue
        oid = "OFR-" + uuid.uuid4().hex[:8]
        timeout = d["offer_timeout_s"] or config.OFFER_TIMEOUT_SECONDS
        expires = min(t + timedelta(seconds=timeout),
                      parse(d["safe_until"]) - timedelta(minutes=config.SERVE_BUFFER_MIN + m["round_trip_min"]))
        execute("INSERT INTO offers VALUES (?,?,?,?,?,?,?,?)",
                (oid, donation_id, m["ngo_id"], m["rank"], "pending", iso(t), iso(expires), None))
        execute("UPDATE donations SET status='offered' WHERE id=?", (donation_id,))
        publish("offer.created", {"offer_id": oid, "donation_id": donation_id, "ngo_id": m["ngo_id"], "rank": m["rank"]})
        return oid
    execute("UPDATE donations SET status='unmatched' WHERE id=?", (donation_id,))
    publish("donation.unmatched", {"donation_id": donation_id})
    return None


def _expire_donation(donation_id, reason):
    execute("UPDATE offers SET status='cancelled', responded_at=? WHERE donation_id=? AND status='pending'",
            (iso(now()), donation_id))
    execute("UPDATE donations SET status='expired' WHERE id=?", (donation_id,))
    publish("donation.expired", {"donation_id": donation_id, "reason": reason})
    return None


def respond(offer_id, accept: bool):
    o = one("SELECT * FROM offers WHERE id=?", (offer_id,))
    if not o or o["status"] != "pending":
        return {"ok": False, "error": f"Offer is {o['status'] if o else 'missing'}"}
    t = now()
    if accept:
        execute("UPDATE offers SET status='accepted', responded_at=? WHERE id=?", (iso(t), offer_id))
        execute("UPDATE donations SET status='accepted' WHERE id=?", (o["donation_id"],))
        publish("offer.accepted", {"offer_id": offer_id, "donation_id": o["donation_id"], "ngo_id": o["ngo_id"]})
    else:
        execute("UPDATE offers SET status='declined', responded_at=? WHERE id=?", (iso(t), offer_id))
        publish("offer.declined", {"offer_id": offer_id, "donation_id": o["donation_id"], "ngo_id": o["ngo_id"]})
        _offer_next(o["donation_id"])
    return {"ok": True}


def mark_picked_up(donation_id):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    if not d or d["status"] != "accepted":
        return {"ok": False, "error": "Donation is not in accepted state"}
    execute("UPDATE donations SET status='picked_up' WHERE id=?", (donation_id,))
    publish("donation.picked_up", {"donation_id": donation_id, "at": iso(now())})
    return {"ok": True}


def mark_delivered(donation_id):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    if not d or d["status"] != "picked_up":
        return {"ok": False, "error": "Donation is not picked up yet"}
    t = now()
    offers = query("SELECT * FROM offers WHERE donation_id=? ORDER BY created_at", (donation_id,))
    acc = next(o for o in offers if o["status"] == "accepted")
    picked = next((json.loads(e["payload"])["at"] for e in query(
        "SELECT payload FROM events WHERE topic='donation.picked_up' AND payload LIKE ?", (f'%{donation_id}%',))), None)
    match = next(m for m in json.loads(d["matches"])["eligible"] if m["ngo_id"] == acc["ngo_id"])
    analysis = json.loads(d["analysis"])
    kg = round(d["servings"] * config.KG_PER_SERVING, 1)
    execute("UPDATE donations SET status='delivered' WHERE id=?", (donation_id,))
    execute("INSERT OR REPLACE INTO pickups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        donation_id, d["donor_id"], acc["ngo_id"], analysis.get("dish_name"), d["diet"], d["servings"], kg,
        round(kg * config.CO2E_PER_KG, 1), offers[0]["created_at"], acc["responded_at"], picked, iso(t),
        d["safe_until"], int(t <= parse(d["safe_until"])), len(offers), match["km"], d["simulated"]))
    publish("donation.delivered", {"donation_id": donation_id, "ngo_id": acc["ngo_id"], "servings": d["servings"]})
    return {"ok": True}


def tick():
    """Run every few seconds: time out unanswered offers, block food past its safe window."""
    t = now()
    for o in query("SELECT * FROM offers WHERE status='pending'"):
        if parse(o["expires_at"]) <= t:
            execute("UPDATE offers SET status='expired', responded_at=? WHERE id=?", (iso(t), o["id"]))
            publish("offer.expired", {"offer_id": o["id"], "donation_id": o["donation_id"], "ngo_id": o["ngo_id"]})
            _offer_next(o["donation_id"])
    for d in query("SELECT id, safe_until FROM donations WHERE status IN ('offered','accepted')"):
        if parse(d["safe_until"]) <= t:
            _expire_donation(d["id"], "Safe window passed")


def donation_view(donation_id):
    d = one("SELECT * FROM donations WHERE id=?", (donation_id,))
    if not d:
        return None
    for k in ("analysis", "safety", "matches"):
        d[k] = json.loads(d[k]) if d[k] else None
    d["donor"] = DONORS.get(d["donor_id"])
    d["offers"] = [o | {"ngo_name": NGOS[o["ngo_id"]]["name"]} for o in
                   query("SELECT * FROM offers WHERE donation_id=? ORDER BY created_at", (donation_id,))]
    d["server_time"] = iso(now())
    return d
