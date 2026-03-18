"""
llm/insights.py — LLM insight layer for SignalDeck AI.

Uses LangChain + OpenAI (or Anthropic fallback) to generate five structured
insight fields for each ticker, grounded exclusively in warehouse data.

Grounding strategy
------------------
Every piece of text the LLM receives is pulled from the database — price
features, signal engine output, news headlines, and sentiment scores.
The system prompt explicitly prohibits referencing facts not in the context.
This prevents hallucination while still producing useful, natural-language
commentary.

Output fields
-------------
  what_is_happening  — 1-2 sentences: current price/technical state
  why_signal_changed — 1 sentence: primary driver of the signal
  bull_case          — 1-2 sentences: conditions that would validate upside
  bear_case          — 1-2 sentences: conditions that would confirm downside
  summary            — 1 sentence: bottom-line takeaway

Backends
--------
  1. OpenAI gpt-4o-mini       (if OPENAI_API_KEY set)
  2. Anthropic claude-haiku   (if ANTHROPIC_API_KEY set)
  3. Rule-based template      (always available — no API key required)

LangChain is used for structured output via .with_structured_output(Pydantic),
which internally uses OpenAI function-calling / Anthropic tool-use — no manual
JSON parsing, no regex, type-safe at the boundary.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

import config
from logger import log
from pipeline.database import (
    get_latest_features,
    get_latest_signal,
    get_latest_news,
    insert_llm_insights,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic output schema  (also serves as the LangChain structured-output spec)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from pydantic import BaseModel, Field

    class InsightOutput(BaseModel):
        what_is_happening: str = Field(
            description=(
                "1-2 sentences describing the ticker's current technical state: "
                "price level, trend direction, and RSI. Reference specific numbers."
            )
        )
        why_signal_changed: str = Field(
            description=(
                "1 sentence explaining the primary reason the signal engine "
                "produced the current signal. Reference the sub-signal that "
                "had the most influence."
            )
        )
        bull_case: str = Field(
            description=(
                "1-2 sentences describing the conditions that would support a "
                "bullish move, based on the provided data."
            )
        )
        bear_case: str = Field(
            description=(
                "1-2 sentences describing the conditions that would confirm "
                "downside risk, based on the provided data."
            )
        )
        summary: str = Field(
            description=(
                "Exactly 1 sentence: the bottom-line takeaway combining signal, "
                "confidence, and the most important supporting reason."
            )
        )

    _PYDANTIC_AVAILABLE = True

except ImportError:
    InsightOutput = None  # type: ignore[misc,assignment]
    _PYDANTIC_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are SignalDeck AI's market commentary engine.

Your job is to write concise, factual analysis grounded ONLY in the data \
provided in the user message. Every claim you make must be directly \
supported by a number or fact in that context.

Rules:
- DO NOT mention any news events, company names, or figures not present below.
- DO NOT speculate about macro events, earnings, or news not provided.
- Keep each field to the length described.
- Use plain English — no jargon, no emojis, no markdown.
- If a data point is missing (shown as N/A), acknowledge uncertainty rather \
  than guessing.\
"""

