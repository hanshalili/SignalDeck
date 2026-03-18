"""
run_pipeline.py — CLI entry point for SignalDeck AI.

Usage
-----
    python run_pipeline.py --steps all
    python run_pipeline.py --steps ingest sentiment transform
    python run_pipeline.py --steps llm agent --ticker AAPL

Steps
-----
    init        — Initialise database schema
    ingest      — Ingest stocks, news, social, and fundamentals
    sentiment   — Score news articles with VADER / passthrough
    transform   — Compute derived features
    signals     — Run interpretable signal engine (buy/hold/sell)
    insights    — Generate LLM narrative insights (what/why/bull/bear/summary)
    llm         — Run LLM market analysis
    agent       — Run ReAct agent recommendations
    all         — Run every step in sequence
"""
import argparse
import sys
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config
from logger import log

console = Console()

STEP_ORDER = ["init", "ingest", "sentiment", "transform", "signals", "insights", "llm", "agent"]

# ─────────────────────────────────────────────────────────────────────────────
# Step functions
# ─────────────────────────────────────────────────────────────────────────────

def step_init(tickers: list[str]) -> dict:
    from pipeline.database import init_db
    init_db()
    return {"status": "ok", "message": "Database initialised"}


def step_ingest_stocks(tickers: list[str]) -> dict:
    from pipeline.ingest_stocks import ingest_all_stocks
    return ingest_all_stocks(tickers)


def step_ingest_news(tickers: list[str]) -> dict:
    from pipeline.ingest_news import ingest_all_news
    return ingest_all_news(tickers)


def step_ingest_social(tickers: list[str]) -> dict:
    from pipeline.ingest_social import ingest_all_social
    return ingest_all_social(tickers)


def step_ingest_fundamentals(tickers: list[str]) -> dict:
    from pipeline.ingest_fundamentals import ingest_all_fundamentals
    return ingest_all_fundamentals(tickers)


def step_ingest(tickers: list[str]) -> dict:
    results = {}
    for name, fn in [
        ("stocks", step_ingest_stocks),
        ("news", step_ingest_news),
        ("social", step_ingest_social),
        ("fundamentals", step_ingest_fundamentals),
    ]:
        try:
            results[name] = fn(tickers)
        except Exception as exc:
            log.error("Ingest {} failed: {}", name, exc)
            results[name] = {"error": str(exc)}
    return results


def step_sentiment(tickers: list[str]) -> dict:
    from pipeline.sentiment import score_all_news
    return score_all_news(tickers)


def step_signals(tickers: list[str]) -> dict:
    from features.signal_engine import run_all_signals
    return run_all_signals(tickers)


def step_insights(tickers: list[str]) -> dict:
    from llm.insights import run_insights_pipeline
    return run_insights_pipeline(tickers)


def step_transform(tickers: list[str]) -> dict:
    from pipeline.transform import transform_all
    return transform_all(tickers)


def step_llm(tickers: list[str]) -> dict:
    from llm.analysis_pipeline import run_analysis_pipeline
    return run_analysis_pipeline(tickers)


def step_agent(tickers: list[str]) -> dict:
    from agent.market_agent import run_all_agents
    return run_all_agents(tickers)


STEPS = {
    "init":      step_init,
    "ingest":    step_ingest,
    "sentiment": step_sentiment,
    "transform": step_transform,
    "signals":   step_signals,
    "insights":  step_insights,
    "llm":       step_llm,
    "agent":     step_agent,
}

# ─────────────────────────────────────────────────────────────────────────────
# Rich output helpers
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_COLOR = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}
_SENTIMENT_COLOR = {"bullish": "green", "bearish": "red", "neutral": "yellow"}


def print_summary(llm_results: dict, agent_results: dict, tickers: list[str]) -> None:
    table = Table(
        title="SignalDeck AI — Portfolio Summary",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Ticker", style="bold white", width=8)
    table.add_column("Sentiment", width=10)
    table.add_column("Trend", width=11)
    table.add_column("Risk", width=8)
    table.add_column("LLM Rec", width=9)
    table.add_column("Agent Action", width=13)
    table.add_column("Confidence", width=11)
    table.add_column("Price Target", width=13)

    for ticker in tickers:
        llm = llm_results.get(ticker, {})
        agent = agent_results.get(ticker, {})

        sentiment = llm.get("sentiment", "—")
        sent_color = _SENTIMENT_COLOR.get(sentiment, "white")

        llm_rec = llm.get("recommendation", "—")
        llm_color = _ACTION_COLOR.get(llm_rec, "white")

        agent_action = agent.get("action", "—")
        agent_color = _ACTION_COLOR.get(agent_action, "white")

        conf = agent.get("confidence_score")
        conf_str = f"{conf:.0%}" if conf is not None else "—"

        target = llm.get("price_target")
        target_str = f"${target:.2f}" if target else "—"

        table.add_row(
            ticker,
            f"[{sent_color}]{sentiment}[/{sent_color}]",
            llm.get("trend", "—"),
            llm.get("risk_level", "—"),
            f"[{llm_color}]{llm_rec}[/{llm_color}]",
            f"[{agent_color}]{agent_action}[/{agent_color}]",
            conf_str,
            target_str,
        )

    console.print()
    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="signaldeck",
        description="SignalDeck AI — Intelligent Market Analysis System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        default=["all"],
        choices=STEP_ORDER + ["all"],
        metavar="STEP",
        help="Steps to run: init ingest sentiment transform signals insights llm agent  (default: all)",
    )
    parser.add_argument(
        "--ticker",
        nargs="+",
        default=None,
        help="Override tickers from config (e.g. --ticker AAPL TSLA)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tickers = [t.upper() for t in args.ticker] if args.ticker else config.TICKERS

    steps = STEP_ORDER if "all" in args.steps else args.steps

    console.print(Panel(
        f"[bold cyan]SignalDeck AI[/bold cyan] — Intelligent Market Analysis System\n"
        f"Tickers : [yellow]{', '.join(tickers)}[/yellow]\n"
        f"Steps   : [yellow]{', '.join(steps)}[/yellow]\n"
        f"Backend : [yellow]{config.get_storage_backend()}[/yellow]",
        title="[bold]Pipeline Start[/bold]",
        border_style="cyan",
    ))

    results_by_step: dict[str, dict] = {}
    total_start = time.time()

    for step in steps:
        fn = STEPS.get(step)
        if fn is None:
            console.print(f"[red]Unknown step: {step}[/red]")
            continue

        step_start = time.time()
        console.print(f"\n[bold cyan]▶ Running step:[/bold cyan] {step}")
        try:
            result = fn(tickers)
            results_by_step[step] = result or {}
            elapsed = time.time() - step_start
            console.print(f"[green]✓[/green] {step} completed in {elapsed:.1f}s")
        except Exception as exc:
            elapsed = time.time() - step_start
            console.print(f"[red]✗ {step} failed after {elapsed:.1f}s: {exc}[/red]")
            log.exception("Step {} failed", step)

    total_elapsed = time.time() - total_start

    # Print summary if both llm and agent ran
    llm_results = results_by_step.get("llm", {})
    agent_results = results_by_step.get("agent", {})
    if llm_results or agent_results:
        print_summary(llm_results, agent_results, tickers)

    console.print(Panel(
        f"[bold green]Pipeline complete[/bold green] in {total_elapsed:.1f}s\n"
        f"Steps run: {', '.join(steps)}",
        border_style="green",
    ))

    return 0


if __name__ == "__main__":
    sys.exit(main())
