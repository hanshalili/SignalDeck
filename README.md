# SignalDeck AI

> **End-to-end data and AI engineering portfolio project** — a production-grade market intelligence platform that orchestrates multi-source ingestion, feature engineering, LLM analysis, and an autonomous ReAct agent into a single deployable system backed by Google BigQuery and Apache Airflow.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Apache Airflow](https://img.shields.io/badge/Airflow-2.9-017cee.svg)](https://airflow.apache.org/)
[![BigQuery](https://img.shields.io/badge/BigQuery-GCP-4285F4.svg)](https://cloud.google.com/bigquery)
[![LangChain](https://img.shields.io/badge/LangChain-ReAct%20Agent-1C3C3C.svg)](https://langchain.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B.svg)](https://streamlit.io/)
[![Tests](https://img.shields.io/badge/tests-53%20passing-brightgreen.svg)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What This Project Demonstrates

This is a deliberately full-stack data + AI engineering project, built to be production-deployable rather than notebook-ready. It covers the complete engineering surface area from raw API ingestion through BigQuery warehousing, feature engineering, LLM orchestration, and a live Streamlit dashboard — with no shortcuts.

**Specific skills demonstrated:**

| Area | What's covered |
|------|---------------|
| **Data Engineering** | 4-source parallel ingestion, feature engineering, dual storage backend (SQLite ↔ BigQuery), time-partitioned + clustered tables, Airflow DAG with XCom |
| **Cloud / GCP** | BigQuery dataset design, TIME partitioning, clustering on `ticker`, named query parameters via `QueryJobConfig`, service account IAM |
| **AI / LLM Engineering** | Structured prompt design, enum-validated JSON output, multi-model support (OpenAI + Anthropic), ReAct agent with 3 custom LangChain tools |
| **Software Engineering** | Adapter pattern for backend abstraction, retry with exponential backoff, parameterised SQL (no injection vectors), AST-safe expression evaluation, rotating structured logging |
| **Testing** | 53 isolated tests across 9 classes — zero API keys required; all external dependencies mocked via `monkeypatch` + deterministic seeded fallbacks |
| **Observability** | Dual-sink Loguru logging, Airflow task-level XCom introspection, BigQuery job ID tracing |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         SignalDeck AI — Data Flow                            │
│                                                                              │
│   External Sources              Ingestion Layer (4 parallel Airflow tasks)   │
│  ┌──────────────┐   ┌─────────────────────────────────────────────────────┐  │
│  │ Alpha Vantage│──▶│ ingest_stocks.py      OHLCV + GBM mock fallback     │  │
│  │ NewsAPI      │──▶│ ingest_news.py        Articles + templated fallback  │  │
│  │ Reddit       │──▶│ ingest_social.py      Sentiment + Gaussian fallback  │  │
│  │ StockTwits   │──▶│ ingest_fundamentals.py  P/E, EPS + snapshot fallback │  │
│  └──────────────┘   └────────────────────────────┬────────────────────────┘  │
│                                                  │                           │
│                                       ┌──────────▼──────────┐               │
│                                       │    transform.py      │               │
│                                       │  MA5/20/50 · RSI-14  │               │
│                                       │  Volatility · Sentiment aggregation  │
│                                       └──────────┬──────────┘               │
│                                                  │                           │
│                             ┌────────────────────┴────────────────────┐     │
│                             │                                          │     │
│                   ┌─────────▼───────────┐              ┌──────────────▼───┐ │
│                   │   LLM Analysis       │              │   ReAct Agent    │ │
│                   │   GPT-4o-mini /      │              │   LangChain      │ │
│                   │   Claude Haiku       │              │   3 custom tools │ │
│                   │   Rule-based fallback│              │   Rule-based     │ │
│                   └─────────┬───────────┘              └──────────────┬───┘ │
│                             └────────────────┬───────────────────────┘      │
│                                              │                               │
│                                 ┌────────────▼─────────────┐                │
│                                 │   SQLite / BigQuery       │                │
│                                 │   7 tables · partitioned  │                │
│                                 │   clustered on ticker     │                │
│                                 └────────────┬─────────────┘                │
│                                              │                               │
│                                 ┌────────────▼─────────────┐                │
│                                 │   Streamlit Dashboard     │                │
│                                 │   5 tabs · TTL cache      │                │
│                                 └──────────────────────────┘                │
│                                                                              │
│   Orchestration: Airflow DAG "signaldeck_daily_pipeline" · 0 6 * * 1-5      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Engineering Decisions Worth Highlighting

### 1. Dual-backend storage with an adapter pattern

All pipeline modules call a single public API (`insert_*`, `query()`, `get_latest_*`) defined in `pipeline/database.py`. At runtime, every call dispatches to either `_sqlite_*` or `_bq_*` functions based on `STORAGE_BACKEND`. Callers have zero awareness of the underlying store.

This means `pytest` runs entirely on SQLite in ~2 seconds, while production deploys to BigQuery with no code changes.

### 2. BigQuery schema designed for query performance

Every table that will be time-filtered uses `TIME_PARTITIONING` on its most-queried date column (`date` → `DATE`, `published_at` → `TIMESTAMP`). All 7 tables are clustered on `ticker`.

A query like "give me the latest features for AAPL" scans a single partition and a single cluster — not the full table — regardless of how much historical data accumulates.

### 3. Zero SQL injection surface

SQLite queries use `?` positional placeholders; BigQuery queries use `@named_parameter` with `google.cloud.bigquery.QueryJobConfig`. No user-supplied values are ever interpolated into query strings. The linter-level guarantee is enforced by a Contributing guideline in this repo.

### 4. Safe expression evaluation in the agent's calculator tool

The LangChain `CalculatorTool` uses a custom `_safe_eval()` built on Python's `ast.parse()` rather than `eval()`. It walks the AST and raises `ValueError` on anything that isn't `ast.Constant`, `ast.BinOp`, or `ast.UnaryOp` with a whitelisted operator. The LLM cannot execute arbitrary code through this tool.

### 5. Deterministic, seeded mock data across all external sources

Every external dependency (Alpha Vantage, NewsAPI, Reddit, OpenAI, Anthropic) has a fallback that produces deterministic output seeded by ticker symbol and/or date. This means:
- The CI test suite never needs API keys
- Local demos produce the same output every time
- Integration tests can assert on exact values

GBM (Geometric Brownian Motion) is used for price simulation because it mirrors the statistical properties of real equity prices (log-normal returns, drift, volatility) — not because it's the simplest option.

### 6. LLM output is validated, not trusted

`parse_llm_response()` and `parse_agent_response()` strip markdown fences, use `json.JSONDecoder.raw_decode()` (not greedy `re.DOTALL` regex) to find the first valid JSON object, then validate every field against an explicit enum set. Any out-of-range value is replaced with a safe default before being written to the database.

---

## Project Structure

```
signaldeck/
│
├── config.py                   # Typed env-var constants; auto-creates data/ & logs/
├── logger.py                   # Loguru: colourised stderr + 10 MB rotating file
├── run_pipeline.py             # CLI entry point: argparse + Rich table output
├── setup.py / pyproject.toml   # Pip-installable package definition + pytest config
├── requirements.txt            # Pinned dependencies
├── .env.example                # Template for all 17 environment variables
│
├── pipeline/
│   ├── database.py             # ★ Unified SQLite + BigQuery adapter; 7 table schemas;
│   │                           #   parameterised queries; TIME_PARTITIONING + clustering
│   ├── ingest_stocks.py        # Alpha Vantage OHLCV; GBM mock (seeded by ticker hash)
│   ├── ingest_news.py          # NewsAPI articles; templated mock with preset sentiments
│   ├── ingest_social.py        # Reddit OAuth2 + StockTwits; Gaussian mock (±12% std dev)
│   ├── ingest_fundamentals.py  # Alpha Vantage OVERVIEW; snapshot mock per ticker
│   └── transform.py            # MA5/20/50, RSI-14, realised volatility, sentiment agg
│
├── llm/
│   └── analysis_pipeline.py   # 4-section structured prompt; multi-model support;
│                               #   enum-validated JSON output; rule-based fallback
│
├── agent/
│   └── market_agent.py        # LangChain AgentExecutor; 3 BaseTool subclasses;
│                               #   AST-safe calculator; ReAct max_iterations=6
│
├── dags/
│   └── signaldeck_dag.py      # Airflow DAG: 4 parallel ingest → transform → llm → agent
│                               #   XCom result pushes; graceful LLM key skip
│
├── app/
│   └── dashboard.py           # Streamlit: 5 tabs; @st.cache_data(ttl=300); portfolio KPI row
│
└── tests/
    └── test_signaldeck.py     # 9 test classes; 53 tests; monkeypatch + temp SQLite isolation
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- (Optional) A Google Cloud project with BigQuery enabled
- (Optional) API keys for Alpha Vantage, NewsAPI, OpenAI, or Anthropic

```bash
git clone https://github.com/hanshalili/SignalDeck.git
cd SignalDeck

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# All keys are optional — the pipeline runs entirely on mock data without them.
```

### Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `STORAGE_BACKEND` | `sqlite` | Set to `bigquery` for GCP |
| `SQLITE_DB_PATH` | `./data/signaldeck.db` | Local dev path |
| `GCP_PROJECT_ID` | — | Required if BigQuery |
| `GCP_DATASET_ID` | `signaldeck` | Required if BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Service account JSON path |
| `ALPHA_VANTAGE_API_KEY` | — | Falls back to GBM simulation |
| `NEWS_API_KEY` | — | Falls back to templated mock |
| `OPENAI_API_KEY` | — | Falls back to rule-based scoring |
| `ANTHROPIC_API_KEY` | — | Falls back to rule-based scoring |
| `REDDIT_CLIENT_ID` / `_SECRET` | — | Falls back to Gaussian mock |
| `STOCKTWITS_ACCESS_TOKEN` | — | Falls back to Gaussian mock |
| `TICKERS` | `AAPL,MSFT,GOOGL,AMZN,META` | Comma-separated |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `AIRFLOW_HOME` | `./airflow` | Required if using Airflow |

### Google Cloud Setup (BigQuery backend)

```bash
# Create a service account + BigQuery Admin role in GCP console, then:
export GOOGLE_APPLICATION_CREDENTIALS=./service-account.json

# Update .env:
STORAGE_BACKEND=bigquery
GCP_PROJECT_ID=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
```

### Airflow (optional)

```bash
pip install apache-airflow==2.9.0
bash setup_airflow.sh
# → http://localhost:8080  (admin / admin)
```

---

## Usage

### Run the full pipeline (zero API keys)

```bash
python run_pipeline.py --steps all
```

### Run specific steps or specific tickers

```bash
python run_pipeline.py --steps ingest transform
python run_pipeline.py --steps llm agent --ticker AAPL NVDA TSLA
```

### Dashboard

```bash
streamlit run app/dashboard.py
# → http://localhost:8501
```

### Tests

```bash
pytest tests/ -v
# 53 tests · ~2 seconds · no API keys required
```

### Example output

```
╭──────────────────────────── Pipeline Start ────────────────────────────────╮
│ SignalDeck AI — Intelligent Market Analysis System                          │
│ Tickers : AAPL, MSFT, GOOGL, AMZN, META                                    │
│ Steps   : init, ingest, transform, llm, agent                              │
│ Backend : bigquery                                                          │
╰─────────────────────────────────────────────────────────────────────────────╯

▶ init        ✓  6.7s
▶ ingest      ✓ 16.9s   (90 price rows × 5 tickers, 4 parallel tasks)
▶ transform   ✓  4.2s   (MA, RSI-14, volatility, sentiment aggregated)
▶ llm         ✓ 19.9s   (rule-based — no LLM key)
▶ agent       ✓ 18.2s   (rule-based — no LLM key)

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

## Pipeline Deep Dive

### Step 1 — Database Initialisation (`pipeline/database.py`)

Creates 7 tables in either SQLite or BigQuery. BigQuery tables are created with `TIME_PARTITIONING` on the primary date column (mapped by name: `date` → `DATE`, `*_at` → `TIMESTAMP`) and clustering on `ticker`. A helper `_bq_col_type()` ensures partition columns are typed correctly — a common source of `400` errors in BQ schema design.

### Step 2 — Ingestion (4 parallel Airflow tasks)

| Module | Primary | Fallback | Seeded by |
|--------|---------|---------|-----------|
| `ingest_stocks.py` | Alpha Vantage `TIME_SERIES_DAILY` | Geometric Brownian Motion | `md5(ticker)` |
| `ingest_news.py` | NewsAPI `/everything` | Templated articles + preset sentiments | ticker + date |
| `ingest_social.py` | Reddit OAuth2 + StockTwits | Gaussian noise ±12% std dev | ticker + date |
| `ingest_fundamentals.py` | Alpha Vantage `OVERVIEW` | Pre-built snapshots (AAPL–META) | — |

All ingesters use `@retry` via tenacity with exponential backoff. Fallbacks activate automatically on `ImportError`, network failure, or missing credentials.

### Step 3 — Feature Engineering (`pipeline/transform.py`)

| Feature | Method |
|---------|--------|
| `ma_5`, `ma_20`, `ma_50` | Simple moving average over `close` |
| `rsi_14` | Wilder's RSI: exponential smoothed up/down deltas |
| `volatility_20` | Annualised std dev of 20-day log returns |
| `avg_sentiment_score` | Mean of recent `news_articles.sentiment_score` |
| `social_bullish_pct` | Average bullish % across social sources |
| `price_change_pct` | 1-day price momentum |
| `volume_change_pct` | 1-day volume momentum |

### Step 4 — LLM Analysis (`llm/analysis_pipeline.py`)

A structured prompt with 4 `━━━`-separated sections (price action, news, social, fundamentals) is sent to GPT-4o-mini or Claude Haiku. The response is validated against explicit enum sets for all 7 output fields before being written to `llm_analysis`. Falls back to a deterministic rule-based scoring algorithm when no LLM key is present.

### Step 5 — ReAct Agent (`agent/market_agent.py`)

A LangChain `AgentExecutor` with `max_iterations=6` reasons step-by-step using three custom `BaseTool` subclasses:

| Tool | Input | What it does |
|------|-------|-------------|
| `query_stock_data` | `days: int` | Fetches price history + computed features |
| `query_news` | `limit: int` | Fetches recent articles with sentiment scores |
| `calculator` | `expression: str` | Evaluates arithmetic via AST — no `eval()` |

Output: structured JSON with `action`, `rationale`, `confidence_score`, `entry_price`, `stop_loss`, `take_profit`, `time_horizon`.

### Step 6 — Dashboard (`app/dashboard.py`)

Streamlit with `@st.cache_data(ttl=300)` and a 5-tab per-ticker layout:

| Tab | Content |
|-----|---------|
| Price Chart | Candlestick OHLC + MA5/20/50 overlays + volume bar |
| AI Analysis | LLM recommendation card + agent recommendation + rationale trace |
| Social Signals | Bullish/bearish breakdown by source |
| Fundamentals | P/E, EPS, margins, beta, 52-week range |
| Raw Data | Interactive table for any of the 7 database tables |

---

## Database Schema

```
signaldeck (BigQuery dataset) / signaldeck.db (SQLite)
│
├── stock_prices          PK (ticker, date)      OHLCV daily · DATE partitioned
├── news_articles         PK (article_id)        Title, sentiment, score · TIMESTAMP partitioned
├── social_signals        PK (signal_id)         Source, bullish%, score · TIMESTAMP partitioned
├── fundamentals          PK (ticker)            P/E, EPS, margins, sector, beta
├── processed_features    PK (ticker, date)      MA, RSI, volatility, aggregated sentiment
├── llm_analysis          PK (analysis_id)       Recommendation, confidence, target, observations
└── agent_recommendations PK (rec_id)            Action, rationale, entry, stop, take-profit
```

All BigQuery tables are clustered on `ticker`. Partition pruning + cluster filtering means per-ticker queries scan a fraction of each table regardless of data volume.

---

## Testing

```bash
pytest tests/ -v
```

**53 tests across 9 classes, ~2 seconds, zero external dependencies.**

| Class | What's tested |
|-------|--------------|
| `TestConfig` | Env var loading, defaults, storage backend switching |
| `TestDatabase` | Table creation, insert, query, upsert for both backends |
| `TestIngestStocks` | GBM fallback determinism, row count, schema correctness |
| `TestIngestNews` | Mock article generation, sentiment field validation |
| `TestIngestSocial` | Gaussian mock reproducibility, source multiplicity |
| `TestIngestFundamentals` | Snapshot loading, `_safe_float()` edge cases |
| `TestTransform` | RSI correctness, MA calculation, aggregation pipeline |
| `TestLLMPipeline` | JSON parsing, enum validation, fallback trigger conditions |
| `TestAgent` | AST evaluator security, agent response parsing, fallback |

Test isolation is enforced via `autouse` fixture: `monkeypatch` + `tmp_path` redirect `SQLITE_DB_PATH` to a fresh temp file for every test, with `importlib.reload(config)` to pick up env changes. No shared state between tests.

---

## Observability

### Structured logging (Loguru)

```
# Colourised stderr — development
2026-03-17 21:31:25 | INFO     | pipeline.ingest_stocks:164 — Stored 90 price rows for AAPL

# logs/signaldeck_YYYY-MM-DD.log — 10 MB rotation · 14-day retention · gzip
```

`LOG_LEVEL=DEBUG` in `.env` exposes BigQuery Job IDs, SQL strings (with parameters redacted), and per-ticker timing.

### Airflow XCom

Each task pushes a result dict to XCom for downstream inspection and alerting:

```python
# ingest_stocks task
ti.xcom_push(key="rows_inserted", value={"AAPL": 90, "MSFT": 90, ...})
```

Tasks are configured with `retries=2`, `retry_delay=timedelta(minutes=5)`. LLM tasks log a warning and continue — they do not fail the DAG — when no API key is present.

---

## Data Sources & Fallback Strategy

| Source | Primary | Fallback | Deterministic? |
|--------|---------|---------|----------------|
| Stock prices | Alpha Vantage | Geometric Brownian Motion | Yes — seeded by `md5(ticker)` |
| News | NewsAPI | Templated articles | Yes — seeded by ticker + date |
| Social | Reddit + StockTwits | Gaussian noise signals | Yes — seeded by ticker + date |
| Fundamentals | Alpha Vantage OVERVIEW | Per-ticker snapshot | Yes |
| LLM analysis | OpenAI / Anthropic | Rule-based signal scoring | Yes |
| Agent | LangChain ReAct | Rule-based recommendation | Yes |

The determinism guarantee means `pytest` can assert on exact output values and CI never produces flaky tests due to mock randomness.

---

## Future Work

- [ ] **Real-time feeds** — Alpaca / Polygon.io WebSocket for intraday prices alongside daily batch
- [ ] **FinBERT sentiment** — Replace heuristic sentiment scores with a fine-tuned NLP model on financial text
- [ ] **Vector memory** — Persist agent reasoning traces in Chroma/Pinecone for cross-ticker retrieval-augmented analysis
- [ ] **Backtesting** — Replay historical recommendations against actual returns to measure strategy performance
- [ ] **Terraform IaC** — Codify BigQuery dataset, IAM service account, and GCS bucket creation
- [ ] **Multi-model ensemble** — Run GPT-4o and Claude in parallel; resolve disagreements via confidence-weighted voting
- [ ] **Alerting** — Slack/email push when agent confidence crosses a threshold
- [ ] **CI/CD** — GitHub Actions: lint → test → deploy DAG → smoke test
- [ ] **GCP Secret Manager** — Replace local `.env` for production deployments
- [ ] **Expanded universe** — ETFs, crypto, and non-US equities beyond the default 5 tickers

---

## Contributing

1. Fork and create a feature branch: `git checkout -b feature/your-feature`
2. Ensure all tests pass: `pytest tests/ -v`
3. Follow existing commit style: `<type>: <short summary>`
4. Open a PR against `main`

**Non-negotiable code standards:**
- All database queries must use parameterised placeholders — no f-string SQL
- No `eval()` — use `ast`-based evaluation for any expression handling
- New ingestion sources must include a deterministic seeded mock fallback
- New features require at least one corresponding test

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built by Hans Matthew Halili — [GitHub](https://github.com/hanshalili)*
