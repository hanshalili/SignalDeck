"""
tests/test_signaldeck.py — Test suite for SignalDeck AI.

9 test classes, ~55 tests.
Requires zero API keys — uses SQLite and mock/rule-based pipelines only.
Run with: pytest tests/ -v
"""
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Force SQLite backend for all tests ───────────────────────────────────────
os.environ.setdefault("STORAGE_BACKEND", "sqlite")
os.environ.setdefault("LOG_LEVEL", "WARNING")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point all tests at a fresh temporary SQLite database."""
    db = tmp_path / "test_signaldeck.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db))
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")

    # Reload config so it picks up the env var
    import importlib
    import config
    importlib.reload(config)
    monkeypatch.setattr(config, "SQLITE_DB_PATH", str(db))
    monkeypatch.setattr(config, "STORAGE_BACKEND", "sqlite")

    yield db


@pytest.fixture
def initialized_db(temp_db):
    """Return a DB path after init_db() has been called."""
    from pipeline.database import init_db
    init_db()
    return temp_db


# ─────────────────────────────────────────────────────────────────────────────
# 1. Config tests
# ─────────────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_default_storage_backend(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        import importlib, config
        importlib.reload(config)
        assert config.get_storage_backend() == "sqlite"

    def test_tickers_parsed(self, monkeypatch):
        monkeypatch.setenv("TICKERS", "AAPL,TSLA,NVDA")
        import importlib, config
        importlib.reload(config)
        assert "AAPL" in config.TICKERS
        assert "TSLA" in config.TICKERS
        assert "NVDA" in config.TICKERS

    def test_default_tickers(self):
        import config
        assert len(config.TICKERS) > 0

    def test_data_dir_created(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        import config
        assert config.DATA_DIR.exists()

    def test_logs_dir_created(self):
        import config
        assert config.LOGS_DIR.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Database — schema + init
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase:
    def test_init_db_creates_tables(self, initialized_db):
        conn = sqlite3.connect(str(initialized_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        expected = {"stock_prices", "news_articles", "social_signals", "fundamentals",
                    "processed_features", "llm_analysis", "agent_recommendations"}
        assert expected.issubset(tables)

    def test_init_db_idempotent(self, initialized_db):
        from pipeline.database import init_db
        init_db()  # second call should not raise
        init_db()

    def test_table_helper(self):
        from pipeline.database import _table
        assert _table("stock_prices") == "stock_prices"

    def test_table_helper_invalid(self):
        from pipeline.database import _table
        with pytest.raises(ValueError):
            _table("nonexistent_table")

    def test_insert_and_query_stock_prices(self, initialized_db):
        from pipeline.database import insert_stock_prices, query
        rows = [{"ticker": "AAPL", "date": "2024-01-15", "open": 180.0, "high": 185.0,
                 "low": 179.0, "close": 182.5, "volume": 55_000_000, "source": "test"}]
        n = insert_stock_prices(rows)
        assert n == 1
        result = query("stock_prices", ticker="AAPL", limit=10)
        assert len(result) == 1
        assert result[0]["close"] == 182.5

    def test_upsert_updates_existing(self, initialized_db):
        from pipeline.database import insert_stock_prices, query
        row = {"ticker": "MSFT", "date": "2024-01-15", "open": 400.0, "high": 410.0,
               "low": 398.0, "close": 405.0, "volume": 20_000_000, "source": "test"}
        insert_stock_prices([row])
        row["close"] = 410.0
        insert_stock_prices([row])
        result = query("stock_prices", ticker="MSFT", limit=10)
        assert len(result) == 1
        assert result[0]["close"] == 410.0

    def test_insert_news_articles(self, initialized_db):
        from pipeline.database import insert_news_articles, query
        rows = [{"article_id": "art-001", "ticker": "AAPL", "title": "Test article",
                 "description": "Desc", "source_name": "Reuters", "published_at": "2024-01-15T10:00:00",
                 "sentiment": "positive", "sentiment_score": 0.75, "url": "https://example.com"}]
        n = insert_news_articles(rows)
        assert n == 1
        result = query("news_articles", ticker="AAPL")
        assert result[0]["title"] == "Test article"

    def test_insert_fundamentals(self, initialized_db):
        from pipeline.database import insert_fundamentals, query
        rows = [{"ticker": "GOOGL", "pe_ratio": 24.0, "eps": 7.0, "sector": "Technology"}]
        insert_fundamentals(rows)
        result = query("fundamentals", ticker="GOOGL")
        assert len(result) == 1

    def test_get_latest_stock_price(self, initialized_db):
        from pipeline.database import insert_stock_prices, get_latest_stock_price
        rows = [
            {"ticker": "AAPL", "date": "2024-01-14", "close": 180.0, "open": 179.0, "high": 181.0, "low": 178.0, "volume": 10_000_000, "source": "test"},
            {"ticker": "AAPL", "date": "2024-01-15", "close": 185.0, "open": 180.0, "high": 186.0, "low": 179.0, "volume": 12_000_000, "source": "test"},
        ]
        insert_stock_prices(rows)
        latest = get_latest_stock_price("AAPL")
        assert latest is not None
        assert latest["close"] == 185.0

    def test_query_empty_table(self, initialized_db):
        from pipeline.database import query
        result = query("stock_prices", ticker="ZZZZ")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Stock ingestion
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestStocks:
    def test_gbm_mock_generates_data(self):
        from pipeline.ingest_stocks import _gbm_mock
        rows = _gbm_mock("AAPL", n_days=30)
        assert len(rows) == 30
        assert all(r["ticker"] == "AAPL" for r in rows)
        assert all(r["source"] == "mock_gbm" for r in rows)

    def test_gbm_mock_prices_positive(self):
        from pipeline.ingest_stocks import _gbm_mock
        rows = _gbm_mock("MSFT", n_days=20)
        assert all(r["close"] > 0 for r in rows)
        assert all(r["high"] >= r["low"] for r in rows)

    def test_gbm_mock_deterministic(self):
        from pipeline.ingest_stocks import _gbm_mock
        rows1 = _gbm_mock("AAPL", n_days=10)
        rows2 = _gbm_mock("AAPL", n_days=10)
        assert [r["close"] for r in rows1] == [r["close"] for r in rows2]

    def test_trading_days_excludes_weekends(self):
        from pipeline.ingest_stocks import _trading_days
        start = date(2024, 1, 1)  # Monday
        end = date(2024, 1, 7)    # Sunday
        days = _trading_days(start, end)
        assert all(d.weekday() < 5 for d in days)
        assert len(days) == 5

    def test_ingest_ticker_uses_mock_without_key(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.ALPHA_VANTAGE_API_KEY", "")
        from pipeline.ingest_stocks import ingest_ticker_prices
        n = ingest_ticker_prices("AAPL")
        assert n > 0

    def test_ingest_all_stocks(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.ALPHA_VANTAGE_API_KEY", "")
        from pipeline.ingest_stocks import ingest_all_stocks
        results = ingest_all_stocks(["AAPL", "MSFT"])
        assert "AAPL" in results
        assert results["AAPL"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. News ingestion
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestNews:
    def test_mock_articles_generated(self):
        from pipeline.ingest_news import _mock_articles
        rows = _mock_articles("AAPL", n=5)
        assert len(rows) == 5
        assert all(r["ticker"] == "AAPL" for r in rows)
        assert all(r["sentiment"] in ("positive", "negative", "neutral") for r in rows)

    def test_mock_articles_deterministic(self):
        from pipeline.ingest_news import _mock_articles
        rows1 = _mock_articles("MSFT")
        rows2 = _mock_articles("MSFT")
        assert [r["title"] for r in rows1] == [r["title"] for r in rows2]

    def test_mock_articles_have_unique_ids(self):
        from pipeline.ingest_news import _mock_articles
        rows = _mock_articles("GOOGL")
        ids = [r["article_id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_ingest_ticker_news_without_key(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.NEWS_API_KEY", "")
        from pipeline.ingest_news import ingest_ticker_news
        n = ingest_ticker_news("AAPL")
        assert n > 0

    def test_ingest_all_news(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.NEWS_API_KEY", "")
        from pipeline.ingest_news import ingest_all_news
        results = ingest_all_news(["AAPL", "MSFT"])
        assert all(v >= 0 for v in results.values())


# ─────────────────────────────────────────────────────────────────────────────
# 5. Social ingestion
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestSocial:
    def test_mock_social_generated(self):
        from pipeline.ingest_social import _mock_social
        rows = _mock_social("AAPL")
        assert len(rows) == 3
        assert all(r["ticker"] == "AAPL" for r in rows)

    def test_mock_social_sentiment_range(self):
        from pipeline.ingest_social import _mock_social
        rows = _mock_social("MSFT")
        for r in rows:
            assert -1.0 <= r["sentiment_score"] <= 1.0
            assert 0 <= r["bullish_pct"] <= 100

    def test_noisy_sentiment_bounded(self):
        from pipeline.ingest_social import _noisy_sentiment
        for ticker in ["AAPL", "MSFT", "GOOGL", "UNKNOWN"]:
            score = _noisy_sentiment(0.6, ticker)
            assert 0.0 <= score <= 1.0

    def test_ingest_all_social_without_keys(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.REDDIT_CLIENT_ID", "")
        monkeypatch.setattr("config.STOCKTWITS_ACCESS_TOKEN", "")
        from pipeline.ingest_social import ingest_all_social
        results = ingest_all_social(["AAPL"])
        assert results["AAPL"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Fundamentals ingestion
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestFundamentals:
    def test_safe_float_valid(self):
        from pipeline.ingest_fundamentals import _safe_float
        assert _safe_float("28.4") == pytest.approx(28.4)
        assert _safe_float("1,234.5") == pytest.approx(1234.5)

    def test_safe_float_invalid(self):
        from pipeline.ingest_fundamentals import _safe_float
        assert _safe_float("N/A") == 0.0
        assert _safe_float("None") == 0.0
        assert _safe_float(None) == 0.0
        assert _safe_float("") == 0.0

    def test_safe_float_custom_default(self):
        from pipeline.ingest_fundamentals import _safe_float
        assert _safe_float("bad", default=1.0) == 1.0

    def test_mock_fundamentals_used_without_key(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.ALPHA_VANTAGE_API_KEY", "")
        from pipeline.ingest_fundamentals import ingest_ticker_fundamentals
        n = ingest_ticker_fundamentals("AAPL")
        assert n == 1

    def test_mock_fundamentals_for_unknown_ticker(self, initialized_db, monkeypatch):
        monkeypatch.setattr("config.ALPHA_VANTAGE_API_KEY", "")
        from pipeline.ingest_fundamentals import ingest_ticker_fundamentals
        n = ingest_ticker_fundamentals("ZZTEST")
        assert n == 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. Transform
# ─────────────────────────────────────────────────────────────────────────────

class TestTransform:
    def _seed_prices(self, ticker: str, n: int = 60):
        from pipeline.ingest_stocks import _gbm_mock
        from pipeline.database import insert_stock_prices
        rows = _gbm_mock(ticker, n_days=n)
        insert_stock_prices(rows)

    def test_moving_average(self):
        from pipeline.transform import _moving_average
        prices = list(range(1, 11))
        assert _moving_average(prices, 5) == pytest.approx(8.0)
        assert _moving_average(prices, 3) == pytest.approx(9.0)

    def test_moving_average_insufficient_data(self):
        from pipeline.transform import _moving_average
        assert _moving_average([1.0, 2.0], 5) is None

    def test_compute_volatility(self):
        from pipeline.transform import _compute_volatility
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 102.0, 104.0,
                  101.0, 103.0, 105.0, 102.0, 100.0, 103.0, 101.0,
                  104.0, 102.0, 105.0, 103.0, 101.0, 104.0, 102.0]
        vol = _compute_volatility(prices)
        assert vol is not None
        assert vol > 0

    def test_transform_ticker_full_pipeline(self, initialized_db):
        self._seed_prices("AAPL", n=60)
        from pipeline.transform import transform_ticker
        n = transform_ticker("AAPL")
        assert n >= 1

    def test_transform_all(self, initialized_db):
        for t in ["AAPL", "MSFT"]:
            self._seed_prices(t, n=60)
        from pipeline.transform import transform_all
        results = transform_all(["AAPL", "MSFT"])
        assert all(v >= 0 for v in results.values())

    def test_compute_news_features_empty(self, initialized_db):
        from pipeline.transform import compute_news_features
        feats = compute_news_features("ZZUNKNOWN")
        assert feats["news_count"] == 0
        assert feats["avg_sentiment_score"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 8. LLM Analysis
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMAnalysis:
    def _seed_all(self, ticker: str):
        from pipeline.ingest_stocks import _gbm_mock
        from pipeline.ingest_news import _mock_articles
        from pipeline.ingest_social import _mock_social
        from pipeline.ingest_fundamentals import _MOCK_FUNDAMENTALS, _DEFAULT_FUNDAMENTALS
        from pipeline.database import (
            init_db, insert_stock_prices, insert_news_articles,
            insert_social_signals, insert_fundamentals
        )
        init_db()
        insert_stock_prices(_gbm_mock(ticker, 60))
        insert_news_articles(_mock_articles(ticker))
        insert_social_signals(_mock_social(ticker))
        tmpl = _MOCK_FUNDAMENTALS.get(ticker.upper(), _DEFAULT_FUNDAMENTALS)
        insert_fundamentals([{"ticker": ticker, **tmpl}])

    def test_parse_llm_response_valid(self):
        from llm.analysis_pipeline import parse_llm_response
        raw = json.dumps({
            "sentiment": "bullish",
            "trend": "uptrend",
            "risk_level": "medium",
            "recommendation": "BUY",
            "confidence": "high",
            "price_target": 210.0,
            "key_observations": ["RSI is low", "News is positive"],
        })
        result = parse_llm_response(raw)
        assert result["recommendation"] == "BUY"
        assert result["sentiment"] == "bullish"
        assert len(result["key_observations"]) == 2

    def test_parse_llm_response_with_fences(self):
        from llm.analysis_pipeline import parse_llm_response
        raw = '```json\n{"sentiment":"neutral","trend":"sideways","risk_level":"low","recommendation":"HOLD","confidence":"medium","price_target":null,"key_observations":[]}\n```'
        result = parse_llm_response(raw)
        assert result["recommendation"] == "HOLD"

    def test_parse_llm_response_caps_observations(self):
        from llm.analysis_pipeline import parse_llm_response
        raw = json.dumps({
            "sentiment": "bearish", "trend": "downtrend", "risk_level": "high",
            "recommendation": "SELL", "confidence": "low", "price_target": None,
            "key_observations": ["a", "b", "c", "d", "e", "f", "g"],
        })
        result = parse_llm_response(raw)
        assert len(result["key_observations"]) <= 5

    def test_rule_based_analysis_buy(self):
        from llm.analysis_pipeline import _rule_based_analysis
        features = {"rsi_14": 28.0, "ma_5": 180.0, "ma_20": 175.0, "avg_sentiment_score": 0.5, "social_bullish_pct": 70.0, "price_change_pct": 3.0}
        price = {"close": 180.0}
        result = _rule_based_analysis("AAPL", features, price)
        assert result["recommendation"] == "BUY"

    def test_rule_based_analysis_sell(self):
        from llm.analysis_pipeline import _rule_based_analysis
        features = {"rsi_14": 78.0, "ma_5": 160.0, "ma_20": 175.0, "avg_sentiment_score": -0.5, "social_bullish_pct": 30.0, "price_change_pct": -4.0}
        price = {"close": 160.0}
        result = _rule_based_analysis("AAPL", features, price)
        assert result["recommendation"] == "SELL"

    def test_analyze_ticker_without_llm_key(self, initialized_db, monkeypatch):
        self._seed_all("AAPL")
        monkeypatch.setattr("config.OPENAI_API_KEY", "")
        monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
        from llm.analysis_pipeline import analyze_ticker
        result = analyze_ticker("AAPL")
        assert result["recommendation"] in ("BUY", "HOLD", "SELL")
        assert "ticker" in result

    def test_run_analysis_pipeline(self, initialized_db, monkeypatch):
        for t in ["AAPL", "MSFT"]:
            self._seed_all(t)
        monkeypatch.setattr("config.OPENAI_API_KEY", "")
        monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
        from llm.analysis_pipeline import run_analysis_pipeline
        results = run_analysis_pipeline(["AAPL", "MSFT"])
        assert "AAPL" in results
        assert "MSFT" in results


# ─────────────────────────────────────────────────────────────────────────────
# 9. Agent
# ─────────────────────────────────────────────────────────────────────────────

class TestAgent:
    def _seed_all(self, ticker: str, initialized_db):
        from pipeline.ingest_stocks import _gbm_mock
        from pipeline.ingest_news import _mock_articles
        from pipeline.database import (
            insert_stock_prices, insert_news_articles,
            insert_processed_features,
        )
        insert_stock_prices(_gbm_mock(ticker, 60))
        insert_news_articles(_mock_articles(ticker))
        insert_processed_features([{
            "ticker": ticker, "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "ma_5": 180.0, "ma_20": 175.0, "ma_50": 170.0, "rsi_14": 45.0,
            "volatility_20": 0.015, "avg_sentiment_score": 0.3, "news_count": 5,
            "social_bullish_pct": 58.0, "price_change_pct": 1.2, "volume_change_pct": 5.0,
        }])

    def test_parse_agent_response_valid(self):
        from agent.market_agent import parse_agent_response
        raw = json.dumps({
            "action": "BUY",
            "rationale": "Strong bullish signals.",
            "confidence_score": 0.8,
            "entry_price": 185.0,
            "stop_loss": 175.0,
            "take_profit": 205.0,
            "time_horizon": "medium",
        })
        result = parse_agent_response(raw)
        assert result["action"] == "BUY"
        assert result["confidence_score"] == pytest.approx(0.8)

    def test_parse_agent_response_clamps_confidence(self):
        from agent.market_agent import parse_agent_response
        raw = json.dumps({
            "action": "SELL", "rationale": "test", "confidence_score": 1.5,
            "entry_price": None, "stop_loss": None, "take_profit": None, "time_horizon": "short",
        })
        result = parse_agent_response(raw)
        assert result["confidence_score"] <= 1.0

    def test_rule_based_rec_hold(self, initialized_db, monkeypatch):
        self._seed_all("AAPL", initialized_db)
        monkeypatch.setattr("config.OPENAI_API_KEY", "")
        monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
        from agent.market_agent import _rule_based_recommendation
        result = _rule_based_recommendation("AAPL")
        assert result["action"] in ("BUY", "HOLD", "SELL")
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_run_agent_for_ticker_without_llm(self, initialized_db, monkeypatch):
        self._seed_all("AAPL", initialized_db)
        monkeypatch.setattr("config.OPENAI_API_KEY", "")
        monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
        from agent.market_agent import run_agent_for_ticker
        result = run_agent_for_ticker("AAPL")
        assert result["action"] in ("BUY", "HOLD", "SELL")
        assert "rec_id" in result

    def test_run_all_agents(self, initialized_db, monkeypatch):
        for t in ["AAPL", "MSFT"]:
            self._seed_all(t, initialized_db)
        monkeypatch.setattr("config.OPENAI_API_KEY", "")
        monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
        from agent.market_agent import run_all_agents
        results = run_all_agents(["AAPL", "MSFT"])
        assert "AAPL" in results
        assert "MSFT" in results
        assert results["AAPL"].get("action") in ("BUY", "HOLD", "SELL")
