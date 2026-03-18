"""
api/routers/signals.py — Signal engine endpoints.

GET /api/signals/{ticker}    — latest signal + 30-day history
"""
from fastapi import APIRouter, HTTPException

import config
from pipeline.database import get_latest_signal, get_signal_history
from api.models import SignalsResponse, SignalRow

router = APIRouter(prefix="/signals", tags=["signals"])


def _valid_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t not in config.TICKERS:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{t}' not found. Available: {config.TICKERS}",
        )
    return t


def _to_signal_row(row: dict) -> SignalRow:
    return SignalRow(
        signal_id=row.get("signal_id", ""),
        date=row.get("date", ""),
        signal=row.get("signal", "hold"),
        confidence_score=row.get("confidence_score", 0.5),
        raw_score=row.get("raw_score", 0),
        trend_signal=row.get("trend_signal", "neutral"),
        momentum_signal=row.get("momentum_signal", "neutral"),
        volatility_signal=row.get("volatility_signal", ""),
        sentiment_signal=row.get("sentiment_signal", "neutral"),
        supporting_reason=row.get("supporting_reason", ""),
    )


@router.get("/{ticker}", response_model=SignalsResponse)
def get_signals(ticker: str):
    """Latest signal and 30-day signal history for one ticker."""
    t = _valid_ticker(ticker)

    latest_row = get_latest_signal(t)
    history_rows = get_signal_history(t, days=30)

    return SignalsResponse(
        ticker=t,
        latest=_to_signal_row(latest_row) if latest_row else None,
        history=[_to_signal_row(r) for r in history_rows],
    )
