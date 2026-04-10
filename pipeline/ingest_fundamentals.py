"""
pipeline/ingest_fundamentals.py — Fundamental data ingestion for SignalDeck AI.

Primary source : Alpha Vantage OVERVIEW endpoint
Fallback       : Realistic mock fundamentals seeded per ticker
"""
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import config
from logger import log
from pipeline.database import insert_fundamentals
from pipeline.gcs_writer import upload_raw_to_gcs

_AV_URL = "https://www.alphavantage.co/query"

# ── Mock fundamental data ─────────────────────────────────────────────────────

_MOCK_FUNDAMENTALS: dict[str, dict] = {
    "AAPL": {
        "market_cap": 2_870_000_000_000.0,
        "pe_ratio": 28.4,
        "forward_pe": 24.8,
        "peg_ratio": 2.1,
        "price_to_book": 46.2,
        "dividend_yield": 0.005,
        "eps": 6.58,
        "revenue_ttm": 385_000_000_000.0,
        "profit_margin": 0.254,
        "debt_to_equity": 151.8,
        "analyst_target": 210.0,
        "week52_high": 199.6,
        "week52_low": 164.1,
        "beta": 1.24,
        "sector": "Technology",
    },
    "MSFT": {
        "market_cap": 3_120_000_000_000.0,
        "pe_ratio": 35.7,
        "forward_pe": 29.1,
        "peg_ratio": 2.4,
        "price_to_book": 12.4,
        "dividend_yield": 0.007,
        "eps": 11.45,
        "revenue_ttm": 236_000_000_000.0,
        "profit_margin": 0.361,
        "debt_to_equity": 35.4,
        "analyst_target": 480.0,
        "week52_high": 468.3,
        "week52_low": 362.9,
        "beta": 0.90,
        "sector": "Technology",
    },
    "GOOGL": {
        "market_cap": 2_180_000_000_000.0,
        "pe_ratio": 24.1,
        "forward_pe": 20.5,
        "peg_ratio": 1.4,
        "price_to_book": 6.8,
        "dividend_yield": 0.0,
        "eps": 7.26,
        "revenue_ttm": 307_000_000_000.0,
        "profit_margin": 0.239,
        "debt_to_equity": 9.2,
        "analyst_target": 200.0,
        "week52_high": 193.3,
        "week52_low": 129.4,
        "beta": 1.07,
        "sector": "Communication Services",
    },
    "AMZN": {
        "market_cap": 1_930_000_000_000.0,
        "pe_ratio": 42.5,
        "forward_pe": 33.2,
        "peg_ratio": 1.9,
        "price_to_book": 8.1,
        "dividend_yield": 0.0,
        "eps": 4.35,
        "revenue_ttm": 590_000_000_000.0,
        "profit_margin": 0.073,
        "debt_to_equity": 58.1,
        "analyst_target": 225.0,
        "week52_high": 201.2,
        "week52_low": 151.6,
        "beta": 1.16,
        "sector": "Consumer Discretionary",
    },
    "META": {
        "market_cap": 1_280_000_000_000.0,
        "pe_ratio": 23.8,
        "forward_pe": 19.4,
        "peg_ratio": 1.1,
        "price_to_book": 8.6,
        "dividend_yield": 0.004,
        "eps": 20.84,
        "revenue_ttm": 134_000_000_000.0,
        "profit_margin": 0.347,
        "debt_to_equity": 18.9,
        "analyst_target": 575.0,
        "week52_high": 544.1,
        "week52_low": 390.6,
        "beta": 1.21,
        "sector": "Communication Services",
    },
    "NVDA": {
        # AI chip leader — elevated PE reflects growth premium
        "market_cap": 2_300_000_000_000.0,
        "pe_ratio": 66.5,
        "forward_pe": 38.2,
        "peg_ratio": 1.7,
        "price_to_book": 36.4,
        "dividend_yield": 0.001,
        "eps": 13.89,
        "revenue_ttm": 96_000_000_000.0,
        "profit_margin": 0.557,
        "debt_to_equity": 13.4,
        "analyst_target": 1_000.0,
        "week52_high": 974.0,
        "week52_low": 430.0,
        "beta": 1.68,
        "sector": "Technology",
    },
    "TSLA": {
        # EV + energy company — high beta, no dividend, growth PE
        "market_cap": 620_000_000_000.0,
        "pe_ratio": 55.2,
        "forward_pe": 40.1,
        "peg_ratio": 2.4,
        "price_to_book": 10.8,
        "dividend_yield": 0.0,
        "eps": 3.62,
        "revenue_ttm": 97_000_000_000.0,
        "profit_margin": 0.133,
        "debt_to_equity": 19.2,
        "analyst_target": 250.0,
        "week52_high": 299.3,
        "week52_low": 138.8,
        "beta": 2.31,
        "sector": "Consumer Discretionary",
    },
}

