"""Thin adapters for Cloud Storage, Pub/Sub and BigQuery.

Every function is a no-op (or returns None) when its service is not configured, and never raises:
a cloud hiccup must not break a donation in progress. Errors are printed to the log.
"""
import json
import threading
import time

from .. import config

_clients = {}
_lock = threading.Lock()


def _client(kind):
    with _lock:
        if kind not in _clients:
            if kind == "storage":
                from google.cloud import storage
                _clients[kind] = storage.Client(project=config.GCP_PROJECT or None).bucket(config.GCS_BUCKET)
            elif kind == "pubsub":
                from google.cloud import pubsub_v1
                pub = pubsub_v1.PublisherClient()
                _clients[kind] = (pub, pub.topic_path(config.GCP_PROJECT, config.PUBSUB_TOPIC))
            elif kind == "bigquery":
                from google.cloud import bigquery
                _clients[kind] = bigquery.Client(project=config.GCP_PROJECT or None)
        return _clients[kind]


# ---------------- Cloud Storage ----------------
def gcs_enabled():
    return bool(config.GCS_BUCKET)


def gcs_upload(path, data: bytes, content_type="application/octet-stream"):
    if not gcs_enabled():
        return False
    try:
        _client("storage").blob(path).upload_from_string(data, content_type=content_type)
        return True
    except Exception as e:
        print(f"[gcs] upload {path} failed: {e}")
        return False


def gcs_download(path):
    if not gcs_enabled():
        return None
    try:
        blob = _client("storage").blob(path)
        return blob.download_as_bytes() if blob.exists() else None
    except Exception as e:
        print(f"[gcs] download {path} failed: {e}")
        return None


# ---------------- Pub/Sub ----------------
def publish(topic, payload, ts):
    """Fire-and-forget; a BigQuery subscription writes every message into foodbridge.events."""
    if not (config.PUBSUB_TOPIC and config.GCP_PROJECT):
        return
    try:
        pub, path = _client("pubsub")
        data = json.dumps({"topic": topic, "ts": ts, "payload": payload}).encode()
        pub.publish(path, data, event_type=topic)
    except Exception as e:
        print(f"[pubsub] publish {topic} failed: {e}")


# ---------------- BigQuery ----------------
def bq_enabled():
    return bool(config.BQ_DATASET and config.GCP_PROJECT)


def bq_table(name):
    return f"{config.GCP_PROJECT}.{config.BQ_DATASET}.{name}"


def bq_insert(table, rows):
    if not bq_enabled() or not rows:
        return
    try:
        errors = _client("bigquery").insert_rows_json(bq_table(table), rows)
        if errors:
            print(f"[bq] insert into {table} errors: {errors[:2]}")
    except Exception as e:
        print(f"[bq] insert into {table} failed: {e}")


def bq_load(table, rows):
    """Batch load job (free, unlike streaming); used once to seed history."""
    if not bq_enabled() or not rows:
        return 0
    from google.cloud import bigquery
    job = _client("bigquery").load_table_from_json(
        rows, bq_table(table), job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"))
    job.result(timeout=120)
    return len(rows)


_cache = {}


def bq_query(sql, ttl_s=300):
    """Cached query: every query bills at least 10 MB, so dashboards must not hit BigQuery on each refresh."""
    if not bq_enabled():
        return None
    hit = _cache.get(sql)
    if hit and time.time() - hit[0] < ttl_s:
        return hit[1]
    try:
        rows = [dict(r) for r in _client("bigquery").query(sql).result(timeout=30)]
        _cache[sql] = (time.time(), rows)
        return rows
    except Exception as e:
        print(f"[bq] query failed: {e}")
        return hit[1] if hit else None
