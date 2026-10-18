"""Keep the SQLite state across Cloud Run scale-to-zero: snapshot to Cloud Storage, restore on start."""
import sqlite3
import time

from .. import config
from ..store import DIRTY, db
from . import cloud

STATE_BLOB = "state/foodbridge.db"
_last = {"at": 0.0}


def restore():
    """Download the last snapshot before the app opens the DB. Returns True if one existed."""
    data = cloud.gcs_download(STATE_BLOB)
    if not data:
        return False
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.DB_PATH.write_bytes(data)
    return True


def snapshot(force=False):
    if not cloud.gcs_enabled() or (not force and not DIRTY["since"]):
        return False
    if not force and time.time() - _last["at"] < config.SNAPSHOT_EVERY_S:
        return False
    tmp = config.DB_PATH.with_suffix(".snapshot")
    dst = sqlite3.connect(tmp)
    db().backup(dst)  # consistent copy while the app keeps writing
    dst.close()
    DIRTY["since"] = None
    _last["at"] = time.time()
    ok = cloud.gcs_upload(STATE_BLOB, tmp.read_bytes(), "application/x-sqlite3")
    tmp.unlink(missing_ok=True)
    return ok