_DEFAULT_FUNDAMENTALS: dict = {
    "market_cap": 50_000_000_000.0,
    "pe_ratio": 18.0,
    "forward_pe": 15.0,
    "peg_ratio": 1.5,
    "price_to_book": 3.0,
    "dividend_yield": 0.015,
    "eps": 3.00,
    "revenue_ttm": 10_000_000_000.0,
    "profit_margin": 0.12,
    "debt_to_equity": 40.0,
    "analyst_target": 110.0,
    "week52_high": 120.0,
    "week52_low": 80.0,
    "beta": 1.0,
    "sector": "Unknown",
}


def _safe_float(value: str | None, default: float = 0.0) -> float:
    """Convert a string to float safely, returning default on failure."""
    if value is None:
        return default
    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        if cleaned in ("None", "N/A", "-", ""):
            return default
        return float(cleaned)
    except (ValueError, TypeError):
        return default


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=False,
)
def _fetch_av_overview(ticker: str) -> Optional[dict]:
    if not config.ALPHA_VANTAGE_API_KEY:
        return None

    params = {
        "function": "OVERVIEW",
        "symbol": ticker,
        "apikey": config.ALPHA_VANTAGE_API_KEY,
    }
    resp = requests.get(_AV_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if not data or "Symbol" not in data:
        log.warning("Alpha Vantage OVERVIEW: no data for {}", ticker)
        return None

    return {
        "ticker": ticker,
        "market_cap": _safe_float(data.get("MarketCapitalization")),
        "pe_ratio": _safe_float(data.get("PERatio")),
        "forward_pe": _safe_float(data.get("ForwardPE")),
        "peg_ratio": _safe_float(data.get("PEGRatio")),
        "price_to_book": _safe_float(data.get("PriceToBookRatio")),
        "dividend_yield": _safe_float(data.get("DividendYield")),
        "eps": _safe_float(data.get("EPS")),
        "revenue_ttm": _safe_float(data.get("RevenueTTM")),
        "profit_margin": _safe_float(data.get("ProfitMargin")),
        "debt_to_equity": _safe_float(data.get("DebtToEquityRatio")),
        "analyst_target": _safe_float(data.get("AnalystTargetPrice")),
        "week52_high": _safe_float(data.get("52WeekHigh")),
        "week52_low": _safe_float(data.get("52WeekLow")),
        "beta": _safe_float(data.get("Beta"), default=1.0),
        "sector": data.get("Sector", "Unknown"),
    }


def ingest_ticker_fundamentals(ticker: str) -> int:
    """
    Ingest fundamental data for a single ticker.

    Primary: Alpha Vantage OVERVIEW endpoint.
    Fallback: pre-built mock snapshot seeded per ticker.
    Stages the record to GCS before writing to the database.

    Returns 1 on success, 0 on unexpected failure.
    """
    log.info("Ingesting fundamentals: {}", ticker)

    row = None
    try:
        row = _fetch_av_overview(ticker)
    except Exception as exc:
        log.warning("Alpha Vantage OVERVIEW failed for {}: {}", ticker, exc)

    if not row:
        log.info("Using mock fundamentals for {}", ticker)
        template = _MOCK_FUNDAMENTALS.get(ticker.upper(), _DEFAULT_FUNDAMENTALS)
        row = {"ticker": ticker, **template}

    upload_raw_to_gcs([row], source="fundamentals")
    n = insert_fundamentals([row])
    log.info("Stored fundamentals for {}", ticker)
    return n


def ingest_all_fundamentals(tickers: Optional[list[str]] = None) -> dict[str, int]:
    """Ingest fundamentals for all configured tickers. Returns {ticker: row_count}."""
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = ingest_ticker_fundamentals(ticker)
        except Exception as exc:
            log.error("Failed to ingest {} fundamentals: {}", ticker, exc)
            results[ticker] = 0
    return results
