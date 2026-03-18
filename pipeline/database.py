"""
pipeline/database.py — Unified storage layer for SignalDeck AI.

Supports two backends selected via STORAGE_BACKEND env var:
  - sqlite   (default) — local SQLite file, zero dependencies
  - bigquery           — Google BigQuery with partitioning + clustering

Public API
----------
init_db()
insert_stock_prices(rows)
insert_news_articles(rows)
insert_sentiment_scores(rows)
insert_social_signals(rows)
insert_fundamentals(rows)
insert_processed_features(rows)
insert_llm_analysis(rows)
insert_agent_recommendations(rows)
insert_signals(rows)
insert_llm_insights(rows)
query(table, ticker, limit)
get_latest_stock_price(ticker)
get_latest_features(ticker)
get_latest_news(ticker, limit)
get_latest_sentiment(ticker, limit)
get_latest_social(ticker, limit)
get_latest_signal(ticker)
get_latest_insight(ticker)
get_stock_history(ticker, days)
get_news_history(ticker, days)
get_signal_history(ticker, days)
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import config
from logger import log

# ─────────────────────────────────────────────────────────────────────────────
# Table schemas
# ─────────────────────────────────────────────────────────────────────────────

SCHEMAS: dict[str, dict] = {
    "stock_prices": {
        "columns": [
            ("ticker", "TEXT NOT NULL"),
            ("date", "TEXT NOT NULL"),
            ("open", "REAL"),
            ("high", "REAL"),
            ("low", "REAL"),
            ("close", "REAL"),
            ("volume", "INTEGER"),
            ("source", "TEXT DEFAULT 'alpha_vantage'"),
            ("ingested_at", "TEXT"),
        ],
        "primary_key": ("ticker", "date"),
        "bq_partition": "date",
        "bq_cluster": ["ticker"],
    },
    "news_articles": {
        "columns": [
            ("article_id", "TEXT NOT NULL"),
            ("ticker", "TEXT NOT NULL"),
            ("title", "TEXT"),
            ("description", "TEXT"),
            ("source_name", "TEXT"),
            ("published_at", "TEXT"),
            ("sentiment", "TEXT"),
            ("sentiment_score", "REAL"),
            ("url", "TEXT"),
            ("ingested_at", "TEXT"),
        ],
        "primary_key": ("article_id",),
        "bq_partition": "published_at",
        "bq_cluster": ["ticker", "sentiment"],
    },
    "sentiment_scores": {
        "columns": [
            ("score_id",   "TEXT NOT NULL"),   # deterministic UUID: article_id + method
            ("article_id", "TEXT NOT NULL"),   # FK → news_articles.article_id
            ("ticker",     "TEXT NOT NULL"),
            ("headline",   "TEXT"),            # article title (denormalised for fast reads)
            ("compound",   "REAL"),            # overall score [-1.0, +1.0]
            ("positive",   "REAL"),            # positive component [0.0, 1.0]
            ("negative",   "REAL"),            # negative component [0.0, 1.0]
            ("neutral",    "REAL"),            # neutral component  [0.0, 1.0]
            ("label",      "TEXT"),            # positive | negative | neutral
            ("method",     "TEXT"),            # vader | alpha_vantage | passthrough
            ("scored_at",  "TEXT"),
        ],
        "primary_key": ("score_id",),
        "bq_partition": "scored_at",           # TIMESTAMP — enables date-range pruning
        "bq_cluster": ["ticker", "label"],     # fast per-ticker sentiment queries
    },
    "social_signals": {
        "columns": [
            ("signal_id", "TEXT NOT NULL"),
            ("ticker", "TEXT NOT NULL"),
            ("source", "TEXT"),
            ("content", "TEXT"),
            ("sentiment_score", "REAL"),
            ("bullish_pct", "REAL"),
            ("bearish_pct", "REAL"),
            ("volume", "INTEGER"),
            ("published_at", "TEXT"),
            ("ingested_at", "TEXT"),
        ],
        "primary_key": ("signal_id",),
        "bq_partition": "published_at",
        "bq_cluster": ["ticker", "source"],
    },
    "fundamentals": {
        "columns": [
            ("ticker", "TEXT NOT NULL"),
            ("market_cap", "REAL"),
            ("pe_ratio", "REAL"),
            ("forward_pe", "REAL"),
            ("peg_ratio", "REAL"),
            ("price_to_book", "REAL"),
            ("dividend_yield", "REAL"),
            ("eps", "REAL"),
            ("revenue_ttm", "REAL"),
            ("profit_margin", "REAL"),
            ("debt_to_equity", "REAL"),
            ("analyst_target", "REAL"),
            ("week52_high", "REAL"),
            ("week52_low", "REAL"),
            ("beta", "REAL"),
            ("sector", "TEXT"),
            ("ingested_at", "TEXT"),
        ],
        "primary_key": ("ticker",),
        "bq_partition": None,
        "bq_cluster": ["sector"],
    },
    "processed_features": {
        "columns": [
            ("ticker",              "TEXT NOT NULL"),
            ("date",                "TEXT NOT NULL"),
            # ── Price reference ──────────────────────────────────────────────
            ("close",               "REAL"),            # latest closing price
            # ── Return & momentum ────────────────────────────────────────────
            ("daily_return",        "REAL"),            # 1-day % return
            ("momentum_5d",         "REAL"),            # 5-day % price change
            ("momentum_20d",        "REAL"),            # 20-day % price change
            # ── Trend indicators ─────────────────────────────────────────────
            ("sma_5",               "REAL"),            # 5-day simple MA
            ("sma_20",              "REAL"),            # 20-day simple MA
            ("sma_50",              "REAL"),            # 50-day simple MA
            ("rsi_14",              "REAL"),            # Wilder's RSI (14 periods)
            # ── Volatility & volume ──────────────────────────────────────────
            ("volatility_20d",      "REAL"),            # 20-day annualized vol
            ("volume_avg_20d",      "REAL"),            # 20-day avg daily volume
            ("volume_change_pct",   "REAL"),            # 1-day volume change %
            # ── Sentiment aggregates ─────────────────────────────────────────
            ("avg_sentiment_score", "REAL"),            # mean news sentiment
            ("news_count",          "INTEGER"),         # recent article count
            ("social_bullish_pct",  "REAL"),            # avg bullish % (social)
            ("created_at",          "TEXT"),
        ],
        "primary_key": ("ticker", "date"),
        "bq_partition": "date",
        "bq_cluster": ["ticker"],
    },
    "llm_analysis": {
        "columns": [
            ("analysis_id", "TEXT NOT NULL"),
            ("ticker", "TEXT NOT NULL"),
            ("date", "TEXT NOT NULL"),
            ("sentiment", "TEXT"),
            ("trend", "TEXT"),
            ("risk_level", "TEXT"),
            ("recommendation", "TEXT"),
            ("confidence", "TEXT"),
            ("price_target", "REAL"),
            ("key_observations", "TEXT"),  # JSON list
            ("model_used", "TEXT"),
            ("raw_response", "TEXT"),
            ("created_at", "TEXT"),
        ],
        "primary_key": ("analysis_id",),
        "bq_partition": "date",
        "bq_cluster": ["ticker", "recommendation"],
    },
    "agent_recommendations": {
        "columns": [
            ("rec_id", "TEXT NOT NULL"),
            ("ticker", "TEXT NOT NULL"),
            ("date", "TEXT NOT NULL"),
            ("action", "TEXT"),
            ("rationale", "TEXT"),
            ("confidence_score", "REAL"),
            ("entry_price", "REAL"),
            ("stop_loss", "REAL"),
            ("take_profit", "REAL"),
            ("time_horizon", "TEXT"),
            ("agent_trace", "TEXT"),  # JSON
            ("created_at", "TEXT"),
        ],
        "primary_key": ("rec_id",),
        "bq_partition": "date",
        "bq_cluster": ["ticker", "action"],
    },
    "signal_history": {
        "columns": [
            # ── Identity ─────────────────────────────────────────────────────
            ("signal_id",        "TEXT NOT NULL"),   # UUID
            ("ticker",           "TEXT NOT NULL"),
            ("date",             "TEXT NOT NULL"),   # YYYY-MM-DD
            # ── Final output (what the frontend shows) ───────────────────────
            ("signal",           "TEXT"),            # buy | hold | sell
            ("confidence_score", "REAL"),            # 0.30 – 0.90
            ("supporting_reason","TEXT"),            # plain-English explanation
            # ── Sub-signal verdicts (for frontend breakdown cards) ────────────
            ("trend_signal",     "TEXT"),            # bullish | neutral | bearish
            ("momentum_signal",  "TEXT"),            # bullish | neutral | bearish
            ("volatility_signal","TEXT"),            # label + annualised value
            ("sentiment_signal", "TEXT"),            # bullish | neutral | bearish
            # ── Raw score for sorting / filtering ────────────────────────────
            ("raw_score",        "INTEGER"),         # -3 to +3
            ("created_at",       "TEXT"),
        ],
        "primary_key": ("signal_id",),
        "bq_partition": "date",
        "bq_cluster": ["ticker", "signal"],
    },
    "llm_insights": {
        "columns": [
            # ── Identity ─────────────────────────────────────────────────────
            ("insight_id",        "TEXT NOT NULL"),  # UUID
            ("ticker",            "TEXT NOT NULL"),
            ("date",              "TEXT NOT NULL"),  # YYYY-MM-DD
            # ── Five insight fields (what the frontend renders) ───────────────
            ("what_is_happening", "TEXT"),  # 1-2 sentences: current price/signal state
            ("why_signal_changed","TEXT"),  # 1 sentence: what drove the signal
            ("bull_case",         "TEXT"),  # 1-2 sentences: upside scenario
            ("bear_case",         "TEXT"),  # 1-2 sentences: downside risk
            ("summary",           "TEXT"),  # 1 sentence: bottom line
            # ── Provenance ───────────────────────────────────────────────────
            ("signal_ref",        "TEXT"),  # FK → signal_history.signal_id
            ("model_used",        "TEXT"),  # gpt-4o-mini | claude-haiku-... | rule_based
            ("raw_response",      "TEXT"),  # raw LLM text for debugging
            ("created_at",        "TEXT"),
        ],
        "primary_key": ("insight_id",),
        "bq_partition": "date",
        "bq_cluster": ["ticker"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SQLite backend
# ─────────────────────────────────────────────────────────────────────────────

def _sqlite_conn() -> sqlite3.Connection:
    db_path = Path(config.SQLITE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sqlite_col_defs(schema: dict) -> str:
    parts = [f"{col} {dtype}" for col, dtype in schema["columns"]]
    pk = schema["primary_key"]
    parts.append(f"PRIMARY KEY ({', '.join(pk)})")
    return ", ".join(parts)


def _sqlite_init_db() -> None:
    conn = _sqlite_conn()
    with conn:
        for table, schema in SCHEMAS.items():
            col_defs = _sqlite_col_defs(schema)
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})"
            )
    conn.close()
    log.debug("SQLite schema initialised at {}", config.SQLITE_DB_PATH)


def _sqlite_upsert(table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    schema = SCHEMAS[table]
    cols = [c for c, _ in schema["columns"]]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    pk = schema["primary_key"]
    update_cols = [c for c in cols if c not in pk]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(pk)}) DO UPDATE SET {update_clause}"
    )

    values = [[row.get(c) for c in cols] for row in rows]
    conn = _sqlite_conn()
    with conn:
        conn.executemany(sql, values)
    conn.close()
    return len(rows)


def _sqlite_query(
    table: str,
    ticker: str | None = None,
    limit: int = 100,
    order_by: str = "rowid DESC",
    date_col: str | None = None,
    date_cutoff: str | None = None,
) -> list[dict]:
    """
    Parameterised SQLite query. All user-supplied values go through
    placeholders — never interpolated into the SQL string.
    `order_by` is from internal callers only (never user input).
    """
    conn = _sqlite_conn()
    where_parts = []
    params: list[Any] = []
    if ticker:
        where_parts.append("ticker = ?")
        params.append(ticker)
    if date_col and date_cutoff:
        where_parts.append(f"{date_col} >= ?")
        params.append(date_cutoff)
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = f"SELECT * FROM {table} {where_clause} ORDER BY {order_by} LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# BigQuery backend
# ─────────────────────────────────────────────────────────────────────────────

def _bq_client():
    try:
        from google.cloud import bigquery
        return bigquery.Client(project=config.GCP_PROJECT_ID)
    except Exception as exc:
        log.error("BigQuery client init failed: {}", exc)
        raise


def _bq_full_table(table: str) -> str:
    return f"{config.GCP_PROJECT_ID}.{config.GCP_DATASET_ID}.{table}"


def _bq_col_type(col: str, sqlite_type: str) -> str:
    """Map column name + SQLite type to the correct BigQuery type."""
    # Partition columns must be DATE or TIMESTAMP in BigQuery
    if col == "date":
        return "DATE"
    if col.endswith("_at"):
        return "TIMESTAMP"
    t = sqlite_type.upper()
    if "REAL" in t or "FLOAT" in t:
        return "FLOAT64"
    if "INTEGER" in t or "INT" in t:
        return "INT64"
    return "STRING"


def _bq_init_db() -> None:
    from google.cloud import bigquery

    client = _bq_client()
    dataset_ref = client.dataset(config.GCP_DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        ds = bigquery.Dataset(dataset_ref)
        ds.location = "US"
        client.create_dataset(ds)
        log.info("Created BigQuery dataset {}", config.GCP_DATASET_ID)

    for table, schema in SCHEMAS.items():
        bq_schema = [
            bigquery.SchemaField(col, _bq_col_type(col, dtype))
            for col, dtype in schema["columns"]
        ]
        table_ref = client.dataset(config.GCP_DATASET_ID).table(table)
        bq_table = bigquery.Table(table_ref, schema=bq_schema)

        partition_col = schema.get("bq_partition")
        if partition_col:
            col_bq_type = _bq_col_type(partition_col, "TEXT")
            if col_bq_type == "DATE":
                bq_table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY,
                    field=partition_col,
                )
            elif col_bq_type == "TIMESTAMP":
                bq_table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY,
                    field=partition_col,
                )

        if schema.get("bq_cluster"):
            bq_table.clustering_fields = schema["bq_cluster"]

        try:
            client.get_table(bq_table)
        except Exception:
            client.create_table(bq_table)
            log.info("Created BigQuery table {}", table)


def _bq_dtype(sqlite_type: str) -> str:
    t = sqlite_type.upper()
    if "TEXT" in t:
        return "STRING"
    if "REAL" in t or "FLOAT" in t:
        return "FLOAT64"
    if "INTEGER" in t or "INT" in t:
        return "INT64"
    return "STRING"


def _bq_merge(table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    client = _bq_client()
    errors = client.insert_rows_json(_bq_full_table(table), rows)
    if errors:
        log.error("BigQuery insert errors for {}: {}", table, errors)
    return len(rows)


def _bq_timestamp_col(table: str) -> str:
    """Return the best timestamp column for ORDER BY in the given table."""
    cols = {c for c, _ in SCHEMAS[table]["columns"]}
    if "ingested_at" in cols:
        return "ingested_at"
    if "created_at" in cols:
        return "created_at"
    return "_PARTITIONTIME"


def _bq_query(
    table: str,
    ticker: str | None = None,
    limit: int = 100,
    order_by: str | None = None,
    date_col: str | None = None,
    date_cutoff: str | None = None,
) -> list[dict]:
    """
    Parameterised BigQuery SELECT. ticker and date_cutoff are passed as
    named query parameters — never interpolated into the SQL string.
    """
    from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

    client = _bq_client()
    if order_by is None:
        order_by = f"{_bq_timestamp_col(table)} DESC"

    conditions: list[str] = []
    bq_params: list[ScalarQueryParameter] = []

    if ticker:
        conditions.append("ticker = @ticker")
        bq_params.append(ScalarQueryParameter("ticker", "STRING", ticker))
    if date_col and date_cutoff:
        conditions.append(f"{date_col} >= @date_cutoff")
        bq_params.append(ScalarQueryParameter("date_cutoff", "STRING", date_cutoff))

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT * FROM `{_bq_full_table(table)}` "
        f"{where} ORDER BY {order_by} LIMIT {limit}"
    )
    job_config = QueryJobConfig(query_parameters=bq_params)
    return [dict(row) for row in client.query(sql, job_config=job_config).result()]


# ─────────────────────────────────────────────────────────────────────────────
# Public API — backend-agnostic
# ─────────────────────────────────────────────────────────────────────────────

def _table(name: str) -> str:
    """Return the table name (useful for constructing queries externally)."""
    if name not in SCHEMAS:
        raise ValueError(f"Unknown table: {name}")
    return name


def init_db() -> None:
    """Initialise all tables in the configured storage backend."""
    backend = config.get_storage_backend()
    if backend == "bigquery":
        _bq_init_db()
    else:
        _sqlite_init_db()
    log.info("Database initialised (backend={})", backend)


def _insert(table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.utcnow().isoformat()
    schema_cols = {c for c, _ in SCHEMAS[table]["columns"]}
    for row in rows:
        if "ingested_at" in schema_cols and not row.get("ingested_at"):
            row["ingested_at"] = now
        if "created_at" in schema_cols and not row.get("created_at"):
            row["created_at"] = now
        # Remove any keys not in the schema (prevents BQ "no such field" errors)
        for key in list(row.keys()):
            if key not in schema_cols:
                del row[key]

    backend = config.get_storage_backend()
    n = _bq_merge(table, rows) if backend == "bigquery" else _sqlite_upsert(table, rows)
    log.debug("Inserted {} rows into {}", n, table)
    return n


def insert_stock_prices(rows: list[dict]) -> int:
    return _insert("stock_prices", rows)


def insert_news_articles(rows: list[dict]) -> int:
    return _insert("news_articles", rows)


def insert_sentiment_scores(rows: list[dict]) -> int:
    return _insert("sentiment_scores", rows)


def insert_social_signals(rows: list[dict]) -> int:
    return _insert("social_signals", rows)


def insert_fundamentals(rows: list[dict]) -> int:
    return _insert("fundamentals", rows)


def insert_processed_features(rows: list[dict]) -> int:
    return _insert("processed_features", rows)


def insert_llm_analysis(rows: list[dict]) -> int:
    return _insert("llm_analysis", rows)


def insert_agent_recommendations(rows: list[dict]) -> int:
    return _insert("agent_recommendations", rows)


def insert_signals(rows: list[dict]) -> int:
    return _insert("signal_history", rows)


def insert_llm_insights(rows: list[dict]) -> int:
    return _insert("llm_insights", rows)


def query(
    table: str,
    ticker: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Generic query across any table."""
    backend = config.get_storage_backend()
    if backend == "bigquery":
        return _bq_query(table, ticker, limit)
    return _sqlite_query(table, ticker, limit)


