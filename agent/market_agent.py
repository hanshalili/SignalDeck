"""
agent/market_agent.py — ReAct market agent for SignalDeck AI.

Uses LangChain's AgentExecutor with three tools:
  1. QueryStockDataTool   — retrieve price history + features
  2. QueryNewsTool        — retrieve recent news with sentiment
  3. CalculatorTool       — perform arithmetic calculations

Falls back to a deterministic rule-based recommendation when no LLM is
configured so the pipeline runs without API keys.
"""
import json
import re
import uuid
from datetime import datetime
from typing import Optional, Type

import config
from logger import log
from pipeline.database import (
    query,
    get_latest_stock_price,
    insert_agent_recommendations,
)

# ─────────────────────────────────────────────────────────────────────────────
# Agent system prompt
# ─────────────────────────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are SignalDeck's autonomous market research agent.

Your job is to gather evidence about a stock, reason step-by-step, and output a
final structured trading recommendation.

BUY  guidelines : RSI < 40, MA5 > MA20, positive news sentiment, bullish social >55%
HOLD guidelines : Mixed signals, RSI 40–60, no clear trend
SELL guidelines : RSI > 65, MA5 < MA20, negative news, bearish social >50%

Use the available tools to retrieve data before making your recommendation.
After your reasoning, output ONLY valid JSON in this exact schema:
{
  "action": "BUY" | "HOLD" | "SELL",
  "rationale": "<one paragraph>",
  "confidence_score": <0.0–1.0>,
  "entry_price": <float or null>,
  "stop_loss": <float or null>,
  "take_profit": <float or null>,
  "time_horizon": "short" | "medium" | "long"
}"""


# ─────────────────────────────────────────────────────────────────────────────
# LangChain tools
# ─────────────────────────────────────────────────────────────────────────────

def _build_langchain_tools(ticker: str):
    """Build LangChain BaseTool subclasses scoped to the given ticker."""
    try:
        from langchain.tools import BaseTool
        from pydantic import BaseModel, Field
    except ImportError:
        return None

    class StockInput(BaseModel):
        days: int = Field(default=30, description="Number of days of history to retrieve")

    class QueryStockDataTool(BaseTool):
        name: str = "query_stock_data"
        description: str = (
            "Retrieve recent stock price history and computed features "
            f"for {ticker}. Input: number of days (int, default 30)."
        )
        args_schema: Type[BaseModel] = StockInput

        def _run(self, days: int = 30) -> str:
            prices = query("stock_prices", ticker=ticker, limit=days)
            features = query("processed_features", ticker=ticker, limit=1)
            prices_sorted = sorted(prices, key=lambda r: r.get("date", ""), reverse=True)[:5]
            result = {
                "latest_prices": prices_sorted,
                "features": features[0] if features else {},
            }
            return json.dumps(result, default=str)

    class NewsInput(BaseModel):
        limit: int = Field(default=10, description="Number of recent news articles")

    class QueryNewsTool(BaseTool):
        name: str = "query_news"
        description: str = (
            f"Retrieve recent news articles and sentiment scores for {ticker}. "
            "Input: limit (int, default 10)."
        )
        args_schema: Type[BaseModel] = NewsInput

        def _run(self, limit: int = 10) -> str:
            articles = query("news_articles", ticker=ticker, limit=limit)
            summary = [
                {
                    "title": a.get("title", ""),
                    "sentiment": a.get("sentiment", "neutral"),
                    "score": a.get("sentiment_score", 0),
                    "published_at": a.get("published_at", ""),
                }
                for a in articles
            ]
            return json.dumps(summary, default=str)

    class CalcInput(BaseModel):
        expression: str = Field(description="A safe arithmetic expression to evaluate, e.g. '185 * 1.08'")

    class CalculatorTool(BaseTool):
        name: str = "calculator"
        description: str = (
            "Evaluate a simple arithmetic expression. "
            "Useful for computing price targets, percentage changes, etc."
        )
        args_schema: Type[BaseModel] = CalcInput

        def _run(self, expression: str) -> str:
            # Restrict to safe arithmetic only
            safe = re.sub(r"[^0-9+\-*/().\s]", "", expression)
            try:
                result = eval(safe, {"__builtins__": {}})  # noqa: S307
                return str(round(float(result), 4))
            except Exception as exc:
                return f"Error: {exc}"

    return [QueryStockDataTool(), QueryNewsTool(), CalculatorTool()]


# ─────────────────────────────────────────────────────────────────────────────
# Agent construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_agent(ticker: str):
    """Build a LangChain AgentExecutor for the given ticker."""
    from langchain.agents import AgentExecutor, create_react_agent
    from langchain_openai import ChatOpenAI
    from langchain_community.chat_models import ChatAnthropic
    from langchain import hub

    tools = _build_langchain_tools(ticker)
    if tools is None:
        raise ImportError("LangChain tools could not be built")

    if config.OPENAI_API_KEY:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            api_key=config.OPENAI_API_KEY,
        )
    elif config.ANTHROPIC_API_KEY:
        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            temperature=0.1,
            anthropic_api_key=config.ANTHROPIC_API_KEY,
        )
    else:
        raise ValueError("No LLM API key configured")

    try:
        prompt = hub.pull("hwchase17/react")
    except Exception:
        from langchain_core.prompts import PromptTemplate
        prompt = PromptTemplate.from_template(
            "Answer the following questions as best you can.\n\n"
            "You have access to the following tools:\n{tools}\n\n"
            "Use the following format:\n"
            "Question: the input question you must answer\n"
            "Thought: you should always think about what to do\n"
            "Action: the action to take, should be one of [{tool_names}]\n"
            "Action Input: the input to the action\n"
            "Observation: the result of the action\n"
            "... (this Thought/Action/Action Input/Observation can repeat N times)\n"
            "Thought: I now know the final answer\n"
            "Final Answer: {agent_scratchpad}\n\n"
            "Begin!\n\nQuestion: {input}\n\nThought:{agent_scratchpad}"
        )

    agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=6,
        handle_parsing_errors=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_agent_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in agent response: {raw[:200]}")

    data = json.loads(match.group())
    action = str(data.get("action", "HOLD")).upper()
    if action not in ("BUY", "HOLD", "SELL"):
        action = "HOLD"

    try:
        confidence = float(data.get("confidence_score", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    horizon = str(data.get("time_horizon", "medium")).lower()
    if horizon not in ("short", "medium", "long"):
        horizon = "medium"

    def _safe_float(v) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "action": action,
        "rationale": str(data.get("rationale", ""))[:1000],
        "confidence_score": confidence,
        "entry_price": _safe_float(data.get("entry_price")),
        "stop_loss": _safe_float(data.get("stop_loss")),
        "take_profit": _safe_float(data.get("take_profit")),
        "time_horizon": horizon,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based fallback
# ─────────────────────────────────────────────────────────────────────────────

def _rule_based_recommendation(ticker: str) -> dict:
    price_row = get_latest_stock_price(ticker) or {}
    features_rows = query("processed_features", ticker=ticker, limit=1)
    features = features_rows[0] if features_rows else {}

    close = price_row.get("close") or 100.0
    rsi = features.get("rsi_14") or 50.0
    ma5 = features.get("ma_5")
    ma20 = features.get("ma_20")
    news_score = features.get("avg_sentiment_score") or 0.0
    social_bullish = features.get("social_bullish_pct") or 50.0

    score = 0
    if ma5 and ma20:
        score += 1 if ma5 > ma20 else -1
    if rsi < 40:
        score += 2
    elif rsi > 65:
        score -= 2
    if news_score > 0.2:
        score += 1
    elif news_score < -0.2:
        score -= 1
    if social_bullish > 55:
        score += 1
    elif social_bullish < 45:
        score -= 1

    if score >= 2:
        action = "BUY"
        entry = close
        stop = round(close * 0.95, 2)
        target = round(close * 1.10, 2)
        conf = min(0.5 + score * 0.1, 0.9)
    elif score <= -2:
        action = "SELL"
        entry = close
        stop = round(close * 1.05, 2)
        target = round(close * 0.90, 2)
        conf = min(0.5 + abs(score) * 0.1, 0.9)
    else:
        action = "HOLD"
        entry = None
        stop = None
        target = None
        conf = 0.5

    return {
        "action": action,
        "rationale": (
            f"Rule-based analysis (no LLM configured). "
            f"RSI={rsi:.1f}, MA signal={'bullish' if (ma5 and ma20 and ma5 > ma20) else 'bearish'}, "
            f"news_score={news_score:+.3f}, social_bullish={social_bullish:.1f}%."
        ),
        "confidence_score": round(conf, 2),
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "time_horizon": "medium",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_for_ticker(ticker: str) -> dict:
    log.info("Running market agent: {}", ticker)

    parsed: dict = {}
    agent_trace = ""

    if config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY:
        try:
            executor = _build_agent(ticker)
            question = (
                f"Analyse {ticker} stock. Retrieve the latest price data and news, "
                f"then provide a structured trading recommendation."
            )
            result = executor.invoke({"input": question})
            raw_output = result.get("output", "")
            agent_trace = raw_output
            parsed = parse_agent_response(raw_output)
            log.info("Agent recommendation for {}: {}", ticker, parsed.get("action"))
        except Exception as exc:
            log.warning("Agent failed for {}: {} — using rule-based fallback", ticker, exc)

    if not parsed:
        log.info("Using rule-based recommendation for {}", ticker)
        parsed = _rule_based_recommendation(ticker)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    rec_id = str(uuid.uuid4())
    row = {
        "rec_id": rec_id,
        "ticker": ticker,
        "date": today,
        "agent_trace": agent_trace[:2000] if agent_trace else json.dumps(parsed),
        **parsed,
    }
    insert_agent_recommendations([row])
    return {**parsed, "ticker": ticker, "rec_id": rec_id}


def run_all_agents(tickers: Optional[list[str]] = None) -> dict[str, dict]:
    tickers = tickers or config.TICKERS
    results: dict[str, dict] = {}
    for ticker in tickers:
        try:
            results[ticker] = run_agent_for_ticker(ticker)
        except Exception as exc:
            log.error("Agent failed for {}: {}", ticker, exc)
            results[ticker] = {}
    return results
