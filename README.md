# SignalDeck AI — Intelligent Market Analysis System

A production-grade Python pipeline for automated stock market analysis.
Ingests price, news, social, and fundamental data → transforms features →
generates LLM-powered analysis → runs a ReAct agent for trade recommendations →
visualises everything in an interactive Streamlit dashboard.

Runs end-to-end with **zero API keys** using high-quality mock data.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         SignalDeck AI Pipeline                          │
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ Stocks   │  │  News    │  │  Social  │  │   Fundamentals       │   │
│  │Alpha Vant│  │ NewsAPI  │  │  Reddit  │  │  Alpha Vantage       │   │
│  │  + GBM   │  │ + Mock   │  │StockTwits│  │    + Mock            │   │
│  │  mock    │  │          │  │ + Mock   │  │                      │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┬───────────┘  │
│       └──────────────┴──────────────┴────────────────────┘            │
│                                    │                                   │
│                            ┌───────▼───────┐                          │
│                            │   Transform   │                          │
│                            │ MA/RSI/Vol/   │                          │
│                            │ Sentiment agg │                          │
│                            └───────┬───────┘                          │
│                                    │                                   │
│                       ┌────────────┴────────────┐                     │
│                       │                         │                     │
│               ┌───────▼───────┐       ┌─────────▼───────┐            │
│               │  LLM Analysis │       │   ReAct Agent   │            │
│               │ GPT-4o-mini / │       │  LangChain +    │            │
│               │ Claude Haiku  │       │  3 custom tools │            │
│               │  (rule-based  │       │  (rule-based    │            │
│               │   fallback)   │       │   fallback)     │            │
│               └───────┬───────┘       └─────────┬───────┘            │
│                       └─────────────┬───────────┘                    │
│                                     │                                 │
│                        ┌────────────▼────────────┐                   │
│                        │  SQLite / BigQuery DB    │                   │
│                        └────────────┬────────────┘                   │
│                                     │                                 │
│                        ┌────────────▼────────────┐                   │
│                        │  Streamlit Dashboard     │                   │
│                        │  Chart/AI/Social/Fund    │                   │
│                        └─────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘

Orchestration: Apache Airflow (schedule: 0 6 * * 1-5)
```

---

## Tech Stack

| Layer         | Technology                              |
|---------------|-----------------------------------------|
| Language      | Python 3.10+                            |
| Orchestration | Apache Airflow 2.9                      |
| Storage       | SQLite (dev) / Google BigQuery (prod)   |
| LLM           | OpenAI GPT-4o-mini / Anthropic Claude   |
| Agent         | LangChain ReAct + AgentExecutor         |
| Dashboard     | Streamlit + Plotly                      |
| Data          | pandas, numpy, scipy, scikit-learn      |
| CLI           | Rich console output                     |
| Testing       | pytest + pytest-mock                    |

---

## Project Structure

```
signaldeck/
├── .env.example              # All 17 env vars with comments
├── .gitignore
├── README.md
├── config.py                 # Typed env var constants, auto-mkdir
├── logger.py                 # Loguru: stderr + rotating file
├── run_pipeline.py           # CLI entry point (argparse + Rich)
├── setup.py
├── setup_airflow.sh          # Bootstrap Airflow
├── pyproject.toml
├── requirements.txt
├── data/.gitkeep
├── logs/.gitkeep
├── dags/
│   └── signaldeck_dag.py     # Airflow DAG (daily, 0 6 * * 1-5)
├── pipeline/
│   ├── database.py           # SQLite + BigQuery backend, 7 tables
│   ├── ingest_stocks.py      # Alpha Vantage + GBM mock
│   ├── ingest_news.py        # NewsAPI + templated mock
│   ├── ingest_social.py      # Reddit + StockTwits + mock
│   ├── ingest_fundamentals.py# Alpha Vantage OVERVIEW + mock
│   └── transform.py          # MA, RSI, volatility, sentiment agg
├── llm/
│   └── analysis_pipeline.py  # SYSTEM_PROMPT, parse, rule-based fallback
├── agent/
│   └── market_agent.py       # ReAct agent, 3 tools, rule-based fallback
├── app/
│   └── dashboard.py          # Streamlit 5-tab dashboard
└── tests/
    └── test_signaldeck.py    # 9 test classes, ~55 tests
```

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — all fields are optional; the pipeline runs without any keys
```

### 3. Run the full pipeline (zero API keys required)

```bash
python run_pipeline.py --steps all
```

### 4. Run the dashboard

```bash
streamlit run app/dashboard.py
```

### 5. Run tests

```bash
pytest tests/ -v
```

### 6. Set up Airflow (optional)

```bash
pip install apache-airflow==2.9.0
bash setup_airflow.sh
airflow scheduler &
airflow webserver --port 8080
# Open http://localhost:8080  (admin / admin)
```

---