def get_latest_features(ticker: str) -> dict | None:
    """Return the most recent processed_features row for a ticker."""
    if config.get_storage_backend() == "bigquery":
        rows = _bq_query("processed_features", ticker=ticker, limit=1, order_by="date DESC")
    else:
        rows = _sqlite_query("processed_features", ticker=ticker, limit=1, order_by="date DESC")
    return rows[0] if rows else None


def get_latest_stock_price(ticker: str) -> dict | None:
    if config.get_storage_backend() == "bigquery":
        rows = _bq_query("stock_prices", ticker=ticker, limit=1, order_by="date DESC")
    else:
        rows = _sqlite_query("stock_prices", ticker=ticker, limit=1, order_by="date DESC")
    return rows[0] if rows else None


def get_latest_sentiment(ticker: str, limit: int = 20) -> list[dict]:
    """Return most recent sentiment_scores rows for a ticker."""
    if config.get_storage_backend() == "bigquery":
        return _bq_query("sentiment_scores", ticker=ticker, limit=limit, order_by="scored_at DESC")
    return _sqlite_query("sentiment_scores", ticker=ticker, limit=limit, order_by="scored_at DESC")


def get_latest_news(ticker: str, limit: int = 10) -> list[dict]:
    if config.get_storage_backend() == "bigquery":
        return _bq_query("news_articles", ticker=ticker, limit=limit, order_by="published_at DESC")
    return _sqlite_query("news_articles", ticker=ticker, limit=limit, order_by="published_at DESC")


