"""Reference data (NGOs, donors) in memory + transactional data in SQLite.

SQLite stands in for BigQuery locally; the pickups table has the same shape we'll stream to BigQuery.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pandas as pd

from . import config

_lock = threading.Lock()
_conn = None
DIRTY = {"since": None}  # set on writes; persist.py snapshots the DB to Cloud Storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS donations (
  id TEXT PRIMARY KEY, donor_id TEXT, photo_path TEXT, created_at TEXT,
  status TEXT,                -- draft / offered / accepted / picked_up / delivered / expired / unmatched
  analysis TEXT,              -- Gemini JSON
  servings INTEGER, diet TEXT, cooked_at TEXT, storage TEXT,
  safe_until TEXT, safety TEXT, matches TEXT,
  simulated INTEGER DEFAULT 0,  -- 0 live, 1 simulated history, 2 live demo scenario
  offer_timeout_s INTEGER
);
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, donation_id TEXT, ngo_id TEXT, rank INTEGER,
  status TEXT,                -- pending / accepted / declined / expired / cancelled
  created_at TEXT, expires_at TEXT, responded_at TEXT
);
CREATE TABLE IF NOT EXISTS pickups (
  donation_id TEXT PRIMARY KEY, donor_id TEXT, ngo_id TEXT, dish TEXT, diet TEXT,
  servings INTEGER, kg REAL, co2e_kg REAL,
  offered_at TEXT, accepted_at TEXT, picked_up_at TEXT, delivered_at TEXT, safe_until TEXT,
  within_window INTEGER, offers_made INTEGER, distance_km REAL, simulated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT, key TEXT, n INTEGER, PRIMARY KEY (day, key)   -- daily cost-guard counters
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, topic TEXT, payload TEXT
);
"""


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse(s):
    return datetime.fromisoformat(s) if s else None


def db():
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(donations)")}
        if "offer_timeout_s" not in cols:  # per-donation override (demo scenario uses short offers)
            _conn.execute("ALTER TABLE donations ADD COLUMN offer_timeout_s INTEGER")
    return _conn


def execute(sql, params=()):
    with _lock:
        cur = db().execute(sql, params)
        db().commit()
        DIRTY["since"] = DIRTY["since"] or now()
        return cur


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in db().execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def publish(topic, payload):
    """Events table feeds the live UI; Pub/Sub (when configured) streams the same event into BigQuery."""
    ts = iso(now())
    execute("INSERT INTO events (ts, topic, payload) VALUES (?, ?, ?)", (ts, topic, json.dumps(payload)))
    from .services import cloud
    cloud.publish(topic, payload, ts)


# ---------------- reference data ----------------
def _records(path):
    df = pd.read_csv(path)
    df = df.astype(object).where(df.notna(), None)
    return {r[df.columns[0]]: r for r in df.to_dict("records")}


NGOS = _records(config.NGOS_CSV)
DONORS = _records(config.DONORS_CSV)
