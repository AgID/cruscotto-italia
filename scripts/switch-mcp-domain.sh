#!/usr/bin/env bash
# scripts/switch-mcp-domain.sh
#
# Sostituisce tutti i riferimenti hardcoded al subdomain workers.dev
# del Worker MCP con il custom domain dati.gov.it.
#
# QUANDO ESEGUIRE
#   Da lanciare SOLO dopo che:
#     1. DNS CNAME cruscotto-italia-mcp.dati.gov.it è propagato (dig +short CNAME)
#     2. Cloudflare Custom Domain è stato aggiunto al Worker via dashboard
#     3. Cloudflare ha emesso il cert Universal SSL per il custom domain
#        (test: curl -sI https://cruscotto-italia-mcp.dati.gov.it/health
#         deve dare HTTP 200, NON HTTP 404 / TLS error)
#
# COSA SOSTITUISCE
#   Cerca: https://cruscotto-italia-mcp.agid.workers.dev (e bare hostname)
#   Sostituisce con: https://cruscotto-italia-mcp.dati.gov.it
#   In 13 file di:
#     - frontend HTML (3 file: MCP_ENDPOINT funzionale + 2 testi descrittivi)
#     - worker/src/http.ts (landing page setup MCP)
#     - README.md
#     - .github/workflows/etl-{annual,monthly,weekly}.yml (commenti)
#
# USO
#   bash scripts/switch-mcp-domain.sh           # esegue, mostra count
#   bash scripts/switch-mcp-domain.sh --dry-run # solo preview, no scrittura
#
# DOPO IL RUN
#   - Deploy Worker: cd worker && npm run typecheck && npm run deploy
#   - Copia frontend su /var/www: sudo cp -r frontend/* /var/www/cruscotto-italia/frontend/
#   - Commit + push entrambe le repo (piersoft + AgID)
#
# ROLLBACK
#   git checkout -- .   # se non hai ancora committato

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

cd "$(dirname "$0")/.."
REPO="$(pwd)"

OLD_HOST="cruscotto-italia-mcp.agid.workers.dev"
NEW_HOST="cruscotto-italia-mcp.dati.gov.it"

echo "🔄 Switch MCP domain"
echo "   $OLD_HOST"
echo "   → $NEW_HOST"
echo "   Repo: $REPO"
echo "   Dry-run: $DRY_RUN"
echo

# Conta occorrenze prima
before_count=$(grep -rln "$OLD_HOST" \
  frontend/ worker/src/ README.md .github/workflows/ 2>/dev/null | wc -l)

if [ "$before_count" -eq 0 ]; then
  echo "ℹ  Nessuna occorrenza di '$OLD_HOST' trovata. Già migrato?"
  exit 0
fi

echo "▶ File coinvolti ($before_count):"
grep -rln "$OLD_HOST" \
  frontend/ worker/src/ README.md .github/workflows/ 2>/dev/null | \
  sed 's/^/    /'
echo

if [ "$DRY_RUN" -eq 1 ]; then
  echo "▶ Dry-run: anteprima sostituzioni"
  echo
  grep -rnE "$OLD_HOST" \
    frontend/ worker/src/ README.md .github/workflows/ 2>/dev/null | \
    head -30
  echo
  echo "✓ Dry-run completo. Per applicare: bash scripts/switch-mcp-domain.sh"
  exit 0
fi

# Sostituzione effettiva
echo "▶ Sostituzione in corso..."
find frontend/ worker/src/ .github/workflows/ \
  -type f \( -name "*.html" -o -name "*.ts" -o -name "*.yml" \) \
  -exec sed -i "s|$OLD_HOST|$NEW_HOST|g" {} +

# README.md singolo file
sed -i "s|$OLD_HOST|$NEW_HOST|g" README.md

# Verifica zero residui
after_count=$(grep -rln "$OLD_HOST" \
  frontend/ worker/src/ README.md .github/workflows/ 2>/dev/null | wc -l)

if [ "$after_count" -ne 0 ]; then
  echo "❌ Errore: $after_count file contengono ancora '$OLD_HOST'"
  grep -rln "$OLD_HOST" frontend/ worker/src/ README.md .github/workflows/
  exit 1
fi

# Conta nuove occorrenze del dominio target
new_count=$(grep -rln "$NEW_HOST" \
  frontend/ worker/src/ README.md .github/workflows/ 2>/dev/null | wc -l)

echo
echo "✓ Switch completato"
echo "  File con '$OLD_HOST': $after_count (atteso 0)"
echo "  File con '$NEW_HOST': $new_count"
echo
echo "Prossimi step:"
echo "  1. cd worker && npm run typecheck && npm run deploy"
echo "  2. sudo cp -r frontend/* /var/www/cruscotto-italia/frontend/"
echo "  3. sudo chown -R www-data:www-data /var/www/cruscotto-italia/frontend"
echo "  4. git diff per review, poi:"
echo "     git commit -am 'feat(cutover): switch MCP endpoint a custom domain dati.gov.it'"
echo "     git push origin main"
echo "  5. Smoke test:"
echo "     curl -sI https://$NEW_HOST/health"
echo "     curl -s https://cruscotto-italia.dati.gov.it/ | grep -c 'dati.gov.it/mcp'"