def get_latest_social(ticker: str, limit: int = 10) -> list[dict]:
    if config.get_storage_backend() == "bigquery":
        return _bq_query("social_signals", ticker=ticker, limit=limit, order_by="published_at DESC")
    return _sqlite_query("social_signals", ticker=ticker, limit=limit, order_by="published_at DESC")


def get_stock_history(ticker: str, days: int = 30) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    if config.get_storage_backend() == "bigquery":
        return _bq_query(
            "stock_prices", ticker=ticker, limit=days + 5,
            order_by="date DESC", date_col="date", date_cutoff=cutoff,
        )
    return _sqlite_query(
        "stock_prices", ticker=ticker, limit=days + 5,
        order_by="date DESC", date_col="date", date_cutoff=cutoff,
    )


def get_latest_signal(ticker: str) -> dict | None:
    """Return the most recent signal_history row for a ticker."""
    if config.get_storage_backend() == "bigquery":
        rows = _bq_query("signal_history", ticker=ticker, limit=1, order_by="date DESC")
    else:
        rows = _sqlite_query("signal_history", ticker=ticker, limit=1, order_by="date DESC")
    return rows[0] if rows else None


def get_latest_insight(ticker: str) -> dict | None:
    """Return the most recent llm_insights row for a ticker."""
    if config.get_storage_backend() == "bigquery":
        rows = _bq_query("llm_insights", ticker=ticker, limit=1, order_by="date DESC")
    else:
        rows = _sqlite_query("llm_insights", ticker=ticker, limit=1, order_by="date DESC")
    return rows[0] if rows else None


def get_signal_history(ticker: str, days: int = 30) -> list[dict]:
    """Return recent signal_history rows for a ticker."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    if config.get_storage_backend() == "bigquery":
        return _bq_query(
            "signal_history", ticker=ticker, limit=days + 5,
            order_by="date DESC", date_col="date", date_cutoff=cutoff,
        )
    return _sqlite_query(
        "signal_history", ticker=ticker, limit=days + 5,
        order_by="date DESC", date_col="date", date_cutoff=cutoff,
    )


def get_news_history(ticker: str, days: int = 7) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    if config.get_storage_backend() == "bigquery":
        return _bq_query(
            "news_articles", ticker=ticker, limit=50,
            order_by="published_at DESC", date_col="published_at", date_cutoff=cutoff,
        )
    return _sqlite_query(
        "news_articles", ticker=ticker, limit=50,
        order_by="published_at DESC", date_col="published_at", date_cutoff=cutoff,
    )