_CONTEXT_TEMPLATE = """\
=== TICKER: {ticker} | DATE: {date} ===

--- PRICE & TECHNICALS ---
Close       : ${close}
Daily return: {daily_return}%
5d momentum : {momentum_5d}%   |  20d momentum: {momentum_20d}%
SMA-5       : {sma_5}          |  SMA-20: {sma_20}   |  SMA-50: {sma_50}
RSI-14      : {rsi_14}
Ann. volatility (20d): {volatility_20d}%

--- SIGNAL ENGINE OUTPUT ---
Signal      : {signal} (confidence: {confidence_pct}%)
Raw score   : {raw_score} / 3
Trend       : {trend_signal}
Momentum    : {momentum_signal}
Sentiment   : {sentiment_signal}
Volatility  : {volatility_signal}
Reason      : {supporting_reason}

--- PREVIOUS SIGNAL (for "why changed") ---
{prev_signal_block}

--- RECENT NEWS HEADLINES ---
{news_block}

--- SENTIMENT SUMMARY ---
Avg news sentiment score : {avg_sentiment_score}
Social bullish %         : {social_bullish_pct}%

Generate the five insight fields based strictly on the data above.\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Context builder  (assembles warehouse data into the prompt)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v, spec=".2f", fallback="N/A") -> str:
    if v is None:
        return fallback
    try:
        return format(float(v), spec)
    except (TypeError, ValueError):
        return fallback


def _build_context(ticker: str, features: dict, signal: dict) -> str:
    """
    Pull all grounding data from the warehouse and render the prompt context.
    Only real DB values are injected — nothing is fabricated here.
    """
    # Previous signal for "why changed" comparison
    from pipeline.database import get_signal_history
    history = get_signal_history(ticker, days=2)
    # history is newest-first; skip today's signal to find the prior one
    today = datetime.utcnow().strftime("%Y-%m-%d")
    prior = next((r for r in history if r.get("date") != today), None)
    if prior:
        prev_signal_block = (
            f"Date: {prior.get('date')}  Signal: {prior.get('signal', 'N/A').upper()}  "
            f"Score: {prior.get('raw_score', 'N/A')}  "
            f"Confidence: {_fmt(prior.get('confidence_score'), '.0%')}"
        )
    else:
        prev_signal_block = "No prior signal on record."

    # News headlines (real, from DB)
    news_rows = get_latest_news(ticker, limit=5)
    if news_rows:
        news_lines = []
        for r in news_rows:
            sent  = r.get("sentiment", "neutral")
            score = _fmt(r.get("sentiment_score"), "+.2f")
            title = (r.get("title") or "")[:120]
            news_lines.append(f"  [{sent:8s} {score}] {title}")
        news_block = "\n".join(news_lines)
    else:
        news_block = "  No recent news available."

    return _CONTEXT_TEMPLATE.format(
        ticker          = ticker,
        date            = today,
        close           = _fmt(features.get("close")),
        daily_return    = _fmt(features.get("daily_return"), "+.2f"),
        momentum_5d     = _fmt(features.get("momentum_5d"), "+.2f"),
        momentum_20d    = _fmt(features.get("momentum_20d"), "+.2f"),
        sma_5           = _fmt(features.get("sma_5")),
        sma_20          = _fmt(features.get("sma_20")),
        sma_50          = _fmt(features.get("sma_50"), fallback="N/A"),
        rsi_14          = _fmt(features.get("rsi_14"), ".1f"),
        volatility_20d  = _fmt(features.get("volatility_20d"), ".1%"),
        signal          = signal.get("signal", "hold").upper(),
        confidence_pct  = int(round(signal.get("confidence_score", 0.5) * 100)),
        raw_score       = signal.get("raw_score", 0),
        trend_signal    = signal.get("trend_signal", "N/A"),
        momentum_signal = signal.get("momentum_signal", "N/A"),
        sentiment_signal= signal.get("sentiment_signal", "N/A"),
        volatility_signal= signal.get("volatility_signal", "N/A"),
        supporting_reason= signal.get("supporting_reason", "N/A"),
        prev_signal_block= prev_signal_block,
        news_block      = news_block,
        avg_sentiment_score = _fmt(features.get("avg_sentiment_score"), "+.3f"),
        social_bullish_pct  = _fmt(features.get("social_bullish_pct"), ".1f"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM callers
# ─────────────────────────────────────────────────────────────────────────────

def _call_openai_structured(context: str) -> tuple[dict, str]:
    """LangChain ChatOpenAI with structured output via function-calling."""
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        api_key=config.OPENAI_API_KEY,
    )
    structured_llm = llm.with_structured_output(InsightOutput)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human",  "{context}"),
    ])
    chain = prompt | structured_llm
    result: InsightOutput = chain.invoke({"context": context})

    return result.model_dump(), "gpt-4o-mini"


def _call_anthropic_structured(context: str) -> tuple[dict, str]:
    """LangChain ChatAnthropic with structured output via tool-use."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.prompts import ChatPromptTemplate

    model_id = "claude-haiku-4-5-20251001"
    llm = ChatAnthropic(
        model=model_id,
        temperature=0.2,
        anthropic_api_key=config.ANTHROPIC_API_KEY,
        max_tokens=800,
    )
    structured_llm = llm.with_structured_output(InsightOutput)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human",  "{context}"),
    ])
    chain = prompt | structured_llm
    result: InsightOutput = chain.invoke({"context": context})

    return result.model_dump(), model_id


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based fallback (no API key required)
# ─────────────────────────────────────────────────────────────────────────────

