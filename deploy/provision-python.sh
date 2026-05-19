#!/usr/bin/env bash
# deploy/provision-python.sh — installa le dipendenze Python ETL sistema-wide
#
# CONTESTO: bootstrap.sh usa un .venv/ locale, MA su VM produzione (AgID, Aruba)
# gli ETL vengono eseguiti da cron e da `sudo -u ubuntu /usr/bin/python3 -m etl.sources.X`
# (no venv attivato). Quindi le deps DEVONO essere installate nel python di sistema.
#
# Questo script è idempotente: rieseguilo quante volte vuoi, installa solo
# quello che manca o aggiorna alle versioni richieste in etl/requirements.txt.
#
# Uso:
#   sudo bash deploy/provision-python.sh
#
# Quando lanciarlo:
#   - prima volta dopo bootstrap della VM AgID (martedì 19/05/2026)
#   - dopo ogni modifica a etl/requirements.txt
#
# TODO post-go-live: refactor cron + ETL per usare .venv/ invece del python
# sistema-wide (più igienico, allinea bootstrap.sh con la realtà operativa).

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

echo "🐍 Provisioning Python ETL deps (sistema-wide)"
echo "==============================================="
echo "Repo: $REPO_DIR"

# Verifica root (serve per pip install sistema-wide)
if [ "$(id -u)" -ne 0 ]; then
  echo "❌ Questo script va eseguito come root (sudo)." >&2
  exit 1
fi

# Verifica python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 non trovato. Installa Python 3.10+ prima di procedere." >&2
  exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  ✓ python3 $PY_VERSION ($(which python3))"

# Verifica pip3
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "▶ pip non installato, installo python3-pip via apt…"
  apt-get update -qq
  apt-get install -y python3-pip
fi
echo "  ✓ pip $(python3 -m pip --version | awk '{print $2}')"

# Verifica requirements.txt
REQ_FILE="$REPO_DIR/etl/requirements.txt"
if [ ! -f "$REQ_FILE" ]; then
  echo "❌ $REQ_FILE non trovato." >&2
  exit 1
fi
echo "  ✓ $REQ_FILE"

# Install
echo
echo "▶ Installazione dipendenze ETL sistema-wide…"
python3 -m pip install --root-user-action=ignore --break-system-packages -r "$REQ_FILE"

# Smoke test: tutti gli import critici devono funzionare per l'utente ubuntu
# (sotto cui girano gli ETL in produzione)
echo
echo "▶ Smoke test import per utente ubuntu…"
if id ubuntu >/dev/null 2>&1; then
  sudo -u ubuntu python3 -c "
import boto3, botocore, duckdb, openpyxl, pandas, requests, structlog
import click, tqdm
from dateutil import parser as _dp
print('  ✓ Tutti i moduli ETL importabili da ubuntu')
print(f'    boto3={boto3.__version__}')
print(f'    duckdb={duckdb.__version__}')
print(f'    openpyxl={openpyxl.__version__}')
print(f'    pandas={pandas.__version__}')
print(f'    structlog={structlog.__version__}')
"
else
  echo "  ⚠ utente 'ubuntu' non esiste, salto smoke test"
fi

echo
echo "✓ Provisioning Python completato."
echo
echo "Prossimi step:"
echo "  - Verifica un ETL veloce: sudo -u ubuntu python3 -m etl.sources.carburanti --help"
echo "  - Rilancia un ETL che era fallito: sudo -u ubuntu python3 -m etl.sources.runts"
