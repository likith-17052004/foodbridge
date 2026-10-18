"""Container start-up: restore state from Cloud Storage, or seed simulated history on first boot.

Run before the web server:  python -m app.bootstrap && uvicorn app.main:app ...
"""
import subprocess
import sys

from . import config
from .services import cloud, persist


def main():
    restored = persist.restore()
    print(f"[bootstrap] state restored from Cloud Storage: {restored}")
    from .store import NGOS, query
    if not query("SELECT 1 FROM donations LIMIT 1"):
        subprocess.run([sys.executable, str(config.ROOT / "scripts/simulate_history.py"), "--days", "45"], check=True)
        print("[bootstrap] seeded simulated history")
    if cloud.bq_enabled():
        rows = cloud.bq_query(f"SELECT COUNT(*) n FROM `{cloud.bq_table('pickups')}`", ttl_s=0)
        if rows is not None and rows[0]["n"] == 0:
            hist = query("SELECT * FROM pickups WHERE simulated = 1")
            for r in hist:
                r["within_window"] = bool(r["within_window"])
                r["ngo_category"] = NGOS.get(r["ngo_id"], {}).get("category")
            print(f"[bootstrap] loaded {cloud.bq_load('pickups', hist)} history pickups into BigQuery")
    persist.snapshot(force=True)


if __name__ == "__main__":
    main()
