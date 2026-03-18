"""
features/signal_engine.py — Interpretable signal engine for SignalDeck AI.

Combines four independent sub-signals into a single BUY / HOLD / SELL decision.

Sub-signals
-----------
  trend      — SMA crossover: sma_5 vs sma_20 (and sma_20 vs sma_50 for strength)
  momentum   — 5-day and 20-day price-change momentum
  volatility — 20-day annualised vol; high vol reduces confidence but does not
               change direction (it reflects uncertainty, not direction)
  sentiment  — average news sentiment score + social bullish percentage

Scoring
-------
Each directional sub-signal (trend, momentum, sentiment) returns:
  +1  bullish
   0  neutral
  -1  bearish

Volatility does not vote on direction; it adjusts the final confidence.

  raw_score = sum(trend, momentum, sentiment)   ∈ [-3, +3]

  raw_score >= 2   →  BUY
  raw_score <= -2  →  SELL
  else             →  HOLD

Confidence
----------
  base = 0.5
  + 0.12 per signal in agreement with final direction   (max +0.36)
  - 0.10 if volatility_20d > 0.45  (very high — >45% annualised)
  - 0.05 if volatility_20d > 0.25  (elevated — >25% annualised)
  clipped to [0.30, 0.90]

Supporting reason
-----------------
A plain-English sentence listing each sub-signal verdict, e.g.
"Trend: bullish (SMA5 > SMA20). Momentum: bullish (+4.2% / +9.1%). Sentiment: neutral (score=+0.05). Volatility: elevated (28.3% ann)."

This is deliberately human-readable so it can be surfaced as-is in the frontend
without any post-processing.
"""
import uuid
from datetime import datetime
from typing import Optional

import config
from logger import log
from pipeline.database import (
    query,
    get_latest_features,
    insert_signals,
)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-signal evaluators  (pure functions — no I/O, fully unit-testable)
# ─────────────────────────────────────────────────────────────────────────────

def _trend_signal(sma_5: Optional[float], sma_20: Optional[float], sma_50: Optional[float]) -> tuple[int, str]:
    """
    Returns (vote, description).
    Strong bullish: SMA5 > SMA20 > SMA50
    Bullish:        SMA5 > SMA20
    Bearish:        SMA5 < SMA20
    Strong bearish: SMA5 < SMA20 < SMA50
    """
    if sma_5 is None or sma_20 is None:
        return 0, "neutral (insufficient data)"

    if sma_5 > sma_20:
        if sma_50 and sma_20 > sma_50:
            return 1, f"bullish (SMA5 {sma_5:.2f} > SMA20 {sma_20:.2f} > SMA50 {sma_50:.2f})"
        return 1, f"bullish (SMA5 {sma_5:.2f} > SMA20 {sma_20:.2f})"

    if sma_5 < sma_20:
        if sma_50 and sma_20 < sma_50:
            return -1, f"bearish (SMA5 {sma_5:.2f} < SMA20 {sma_20:.2f} < SMA50 {sma_50:.2f})"
        return -1, f"bearish (SMA5 {sma_5:.2f} < SMA20 {sma_20:.2f})"

    return 0, f"neutral (SMA5 ≈ SMA20 = {sma_5:.2f})"


def _momentum_signal(mom_5d: Optional[float], mom_20d: Optional[float]) -> tuple[int, str]:
    """
    Both 5-day and 20-day momentum must agree for a directional vote.
    Mixed signals → neutral.
    Thresholds: ±2% for 5-day, ±5% for 20-day.
    """
    m5  = mom_5d  if mom_5d  is not None else 0.0
    m20 = mom_20d if mom_20d is not None else 0.0

    bullish = m5 > 2.0 and m20 > 5.0
    bearish = m5 < -2.0 and m20 < -5.0

    desc = f"(5d={m5:+.1f}% / 20d={m20:+.1f}%)"
    if bullish:
        return 1, f"bullish {desc}"
    if bearish:
        return -1, f"bearish {desc}"
    return 0, f"neutral {desc}"


def _volatility_label(vol: Optional[float]) -> tuple[str, float]:
    """
    Returns (label, confidence_penalty).
    Does NOT contribute a directional vote.
    """
    if vol is None:
        return "unknown", 0.0
    if vol > 0.45:
        return f"very high ({vol:.1%} ann)", -0.10
    if vol > 0.25:
        return f"elevated ({vol:.1%} ann)", -0.05
    return f"normal ({vol:.1%} ann)", 0.0


