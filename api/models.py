"""
api/models.py — Pydantic response schemas for the SignalDeck API.

Defines the exact JSON shape of every endpoint response.
Frontend can use these as the TypeScript interface contract.
"""
from typing import Optional
from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# Shared primitives
# ─────────────────────────────────────────────────────────────────────────────

class TickerSummary(BaseModel):
    ticker: str
    close: Optional[float]
    daily_return: Optional[float]       # 1-day % return
    signal: Optional[str]               # buy | hold | sell
    confidence_score: Optional[float]   # 0.30 – 0.90
    trend_signal: Optional[str]         # bullish | neutral | bearish
    date: Optional[str]                 # YYYY-MM-DD of latest data


# ─────────────────────────────────────────────────────────────────────────────
# /api/stocks
# ─────────────────────────────────────────────────────────────────────────────

class StocksResponse(BaseModel):
    tickers: list[str]
    data: list[TickerSummary]


# ─────────────────────────────────────────────────────────────────────────────
# /api/stocks/{ticker}
# ─────────────────────────────────────────────────────────────────────────────

class PriceBar(BaseModel):
    date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[int]


class Features(BaseModel):
    date: Optional[str]
    close: Optional[float]
    daily_return: Optional[float]
    momentum_5d: Optional[float]
    momentum_20d: Optional[float]
    sma_5: Optional[float]
    sma_20: Optional[float]
    sma_50: Optional[float]
    rsi_14: Optional[float]
    volatility_20d: Optional[float]
    volume_avg_20d: Optional[float]
    volume_change_pct: Optional[float]
    avg_sentiment_score: Optional[float]
    social_bullish_pct: Optional[float]


class StockDetailResponse(BaseModel):
    ticker: str
    latest_price: Optional[PriceBar]
    features: Optional[Features]
    history: list[PriceBar]             # newest-first, up to 30 bars


# ─────────────────────────────────────────────────────────────────────────────
# /api/signals/{ticker}
# ─────────────────────────────────────────────────────────────────────────────

class SignalRow(BaseModel):
    signal_id: str
    date: str
    signal: str                         # buy | hold | sell
    confidence_score: float
    raw_score: int                      # -3 to +3
    trend_signal: str
    momentum_signal: str
    volatility_signal: str
    sentiment_signal: str
    supporting_reason: str


class SignalsResponse(BaseModel):
    ticker: str
    latest: Optional[SignalRow]
    history: list[SignalRow]            # newest-first, up to 30 days


# ─────────────────────────────────────────────────────────────────────────────
# /api/insights/{ticker}
# ─────────────────────────────────────────────────────────────────────────────

class InsightResponse(BaseModel):
    ticker: str
    date: str
    model_used: str
    signal_ref: Optional[str]          # FK to signal_history.signal_id
    what_is_happening: str
    why_signal_changed: str
    bull_case: str
    bear_case: str
    summary: str


# ─────────────────────────────────────────────────────────────────────────────
# /api/news/{ticker}
# ─────────────────────────────────────────────────────────────────────────────

class ArticleRow(BaseModel):
    article_id: str
    title: Optional[str]
    source_name: Optional[str]
    published_at: Optional[str]
    sentiment: Optional[str]           # positive | neutral | negative
    sentiment_score: Optional[float]   # provider score [-1, +1]
    vader_compound: Optional[float]    # VADER compound score [-1, +1]
    url: Optional[str]


class NewsResponse(BaseModel):
    ticker: str
    count: int
    articles: list[ArticleRow]


# ─────────────────────────────────────────────────────────────────────────────
# /api/health
# ─────────────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str                         # ok
    backend: str                        # sqlite | bigquery
    tickers: list[str]
    version: str
