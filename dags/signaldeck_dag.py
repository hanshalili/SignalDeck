"""
dags/signaldeck_dag.py — Airflow DAG for the SignalDeck AI daily pipeline.

Schedule : 06:00 UTC weekdays (Mon–Fri)
Steps    : init_db → [ingest_stocks, ingest_news, ingest_social, ingest_fundamentals]
           → transform → llm_analysis → agent_recommendations

Each ingest task runs in parallel after init_db.
LLM/agent steps warn-and-skip gracefully when no API key is configured.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the project root is on sys.path so local modules are importable
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator

# ── DAG default args ──────────────────────────────────────────────────────────

default_args = {
    "owner": "signaldeck",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}

# ── Task functions ─────────────────────────────────────────────────────────────

def task_init_db(**context):
    from pipeline.database import init_db
    init_db()
    context["ti"].xcom_push(key="db_ready", value=True)


def task_ingest_stocks(**context):
    from pipeline.ingest_stocks import ingest_all_stocks
    results = ingest_all_stocks()
    context["ti"].xcom_push(key="stock_rows", value=results)
    return results


def task_ingest_news(**context):
    from pipeline.ingest_news import ingest_all_news
    results = ingest_all_news()
    context["ti"].xcom_push(key="news_rows", value=results)
    return results


def task_ingest_social(**context):
    from pipeline.ingest_social import ingest_all_social
    results = ingest_all_social()
    context["ti"].xcom_push(key="social_rows", value=results)
    return results


def task_ingest_fundamentals(**context):
    from pipeline.ingest_fundamentals import ingest_all_fundamentals
    results = ingest_all_fundamentals()
    context["ti"].xcom_push(key="fundamental_rows", value=results)
    return results


def task_transform(**context):
    from pipeline.transform import transform_all
    results = transform_all()
    context["ti"].xcom_push(key="feature_rows", value=results)
    return results


def task_llm_analysis(**context):
    import config
    from logger import log

    if not config.OPENAI_API_KEY and not config.ANTHROPIC_API_KEY:
        log.warning(
            "No LLM API key configured (OPENAI_API_KEY / ANTHROPIC_API_KEY). "
            "Falling back to rule-based analysis."
        )

    from llm.analysis_pipeline import run_analysis_pipeline
    results = run_analysis_pipeline()
    context["ti"].xcom_push(key="llm_results", value={k: v.get("recommendation") for k, v in results.items()})
    return results


def task_agent_recommendations(**context):
    import config
    from logger import log

    if not config.OPENAI_API_KEY and not config.ANTHROPIC_API_KEY:
        log.warning(
            "No LLM API key configured. Agent will use rule-based recommendations."
        )

    from agent.market_agent import run_all_agents
    results = run_all_agents()
    context["ti"].xcom_push(key="agent_results", value={k: v.get("action") for k, v in results.items()})
    return results


# ── DAG definition ────────────────────────────────────────────────────────────

with DAG(
    dag_id="signaldeck_daily_pipeline",
    description="SignalDeck AI: daily market data ingestion, transformation, LLM analysis, and agent recommendations",
    schedule="0 6 * * 1-5",  # 06:00 UTC, Monday–Friday
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["signaldeck", "market-analysis", "production"],
    doc_md=__doc__,
) as dag:

    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    init_db = PythonOperator(
        task_id="init_db",
        python_callable=task_init_db,
    )

    ingest_stocks = PythonOperator(
        task_id="ingest_stocks",
        python_callable=task_ingest_stocks,
    )

    ingest_news = PythonOperator(
        task_id="ingest_news",
        python_callable=task_ingest_news,
    )

    ingest_social = PythonOperator(
        task_id="ingest_social",
        python_callable=task_ingest_social,
    )

    ingest_fundamentals = PythonOperator(
        task_id="ingest_fundamentals",
        python_callable=task_ingest_fundamentals,
    )

    transform = PythonOperator(
        task_id="transform",
        python_callable=task_transform,
    )

    llm_analysis = PythonOperator(
        task_id="llm_analysis",
        python_callable=task_llm_analysis,
    )

    agent_recommendations = PythonOperator(
        task_id="agent_recommendations",
        python_callable=task_agent_recommendations,
    )

    # ── Dependency chain ──────────────────────────────────────────────────────
    # start >> init_db >> [4 parallel ingest] >> transform >> llm >> agent >> end
    start >> init_db
    init_db >> [ingest_stocks, ingest_news, ingest_social, ingest_fundamentals]
    [ingest_stocks, ingest_news, ingest_social, ingest_fundamentals] >> transform
    transform >> llm_analysis >> agent_recommendations >> end
