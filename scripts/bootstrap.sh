#!/usr/bin/env bash
# bootstrap.sh - Setup iniziale di cruscotto-italia su una macchina fresca
#
# Cosa fa:
#  1. Verifica i prerequisiti (Node 20+, Python 3.12+, wrangler)
#  2. Installa le dipendenze del worker (npm)
#  3. Crea un venv Python per ETL e installa le dipendenze
#  4. Verifica le credenziali Cloudflare
#  5. Crea il bucket R2 se non esiste (richiede wrangler login)
#
# Uso: ./scripts/bootstrap.sh
#
# NOTA per VM produzione (AgID, Aruba): gli ETL girano da cron come utente
# ubuntu con /usr/bin/python3 sistema-wide (NO venv attivato). Per quel
# caso d'uso eseguire ANCHE: sudo bash deploy/provision-python.sh
# che installa etl/requirements.txt sistema-wide e fa smoke test import.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "🇮🇹 Cruscotto Italia · bootstrap"
echo "=================================="

# --- Prerequisites ---
echo
echo "▶ Verifica prerequisiti…"

command -v node >/dev/null 2>&1 || { echo "❌ Node.js non trovato. Installa Node 20+: https://nodejs.org" >&2; exit 1; }
NODE_MAJOR=$(node -v | sed 's/v\([0-9]*\).*/\1/')
if [ "$NODE_MAJOR" -lt 20 ]; then
  echo "❌ Node $NODE_MAJOR < 20. Aggiorna a Node 20+." >&2
  exit 1
fi
echo "  ✓ Node $(node -v)"

command -v python3 >/dev/null 2>&1 || { echo "❌ Python3 non trovato." >&2; exit 1; }
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  ✓ Python $PY_VERSION"

# --- Worker setup ---
echo
echo "▶ Setup worker…"
(cd worker && npm install)
echo "  ✓ Worker dependencies installed"

# --- ETL setup ---
echo
echo "▶ Setup ETL Python venv…"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r etl/requirements.txt --quiet
pip install -r etl/requirements-dev.txt --quiet
echo "  ✓ ETL dependencies installed in .venv/"

# --- Cloudflare check ---
echo
echo "▶ Cloudflare wrangler…"
if ! command -v wrangler >/dev/null 2>&1; then
  echo "  ⚠ wrangler non trovato globalmente. Installazione locale via worker/node_modules."
  WRANGLER="$(pwd)/worker/node_modules/.bin/wrangler"
else
  WRANGLER="wrangler"
fi
echo "  Per autenticarti: $WRANGLER login"
echo "  Per creare il bucket R2: $WRANGLER r2 bucket create cruscotto-italia-data"
echo "  Per creare la KV: $WRANGLER kv:namespace create CACHE"
echo
echo "  Dopo, edita worker/wrangler.toml inserendo gli ID restituiti."

# --- Done ---
echo
echo "✓ Bootstrap completato."
echo
echo "Prossimi passi:"
echo "  1. Aggiorna LICENSE con il testo completo AGPL-3.0 (vedi commento dentro il file)"
echo "  2. wrangler login (se non già fatto)"
echo "  3. wrangler r2 bucket create cruscotto-italia-data"
echo "  4. wrangler kv:namespace create CACHE  → aggiorna wrangler.toml"
echo "  5. (cd worker && npm run dev)  → http://localhost:8787"
echo "  6. (cd frontend && python3 -m http.server 8000)  → http://localhost:8000"
echo "  7. source .venv/bin/activate && python -m etl.sources.anagrafica --target=local"
