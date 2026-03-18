"""
config.py — Centralised configuration for SignalDeck AI.

All settings are loaded from environment variables (via .env).
Constants are typed and available for direct import throughout the project.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (one level up from wherever config.py lives)
_ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(_ENV_FILE)

# ── Storage backend ──────────────────────────────────────────────────────────
# "sqlite"   — default, zero-config
# "bigquery" — production GCP data warehouse
STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "sqlite").lower()

# ── SQLite ───────────────────────────────────────────────────────────────────
SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/signaldeck.db")

# ── BigQuery ─────────────────────────────────────────────────────────────────
GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
GCP_DATASET_ID: str = os.getenv("GCP_DATASET_ID", "signaldeck")
GCS_BUCKET: str = os.getenv("GCS_BUCKET", "")
GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# ── API Keys ─────────────────────────────────────────────────────────────────
ALPHA_VANTAGE_API_KEY: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")
NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── Reddit OAuth2 ─────────────────────────────────────────────────────────────
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "SignalDeck/1.0")

# ── StockTwits ───────────────────────────────────────────────────────────────
STOCKTWITS_ACCESS_TOKEN: str = os.getenv("STOCKTWITS_ACCESS_TOKEN", "")

# ── Pipeline ─────────────────────────────────────────────────────────────────
_raw_tickers: str = os.getenv("TICKERS", "AAPL,MSFT,GOOGL,AMZN,META")
TICKERS: list[str] = [t.strip().upper() for t in _raw_tickers.split(",") if t.strip()]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Airflow ───────────────────────────────────────────────────────────────────
AIRFLOW_HOME: str = os.getenv("AIRFLOW_HOME", "./airflow")

# ── Filesystem paths ──────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"

# Auto-create required directories so the project works out-of-the-box
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_storage_backend() -> str:
    """Return the active storage backend identifier ('sqlite' or 'bigquery')."""
    return STORAGE_BACKEND
