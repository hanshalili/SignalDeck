"""
pipeline/transform.py — Feature engineering for SignalDeck AI.

Reads raw ingested data from the database and computes:
  - Moving averages (MA5, MA20, MA50)
  - RSI-14
  - 20-day volatility
  - News sentiment aggregates
  - Social signal aggregates
  - Price + volume momentum

Results are stored in processed_features.
"""
from datetime import datetime
from typing import Optional

import config
from logger import log
from pipeline.database import (
    query,
    insert_processed_features,
)

# ── Price feature computation ─────────────────────────────────────────────────

def _moving_average(prices: list[float], n: int) -> Optional[float]:
    if len(prices) < n:
        return None
    return round(sum(prices[-n:]) / n, 4)


def _compute_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = prices[-(period + 1 - i + 1)] - prices[-(period + 1 - i + 2) + 1]
    # Recalculate properly
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _compute_volatility(prices: list[float], n: int = 20) -> Optional[float]:
    if len(prices) < n + 1:
        return None
    recent = prices[-(n + 1):]
    returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(variance ** 0.5, 6)


def compute_price_features(ticker: str) -> dict:
    rows = query("stock_prices", ticker=ticker, limit=60)
    rows = sorted(rows, key=lambda r: r.get("date", ""))

    closes = [r["close"] for r in rows if r.get("close")]
    volumes = [r["volume"] for r in rows if r.get("volume")]

    price_change_pct = None
    volume_change_pct = None

    if len(closes) >= 2:
        price_change_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 4)
    if len(volumes) >= 2:
        volume_change_pct = round((volumes[-1] - volumes[-2]) / volumes[-2] * 100, 4)

    return {
        "ma_5": _moving_average(closes, 5),
        "ma_20": _moving_average(closes, 20),
        "ma_50": _moving_average(closes, 50),
        "rsi_14": _compute_rsi(closes),
        "volatility_20": _compute_volatility(closes),
        "price_change_pct": price_change_pct,
        "volume_change_pct": volume_change_pct,
    }


# ── News feature computation ──────────────────────────────────────────────────

def compute_news_features(ticker: str) -> dict:
    rows = query("news_articles", ticker=ticker, limit=20)
    if not rows:
        return {"avg_sentiment_score": 0.0, "news_count": 0}

    scores = [r.get("sentiment_score") or 0.0 for r in rows]
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    return {
        "avg_sentiment_score": avg_score,
        "news_count": len(rows),
    }


# ── Social feature computation ────────────────────────────────────────────────

def compute_social_features(ticker: str) -> dict:
    rows = query("social_signals", ticker=ticker, limit=20)
    if not rows:
        return {"social_bullish_pct": 50.0}

    bullish_pcts = [r.get("bullish_pct") or 0.0 for r in rows]
    avg_bullish = round(sum(bullish_pcts) / len(bullish_pcts), 2) if bullish_pcts else 50.0

    return {"social_bullish_pct": avg_bullish}


# ── Per-ticker transform ──────────────────────────────────────────────────────

def transform_ticker(ticker: str) -> int:
    log.info("Transforming features: {}", ticker)

    price_feats = compute_price_features(ticker)
    news_feats = compute_news_features(ticker)
    social_feats = compute_social_features(ticker)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = {
        "ticker": ticker,
        "date": today,
        **price_feats,
        **news_feats,
        **social_feats,
    }

    n = insert_processed_features([row])
    log.info("Stored {} feature rows for {}", n, ticker)
    return n


def transform_all(tickers: Optional[list[str]] = None) -> dict[str, int]:
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = transform_ticker(ticker)
        except Exception as exc:
            log.error("Failed to transform {}: {}", ticker, exc)
            results[ticker] = 0
    return results
