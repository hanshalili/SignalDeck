#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_airflow.sh — Bootstrap Airflow for SignalDeck AI
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Setting AIRFLOW_HOME"
export AIRFLOW_HOME="${SCRIPT_DIR}/airflow"
mkdir -p "${AIRFLOW_HOME}/dags"

# Write AIRFLOW_HOME to .env if not already present
if ! grep -q "^AIRFLOW_HOME=" "${SCRIPT_DIR}/.env" 2>/dev/null; then
    echo "AIRFLOW_HOME=${AIRFLOW_HOME}" >> "${SCRIPT_DIR}/.env"
fi

echo "==> Initialising Airflow database"
airflow db init

echo "==> Symlinking DAG"
DAG_SRC="${SCRIPT_DIR}/dags/signaldeck_dag.py"
DAG_DST="${AIRFLOW_HOME}/dags/signaldeck_dag.py"
if [ ! -L "${DAG_DST}" ]; then
    ln -sf "${DAG_SRC}" "${DAG_DST}"
    echo "    Symlink created: ${DAG_DST} -> ${DAG_SRC}"
else
    echo "    Symlink already exists: ${DAG_DST}"
fi

echo "==> Creating Airflow admin user"
airflow users create \
    --username admin \
    --password admin \
    --firstname SignalDeck \
    --lastname Admin \
    --role Admin \
    --email admin@signaldeck.local \
    2>/dev/null || echo "    Admin user already exists — skipping."

echo ""
echo "✓ Airflow setup complete."
echo ""
echo "  Start the scheduler:  airflow scheduler"
echo "  Start the webserver:  airflow webserver --port 8080"
echo "  UI:                   http://localhost:8080  (admin / admin)"
