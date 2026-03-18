"""
api/routers/stocks.py — Stock price endpoints.

GET /api/stocks              — portfolio overview (all tickers, latest price + signal)
GET /api/stocks/{ticker}     — full detail: latest price, features, 30-day history
"""
from fastapi import APIRouter, HTTPException

import config
from pipeline.database import (
    get_latest_stock_price,
    get_latest_features,
    get_latest_signal,
    get_stock_history,
)
from api.models import (
    StocksResponse,
    StockDetailResponse,
    TickerSummary,
    PriceBar,
    Features,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _valid_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t not in config.TICKERS:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{t}' not found. Available: {config.TICKERS}",
        )
    return t


def _to_price_bar(row: dict) -> PriceBar:
    return PriceBar(
        date=row.get("date", ""),
        open=row.get("open"),
        high=row.get("high"),
        low=row.get("low"),
        close=row.get("close"),
        volume=row.get("volume"),
    )


def _to_features(row: dict) -> Features:
    return Features(
        date=row.get("date"),
        close=row.get("close"),
        daily_return=row.get("daily_return"),
        momentum_5d=row.get("momentum_5d"),
        momentum_20d=row.get("momentum_20d"),
        sma_5=row.get("sma_5"),
        sma_20=row.get("sma_20"),
        sma_50=row.get("sma_50"),
        rsi_14=row.get("rsi_14"),
        volatility_20d=row.get("volatility_20d"),
        volume_avg_20d=row.get("volume_avg_20d"),
        volume_change_pct=row.get("volume_change_pct"),
        avg_sentiment_score=row.get("avg_sentiment_score"),
        social_bullish_pct=row.get("social_bullish_pct"),
    )


@router.get("", response_model=StocksResponse)
def list_stocks():
    """Portfolio overview — latest price and signal for every ticker."""
    summaries = []
    for ticker in config.TICKERS:
        price    = get_latest_stock_price(ticker) or {}
        features = get_latest_features(ticker) or {}
        signal   = get_latest_signal(ticker) or {}

        summaries.append(TickerSummary(
            ticker=ticker,
            close=price.get("close") or features.get("close"),
            daily_return=features.get("daily_return"),
            signal=signal.get("signal"),
            confidence_score=signal.get("confidence_score"),
            trend_signal=signal.get("trend_signal"),
            date=price.get("date") or features.get("date"),
        ))

    return StocksResponse(tickers=config.TICKERS, data=summaries)


@router.get("/{ticker}", response_model=StockDetailResponse)
def get_stock(ticker: str):
    """Full detail for one ticker: latest price, computed features, 30-day history."""
    t = _valid_ticker(ticker)

    latest_row = get_latest_stock_price(t)
    features_row = get_latest_features(t)
    history_rows = get_stock_history(t, days=30)

    return StockDetailResponse(
        ticker=t,
        latest_price=_to_price_bar(latest_row) if latest_row else None,
        features=_to_features(features_row) if features_row else None,
        history=[_to_price_bar(r) for r in history_rows],
    )
