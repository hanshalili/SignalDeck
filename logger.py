"""
logger.py — Structured logging for SignalDeck AI (powered by Loguru).

Two sinks:
  1. Colorised stderr   — human-readable during development
  2. Rotating file      — 10 MB max, 14-day retention, gzip compression

Usage:
    from logger import log
    log.info("Pipeline started")
    log.debug("Fetched {n} rows", n=42)
"""
import sys
from pathlib import Path

from loguru import logger

import config

# Remove the default Loguru handler so we control all output
logger.remove()

# ── Sink 1: colourised stderr ─────────────────────────────────────────────────
logger.add(
    sys.stderr,
    level=config.LOG_LEVEL,
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    ),
)

# ── Sink 2: rotating file ─────────────────────────────────────────────────────
_log_file = config.LOGS_DIR / "signaldeck_{time:YYYY-MM-DD}.log"

logger.add(
    str(_log_file),
    level=config.LOG_LEVEL,
    rotation="10 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{name}:{line} — {message}"
    ),
)

# Public export — import this everywhere
log = logger
