"""
pipeline/transform.py — Feature engineering for SignalDeck AI.

Reads raw ingested data from the database and computes a clean, interpretable
feature set for each ticker. Results are written to the processed_features table.

Features produced
-----------------
Price & return
    close             Latest closing price
    daily_return      1-day percentage return
    momentum_5d       5-day price momentum  (% change over 5 trading days)
    momentum_20d      20-day price momentum (% change over 20 trading days)

Trend
    sma_5             5-day  simple moving average of close
    sma_20            20-day simple moving average of close
    sma_50            50-day simple moving average of close
    rsi_14            Wilder's RSI over 14 periods

Volatility & volume
    volatility_20d    20-day annualized realized volatility (decimal, e.g. 0.28)
    volume_avg_20d    20-day average daily volume
    volume_change_pct 1-day volume change %

Sentiment (aggregated from news + social tables)
    avg_sentiment_score  Mean news sentiment score (range -1 to +1)
    news_count           Number of recent news articles
    social_bullish_pct   Average bullish % across social signal sources
"""
import math
from datetime import datetime
from typing import Optional

import config
from logger import log
from pipeline.database import query, insert_processed_features


# ─────────────────────────────────────────────────────────────────────────────
# Pure computation helpers — no I/O, easy to unit test
# ─────────────────────────────────────────────────────────────────────────────

def _sma(prices: list[float], n: int) -> Optional[float]:
    """Simple moving average of the last n prices."""
    if len(prices) < n:
        return None
    return round(sum(prices[-n:]) / n, 4)


def _momentum(prices: list[float], n: int) -> Optional[float]:
    """
    n-day price momentum as a percentage return.

    Formula: (close[-1] - close[-(n+1)]) / close[-(n+1)] * 100

    Requires at least n + 1 data points. Returns None if data is insufficient
    or if the starting price is zero.
    """
    if len(prices) < n + 1:
        return None
    start = prices[-(n + 1)]
    if start == 0:
        return None
    return round((prices[-1] - start) / start * 100, 4)


def _rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """
    Wilder's Relative Strength Index over `period` days.

    Uses simple (non-smoothed) average gain/loss for stability with
    short price series. Returns None if there are fewer than period + 1 prices.
    """
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains  = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains)  / period if gains  else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _volatility(prices: list[float], n: int = 20) -> Optional[float]:
    """
    Annualized realized volatility over n trading days.

    Steps:
      1. Compute n daily arithmetic returns
      2. Calculate their standard deviation (population)
      3. Annualize by multiplying by sqrt(252)

    Returns a decimal — e.g. 0.28 means 28% annualized volatility.
    Returns None if there are fewer than n + 1 prices.
    """
    if len(prices) < n + 1:
        return None
    recent  = prices[-(n + 1):]
    returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))]
    mean    = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    daily_vol = math.sqrt(variance)
    return round(daily_vol * math.sqrt(252), 4)


def _volume_avg(volumes: list[float], n: int = 20) -> Optional[float]:
    """Average daily volume over the last n periods."""
    if len(volumes) < n:
        return None
    return round(sum(volumes[-n:]) / n, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Feature computation — reads from DB, returns dicts
# ─────────────────────────────────────────────────────────────────────────────

def compute_price_features(ticker: str) -> dict:
    """
    Compute all price, return, trend, and volume features for a ticker.

    Fetches the last 60 trading days (sufficient for SMA-50 plus a buffer).
    All features return None when there is insufficient history rather than
    raising an exception — callers should handle None values gracefully.
    """
    rows = query("stock_prices", ticker=ticker, limit=60)
    rows = sorted(rows, key=lambda r: r.get("date", ""))

    closes  = [float(r["close"])  for r in rows if r.get("close")  is not None]
    volumes = [float(r["volume"]) for r in rows if r.get("volume") is not None]

    daily_return      = None
    volume_change_pct = None

    if len(closes) >= 2:
        prev = closes[-2]
        if prev != 0:
            daily_return = round((closes[-1] - prev) / prev * 100, 4)

    if len(volumes) >= 2:
        prev_vol = volumes[-2]
        if prev_vol != 0:
            volume_change_pct = round((volumes[-1] - prev_vol) / prev_vol * 100, 4)

    return {
        "close":            closes[-1] if closes else None,
        "daily_return":     daily_return,
        "momentum_5d":      _momentum(closes, 5),
        "momentum_20d":     _momentum(closes, 20),
        "sma_5":            _sma(closes, 5),
        "sma_20":           _sma(closes, 20),
        "sma_50":           _sma(closes, 50),
        "rsi_14":           _rsi(closes),
        "volatility_20d":   _volatility(closes),
        "volume_avg_20d":   _volume_avg(volumes),
        "volume_change_pct": volume_change_pct,
    }


def compute_news_features(ticker: str) -> dict:
    """Aggregate sentiment scores across the most recent news articles."""
    rows = query("news_articles", ticker=ticker, limit=20)
    if not rows:
        return {"avg_sentiment_score": 0.0, "news_count": 0}

    scores    = [float(r.get("sentiment_score") or 0.0) for r in rows]
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    return {
        "avg_sentiment_score": avg_score,
        "news_count":          len(rows),
    }


def compute_social_features(ticker: str) -> dict:
    """Aggregate bullish sentiment percentage across social signal sources."""
    rows = query("social_signals", ticker=ticker, limit=20)
    if not rows:
        return {"social_bullish_pct": 50.0}

    bullish_pcts = [float(r.get("bullish_pct") or 0.0) for r in rows]
    avg_bullish  = round(sum(bullish_pcts) / len(bullish_pcts), 2) if bullish_pcts else 50.0

    return {"social_bullish_pct": avg_bullish}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def transform_ticker(ticker: str) -> int:
    """
    Compute all features for a single ticker and persist to processed_features.
    Returns the number of rows inserted (1 on success).
    """
    log.info("Transforming features: {}", ticker)

    price_feats  = compute_price_features(ticker)
    news_feats   = compute_news_features(ticker)
    social_feats = compute_social_features(ticker)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = {
        "ticker": ticker,
        "date":   today,
        **price_feats,
        **news_feats,
        **social_feats,
    }

    log.debug(
        "{} — close={}, daily_return={}%, rsi={}, sma_5={}, sma_20={}, "
        "momentum_5d={}%, momentum_20d={}%, volatility_20d={}, volume_avg_20d={}",
        ticker,
        price_feats.get("close"),
        price_feats.get("daily_return"),
        price_feats.get("rsi_14"),
        price_feats.get("sma_5"),
        price_feats.get("sma_20"),
        price_feats.get("momentum_5d"),
        price_feats.get("momentum_20d"),
        price_feats.get("volatility_20d"),
        price_feats.get("volume_avg_20d"),
    )

    n = insert_processed_features([row])
    log.info("Stored {} feature rows for {}", n, ticker)
    return n


def transform_all(tickers: Optional[list[str]] = None) -> dict[str, int]:
    """Transform features for all configured tickers. Returns {ticker: row_count}."""
    tickers = tickers or config.TICKERS
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            results[ticker] = transform_ticker(ticker)
        except Exception as exc:
            log.error("Failed to transform {}: {}", ticker, exc)
            results[ticker] = 0
    return results
