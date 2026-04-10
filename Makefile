.PHONY: help install dev test lint pipeline dashboard airflow dbt clean

# ─────────────────────────────────────────────────────────────────────────────
# SignalDeck AI — Developer Makefile
# ─────────────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  SignalDeck AI — available targets"
	@echo "  ─────────────────────────────────────────────────────────────"
	@echo "  make install     Install Python dependencies into active venv"
	@echo "  make dev         Run full pipeline (all steps) locally"
	@echo "  make test        Run pytest test suite"
	@echo "  make lint        Run Ruff linter"
	@echo "  make pipeline    Run pipeline with all steps (alias for dev)"
	@echo "  make dashboard   Launch Streamlit dashboard"
	@echo "  make airflow     Set up and start Airflow locally"
	@echo "  make dbt-run     Run dbt models against BigQuery"
	@echo "  make dbt-test    Run dbt schema tests"
	@echo "  make docker-up   Start full stack via docker compose"
	@echo "  make docker-down Stop docker compose stack"
	@echo "  make clean       Remove __pycache__, .pytest_cache, target/"
	@echo ""

install:
	pip install --upgrade pip
	pip install -r requirements.txt

dev: pipeline

pipeline:
	python run_pipeline.py --steps all

pipeline-stocks:
	python run_pipeline.py --steps init ingest

pipeline-ai:
	python run_pipeline.py --steps llm agent

test:
	pytest tests/ -v --tb=short

lint:
	ruff check . --fix

dashboard:
	streamlit run app/dashboard.py

airflow:
	bash setup_airflow.sh

dbt-run:
	cd dbt && dbt run --profiles-dir . --target dev

dbt-test:
	cd dbt && dbt test --profiles-dir . --target dev

dbt-docs:
	cd dbt && dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "target" -path "*/dbt/target" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "Clean complete."
