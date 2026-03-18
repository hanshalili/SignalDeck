# SignalDeck AI

> **Intelligent Market Analysis System** — an end-to-end data and AI pipeline that ingests multi-source market data, engineers features, generates LLM-powered analysis, runs a ReAct agent for trade recommendations, and surfaces everything in an interactive Streamlit dashboard.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Apache Airflow](https://img.shields.io/badge/orchestration-Airflow%202.9-017cee.svg)](https://airflow.apache.org/)
[![BigQuery](https://img.shields.io/badge/storage-BigQuery-4285F4.svg)](https://cloud.google.com/bigquery)
[![LangChain](https://img.shields.io/badge/agent-LangChain-1C3C3C.svg)](https://langchain.com/)
[![Streamlit](https://img.shields.io/badge/dashboard-Streamlit-FF4B4B.svg)](https://streamlit.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Overview

Most retail market intelligence tools hand you a rating with no explanation. SignalDeck AI is different: it shows you **why** — combining price action, news sentiment, social signals, and fundamental data into a transparent, structured recommendation backed by an LLM reasoning trace and an autonomous ReAct agent.

**Problem it solves:** Manually aggregating price feeds, news APIs, and social sentiment — then synthesising it into a coherent view — is slow, inconsistent, and hard to automate. SignalDeck AI replaces that workflow with a fully orchestrated pipeline you can run on a schedule, inspect at every step, and extend with real API keys or your own data sources.

**Key design principle:** The system operates end-to-end with **zero API keys** using deterministic mock data. Every external source (Alpha Vantage, NewsAPI, Reddit, OpenAI, Anthropic) has a seeded fallback, so you can develop, test, and demo without credentials.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SignalDeck AI — System Overview                      │
│                                                                             │
│   External Sources              Ingestion Layer                             │
│  ┌──────────────┐   ┌────────────────────────────────────────────────┐     │
│  │ Alpha Vantage│──▶│ ingest_stocks.py    (OHLCV + GBM mock)         │     │
│  │ NewsAPI      │──▶│ ingest_news.py      (articles + mock)          │     │
│  │ Reddit       │──▶│ ingest_social.py    (sentiment + mock)         │     │
│  │ StockTwits   │──▶│ ingest_fundamentals.py (P/E, EPS + mock)       │     │
│  └──────────────┘   └────────────────────────┬───────────────────────┘     │
│                                              │                              │
│                                   ┌──────────▼──────────┐                  │
│                                   │   transform.py       │                  │
│                                   │  MA5/20/50, RSI-14,  │                  │
│                                   │  volatility, agg     │                  │
│                                   │  sentiment           │                  │
│                                   └──────────┬──────────┘                  │
│                                              │                              │
│                          ┌───────────────────┴────────────────────┐        │
│                          │                                         │        │
│                ┌─────────▼──────────┐             ┌───────────────▼──────┐ │
│                │  LLM Analysis      │             │   ReAct Agent        │ │
│                │  GPT-4o-mini /     │             │   LangChain +        │ │
│                │  Claude Haiku      │             │   3 custom tools     │ │
│                │  ──────────────    │             │   ────────────────   │ │
│                │  Rule-based        │             │   Rule-based         │ │
│                │  fallback          │             │   fallback           │ │
│                └─────────┬──────────┘             └───────────────┬──────┘ │
│                          └─────────────────┬───────────────────────┘       │
│                                            │                               │
│                               ┌────────────▼────────────┐                 │
│                               │   SQLite / BigQuery      │                 │
│                               │   7 partitioned tables   │                 │
│                               └────────────┬────────────┘                 │
│                                            │                               │
│                               ┌────────────▼────────────┐                 │
│                               │   Streamlit Dashboard   │                 │
│                               │   5 tabs, live refresh  │                 │
│                               └─────────────────────────┘                 │
│                                                                             │
│   Orchestration: Apache Airflow DAG — schedule: 0 6 * * 1-5 (weekdays)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

| Step | Module | Input | Output |
|------|--------|-------|--------|
| 1. Init | `pipeline/database.py` | — | Creates 7 tables in SQLite or BigQuery |
| 2. Ingest | `pipeline/ingest_*.py` | External APIs / mock | Raw rows: prices, news, social, fundamentals |
| 3. Transform | `pipeline/transform.py` | Raw tables | `processed_features`: MA, RSI, volatility, sentiment |
| 4. Analyse | `llm/analysis_pipeline.py` | Features + raw data | `llm_analysis`: sentiment, trend, recommendation, target |
| 5. Agent | `agent/market_agent.py` | Same features + LLM tools | `agent_recommendations`: action, rationale, stop/target |
| 6. Visualise | `app/dashboard.py` | All tables | Interactive 5-tab Streamlit dashboard |

---

## Tech Stack

### Orchestration
| Tool | Version | Purpose |
|------|---------|---------|
| Apache Airflow | 2.9.0 | DAG scheduling, task dependency, XCom |

### Data Engineering
| Tool | Version | Purpose |
|------|---------|---------|
| pandas | 2.2.1 | Tabular data manipulation |
| numpy | 1.26.4 | Numerical computation |
| scipy | 1.13.0 | Statistical functions |
| scikit-learn | 1.4.2 | Feature scaling utilities |
| tenacity | 8.2.3 | Retry logic for API calls |

### Storage
| Tool | Version | Purpose |
|------|---------|---------|
| SQLite | (stdlib) | Zero-config local storage (default) |
| Google BigQuery | 3.17.2 | Production data warehouse (partitioned + clustered) |
| google-cloud-bigquery-storage | 2.24.0 | Fast BQ read via Arrow |

### AI / LLM / Agent
| Tool | Version | Purpose |
|------|---------|---------|
| LangChain | 0.1.16 | ReAct agent framework |
| langchain-openai | 0.1.3 | GPT-4o-mini integration |
| langchain-community | 0.0.36 | Claude integration |
| openai | 1.23.6 | OpenAI API client |
| anthropic | 0.23.1 | Anthropic API client |

### Dashboard / Visualisation
| Tool | Version | Purpose |
|------|---------|---------|
| Streamlit | 1.33.0 | Interactive web dashboard |
| Plotly | 5.21.0 | Candlestick charts, sentiment plots |

### Infrastructure / Dev
| Tool | Version | Purpose |
|------|---------|---------|
| loguru | 0.7.2 | Structured logging (stderr + rotating file) |
| python-dotenv | 1.0.1 | Environment variable management |
| rich | 13.7.1 | CLI tables, panels, coloured output |
| pytest | 8.1.1 | Test suite (53 tests) |
| pytest-mock | 3.14.0 | Mock fixtures |

---

## Project Structure

```
signaldeck/
│
├── config.py                   # Typed env var constants; auto-creates data/ & logs/
├── logger.py                   # Loguru: colourised stderr + 10 MB rotating file
├── run_pipeline.py             # CLI entry point (argparse + Rich output)
├── setup.py                    # Pip-installable package definition
├── setup_airflow.sh            # One-shot Airflow bootstrap script
├── pyproject.toml              # pytest config, build backend
├── requirements.txt            # Pinned dependencies
├── .env.example                # Template for all 17 environment variables
├── .gitignore                  # Excludes .env, service-account.json, data/, logs/
│
├── pipeline/                   # Core data pipeline
│   ├── database.py             # Unified SQLite + BigQuery backend; 7 table schemas;
│   │                           #   parameterised queries (no SQL injection)
│   ├── ingest_stocks.py        # Alpha Vantage OHLCV; GBM mock fallback
│   ├── ingest_news.py          # NewsAPI articles; templated mock fallback
│   ├── ingest_social.py        # Reddit OAuth2 + StockTwits; Gaussian mock fallback
│   ├── ingest_fundamentals.py  # Alpha Vantage OVERVIEW; pre-built mock fallback
│   └── transform.py            # MA5/20/50, RSI-14, volatility, sentiment aggregation
│
├── llm/
│   └── analysis_pipeline.py   # Structured LLM analysis; rule-based fallback;
│                               #   response validation with enum enforcement
│
├── agent/
│   └── market_agent.py        # LangChain ReAct agent; 3 tools (stock data, news,
│                               #   AST-safe calculator); rule-based fallback
│
├── dags/
│   └── signaldeck_dag.py      # Airflow DAG: 0 6 * * 1-5; 4 parallel ingest tasks;
│                               #   XCom pushes; graceful LLM key skip
│
├── app/
│   └── dashboard.py           # Streamlit dashboard; 5 tabs; TTL-cached queries;
│                               #   portfolio overview + per-ticker detail
│
├── tests/
│   └── test_signaldeck.py     # 9 test classes; 53 tests; zero API keys required
│
├── data/
│   └── .gitkeep               # Runtime SQLite DB stored here (gitignored)
│
└── logs/
    └── .gitkeep               # Rotating log files stored here (gitignored)
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- `pip` or `pip3`
- (Optional) A Google Cloud project with BigQuery enabled for production storage
- (Optional) API keys for Alpha Vantage, NewsAPI, OpenAI, or Anthropic

### 1. Clone the repository

```bash
git clone https://github.com/hanshalili/SignalDeck.git
cd SignalDeck
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Open .env and fill in any keys you have.
# All keys are optional — the pipeline runs entirely on mock data without them.
```

### Environment variables reference

| Variable | Default | Required |
|----------|---------|----------|
| `STORAGE_BACKEND` | `sqlite` | No — use `bigquery` for GCP |
| `SQLITE_DB_PATH` | `./data/signaldeck.db` | No |
| `GCP_PROJECT_ID` | — | Only if `STORAGE_BACKEND=bigquery` |
| `GCP_DATASET_ID` | `signaldeck` | Only if BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Only if BigQuery |
| `ALPHA_VANTAGE_API_KEY` | — | No (falls back to GBM mock) |
| `NEWS_API_KEY` | — | No (falls back to mock articles) |
| `OPENAI_API_KEY` | — | No (falls back to rule-based) |
| `ANTHROPIC_API_KEY` | — | No (falls back to rule-based) |
| `REDDIT_CLIENT_ID` | — | No |
| `REDDIT_CLIENT_SECRET` | — | No |
| `REDDIT_USER_AGENT` | `SignalDeck/1.0` | No |
| `STOCKTWITS_ACCESS_TOKEN` | — | No |
| `TICKERS` | `AAPL,MSFT,GOOGL,AMZN,META` | No |
| `LOG_LEVEL` | `INFO` | No |
| `AIRFLOW_HOME` | `./airflow` | Only if using Airflow |

### 5. (Optional) Set up Google Cloud credentials

```bash
# Create a service account with BigQuery Admin role, download the key, then:
export GOOGLE_APPLICATION_CREDENTIALS=./service-account.json

# Update .env:
STORAGE_BACKEND=bigquery
GCP_PROJECT_ID=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
```

### 6. (Optional) Set up Apache Airflow

```bash
pip install apache-airflow==2.9.0
bash setup_airflow.sh
# Opens Airflow at http://localhost:8080 (admin / admin)
```

---

## Usage

### Run the full pipeline (zero API keys required)

```bash
python run_pipeline.py --steps all
```

### Run specific steps

```bash
# Initialise database only
python run_pipeline.py --steps init

# Ingest and transform only
python run_pipeline.py --steps ingest transform

# LLM analysis and agent on specific tickers
python run_pipeline.py --steps llm agent --ticker AAPL TSLA NVDA
```

### Launch the dashboard

```bash
streamlit run app/dashboard.py
# Opens at http://localhost:8501
```

### Run the test suite

```bash
pytest tests/ -v
# 53 tests, ~2 seconds, zero API keys required
```

### Example pipeline output

```
╭─────────────────────────────── Pipeline Start ───────────────────────────────╮
│ SignalDeck AI — Intelligent Market Analysis System                           │
│ Tickers : AAPL, MSFT, GOOGL, AMZN, META                                     │
│ Steps   : init, ingest, transform, llm, agent                               │
│ Backend : bigquery                                                           │
╰──────────────────────────────────────────────────────────────────────────────╯

▶ Running step: init        ✓  6.7s
▶ Running step: ingest      ✓ 16.9s   (90 price rows × 5 tickers)
▶ Running step: transform   ✓ 28.3s   (MA, RSI, volatility computed)
▶ Running step: llm         ✓ 19.9s   (rule-based — no LLM key)
▶ Running step: agent       ✓ 18.2s   (rule-based — no LLM key)

                    SignalDeck AI — Portfolio Summary
╭────────┬───────────┬──────────┬────────┬─────────┬────────┬────────────┬─────────╮
│ Ticker │ Sentiment │ Trend    │ Risk   │ LLM Rec │ Agent  │ Confidence │ Target  │
├────────┼───────────┼──────────┼────────┼─────────┼────────┼────────────┼─────────┤
│ AAPL   │ bullish   │ uptrend  │ medium │ BUY     │ BUY    │ 72%        │ $209.14 │
│ MSFT   │ bullish   │ uptrend  │ low    │ BUY     │ BUY    │ 80%        │ $453.60 │
│ GOOGL  │ neutral   │ sideways │ medium │ HOLD    │ HOLD   │ 50%        │ —       │
│ AMZN   │ bullish   │ uptrend  │ medium │ BUY     │ BUY    │ 65%        │ $199.80 │
│ META   │ neutral   │ sideways │ medium │ HOLD    │ HOLD   │ 55%        │ —       │
╰────────┴───────────┴──────────┴────────┴─────────┴────────┴────────────┴─────────╯
```

---

## Data Pipeline / Workflow

### Step 1 — `init` — Database initialisation

`pipeline/database.py:init_db()`

Creates all 7 tables in either SQLite or BigQuery. BigQuery tables are created with:
- **TIME partitioning** on `date` / `published_at` columns for query efficiency
- **Clustering** on `ticker` for low-latency per-ticker reads
- All queries use **named parameters** (`@ticker`, `@date_cutoff`) — no string interpolation

### Step 2 — `ingest` — Multi-source data ingestion (4 parallel tasks in Airflow)

| Ingester | Primary Source | Fallback |
|----------|---------------|---------|
| `ingest_stocks.py` | Alpha Vantage `TIME_SERIES_DAILY` | Geometric Brownian Motion (seeded by ticker) |
| `ingest_news.py` | NewsAPI `/everything` | Per-ticker templated articles with preset sentiments |
| `ingest_social.py` | Reddit OAuth2 + StockTwits public API | Gaussian-noise signals (±12%, seeded by date) |
| `ingest_fundamentals.py` | Alpha Vantage `OVERVIEW` | Pre-built fundamental snapshots per ticker |

All ingesters use `@retry` (via tenacity) with exponential backoff on network errors. Fallbacks are activated automatically when APIs are unavailable or unconfigured.

### Step 3 — `transform` — Feature engineering

`pipeline/transform.py`

For each ticker, reads raw tables and computes `processed_features`:

| Feature | Computation |
|---------|-------------|
| `ma_5`, `ma_20`, `ma_50` | Simple moving averages over close prices |
| `rsi_14` | Wilder's RSI over 14-day delta series |
| `volatility_20` | 20-day realised volatility (std dev of returns) |
| `avg_sentiment_score` | Mean of recent news sentiment scores |
| `news_count` | Count of articles ingested |
| `social_bullish_pct` | Average bullish % across social sources |
| `price_change_pct` | 1-day price momentum |
| `volume_change_pct` | 1-day volume momentum |

### Step 4 — `llm` — LLM market analysis

`llm/analysis_pipeline.py`

Constructs a structured prompt with 4 sections (price action, news, social, fundamentals) separated by `━━━` dividers. Sends to GPT-4o-mini or Claude Haiku and parses the JSON response with strict enum validation:

- `sentiment`: `bullish` | `bearish` | `neutral`
- `trend`: `uptrend` | `downtrend` | `sideways`
- `risk_level`: `low` | `medium` | `high`
- `recommendation`: `BUY` | `HOLD` | `SELL`
- `confidence`: `low` | `medium` | `high`
- `price_target`: float or null
- `key_observations`: list of up to 5 strings

Falls back to a rule-based scoring algorithm when no LLM key is configured.

### Step 5 — `agent` — ReAct agent recommendations

`agent/market_agent.py`

Builds a LangChain `AgentExecutor` (max 6 iterations) with three tools:

| Tool | Description |
|------|-------------|
| `query_stock_data` | Retrieves price history + computed features for the ticker |
| `query_news` | Retrieves recent news articles with sentiment scores |
| `calculator` | Evaluates arithmetic via AST parser (no `eval()`) |

The agent reasons step-by-step and outputs a JSON recommendation: `action`, `rationale`, `confidence_score`, `entry_price`, `stop_loss`, `take_profit`, `time_horizon`.

Falls back to a rule-based recommendation when no LLM key is configured.

### Step 6 — Dashboard

`app/dashboard.py`

A Streamlit app with TTL-cached queries (5-minute refresh) and five tabs per ticker:

| Tab | Content |
|-----|---------|
| Price Chart | Candlestick (OHLC) + MA5/20/50 overlays + volume bar |
| AI Analysis | LLM recommendation card + agent recommendation card with rationale |
| Social Signals | Bullish/bearish pie chart + sentiment by source bar chart |
| Fundamentals | P/E, EPS, margins, beta, 52-week range metrics |
| Raw Data | Interactive table for any of the 7 database tables |

---

## Features

- **Zero-dependency operation** — Runs end-to-end without any API keys; all sources have deterministic, seeded mock fallbacks
- **Dual storage backend** — Toggle between SQLite (local dev) and Google BigQuery (production) via a single env var
- **LLM-powered analysis** — Supports OpenAI GPT-4o-mini and Anthropic Claude Haiku with structured JSON output and strict field validation
- **ReAct agent** — Autonomous LangChain agent with custom tools, reasoning trace, and stop-loss/take-profit levels
- **Airflow orchestration** — Production-ready DAG with parallel ingest tasks, XCom pushes, and graceful LLM key fallback
- **Parameterised SQL** — All queries use placeholders (`?` in SQLite, `@param` in BigQuery) — no string interpolation
- **Safe expression evaluation** — Calculator tool uses an AST-based evaluator; no `eval()`
- **Rotating structured logs** — Loguru dual-sink: colourised stderr + 10 MB rotating file with 14-day retention
- **Rich CLI** — Coloured portfolio summary table with per-step timing
- **53 automated tests** — Full coverage of ingestion, transforms, LLM parsing, and agent logic; no API keys required

---

## Database Schema

```
signaldeck dataset (BigQuery) / signaldeck.db (SQLite)
│
├── stock_prices          PK (ticker, date)     — OHLCV daily, DATE partitioned
├── news_articles         PK (article_id)       — title, sentiment, score, TIMESTAMP partitioned
├── social_signals        PK (signal_id)        — source, bullish%, score, TIMESTAMP partitioned
├── fundamentals          PK (ticker)           — P/E, EPS, margins, sector, beta
├── processed_features    PK (ticker, date)     — MA, RSI, volatility, aggregated sentiment
├── llm_analysis          PK (analysis_id)      — recommendation, confidence, target, observations
└── agent_recommendations PK (rec_id)           — action, rationale, entry, stop, take-profit
```

All BigQuery tables are clustered on `ticker` for efficient per-symbol queries.

---

## Monitoring / Logging

### Loguru dual-sink

```
# Colourised stderr (development)
2026-03-17 21:31:25 | INFO     | pipeline.ingest_stocks:164 — Stored 90 price rows for AAPL

# Rotating file: logs/signaldeck_YYYY-MM-DD.log
# Rotation:  10 MB per file
# Retention: 14 days
# Compression: gzip
```

Configure the level via `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` in `.env`.

### Airflow task monitoring

Each Airflow task pushes results to XCom for downstream inspection:

```python
# Example XCom value pushed by ingest_stocks task
{"AAPL": 90, "MSFT": 90, "GOOGL": 90, "AMZN": 90, "META": 90}
```

Tasks are configured with `retries=2` and `retry_delay=5m`. LLM tasks warn-and-continue (not fail) when no API key is present.

### BigQuery query observability

All BigQuery jobs are labelled with a unique `Job ID` logged at DEBUG level, making them traceable in the GCP console.

---

## Data Sources & Fallbacks

| Source | Primary | Fallback | Deterministic? |
|--------|---------|---------|----------------|
| Stock prices | Alpha Vantage `TIME_SERIES_DAILY` | Geometric Brownian Motion simulation | Yes — seeded by ticker |
| News | NewsAPI `/everything` | Templated articles with preset sentiments | Yes — seeded by ticker + date |
| Social | Reddit OAuth2 + StockTwits | Gaussian-noise signals (±12% std dev) | Yes — seeded by ticker + date |
| Fundamentals | Alpha Vantage `OVERVIEW` | Per-ticker snapshot (AAPL, MSFT, GOOGL, AMZN, META) | Yes |
| LLM analysis | OpenAI / Anthropic | Rule-based signal scoring | Yes |
| Agent | LangChain ReAct | Rule-based recommendation engine | Yes |

---

## Future Improvements

- [ ] **Real-time data** — Add WebSocket price feeds (Alpaca, Polygon.io) alongside daily batch
- [ ] **FinBERT sentiment** — Replace heuristic sentiment scores with a fine-tuned NLP model
- [ ] **Vector memory** — Persist agent reasoning traces in a vector store (Chroma, Pinecone) for cross-ticker pattern retrieval
- [ ] **Backtesting module** — Replay historical recommendations against actual returns
- [ ] **Terraform IaC** — Codify the BigQuery dataset, IAM service account, and GCS bucket creation
- [ ] **Multi-model ensemble** — Run GPT-4o and Claude in parallel; resolve disagreements via confidence-weighted voting
- [ ] **Alerting** — Push Slack/email notifications when an agent recommendation crosses a confidence threshold
- [ ] **CI/CD pipeline** — GitHub Actions workflow: lint → test → deploy DAG → smoke test
- [ ] **Expanded universe** — Support ETFs, crypto, and non-US equities beyond the default 5 tickers
- [ ] **Secret Manager integration** — Replace local `.env` with GCP Secret Manager for production deployments

---

## Contributing

Contributions are welcome. Please follow this workflow:

1. Fork the repository and create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes. Ensure the test suite still passes:
   ```bash
   pytest tests/ -v
   ```

3. Keep commits focused and descriptive. Follow the existing commit style:
   ```
   <type>: <short summary>

   <body explaining what changed and why>
   ```

4. Open a pull request against `main` with a clear description of the change.

**Code standards:**
- All database queries must use parameterised placeholders — no f-string SQL
- No `eval()` — use `ast`-based evaluation for expressions
- New ingestion sources must include a deterministic mock fallback
- New features should include at least one corresponding test

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [LangChain](https://langchain.com/) — ReAct agent framework
- [Apache Airflow](https://airflow.apache.org/) — workflow orchestration
- [Streamlit](https://streamlit.io/) — dashboard framework
- [Loguru](https://github.com/Delgan/loguru) — structured logging
- [Alpha Vantage](https://www.alphavantage.co/) — market data API
- [NewsAPI](https://newsapi.org/) — financial news API
