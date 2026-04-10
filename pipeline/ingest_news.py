"""
pipeline/ingest_news.py — Financial news ingestion for SignalDeck AI.

Source priority
---------------
1. Alpha Vantage NEWS_SENTIMENT  — ticker-specific articles + built-in sentiment
                                   scores; uses the same API key already in config.
2. NewsAPI /everything           — general news search; no sentiment provided.
3. Mock templates                — deterministic, seeded; always available.

Alpha Vantage is the preferred primary source because:
  - The same ALPHA_VANTAGE_API_KEY is already used for price data
  - Returns ticker-targeted articles (not just keyword matches)
  - Includes per-ticker sentiment labels + numeric scores
  - No additional credentials needed
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
from pipeline.gcs_writer import upload_raw_to_gcs

_AV_URL      = "https://www.alphavantage.co/query"
_AV_FUNCTION = "NEWS_SENTIMENT"

# Alpha Vantage sentiment labels → our standard [-1, +1] score
_AV_LABEL_SCORE: dict[str, float] = {
    "Bullish":          1.0,
    "Somewhat-Bullish": 0.5,
    "Neutral":          0.0,
    "Somewhat-Bearish": -0.5,
    "Bearish":          -1.0,
}

# Alpha Vantage labels → our three-way label
_AV_LABEL_MAP: dict[str, str] = {
    "Bullish":          "positive",
    "Somewhat-Bullish": "positive",
    "Neutral":          "neutral",
    "Somewhat-Bearish": "negative",
    "Bearish":          "negative",
}

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
    "NVDA": [
        {"title": "Nvidia data center revenue hits record $22B as AI demand accelerates", "sentiment": "positive", "score": 0.91},
        {"title": "US expands chip export restrictions to China, curbing Nvidia H20 sales", "sentiment": "negative", "score": -0.74},
        {"title": "Nvidia Blackwell GPU shipments exceed initial production forecasts", "sentiment": "positive", "score": 0.86},
        {"title": "AMD challenges Nvidia with new MI300X benchmarks in LLM inference", "sentiment": "negative", "score": -0.52},
        {"title": "Nvidia announces $500B US AI infrastructure partnership with hyperscalers", "sentiment": "positive", "score": 0.89},
    ],
    "TSLA": [
        {"title": "Tesla Cybertruck deliveries ramp as production bottlenecks ease", "sentiment": "positive", "score": 0.71},
        {"title": "Tesla cuts Model Y prices again amid intensifying EV competition from BYD", "sentiment": "negative", "score": -0.65},
        {"title": "Tesla FSD v13 achieves significant milestone in unsupervised highway driving", "sentiment": "positive", "score": 0.78},
        {"title": "Tesla misses Q4 delivery estimates for second consecutive quarter", "sentiment": "negative", "score": -0.69},
        {"title": "Tesla Megapack energy storage backlog grows to record $12B", "sentiment": "positive", "score": 0.74},
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


# ── Alpha Vantage NEWS_SENTIMENT fetch ────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=False,
)
def _fetch_av_news(ticker: str, limit: int = 10) -> Optional[list[dict]]:
    """
    Fetch news + sentiment from Alpha Vantage NEWS_SENTIMENT endpoint.

    Returns articles with ticker-specific sentiment scores already included,
    so no downstream VADER pass is strictly required for AV-sourced articles
    (though we still run VADER independently for consistency).
    """
    if not config.ALPHA_VANTAGE_API_KEY:
        return None

    params = {
        "function": _AV_FUNCTION,
        "tickers":  ticker,
        "limit":    limit,
        "sort":     "LATEST",
        "apikey":   config.ALPHA_VANTAGE_API_KEY,
    }
    resp = requests.get(_AV_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    feed = data.get("feed", [])
    if not feed:
        # AV returns {"Information": "..."} when rate-limited
        note = data.get("Information") or data.get("Note", "")
        if note:
            log.warning("Alpha Vantage NEWS_SENTIMENT rate-limited for {}: {}", ticker, note[:80])
        return None

    rows: list[dict] = []
    for item in feed:
        title    = item.get("title", "")
        url      = item.get("url", "")
        source   = item.get("source", "")
        raw_time = item.get("time_published", "")

        # Parse AV timestamp: "20241115T120000" → ISO
        try:
            published = datetime.strptime(raw_time, "%Y%m%dT%H%M%S").isoformat()
        except (ValueError, TypeError):
            published = datetime.utcnow().isoformat()

        # Prefer ticker-specific sentiment; fall back to overall
        ticker_entry = next(
            (ts for ts in item.get("ticker_sentiment", []) if ts.get("ticker") == ticker),
            None,
        )
        if ticker_entry:
            label_raw = ticker_entry.get("ticker_sentiment_label", "Neutral")
            try:
                score = float(ticker_entry.get("ticker_sentiment_score", 0.0))
            except (ValueError, TypeError):
                score = _AV_LABEL_SCORE.get(label_raw, 0.0)
        else:
            label_raw = item.get("overall_sentiment_label", "Neutral")
            try:
                score = float(item.get("overall_sentiment_score", 0.0))
            except (ValueError, TypeError):
                score = _AV_LABEL_SCORE.get(label_raw, 0.0)

        sentiment = _AV_LABEL_MAP.get(label_raw, "neutral")
        article_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}_{title}_{published[:10]}"))

        rows.append({
            "article_id":      article_id,
            "ticker":          ticker,
            "title":           title,
            "description":     item.get("summary", "")[:500],
            "source_name":     source,
            "published_at":    published,
            "sentiment":       sentiment,
            "sentiment_score": round(score, 4),
            "url":             url,
        })

    return rows if rows else None


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
    """
    Ingest financial news for a single ticker.

    Source priority: Alpha Vantage → NewsAPI → mock templates.
    Stages raw records to GCS before writing to the database.

    Returns number of rows inserted.
    """
    log.info("Ingesting news: {}", ticker)

    rows = None

    # 1. Alpha Vantage NEWS_SENTIMENT (primary — ticker-targeted + built-in sentiment)
    try:
        rows = _fetch_av_news(ticker)
        if rows:
            log.info("Alpha Vantage returned {} articles for {}", len(rows), ticker)
    except Exception as exc:
        log.warning("Alpha Vantage news failed for {}: {}", ticker, exc)

    # 2. NewsAPI (secondary)
    if not rows:
        try:
            rows = _fetch_newsapi(ticker)
        except Exception as exc:
            log.warning("NewsAPI failed for {}: {}", ticker, exc)

    # 3. Mock templates (always available)
    if not rows:
        log.info("Using mock news for {}", ticker)
        rows = _mock_articles(ticker)

    upload_raw_to_gcs(rows, source="news")
    n = insert_news_articles(rows)
    log.info("Stored {} news rows for {}", n, ticker)
    return n


def ingest_all_news(tickers: Optional[list[str]] = None) -> dict[str, int]:
    """Ingest news for all configured tickers. Returns {ticker: row_count}."""
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = ingest_ticker_news(ticker)
        except Exception as exc:
            log.error("Failed to ingest {} news: {}", ticker, exc)
            results[ticker] = 0
    return results
