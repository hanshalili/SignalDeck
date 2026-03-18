"""
api/routers/insights.py — LLM insight endpoints.

GET /api/insights/{ticker}   — latest narrative insight (5 fields)
"""
from fastapi import APIRouter, HTTPException

import config
from pipeline.database import get_latest_insight
from api.models import InsightResponse

router = APIRouter(prefix="/insights", tags=["insights"])


def _valid_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t not in config.TICKERS:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{t}' not found. Available: {config.TICKERS}",
        )
    return t


@router.get("/{ticker}", response_model=InsightResponse)
def get_insight(ticker: str):
    """Latest LLM-generated insight for one ticker."""
    t = _valid_ticker(ticker)

    row = get_latest_insight(t)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No insight available for '{t}'. Run the pipeline first.",
        )

    return InsightResponse(
        ticker=t,
        date=row.get("date", ""),
        model_used=row.get("model_used", "rule_based"),
        signal_ref=row.get("signal_ref"),
        what_is_happening=row.get("what_is_happening", ""),
        why_signal_changed=row.get("why_signal_changed", ""),
        bull_case=row.get("bull_case", ""),
        bear_case=row.get("bear_case", ""),
        summary=row.get("summary", ""),
    )
