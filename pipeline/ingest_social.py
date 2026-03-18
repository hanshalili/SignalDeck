"""
pipeline/ingest_social.py — Social sentiment ingestion for SignalDeck AI.

Sources (in priority order per ticker):
  1. Reddit (OAuth2 if configured, else public JSON endpoint)
  2. StockTwits (public API)
  3. Mock signals with Gaussian noise seeded by ticker + date

All three sources are attempted; results are merged before storage.
"""
import hashlib
import math
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

import requests

import config
from logger import log
from pipeline.database import insert_social_signals

# ── Mock signal parameters ────────────────────────────────────────────────────

_BASE_SENTIMENT: dict[str, float] = {
    "AAPL": 0.62,
    "MSFT": 0.68,
    "GOOGL": 0.54,
    "AMZN": 0.58,
    "META": 0.55,
    "TSLA": 0.48,
    "NVDA": 0.71,
}
_DEFAULT_SENTIMENT = 0.55
_NOISE_STD = 0.12  # ±12% Gaussian noise

_MOCK_SUBREDDITS = ["wallstreetbets", "investing", "stocks", "SecurityAnalysis"]
_MOCK_CONTENT_TEMPLATES = [
    "{ticker} looking bullish this week — strong technicals",
    "Sold my {ticker} position. Too much uncertainty.",
    "{ticker} earnings preview: analysts divided",
    "Loading up on {ticker} calls ahead of earnings",
    "{ticker} support held at 200MA — buy signal?",
    "Bears are wrong on {ticker} — fundamentals are solid",
]


def _noisy_sentiment(base: float, ticker: str, day_offset: int = 0) -> float:
    """Return sentiment score with reproducible Gaussian noise."""
    seed_str = f"{ticker}{datetime.utcnow().date()}_{day_offset}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    noise = rng.gauss(0, _NOISE_STD)
    return max(0.0, min(1.0, base + noise))


def _mock_social(ticker: str, n_per_source: int = 3) -> list[dict]:
    """Generate mock social signals from 3 simulated sources."""
    base = _BASE_SENTIMENT.get(ticker.upper(), _DEFAULT_SENTIMENT)
    sources = ["reddit_mock", "stocktwits_mock", "twitter_mock"]
    now = datetime.utcnow()
    seed = int(hashlib.md5(f"{ticker}{now.date()}".encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    rows: list[dict] = []

    for i, source in enumerate(sources):
        sentiment = _noisy_sentiment(base, ticker, day_offset=i)
        bullish = round(sentiment * 100, 1)
        bearish = round((1 - sentiment) * 100, 1)
        content = rng.choice(_MOCK_CONTENT_TEMPLATES).format(ticker=ticker)
        published = (now - timedelta(hours=rng.randint(1, 24))).isoformat()
        signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}_{source}_{now.date()}"))

        rows.append({
            "signal_id": signal_id,
            "ticker": ticker,
            "source": source,
            "content": content,
            "sentiment_score": round(sentiment * 2 - 1, 4),  # scale to [-1, 1]
            "bullish_pct": bullish,
            "bearish_pct": bearish,
            "volume": rng.randint(50, 2000),
            "published_at": published,
        })

    return rows


# ── Reddit ────────────────────────────────────────────────────────────────────

def _fetch_reddit(ticker: str) -> Optional[list[dict]]:
    """
    Fetch Reddit posts mentioning the ticker.
    Uses OAuth2 if credentials are configured, else public JSON endpoint.
    """
    headers = {"User-Agent": config.REDDIT_USER_AGENT}

    if config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
        try:
            auth_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers=headers,
                timeout=10,
            )
            auth_resp.raise_for_status()
            token = auth_resp.json().get("access_token")
            if token:
                headers["Authorization"] = f"bearer {token}"
        except Exception as exc:
            log.debug("Reddit OAuth2 failed, falling back to public: {}", exc)

    try:
        url = f"https://www.reddit.com/search.json?q={ticker}+stock&sort=new&limit=10"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        posts = resp.json().get("data", {}).get("children", [])
    except Exception as exc:
        log.debug("Reddit fetch failed for {}: {}", ticker, exc)
        return None

    if not posts:
        return None

    rows: list[dict] = []
    for post in posts:
        p = post.get("data", {})
        title = p.get("title", "")
        score = p.get("score", 0)
        created = datetime.utcfromtimestamp(p.get("created_utc", 0)).isoformat()
        sentiment_raw = (score / (abs(score) + 100)) if score else 0.0  # basic proxy
        signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"reddit_{p.get('id', title)}"))

        rows.append({
            "signal_id": signal_id,
            "ticker": ticker,
            "source": "reddit",
            "content": title[:500],
            "sentiment_score": round(sentiment_raw, 4),
            "bullish_pct": round(max(0, sentiment_raw) * 100, 1),
            "bearish_pct": round(max(0, -sentiment_raw) * 100, 1),
            "volume": score,
            "published_at": created,
        })

    return rows if rows else None


# ── StockTwits ────────────────────────────────────────────────────────────────

def _fetch_stocktwits(ticker: str) -> Optional[list[dict]]:
    headers = {}
    if config.STOCKTWITS_ACCESS_TOKEN:
        headers["Authorization"] = f"OAuth {config.STOCKTWITS_ACCESS_TOKEN}"

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
    except Exception as exc:
        log.debug("StockTwits fetch failed for {}: {}", ticker, exc)
        return None

    if not messages:
        return None

    rows: list[dict] = []
    for msg in messages[:15]:
        entities = msg.get("entities", {})
        sentiment_raw = (entities.get("sentiment") or {}).get("basic", "Neutral")
        score = 0.5 if sentiment_raw == "Bullish" else (-0.5 if sentiment_raw == "Bearish" else 0.0)
        body = msg.get("body", "")[:500]
        created = msg.get("created_at", datetime.utcnow().isoformat())
        signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"stocktwits_{msg.get('id', body)}"))

        rows.append({
            "signal_id": signal_id,
            "ticker": ticker,
            "source": "stocktwits",
            "content": body,
            "sentiment_score": score,
            "bullish_pct": 100.0 if score > 0 else 0.0,
            "bearish_pct": 100.0 if score < 0 else 0.0,
            "volume": msg.get("likes", {}).get("total", 0),
            "published_at": created,
        })

    return rows if rows else None


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_ticker_social(ticker: str) -> int:
    log.info("Ingesting social signals: {}", ticker)
    all_rows: list[dict] = []

    for fetch_fn, name in [(_fetch_reddit, "Reddit"), (_fetch_stocktwits, "StockTwits")]:
        try:
            rows = fetch_fn(ticker)
            if rows:
                all_rows.extend(rows)
                log.debug("{} returned {} signals for {}", name, len(rows), ticker)
        except Exception as exc:
            log.warning("{} failed for {}: {}", name, ticker, exc)

    if not all_rows:
        log.info("Using mock social signals for {}", ticker)
        all_rows = _mock_social(ticker)

    n = insert_social_signals(all_rows)
    log.info("Stored {} social rows for {}", n, ticker)
    return n


def ingest_all_social(tickers: Optional[list[str]] = None) -> dict[str, int]:
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = ingest_ticker_social(ticker)
        except Exception as exc:
            log.error("Failed to ingest {} social: {}", ticker, exc)
            results[ticker] = 0
    return results
