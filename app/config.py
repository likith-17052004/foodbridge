"""All settings come from environment variables (or a local .env file)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "").lower()  # vertex / gemini / demo; blank = auto
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")  # gemini-3.6-flash is served from the global endpoint

# --- data ---
DATA_DIR = ROOT / "data"
NGOS_CSV = DATA_DIR / "processed/ngos_demo.csv"
DONORS_CSV = DATA_DIR / "processed/donors_demo.csv"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "foodbridge.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))

# --- matching / offers ---
OFFER_TIMEOUT_SECONDS = int(os.getenv("OFFER_TIMEOUT_SECONDS", "600"))  # 10 min per the spec
SERVE_BUFFER_MIN = int(os.getenv("SERVE_BUFFER_MIN", "30"))  # food must arrive this long before it expires
LOADING_MIN = 10  # packing + handover at the donor

# --- impact factors ---
KG_PER_SERVING = 0.35  # typical cooked-meal portion
CO2E_PER_KG = 2.5      # FAO Food Wastage Footprint: 3.3 Gt CO2e / 1.3 Gt food wasted

# --- Google Cloud services (all optional: unset = local stand-in) ---
GCS_BUCKET = os.getenv("GCS_BUCKET", "")            # photos/ and state/ snapshots
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "")        # e.g. foodbridge-events
BQ_DATASET = os.getenv("BQ_DATASET", "")            # e.g. foodbridge
ROUTES_API = os.getenv("ROUTES_API", "").lower() in ("1", "true", "on")

# --- cost guardrails ---
GEMINI_DAILY_LIMIT = int(os.getenv("GEMINI_DAILY_LIMIT", "150"))    # Gemini calls per day, whole app
UPLOADS_PER_IP_HOUR = int(os.getenv("UPLOADS_PER_IP_HOUR", "20"))
RUSHES_PER_IP_HOUR = int(os.getenv("RUSHES_PER_IP_HOUR", "3"))
ROUTES_DAILY_LIMIT = int(os.getenv("ROUTES_DAILY_LIMIT", "150"))    # route-matrix elements per day (~4.5k/month)
SNAPSHOT_EVERY_S = int(os.getenv("SNAPSHOT_EVERY_S", "30"))
