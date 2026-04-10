"""
pipeline/gcs_writer.py — GCS raw-zone staging writer for SignalDeck AI.

Uploads ingested records as newline-delimited JSON (NDJSON) to a GCS bucket
before they are loaded into BigQuery.  This creates an immutable raw layer
that can be reloaded or inspected independently of the warehouse.

Path convention
───────────────
    gs://<GCS_BUCKET>/raw/<source>/<YYYY-MM-DD>/<source>_<HHMMSSffffff>.ndjson

Only active when:
    STORAGE_BACKEND = bigquery
    GCS_BUCKET      = <non-empty>

Falls back gracefully (logs a warning, returns None) if:
    - google-cloud-storage is not installed
    - GCS_BUCKET is not set
    - Any network or permission error occurs
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import config
from logger import log

# Optional dependency — google-cloud-storage may not be present in dev/test
_GCS_AVAILABLE = False
try:
    from google.cloud import storage as _gcs_lib  # type: ignore[import]
    _GCS_AVAILABLE = True
except ImportError:
    pass


def _should_stage() -> bool:
    """Return True only when GCS staging is both configured and meaningful."""
    return (
        config.STORAGE_BACKEND == "bigquery"
        and bool(config.GCS_BUCKET)
        and _GCS_AVAILABLE
    )


def _get_client():
    """Return an authenticated GCS client using the configured credentials."""
    import os
    if config.GOOGLE_APPLICATION_CREDENTIALS:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config.GOOGLE_APPLICATION_CREDENTIALS
    return _gcs_lib.Client(project=config.GCP_PROJECT_ID or None)


def _build_blob_path(source: str) -> str:
    """
    Construct a deterministic GCS blob path for a given source name.

    Example:
        source='stocks'  →  raw/stocks/2024-05-14/stocks_143022123456.ndjson
    """
    today = date.today().isoformat()
    ts = datetime.utcnow().strftime("%H%M%S%f")
    return f"raw/{source}/{today}/{source}_{ts}.ndjson"


def upload_raw_to_gcs(
    records: list[dict[str, Any]],
    source: str,
) -> str | None:
    """
    Upload a list of record dicts as NDJSON to the configured GCS bucket.

    Parameters
    ----------
    records:
        Flat list of dicts to serialise.  Each dict becomes one NDJSON line.
    source:
        Logical source name used in the blob path, e.g. 'stocks', 'news',
        'social', 'fundamentals'.

    Returns
    -------
    str | None
        The full GCS URI (``gs://<bucket>/<blob>``) on success, or ``None``
        if staging is disabled or an error occurs.
    """
    if not _should_stage():
        return None

    if not records:
        log.debug("gcs_writer: no records to stage for source={}", source)
        return None

    try:
        client = _get_client()
        bucket = client.bucket(config.GCS_BUCKET)
        blob_path = _build_blob_path(source)
        blob = bucket.blob(blob_path)

        ndjson_bytes = "\n".join(json.dumps(r, default=str) for r in records).encode("utf-8")
        blob.upload_from_string(ndjson_bytes, content_type="application/x-ndjson")

        uri = f"gs://{config.GCS_BUCKET}/{blob_path}"
        log.info("GCS staged {} records → {}", len(records), uri)
        return uri

    except Exception as exc:  # noqa: BLE001
        log.warning("GCS staging failed for source={}: {} — continuing without staging", source, exc)
        return None
