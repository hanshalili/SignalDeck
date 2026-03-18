"""
pipeline/ingest_stocks.py — Stock price ingestion for SignalDeck AI.

Primary source : Alpha Vantage TIME_SERIES_DAILY
Fallback       : Geometric Brownian Motion (GBM) mock data (seeded by ticker)

The fallback ensures the pipeline runs end-to-end with zero API keys.
"""
import hashlib
import math
import random
from datetime import date, timedelta
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import config
from logger import log
from pipeline.database import insert_stock_prices

# ── Constants ─────────────────────────────────────────────────────────────────

_AV_URL = "https://www.alphavantage.co/query"
_AV_FUNCTION = "TIME_SERIES_DAILY"
_OUTPUT_SIZE = "compact"  # last 100 trading days

# Realistic base prices per ticker for GBM seeding
_BASE_PRICES: dict[str, float] = {
    "AAPL": 185.0,
    "MSFT": 420.0,
    "GOOGL": 175.0,
    "AMZN": 185.0,
    "META": 500.0,
    "TSLA": 210.0,
    "NVDA": 850.0,
    "JPM": 195.0,
    "V": 275.0,
    "JNJ": 155.0,
}
_DEFAULT_BASE_PRICE = 100.0

# Per-ticker GBM parameters: (daily_drift, daily_volatility)
# TSLA and NVDA carry materially higher volatility than AAPL.
# Calibrated to approximate each stock's historical realized vol.
_GBM_PARAMS: dict[str, tuple[float, float]] = {
    "AAPL": (0.0003, 0.018),   # ~1.8% daily vol
    "NVDA": (0.0005, 0.032),   # ~3.2% daily vol — AI-driven momentum stock
    "TSLA": (0.0002, 0.038),   # ~3.8% daily vol — historically the most volatile
}
_DEFAULT_GBM_PARAMS = (0.0003, 0.018)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> list[date]:
    """Return a list of weekday dates between start and end (inclusive)."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon–Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def _gbm_mock(ticker: str, n_days: int = 90) -> list[dict]:
    """
    Generate realistic-looking OHLCV data using Geometric Brownian Motion.
    Seeded deterministically by ticker so results are reproducible.
    """
    seed = int(hashlib.md5(ticker.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)

    base = _BASE_PRICES.get(ticker.upper(), _DEFAULT_BASE_PRICE)
    mu, sigma = _GBM_PARAMS.get(ticker.upper(), _DEFAULT_GBM_PARAMS)

    end_date = date.today()
    start_date = end_date - timedelta(days=int(n_days * 1.5))
    days = _trading_days(start_date, end_date)[-n_days:]

    price = base
    rows: list[dict] = []
    for d in days:
        # GBM step
        z = rng.gauss(0, 1)
        daily_return = math.exp((mu - 0.5 * sigma**2) + sigma * z)
        open_price = price
        close_price = round(open_price * daily_return, 4)
        high_price = round(max(open_price, close_price) * (1 + rng.uniform(0, 0.01)), 4)
        low_price = round(min(open_price, close_price) * (1 - rng.uniform(0, 0.01)), 4)
        volume = int(rng.uniform(5_000_000, 80_000_000))

        rows.append({
            "ticker": ticker,
            "date": d.isoformat(),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "source": "mock_gbm",
        })
        price = close_price

    return rows


# ── Alpha Vantage fetch ───────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=False,
)
def _fetch_av_prices(ticker: str) -> Optional[list[dict]]:
    """Fetch daily OHLCV from Alpha Vantage. Returns None on any error."""
    if not config.ALPHA_VANTAGE_API_KEY:
        return None

    params = {
        "function": _AV_FUNCTION,
        "symbol": ticker,
        "outputsize": _OUTPUT_SIZE,
        "apikey": config.ALPHA_VANTAGE_API_KEY,
    }
    resp = requests.get(_AV_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if "Time Series (Daily)" not in data:
        log.warning("Alpha Vantage: no time series for {} — {}", ticker, data.get("Note", data.get("Information", "")))
        return None

    rows: list[dict] = []
    for date_str, ohlcv in data["Time Series (Daily)"].items():
        rows.append({
            "ticker": ticker,
            "date": date_str,
            "open": float(ohlcv["1. open"]),
            "high": float(ohlcv["2. high"]),
            "low": float(ohlcv["3. low"]),
            "close": float(ohlcv["4. close"]),
            "volume": int(ohlcv["5. volume"]),
            "source": "alpha_vantage",
        })
    return rows


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_ticker_prices(ticker: str) -> int:
    """
    Ingest stock prices for a single ticker.
    Falls back to GBM mock if Alpha Vantage is unavailable or rate-limited.

    Returns number of rows inserted.
    """
    log.info("Ingesting stock prices: {}", ticker)

    rows = None
    try:
        rows = _fetch_av_prices(ticker)
    except Exception as exc:
        log.warning("Alpha Vantage failed for {}: {}", ticker, exc)

    if not rows:
        log.info("Using GBM mock data for {}", ticker)
        rows = _gbm_mock(ticker)

    n = insert_stock_prices(rows)
    log.info("Stored {} price rows for {}", n, ticker)
    return n


def ingest_all_stocks(tickers: Optional[list[str]] = None) -> dict[str, int]:
    """Ingest prices for all configured tickers. Returns {ticker: row_count}."""
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = ingest_ticker_prices(ticker)
        except Exception as exc:
            log.error("Failed to ingest {} prices: {}", ticker, exc)
            results[ticker] = 0
    return results