## CLI Reference

```
python run_pipeline.py [--steps STEP ...] [--ticker TICKER ...]

Steps:
  init       Initialise database schema
  ingest     Fetch stocks, news, social, fundamentals
  transform  Compute derived features (MA, RSI, etc.)
  llm        Run LLM market analysis
  agent      Run ReAct agent recommendations
  all        Run all steps (default)

Examples:
  python run_pipeline.py --steps all
  python run_pipeline.py --steps ingest transform
  python run_pipeline.py --steps llm agent --ticker AAPL TSLA
```

---

## Example Output

```
╭──────────────────────────────────────────────────────╮
│                    Pipeline Start                     │
│  SignalDeck AI — Intelligent Market Analysis System   │
│  Tickers : AAPL, MSFT, GOOGL, AMZN, META             │
│  Steps   : init, ingest, transform, llm, agent       │
│  Backend : sqlite                                     │
╰──────────────────────────────────────────────────────╯

▶ Running step: init       ✓ 0.1s
▶ Running step: ingest     ✓ 1.3s
▶ Running step: transform  ✓ 0.4s
▶ Running step: llm        ✓ 0.2s  (rule-based)
▶ Running step: agent      ✓ 0.1s  (rule-based)

 Ticker  Sentiment  Trend      Risk    LLM Rec  Agent  Confidence  Target
 AAPL    bullish    uptrend    medium  BUY      BUY    72%         $199.80
 MSFT    bullish    uptrend    low     BUY      BUY    80%         $453.60
 GOOGL   neutral    sideways   medium  HOLD     HOLD   50%         —
 AMZN    bullish    uptrend    medium  BUY      BUY    65%         $199.80
 META    neutral    sideways   medium  HOLD     HOLD   55%         —
```

---

## Database Schema

| Table                   | Primary Key    | Description                        |
|-------------------------|----------------|------------------------------------|
| `stock_prices`          | (ticker, date) | OHLCV daily price data             |
| `news_articles`         | (article_id)   | News with sentiment scores         |
| `social_signals`        | (signal_id)    | Reddit/StockTwits sentiment        |
| `fundamentals`          | (ticker)       | P/E, EPS, margins, beta, etc.      |
| `processed_features`    | (ticker, date) | MA, RSI, volatility aggregates     |
| `llm_analysis`          | (analysis_id)  | LLM-generated structured analysis  |
| `agent_recommendations` | (rec_id)       | ReAct agent trade recommendations  |

---

## Configuration Reference

| Variable                  | Default                     | Description                             |
|---------------------------|-----------------------------|-----------------------------------------|
| `STORAGE_BACKEND`         | `sqlite`                    | `sqlite` or `bigquery`                  |
| `SQLITE_DB_PATH`          | `./data/signaldeck.db`      | SQLite file path                        |
| `GCP_PROJECT_ID`          | —                           | BigQuery GCP project                    |
| `GCP_DATASET_ID`          | `signaldeck`                | BigQuery dataset name                   |
| `ALPHA_VANTAGE_API_KEY`   | —                           | Stocks + fundamentals (25 req/day free) |
| `NEWS_API_KEY`            | —                           | Financial news (100 req/day free)       |
| `OPENAI_API_KEY`          | —                           | GPT-4o-mini for LLM analysis            |
| `ANTHROPIC_API_KEY`       | —                           | Claude Haiku for LLM analysis           |
| `REDDIT_CLIENT_ID`        | —                           | Reddit OAuth2 (optional)                |
| `REDDIT_CLIENT_SECRET`    | —                           | Reddit OAuth2 (optional)                |
| `REDDIT_USER_AGENT`       | `SignalDeck/1.0`            | Reddit API user agent                   |
| `STOCKTWITS_ACCESS_TOKEN` | —                           | StockTwits (optional)                   |
| `TICKERS`                 | `AAPL,MSFT,GOOGL,AMZN,META` | Comma-separated tickers to track        |
| `LOG_LEVEL`               | `INFO`                      | `DEBUG` / `INFO` / `WARNING` / `ERROR`  |
| `AIRFLOW_HOME`            | `./airflow`                 | Airflow home directory                  |

---

## Data Sources & Fallbacks

| Data type     | Primary              | Fallback                               |
|---------------|----------------------|----------------------------------------|
| Stock prices  | Alpha Vantage        | GBM simulation (seeded, reproducible)  |
| News          | NewsAPI              | Per-ticker templated mock articles     |
| Social        | Reddit + StockTwits  | Gaussian-noise mock signals            |
| Fundamentals  | Alpha Vantage OVERVIEW | Pre-built mock data per ticker       |
| LLM analysis  | OpenAI / Anthropic   | Rule-based scoring algorithm           |
| Agent         | LangChain ReAct      | Rule-based recommendation engine       |

All fallbacks are deterministic and seeded so test runs produce stable results.