def _rule_based_insight(ticker: str, features: dict, signal: dict) -> dict:
    """
    Generate template-driven insights from warehouse data.
    Every sentence references real values — same grounding contract as the LLM.
    """
    sig       = signal.get("signal", "hold").upper()
    conf      = signal.get("confidence_score", 0.5)
    raw_score = signal.get("raw_score", 0)
    trend     = signal.get("trend_signal", "neutral")
    momentum  = signal.get("momentum_signal", "neutral")
    vol_label = signal.get("volatility_signal", "normal")
    reason    = signal.get("supporting_reason", "")

    close   = _fmt(features.get("close"))
    rsi     = _fmt(features.get("rsi_14"), ".1f")
    mom_5d  = _fmt(features.get("momentum_5d"), "+.1f")
    mom_20d = _fmt(features.get("momentum_20d"), "+.1f")
    sma_5   = _fmt(features.get("sma_5"))
    sma_20  = _fmt(features.get("sma_20"))
    news_sc = _fmt(features.get("avg_sentiment_score"), "+.3f")
    social  = _fmt(features.get("social_bullish_pct"), ".1f")
    vol_pct = _fmt(features.get("volatility_20d"), ".1%")

    what = (
        f"{ticker} is trading at ${close} with RSI-14 at {rsi}, "
        f"indicating {'overbought' if float(rsi or 50) > 65 else ('oversold' if float(rsi or 50) < 35 else 'neutral')} conditions. "
        f"The trend is {trend} with SMA-5 at {sma_5} vs SMA-20 at {sma_20}."
    )

    why = (
        f"The {sig} signal (score {raw_score:+d}/3, confidence {conf:.0%}) is "
        f"primarily driven by {trend} trend and {momentum} momentum "
        f"({mom_5d}% 5d / {mom_20d}% 20d)."
    )

    bull = (
        f"If momentum continues ({mom_5d}% 5d), a sustained SMA-5 breakout above "
        f"SMA-20 ({sma_20}) would confirm an uptrend. "
        f"Positive news sentiment ({news_sc}) provides a tailwind."
    )

    bear = (
        f"Volatility is {vol_label}, which elevates risk. "
        f"A drop in SMA-5 below SMA-20 or deteriorating sentiment (currently {news_sc}) "
        f"would confirm downside pressure."
    )

    summary = (
        f"{ticker} shows a {sig} signal with {conf:.0%} confidence; "
        f"key risk is {vol_label} volatility ({vol_pct} annualised)."
    )

    return {
        "what_is_happening":  what,
        "why_signal_changed": why,
        "bull_case":          bull,
        "bear_case":          bear,
        "summary":            summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_insight(ticker: str) -> dict:
    """
    Generate and persist LLM insights for one ticker.
    Returns the insight dict (without DB bookkeeping fields).
    """
    log.info("Generating LLM insight: {}", ticker)

    features = get_latest_features(ticker)
    if not features:
        log.warning("No features found for {} — skipping insight", ticker)
        return {}

    signal = get_latest_signal(ticker)
    if not signal:
        log.warning("No signal found for {} — skipping insight", ticker)
        return {}

    raw_response = ""
    model_used   = "rule_based"
    insight: dict = {}

    if (config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY) and _PYDANTIC_AVAILABLE:
        context = _build_context(ticker, features, signal)
        try:
            if config.OPENAI_API_KEY:
                insight, model_used = _call_openai_structured(context)
            else:
                insight, model_used = _call_anthropic_structured(context)
            raw_response = json.dumps(insight)
            log.info("LLM insight generated for {} via {}", ticker, model_used)
        except Exception as exc:
            log.warning("LLM insight failed for {}: {} — using rule-based", ticker, exc)

    if not insight:
        insight = _rule_based_insight(ticker, features, signal)
        model_used = "rule_based"
        raw_response = json.dumps(insight)
        log.info("Rule-based insight for {}", ticker)

    today     = datetime.utcnow().strftime("%Y-%m-%d")
    insight_id = str(uuid.uuid4())

    row = {
        "insight_id":         insight_id,
        "ticker":             ticker,
        "date":               today,
        **insight,
        "signal_ref":         signal.get("signal_id", ""),
        "model_used":         model_used,
        "raw_response":       raw_response[:4000],
        "created_at":         datetime.utcnow().isoformat(),
    }
    insert_llm_insights([row])

    log.info("  Summary: {}", insight.get("summary", ""))
    return {**insight, "ticker": ticker, "insight_id": insight_id, "model_used": model_used}


def run_insights_pipeline(tickers: Optional[list] = None) -> dict:
    """Generate insights for all tickers. Returns {ticker: insight_dict}."""
    tickers = tickers or config.TICKERS
    results: dict = {}
    for ticker in tickers:
        try:
            results[ticker] = generate_insight(ticker)
        except Exception as exc:
            log.error("Insight generation failed for {}: {}", ticker, exc)
            results[ticker] = {}
    return results