def _sentiment_signal(avg_score: Optional[float], social_bullish: Optional[float]) -> tuple[int, str]:
    """
    Combined news + social sentiment.
    Threshold: news ±0.10, social ±5% from 50.
    Both must agree for a directional vote.
    """
    ns = avg_score     if avg_score     is not None else 0.0
    sb = social_bullish if social_bullish is not None else 50.0

    news_bull    = ns > 0.10
    news_bear    = ns < -0.10
    social_bull  = sb > 55.0
    social_bear  = sb < 45.0

    desc = f"(news={ns:+.3f}, social={sb:.1f}%)"
    if news_bull and social_bull:
        return 1, f"bullish {desc}"
    if news_bear and social_bear:
        return -1, f"bearish {desc}"
    if news_bull or social_bull:
        return 0, f"mixed-positive {desc}"
    if news_bear or social_bear:
        return 0, f"mixed-negative {desc}"
    return 0, f"neutral {desc}"


# ─────────────────────────────────────────────────────────────────────────────
# Signal aggregator
# ─────────────────────────────────────────────────────────────────────────────

def compute_signal(features: dict) -> dict:
    """
    Combine sub-signals into one trading signal.
    Input:  a processed_features row (dict)
    Output: signal dict ready for insert_signals()
    """
    sma_5   = features.get("sma_5")
    sma_20  = features.get("sma_20")
    sma_50  = features.get("sma_50")
    mom_5d  = features.get("momentum_5d")
    mom_20d = features.get("momentum_20d")
    vol     = features.get("volatility_20d")
    avg_sent = features.get("avg_sentiment_score")
    social  = features.get("social_bullish_pct")

    trend_vote,    trend_desc    = _trend_signal(sma_5, sma_20, sma_50)
    momentum_vote, momentum_desc = _momentum_signal(mom_5d, mom_20d)
    vol_label,     vol_penalty   = _volatility_label(vol)
    sentiment_vote, sent_desc    = _sentiment_signal(avg_sent, social)

    raw_score = trend_vote + momentum_vote + sentiment_vote

    # Direction
    if raw_score >= 2:
        signal = "buy"
    elif raw_score <= -2:
        signal = "sell"
    else:
        signal = "hold"

    # Confidence: start at 0.5, reward agreement, penalise high vol
    agreeing = sum(
        1 for v in (trend_vote, momentum_vote, sentiment_vote)
        if (signal == "buy"  and v > 0)
        or (signal == "sell" and v < 0)
        or (signal == "hold" and v == 0)
    )
    confidence = 0.50 + (agreeing * 0.12) + vol_penalty
    confidence = round(max(0.30, min(0.90, confidence)), 2)

    # Human-readable reason (surfaced directly in the UI)
    reason = (
        f"Trend: {trend_desc}. "
        f"Momentum: {momentum_desc}. "
        f"Sentiment: {sent_desc}. "
        f"Volatility: {vol_label}."
    )

    return {
        "signal":           signal,
        "confidence_score": confidence,
        "supporting_reason": reason,
        "trend_signal":     "bullish" if trend_vote > 0 else ("bearish" if trend_vote < 0 else "neutral"),
        "momentum_signal":  "bullish" if momentum_vote > 0 else ("bearish" if momentum_vote < 0 else "neutral"),
        "volatility_signal": vol_label,
        "sentiment_signal": "bullish" if sentiment_vote > 0 else ("bearish" if sentiment_vote < 0 else "neutral"),
        "raw_score":        raw_score,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_signal_engine(ticker: str) -> dict:
    """Compute and persist the signal for one ticker. Returns the signal dict."""
    log.info("Computing signal: {}", ticker)

    features = get_latest_features(ticker)
    if not features:
        log.warning("No features found for {} — skipping signal", ticker)
        return {}

    result = compute_signal(features)

    row = {
        "signal_id":        str(uuid.uuid4()),
        "ticker":           ticker,
        "date":             datetime.utcnow().strftime("%Y-%m-%d"),
        **result,
        "created_at":       datetime.utcnow().isoformat(),
    }
    insert_signals([row])

    log.info(
        "Signal for {}: {} (confidence={:.0%}, score={:+d})",
        ticker, result["signal"].upper(), result["confidence_score"], result["raw_score"],
    )
    log.info("  Reason: {}", result["supporting_reason"])
    return {**result, "ticker": ticker}


def run_all_signals(tickers: Optional[list] = None) -> dict:
    """Run signal engine for all tickers. Returns {ticker: signal_dict}."""
    tickers = tickers or config.TICKERS
    results: dict = {}
    for ticker in tickers:
        try:
            results[ticker] = run_signal_engine(ticker)
        except Exception as exc:
            log.error("Signal engine failed for {}: {}", ticker, exc)
            results[ticker] = {}
    return results
