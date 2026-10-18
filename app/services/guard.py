"""Cost guardrails: daily caps on paid API calls and per-IP rate limits on expensive endpoints."""
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import config
from ..store import execute, one

IST = timezone(timedelta(hours=5, minutes=30))
_ip_hits = {}
_lock = threading.Lock()


class LimitReached(Exception):
    pass


def _today():
    return datetime.now(IST).strftime("%Y-%m-%d")


def used(key):
    r = one("SELECT n FROM usage WHERE day=? AND key=?", (_today(), key))
    return r["n"] if r else 0


def take(key, limit, n=1):
    """Reserve n units of a daily budget (persisted in the DB, so restarts don't reset it)."""
    with _lock:
        if used(key) + n > limit:
            raise LimitReached(f"Daily {key} limit reached ({limit}/day) - cost protection, resets at midnight IST")
        execute("INSERT INTO usage (day, key, n) VALUES (?, ?, ?) ON CONFLICT(day, key) DO UPDATE SET n = n + ?",
                (_today(), key, n, n))


def remaining(key, limit):
    return max(limit - used(key), 0)


def ip_take(ip, bucket, per_hour):
    now = time.time()
    with _lock:
        hits = [t for t in _ip_hits.get((ip, bucket), []) if now - t < 3600]
        if len(hits) >= per_hour:
            raise LimitReached(f"Too many requests from your network ({per_hour}/hour) - please try again later")
        hits.append(now)
        _ip_hits[(ip, bucket)] = hits


def gemini_call(n=1):
    take("gemini_calls", config.GEMINI_DAILY_LIMIT, n)


def status():
    return {"gemini_calls_today": used("gemini_calls"), "gemini_daily_limit": config.GEMINI_DAILY_LIMIT,
            "route_elements_today": used("route_elements"), "routes_daily_limit": config.ROUTES_DAILY_LIMIT}
