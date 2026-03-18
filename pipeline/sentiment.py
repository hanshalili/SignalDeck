"""
pipeline/sentiment.py — Lightweight sentiment scoring for SignalDeck AI.

Scoring methods
---------------
1. VADER (vaderSentiment) — applied to articles that don't already carry
   a provider-supplied score (e.g. NewsAPI articles, which have no sentiment).
2. passthrough — for Alpha Vantage articles whose sentiment was already
   computed by the provider; we record the score as-is so the sentiment_scores
   table is a complete audit trail.

VADER compound score thresholds (industry standard):
  compound >=  0.05  → positive
  compound <= -0.05  → negative
  else               → neutral
"""
import uuid
from datetime import datetime
from typing import Optional

from logger import log
from pipeline.database import query, insert_sentiment_scores


_VADER_AVAILABLE = False
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyzer = SentimentIntensityAnalyzer()
    _VADER_AVAILABLE = True
except ImportError:
    _analyzer = None  # type: ignore[assignment]


# ── VADER helpers ─────────────────────────────────────────────────────────────

def _vader_scores(text: str) -> dict:
    """Return VADER scores dict with compound, pos, neg, neu."""
    if not _VADER_AVAILABLE or not _analyzer:
        return {"compound": 0.0, "pos": 0.0, "neg": 0.0, "neu": 1.0}
    return _analyzer.polarity_scores(text)


def _compound_to_label(compound: float) -> str:
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


# ── Score a single article ────────────────────────────────────────────────────

def _score_article(article: dict) -> Optional[dict]:
    """
    Produce a sentiment_scores row for one article.

    - If the article already has a provider sentiment_score (AV-sourced), we
      record it as method='passthrough' and skip VADER.
    - Otherwise we run VADER on the title + description.
    """
    article_id = article.get("article_id", "")
    ticker     = article.get("ticker", "")
    headline   = (article.get("title") or "")[:500]

    if not article_id or not ticker:
        return None

    provider_score = article.get("sentiment_score")
    provider_label = article.get("sentiment", "")
    source         = article.get("source_name", "")

    # Alpha Vantage articles: use passthrough
    is_passthrough = (
        provider_score is not None
        and provider_score != 0.0
        and provider_label in ("positive", "negative", "neutral")
        and "mock" not in (article.get("url") or "").lower()
    )

    if is_passthrough:
        compound = float(provider_score)
        label    = provider_label
        method   = "passthrough"
        pos = neg = neu = 0.0
    else:
        text   = f"{headline} {article.get('description') or ''}".strip()
        scores = _vader_scores(text)
        compound = round(scores["compound"], 4)
        pos      = round(scores["pos"],      4)
        neg      = round(scores["neg"],      4)
        neu      = round(scores["neu"],      4)
        label    = _compound_to_label(compound)
        method   = "vader" if _VADER_AVAILABLE else "passthrough"

    score_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{article_id}_{method}"))

    return {
        "score_id":   score_id,
        "article_id": article_id,
        "ticker":     ticker,
        "headline":   headline,
        "compound":   compound,
        "positive":   pos,
        "negative":   neg,
        "neutral":    neu,
        "label":      label,
        "method":     method,
        "scored_at":  datetime.utcnow().isoformat(),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def score_ticker_news(ticker: str) -> int:
    """
    Score all unscored news articles for *ticker* and insert into
    sentiment_scores. Returns the number of rows inserted.
    """
    log.info("Scoring news sentiment: {}", ticker)

    articles = query("news_articles", ticker=ticker, limit=50)
    if not articles:
        log.info("No articles found for {}", ticker)
        return 0

    rows = []
    for art in articles:
        row = _score_article(art)
        if row:
            rows.append(row)

    if not rows:
        return 0

    n = insert_sentiment_scores(rows)
    log.info("Stored {} sentiment rows for {}", n, ticker)

    # Log a brief summary
    labels = [r["label"] for r in rows]
    pos_count = labels.count("positive")
    neg_count = labels.count("negative")
    neu_count = labels.count("neutral")
    log.info(
        "  {} sentiment breakdown — positive={} negative={} neutral={}",
        ticker, pos_count, neg_count, neu_count,
    )
    return n


def score_all_news(tickers: Optional[list] = None) -> dict:
    """Score news sentiment for all tickers. Returns {ticker: row_count}."""
    import config
    tickers = tickers or config.TICKERS
    results: dict = {}
    for ticker in tickers:
        try:
            results[ticker] = score_ticker_news(ticker)
        except Exception as exc:
            log.error("Sentiment scoring failed for {}: {}", ticker, exc)
            results[ticker] = 0
    return results
