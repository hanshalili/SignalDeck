"""
llm/analysis_pipeline.py — LLM-powered market analysis for SignalDeck AI.

Uses OpenAI or Anthropic (whichever key is configured) to generate structured
market analysis from aggregated price, news, social, and fundamental signals.

Falls back to a deterministic rule-based analysis when no LLM key is available,
so the pipeline runs end-to-end without any API credentials.
"""
import json
import re
import uuid
from datetime import datetime
from typing import Optional

import config
from logger import log
from pipeline.database import (
    query,
    get_latest_stock_price,
    insert_llm_analysis,
)

# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are SignalDeck AI, an expert quantitative market analyst.
Your role is to synthesise price action, news sentiment, social signals, and
fundamental data into a concise, structured market analysis.

Always respond with EXACTLY this JSON schema — no markdown fences, no extra keys:
{
  "sentiment": "bullish" | "bearish" | "neutral",
  "trend": "uptrend" | "downtrend" | "sideways",
  "risk_level": "low" | "medium" | "high",
  "recommendation": "BUY" | "HOLD" | "SELL",
  "confidence": "low" | "medium" | "high",
  "price_target": <float or null>,
  "key_observations": [<string>, ...]   // 3–5 bullet points
}"""

ANALYSIS_PROMPT_TEMPLATE = """Analyse the following market data for {ticker} and provide a structured recommendation.

━━━ PRICE ACTION ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Current close : ${close:.2f}
MA5  / MA20 / MA50 : {ma5} / {ma20} / {ma50}
RSI-14        : {rsi}
Volatility-20 : {vol}
Price change  : {price_chg}%
Volume change : {vol_chg}%
MA signal     : {ma_signal}

━━━ NEWS SENTIMENT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{news_section}

━━━ SOCIAL SIGNALS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{social_section}

━━━ FUNDAMENTALS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{fundamental_section}

