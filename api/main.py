"""
api/main.py — FastAPI application for SignalDeck AI.

Run
---
    uvicorn api.main:app --reload --port 8000

Endpoints
---------
    GET  /api/health
    GET  /api/stocks
    GET  /api/stocks/{ticker}
    GET  /api/signals/{ticker}
    GET  /api/insights/{ticker}
    GET  /api/news/{ticker}

Interactive docs: http://localhost:8000/docs
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api.models import HealthResponse
from api.routers import stocks, signals, insights, news

app = FastAPI(
    title="SignalDeck AI",
    description="Intelligent market analysis API — prices, signals, LLM insights, and news.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow the Next.js dev server (port 3000) and any localhost origin.
# Tighten to specific origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(stocks.router,   prefix="/api")
app.include_router(signals.router,  prefix="/api")
app.include_router(insights.router, prefix="/api")
app.include_router(news.router,     prefix="/api")


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health():
    """Liveness check — confirms the API is up and shows active config."""
    return HealthResponse(
        status="ok",
        backend=config.get_storage_backend(),
        tickers=config.TICKERS,
        version="1.0.0",
    )
