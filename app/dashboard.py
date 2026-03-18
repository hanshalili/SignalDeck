"""
app/dashboard.py — Streamlit dashboard for SignalDeck AI.

Launch with:
    streamlit run app/dashboard.py

Features
--------
- Portfolio overview with colour-coded signals
- Per-ticker tabs: Price Chart, AI Analysis, Social Signals, Fundamentals, Raw Data
- Auto-refresh every 5 minutes (TTL cache)
- Sidebar controls: ticker selection + date range
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Ensure project root is on path when invoked as `streamlit run app/dashboard.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

import config
from pipeline.database import (
    query,
    get_stock_history,
    get_latest_news,
    get_latest_social,
)

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SignalDeck AI",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main .block-container { padding-top: 1rem; }
    .metric-card {
        background: #1e2130;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.25rem 0;
        border-left: 4px solid #00c0ff;
    }
    .buy-signal  { color: #00e676; font-weight: bold; }
    .sell-signal { color: #ff5252; font-weight: bold; }
    .hold-signal { color: #ffd740; font-weight: bold; }
    .bullish     { color: #00e676; }
    .bearish     { color: #ff5252; }
    .neutral     { color: #ffd740; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cached data loaders
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_stock_history(ticker: str, days: int = 60) -> pd.DataFrame:
    rows = get_stock_history(ticker, days=days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


@st.cache_data(ttl=300)
def load_news(ticker: str, limit: int = 10) -> pd.DataFrame:
    rows = get_latest_news(ticker, limit=limit)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=300)
def load_social(ticker: str, limit: int = 20) -> pd.DataFrame:
    rows = get_latest_social(ticker, limit=limit)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=300)
def load_fundamentals(ticker: str) -> dict:
    rows = query("fundamentals", ticker=ticker, limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=300)
def load_llm_analysis(ticker: str) -> dict:
    rows = query("llm_analysis", ticker=ticker, limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=300)
def load_agent_rec(ticker: str) -> dict:
    rows = query("agent_recommendations", ticker=ticker, limit=1)
    return rows[0] if rows else {}


@st.cache_data(ttl=300)
def load_portfolio_overview(tickers: list[str]) -> pd.DataFrame:
    records = []
    for ticker in tickers:
        llm = load_llm_analysis(ticker)
        agent = load_agent_rec(ticker)
        price_rows = query("stock_prices", ticker=ticker, limit=2)
        price_rows_sorted = sorted(price_rows, key=lambda r: r.get("date", ""), reverse=True)
        close = price_rows_sorted[0].get("close") if price_rows_sorted else None
        prev_close = price_rows_sorted[1].get("close") if len(price_rows_sorted) > 1 else None
        chg = round((close - prev_close) / prev_close * 100, 2) if close and prev_close else None

        records.append({
            "Ticker": ticker,
            "Price": close,
            "Change%": chg,
            "Sentiment": llm.get("sentiment", "—"),
            "Trend": llm.get("trend", "—"),
            "Risk": llm.get("risk_level", "—"),
            "LLM Rec": llm.get("recommendation", "—"),
            "Agent": agent.get("action", "—"),
            "Confidence": agent.get("confidence_score"),
            "Target": llm.get("price_target"),
        })
    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar() -> tuple[list[str], str, int]:
    with st.sidebar:
        st.image("https://via.placeholder.com/200x60/1e2130/00c0ff?text=SignalDeck+AI", use_column_width=True)
        st.markdown("### Configuration")

        all_tickers = config.TICKERS
        selected = st.multiselect(
            "Tickers",
            options=all_tickers,
            default=all_tickers[:5],
        )
        if not selected:
            selected = all_tickers[:1]

        active_ticker = st.selectbox("Active ticker (detail view)", selected)

        days = st.slider("Price history (days)", min_value=7, max_value=90, value=30)

        st.markdown("---")
        st.markdown(f"**Storage:** `{config.get_storage_backend()}`")
        st.markdown(f"**LLM:** {'✅ configured' if config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY else '⚠️ rule-based'}")
        st.markdown(f"*Last refresh: {datetime.now().strftime('%H:%M:%S')}*")

        if st.button("🔄 Refresh data"):
            st.cache_data.clear()
            st.rerun()

    return selected, active_ticker, days


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio overview
# ─────────────────────────────────────────────────────────────────────────────

def render_portfolio_overview(selected: list[str]) -> None:
    st.subheader("Portfolio Overview")
    df = load_portfolio_overview(selected)
    if df.empty:
        st.info("No data yet — run the pipeline first: `python run_pipeline.py --steps all`")
        return

    # KPI row
    cols = st.columns(len(selected))
    for i, row in df.iterrows():
        with cols[i]:
            price_str = f"${row['Price']:.2f}" if row["Price"] else "—"
            delta_str = f"{row['Change%']:+.2f}%" if row["Change%"] is not None else None
            rec = row.get("Agent") or row.get("LLM Rec") or "—"
            css_class = f"{rec.lower()}-signal" if rec in ("BUY", "SELL", "HOLD") else ""
            st.metric(
                label=row["Ticker"],
                value=price_str,
                delta=delta_str,
            )
            st.markdown(f"<span class='{css_class}'>{rec}</span>", unsafe_allow_html=True)

    # Summary table
    st.dataframe(
        df.style.applymap(
            lambda v: "color: #00e676" if v == "BUY" or v == "bullish" else
                      ("color: #ff5252" if v in ("SELL", "bearish") else
                       "color: #ffd740" if v in ("HOLD", "neutral") else ""),
            subset=["Sentiment", "LLM Rec", "Agent"],
        ),
        use_container_width=True,
        hide_index=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Price chart tab
# ─────────────────────────────────────────────────────────────────────────────

def render_price_chart(ticker: str, days: int) -> None:
    df = load_stock_history(ticker, days=days)
    if df.empty:
        st.info(f"No price data for {ticker}.")
        return

    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df["date"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="OHLC",
    ))

    # Moving averages
    for n, color in [(5, "#00c0ff"), (20, "#ff9800"), (50, "#e040fb")]:
        if len(df) >= n:
            df[f"ma{n}"] = df["close"].rolling(n).mean()
            fig.add_trace(go.Scatter(
                x=df["date"], y=df[f"ma{n}"],
                name=f"MA{n}", line=dict(color=color, width=1.5),
            ))

    fig.update_layout(
        title=f"{ticker} — Price Chart",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=500,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Volume bar
    vol_fig = px.bar(df, x="date", y="volume", title="Volume", template="plotly_dark",
                     color_discrete_sequence=["#00c0ff"])
    vol_fig.update_layout(height=200, margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(vol_fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# AI Analysis tab
# ─────────────────────────────────────────────────────────────────────────────

def render_ai_analysis(ticker: str) -> None:
    llm = load_llm_analysis(ticker)
    agent = load_agent_rec(ticker)

    if not llm and not agent:
        st.info("No AI analysis yet — run `python run_pipeline.py --steps llm agent`")
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### LLM Analysis")
        if llm:
            rec = llm.get("recommendation", "—")
            css = f"{rec.lower()}-signal"
            st.markdown(f"**Recommendation:** <span class='{css}'>{rec}</span>", unsafe_allow_html=True)
            st.markdown(f"**Sentiment:** {llm.get('sentiment', '—')}")
            st.markdown(f"**Trend:** {llm.get('trend', '—')}")
            st.markdown(f"**Risk:** {llm.get('risk_level', '—')}")
            st.markdown(f"**Confidence:** {llm.get('confidence', '—')}")
            if llm.get("price_target"):
                st.markdown(f"**Price target:** ${llm['price_target']:.2f}")

            obs_raw = llm.get("key_observations")
            if obs_raw:
                try:
                    obs = json.loads(obs_raw) if isinstance(obs_raw, str) else obs_raw
                    st.markdown("**Key observations:**")
                    for o in obs:
                        st.markdown(f"- {o}")
                except Exception:
                    st.markdown(f"- {obs_raw}")

            st.caption(f"Model: {llm.get('model_used', '—')}  |  Date: {llm.get('date', '—')}")
        else:
            st.info("No LLM analysis available.")

    with col2:
        st.markdown("#### Agent Recommendation")
        if agent:
            action = agent.get("action", "—")
            css = f"{action.lower()}-signal"
            st.markdown(f"**Action:** <span class='{css}'>{action}</span>", unsafe_allow_html=True)
            if agent.get("confidence_score") is not None:
                st.markdown(f"**Confidence:** {agent['confidence_score']:.0%}")
            if agent.get("entry_price"):
                st.markdown(f"**Entry:** ${agent['entry_price']:.2f}")
            if agent.get("stop_loss"):
                st.markdown(f"**Stop loss:** ${agent['stop_loss']:.2f}")
            if agent.get("take_profit"):
                st.markdown(f"**Take profit:** ${agent['take_profit']:.2f}")
            if agent.get("time_horizon"):
                st.markdown(f"**Horizon:** {agent['time_horizon']}")
            if agent.get("rationale"):
                with st.expander("Rationale"):
                    st.markdown(agent["rationale"])
        else:
            st.info("No agent recommendation available.")


# ─────────────────────────────────────────────────────────────────────────────
# Social tab
# ─────────────────────────────────────────────────────────────────────────────

def render_social(ticker: str) -> None:
    df = load_social(ticker)
    if df.empty:
        st.info(f"No social signals for {ticker}.")
        return

    col1, col2 = st.columns([1, 1])

    with col1:
        if "bullish_pct" in df.columns:
            avg_bullish = df["bullish_pct"].mean()
            avg_bearish = df["bearish_pct"].mean() if "bearish_pct" in df.columns else 100 - avg_bullish
            fig = go.Figure(go.Pie(
                labels=["Bullish", "Bearish"],
                values=[avg_bullish, avg_bearish],
                marker_colors=["#00e676", "#ff5252"],
                hole=0.4,
            ))
            fig.update_layout(title=f"{ticker} Social Sentiment", template="plotly_dark", height=300)
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if "sentiment_score" in df.columns and "source" in df.columns:
            fig2 = px.bar(
                df.groupby("source")["sentiment_score"].mean().reset_index(),
                x="source", y="sentiment_score",
                title="Avg Sentiment by Source",
                template="plotly_dark",
                color="sentiment_score",
                color_continuous_scale=["#ff5252", "#ffd740", "#00e676"],
            )
            fig2.update_layout(height=300)
            st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(df[["source", "content", "sentiment_score", "bullish_pct", "published_at"]].head(10),
                 use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Fundamentals tab
# ─────────────────────────────────────────────────────────────────────────────

def render_fundamentals(ticker: str) -> None:
    f = load_fundamentals(ticker)
    if not f:
        st.info(f"No fundamental data for {ticker}.")
        return

    cols = st.columns(4)
    metrics = [
        ("P/E Ratio", f.get("pe_ratio"), None),
        ("Forward P/E", f.get("forward_pe"), None),
        ("EPS", f.get("eps"), "$"),
        ("Beta", f.get("beta"), None),
        ("Profit Margin", f.get("profit_margin"), "%"),
        ("Dividend Yield", f.get("dividend_yield"), "%"),
        ("Analyst Target", f.get("analyst_target"), "$"),
        ("Market Cap", f.get("market_cap"), None),
    ]
    for i, (label, value, prefix) in enumerate(metrics):
        with cols[i % 4]:
            if value is not None:
                if prefix == "$":
                    v_str = f"${value:,.2f}"
                elif prefix == "%":
                    v_str = f"{value:.2%}"
                elif label == "Market Cap":
                    v_str = f"${value/1e9:.1f}B"
                else:
                    v_str = f"{value:.2f}"
                st.metric(label, v_str)

    st.markdown(f"**Sector:** {f.get('sector', '—')}  |  "
                f"52w High: ${f.get('52w_high', 0):.2f}  |  "
                f"52w Low: ${f.get('52w_low', 0):.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# Raw Data tab
# ─────────────────────────────────────────────────────────────────────────────

def render_raw_data(ticker: str) -> None:
    table_choice = st.selectbox(
        "Table",
        ["stock_prices", "news_articles", "social_signals", "fundamentals",
         "processed_features", "llm_analysis", "agent_recommendations"],
    )
    limit = st.slider("Rows", 5, 100, 20)
    rows = query(table_choice, ticker=ticker, limit=limit)
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"No data in {table_choice} for {ticker}.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    selected, active_ticker, days = render_sidebar()

    st.title("📈 SignalDeck AI — Intelligent Market Analysis")

    render_portfolio_overview(selected)

    st.markdown("---")
    st.subheader(f"Detailed View: {active_ticker}")

    tab_chart, tab_ai, tab_social, tab_fund, tab_raw = st.tabs([
        "📊 Price Chart",
        "🤖 AI Analysis",
        "💬 Social Signals",
        "📋 Fundamentals",
        "🗃️ Raw Data",
    ])

    with tab_chart:
        render_price_chart(active_ticker, days)

    with tab_ai:
        render_ai_analysis(active_ticker)

    with tab_social:
        render_social(active_ticker)

    with tab_fund:
        render_fundamentals(active_ticker)

    with tab_raw:
        render_raw_data(active_ticker)


if __name__ == "__main__":
    main()