Based on the above data, provide your structured analysis as valid JSON only."""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(value, fmt=".2f", fallback="N/A") -> str:
    if value is None:
        return fallback
    try:
        return format(value, fmt)
    except (TypeError, ValueError):
        return fallback


def _compute_ma_signal(ma5: Optional[float], ma20: Optional[float], ma50: Optional[float]) -> str:
    if ma5 is None or ma20 is None:
        return "Insufficient data"
    if ma5 > ma20:
        if ma50 and ma20 > ma50:
            return "Strong bullish — MA5 > MA20 > MA50"
        return "Bullish — MA5 above MA20"
    if ma5 < ma20:
        if ma50 and ma20 < ma50:
            return "Strong bearish — MA5 < MA20 < MA50"
        return "Bearish — MA5 below MA20"
    return "Neutral — MA5 ≈ MA20"


def _build_news_section(ticker: str) -> str:
    rows = query("news_articles", ticker=ticker, limit=5)
    if not rows:
        return "No recent news available."
    lines = []
    for r in rows:
        sentiment = r.get("sentiment", "neutral")
        score = _fmt(r.get("sentiment_score"), ".2f")
        title = (r.get("title") or "")[:100]
        lines.append(f"  [{sentiment:8s} {score:>6}] {title}")
    return "\n".join(lines)


def _build_social_section(ticker: str) -> str:
    rows = query("social_signals", ticker=ticker, limit=5)
    if not rows:
        return "No recent social signals."
    lines = []
    for r in rows:
        source = r.get("source", "unknown")
        bullish = _fmt(r.get("bullish_pct"), ".1f")
        bearish = _fmt(r.get("bearish_pct"), ".1f")
        score = _fmt(r.get("sentiment_score"), ".3f")
        lines.append(f"  {source:20s} bullish={bullish}% bearish={bearish}% score={score}")
    return "\n".join(lines)


def _build_fundamental_section(ticker: str) -> str:
    rows = query("fundamentals", ticker=ticker, limit=1)
    if not rows:
        return "No fundamental data available."
    f = rows[0]
    return (
        f"  P/E: {_fmt(f.get('pe_ratio'))}  |  Forward P/E: {_fmt(f.get('forward_pe'))}  |  "
        f"PEG: {_fmt(f.get('peg_ratio'))}\n"
        f"  P/B: {_fmt(f.get('price_to_book'))}  |  Div yield: {_fmt(f.get('dividend_yield'), '.3f')}  |  "
        f"Beta: {_fmt(f.get('beta'))}\n"
        f"  EPS: {_fmt(f.get('eps'))}  |  Profit margin: {_fmt(f.get('profit_margin'), '.2%')}  |  "
        f"Analyst target: ${_fmt(f.get('analyst_target'))}\n"
        f"  Sector: {f.get('sector', 'Unknown')}  |  "
        f"52w High: ${_fmt(f.get('week52_high'))}  |  52w Low: ${_fmt(f.get('week52_low'))}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM call
# ─────────────────────────────────────────────────────────────────────────────

def _call_openai(prompt: str) -> tuple[str, str]:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return response.choices[0].message.content, "gpt-4o-mini"


def _call_anthropic(prompt: str) -> tuple[str, str]:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return response.content[0].text, "claude-haiku-4-5-20251001"


def _rule_based_analysis(ticker: str, features: dict, price: dict) -> dict:
    """Deterministic fallback when no LLM key is configured."""
    close = price.get("close", 0)
    ma5 = features.get("sma_5")
    ma20 = features.get("sma_20")
    rsi = features.get("rsi_14") or 50
    news_score = features.get("avg_sentiment_score") or 0
    social_bullish = features.get("social_bullish_pct") or 50
    price_chg = features.get("daily_return") or 0

    # Simple scoring
    score = 0
    if ma5 and ma20 and ma5 > ma20:
        score += 1
    if rsi < 30:
        score += 2  # oversold = buy
    elif rsi > 70:
        score -= 2  # overbought = sell
    if news_score > 0.2:
        score += 1
    elif news_score < -0.2:
        score -= 1
    if social_bullish > 60:
        score += 1
    elif social_bullish < 40:
        score -= 1
    if price_chg > 2:
        score += 1
    elif price_chg < -2:
        score -= 1

    if score >= 2:
        rec, sentiment, trend = "BUY", "bullish", "uptrend"
    elif score <= -2:
        rec, sentiment, trend = "SELL", "bearish", "downtrend"
    else:
        rec, sentiment, trend = "HOLD", "neutral", "sideways"

    risk = "high" if abs(score) <= 1 else ("low" if abs(score) >= 3 else "medium")
    confidence = "low" if abs(score) <= 1 else ("high" if abs(score) >= 3 else "medium")
    target = round(close * (1.08 if rec == "BUY" else (0.93 if rec == "SELL" else 1.0)), 2)

    observations = [
        f"RSI-14 at {rsi:.1f} — {'oversold' if rsi < 30 else ('overbought' if rsi > 70 else 'neutral')}",
        f"MA signal: {_compute_ma_signal(ma5, ma20, features.get('sma_50'))}",
        f"News sentiment score: {news_score:+.3f}",
        f"Social bullish sentiment: {social_bullish:.1f}%",
        f"Price momentum: {price_chg:+.2f}% (1-day)",
    ]

    return {
        "sentiment": sentiment,
        "trend": trend,
        "risk_level": risk,
        "recommendation": rec,
        "confidence": confidence,
        "price_target": target,
        "key_observations": observations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Response parsing
# ─────────────────────────────────────────────────────────────────────────────

_VALID_SENTIMENTS = {"bullish", "bearish", "neutral"}
_VALID_TRENDS = {"uptrend", "downtrend", "sideways"}
_VALID_RISK = {"low", "medium", "high"}
_VALID_RECS = {"BUY", "HOLD", "SELL"}
_VALID_CONF = {"low", "medium", "high"}


def _extract_json_object(text: str) -> dict:
    """
    Locate and parse the first complete JSON object in `text` using
    JSONDecoder.raw_decode() — avoids greedy regex that can swallow
    content outside the intended object.
    """
    decoder = json.JSONDecoder()
    idx = text.find("{")
    if idx == -1:
        raise ValueError("No JSON object found")
    try:
        obj, _ = decoder.raw_decode(text, idx)
        if not isinstance(obj, dict):
            raise ValueError(f"Expected JSON object, got {type(obj).__name__}")
        return obj
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}") from exc


def parse_llm_response(raw: str) -> dict:
    """Strip markdown fences, parse JSON, validate and normalise fields."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    data = _extract_json_object(cleaned)

    def _lower(key: str) -> str:
        return str(data.get(key, "")).lower()

    sentiment = _lower("sentiment") if _lower("sentiment") in _VALID_SENTIMENTS else "neutral"
    trend = _lower("trend") if _lower("trend") in _VALID_TRENDS else "sideways"
    risk = _lower("risk_level") if _lower("risk_level") in _VALID_RISK else "medium"
    rec = str(data.get("recommendation", "HOLD")).upper()
    rec = rec if rec in _VALID_RECS else "HOLD"
    conf = _lower("confidence") if _lower("confidence") in _VALID_CONF else "medium"

    try:
        target = float(data.get("price_target")) if data.get("price_target") else None
    except (TypeError, ValueError):
        target = None

    observations = data.get("key_observations", [])
    if isinstance(observations, list):
        observations = [str(o) for o in observations[:5]]
    else:
        observations = [str(observations)]

    return {
        "sentiment": sentiment,
        "trend": trend,
        "risk_level": risk,
        "recommendation": rec,
        "confidence": conf,
        "price_target": target,
        "key_observations": observations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str) -> dict:
    """Run LLM analysis for a single ticker. Returns the analysis dict."""
    log.info("Running LLM analysis: {}", ticker)

    price = get_latest_stock_price(ticker) or {}
    features_rows = query("processed_features", ticker=ticker, limit=1)
    features = features_rows[0] if features_rows else {}

    close = price.get("close") or 0
    ma5 = features.get("sma_5")
    ma20 = features.get("sma_20")
    ma50 = features.get("sma_50")
    rsi = features.get("rsi_14")
    vol = features.get("volatility_20d")
    price_chg = features.get("daily_return")
    vol_chg = features.get("volume_change_pct")

    raw_response = None
    model_used = "rule_based"
    parsed: dict = {}

    if config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY:
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            ticker=ticker,
            close=close,
            ma5=_fmt(ma5),
            ma20=_fmt(ma20),
            ma50=_fmt(ma50),
            rsi=_fmt(rsi, ".1f"),
            vol=_fmt(vol, ".4f"),
            price_chg=_fmt(price_chg, "+.2f"),
            vol_chg=_fmt(vol_chg, "+.2f"),
            ma_signal=_compute_ma_signal(ma5, ma20, ma50),
            news_section=_build_news_section(ticker),
            social_section=_build_social_section(ticker),
            fundamental_section=_build_fundamental_section(ticker),
        )

        try:
            if config.OPENAI_API_KEY:
                raw_response, model_used = _call_openai(prompt)
            else:
                raw_response, model_used = _call_anthropic(prompt)
            parsed = parse_llm_response(raw_response)
            log.info("LLM analysis complete for {} ({})", ticker, model_used)
        except Exception as exc:
            log.warning("LLM call failed for {}: {} — falling back to rule-based", ticker, exc)
    else:
        log.info("No LLM key configured — using rule-based analysis for {}", ticker)

    if not parsed:
        parsed = _rule_based_analysis(ticker, features, price)
        model_used = "rule_based"

    today = datetime.utcnow().strftime("%Y-%m-%d")
    analysis_id = str(uuid.uuid4())

    row = {
        "analysis_id": analysis_id,
        "ticker": ticker,
        "date": today,
        "model_used": model_used,
        "raw_response": raw_response or json.dumps(parsed),
        **parsed,
        "key_observations": json.dumps(parsed.get("key_observations", [])),
    }

    insert_llm_analysis([row])
    return {**parsed, "ticker": ticker, "analysis_id": analysis_id}


def run_analysis_pipeline(tickers: Optional[list[str]] = None) -> dict[str, dict]:
    tickers = tickers or config.TICKERS
    results: dict[str, dict] = {}
    for ticker in tickers:
        try:
            results[ticker] = analyze_ticker(ticker)
        except Exception as exc:
            log.error("LLM analysis failed for {}: {}", ticker, exc)
            results[ticker] = {}
    return results
