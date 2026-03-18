"""
api/routers/news.py — News and sentiment endpoints.

GET /api/news/{ticker}       — latest articles with VADER scores joined in
"""
from fastapi import APIRouter, HTTPException, Query

import config
from pipeline.database import get_latest_news, get_latest_sentiment
from api.models import NewsResponse, ArticleRow

router = APIRouter(prefix="/news", tags=["news"])


def _valid_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t not in config.TICKERS:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{t}' not found. Available: {config.TICKERS}",
        )
    return t


@router.get("/{ticker}", response_model=NewsResponse)
def get_news(
    ticker: str,
    limit: int = Query(default=10, ge=1, le=50, description="Number of articles to return"),
):
    """
    Latest news articles for one ticker.
    VADER compound scores from sentiment_scores are joined in so the
    frontend does not need a separate request.
    """
    t = _valid_ticker(ticker)

    articles = get_latest_news(t, limit=limit)
    scores   = get_latest_sentiment(t, limit=limit * 2)

    # Build article_id → vader_compound lookup from sentiment_scores
    vader_map: dict[str, float] = {
        s["article_id"]: s.get("compound", 0.0)
        for s in scores
        if s.get("method") in ("vader",)
    }

    rows = []
    for art in articles:
        aid = art.get("article_id", "")
        rows.append(ArticleRow(
            article_id=aid,
            title=art.get("title"),
            source_name=art.get("source_name"),
            published_at=art.get("published_at"),
            sentiment=art.get("sentiment"),
            sentiment_score=art.get("sentiment_score"),
            vader_compound=vader_map.get(aid),
            url=art.get("url"),
        ))

    return NewsResponse(ticker=t, count=len(rows), articles=rows)
