"""
pipeline/ingest_news.py — Financial news ingestion for SignalDeck AI.

Primary source : NewsAPI (https://newsapi.org)
Fallback       : Per-ticker templated mock articles with preset sentiments

Mock data is deterministic (seeded by ticker + date) so test runs are stable.
"""
import hashlib
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import config
from logger import log
from pipeline.database import insert_news_articles

# ── NewsAPI ───────────────────────────────────────────────────────────────────

_NEWSAPI_URL = "https://newsapi.org/v2/everything"

# ── Mock article templates (per ticker) ──────────────────────────────────────

_MOCK_TEMPLATES: dict[str, list[dict]] = {
    "AAPL": [
        {"title": "Apple Vision Pro sales exceed analyst expectations in Q2", "sentiment": "positive", "score": 0.82},
        {"title": "Apple suppliers warn of iPhone demand slowdown in Asia", "sentiment": "negative", "score": -0.61},
        {"title": "Apple announces record $90B share buyback programme", "sentiment": "positive", "score": 0.74},
        {"title": "EU regulators open new antitrust probe into App Store fees", "sentiment": "negative", "score": -0.55},
        {"title": "Apple intelligence features drive upgrade cycle in enterprise", "sentiment": "positive", "score": 0.68},
    ],
    "MSFT": [
        {"title": "Microsoft Azure AI revenue grows 45% year-over-year", "sentiment": "positive", "score": 0.87},
        {"title": "Microsoft faces EU scrutiny over Teams bundling practices", "sentiment": "negative", "score": -0.48},
        {"title": "Copilot adoption reaches 50M enterprise users", "sentiment": "positive", "score": 0.79},
        {"title": "Microsoft gaming division misses quarterly targets", "sentiment": "negative", "score": -0.52},
        {"title": "Azure openAI service wins major government contracts", "sentiment": "positive", "score": 0.71},
    ],
    "GOOGL": [
        {"title": "Google Search market share hits decade low amid AI competition", "sentiment": "negative", "score": -0.67},
        {"title": "YouTube ad revenue surpasses $10B in single quarter", "sentiment": "positive", "score": 0.76},
        {"title": "Waymo expands robotaxi operations to 10 new US cities", "sentiment": "positive", "score": 0.65},
        {"title": "Google faces $5B antitrust fine in India", "sentiment": "negative", "score": -0.72},
        {"title": "Google Cloud achieves profitability milestone ahead of schedule", "sentiment": "positive", "score": 0.81},
    ],
    "AMZN": [
        {"title": "Amazon AWS launches next-gen AI inference chips", "sentiment": "positive", "score": 0.78},
        {"title": "Amazon logistics costs surge amid fuel price volatility", "sentiment": "negative", "score": -0.59},
        {"title": "Prime membership crosses 300M globally", "sentiment": "positive", "score": 0.83},
        {"title": "Amazon faces FTC investigation over third-party seller practices", "sentiment": "negative", "score": -0.63},
        {"title": "Amazon pharmacy division captures 8% of US retail pharmacy market", "sentiment": "positive", "score": 0.69},
    ],
    "META": [
        {"title": "Meta AI assistant reaches 500M monthly active users", "sentiment": "positive", "score": 0.85},
        {"title": "Meta faces $1.3B GDPR fine in record data privacy ruling", "sentiment": "negative", "score": -0.77},
        {"title": "Ray-Ban Meta smart glasses sell out globally ahead of holiday season", "sentiment": "positive", "score": 0.72},
        {"title": "Threads platform engagement drops 30% from peak", "sentiment": "negative", "score": -0.54},
        {"title": "Meta Reality Labs narrows operating losses significantly", "sentiment": "positive", "score": 0.66},
    ],
}

_DEFAULT_TEMPLATES = [
    {"title": "{ticker} reports strong quarterly earnings beat", "sentiment": "positive", "score": 0.70},
    {"title": "{ticker} stock falls amid sector-wide sell-off", "sentiment": "negative", "score": -0.58},
    {"title": "Analysts raise price targets on {ticker} following product launch", "sentiment": "positive", "score": 0.65},
    {"title": "{ticker} faces regulatory headwinds in key markets", "sentiment": "negative", "score": -0.50},
    {"title": "{ticker} announces strategic partnership expanding market reach", "sentiment": "positive", "score": 0.60},
]

_SOURCES = ["Reuters", "Bloomberg", "CNBC", "Wall Street Journal", "Financial Times", "MarketWatch", "Barron's"]


def _mock_articles(ticker: str, n: int = 5) -> list[dict]:
    """Generate deterministic mock news articles for a ticker."""
    seed = int(hashlib.md5(f"{ticker}{datetime.utcnow().date()}".encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)

    templates = _MOCK_TEMPLATES.get(ticker.upper(), _DEFAULT_TEMPLATES)
    now = datetime.utcnow()
    rows: list[dict] = []

    for i, tmpl in enumerate(templates[:n]):
        title = tmpl["title"].format(ticker=ticker)
        hours_ago = rng.randint(1, 48)
        published = (now - timedelta(hours=hours_ago)).isoformat()
        article_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}_{title}_{published[:10]}"))

        rows.append({
            "article_id": article_id,
            "ticker": ticker,
            "title": title,
            "description": f"[Mock] {title}. This is synthetic data generated for testing.",
            "source_name": rng.choice(_SOURCES),
            "published_at": published,
            "sentiment": tmpl["sentiment"],
            "sentiment_score": tmpl["score"],
            "url": f"https://mock.signaldeck.ai/news/{article_id}",
        })

    return rows


# ── NewsAPI fetch ─────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=False,
)
def _fetch_newsapi(ticker: str, page_size: int = 10) -> Optional[list[dict]]:
    if not config.NEWS_API_KEY:
        return None

    params = {
        "q": ticker,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
        "apiKey": config.NEWS_API_KEY,
    }
    resp = requests.get(_NEWSAPI_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "ok":
        log.warning("NewsAPI error for {}: {}", ticker, data.get("message", "unknown"))
        return None

    rows: list[dict] = []
    for art in data.get("articles", []):
        title = art.get("title", "")
        published = art.get("publishedAt", datetime.utcnow().isoformat())
        article_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}_{title}_{published[:10]}"))

        rows.append({
            "article_id": article_id,
            "ticker": ticker,
            "title": title,
            "description": art.get("description", ""),
            "source_name": (art.get("source") or {}).get("name", ""),
            "published_at": published,
            "sentiment": "neutral",   # NewsAPI doesn't provide sentiment
            "sentiment_score": 0.0,
            "url": art.get("url", ""),
        })

    return rows if rows else None


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_ticker_news(ticker: str) -> int:
    log.info("Ingesting news: {}", ticker)

    rows = None
    try:
        rows = _fetch_newsapi(ticker)
    except Exception as exc:
        log.warning("NewsAPI failed for {}: {}", ticker, exc)

    if not rows:
        log.info("Using mock news for {}", ticker)
        rows = _mock_articles(ticker)

    n = insert_news_articles(rows)
    log.info("Stored {} news rows for {}", n, ticker)
    return n


def ingest_all_news(tickers: Optional[list[str]] = None) -> dict[str, int]:
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = ingest_ticker_news(ticker)
        except Exception as exc:
            log.error("Failed to ingest {} news: {}", ticker, exc)
            results[ticker] = 0
    return results
