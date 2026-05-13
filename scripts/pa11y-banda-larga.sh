#!/usr/bin/env bash
# Pa11y test della tab "Banda larga" (AGCOM Broadband Map) su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 058091 Roma         → comune metropoli (97% DESI, 1.2M famiglie FTTH, level 12)
#   - 075035 Lecce        → capoluogo medio (92% DESI, 38k famiglie, level 13)
#   - 007003 Aosta        → capoluogo piccolo (80% DESI, 13k famiglie, level 13)
#   - 097055 Morterone    → edge case (0% DESI, 22 famiglie, level 15, em-dash null)
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
# La tab Banda larga contiene:
#   - 5 KPI cards
#   - 2 chart Chart.js (canvas con aria-label + role="img")
#   - CTA box deep-link mappa AGCOM (link con aria-label esteso e nuova scheda)
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-banda-larga.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-banda-larga.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-banda-larga.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-banda-larga.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-banda-larga.sh"
  exit 1
fi

declare -A TESTS=(
  ["Roma"]="058091"
  ["Lecce"]="075035"
  ["Aosta"]="007003"
  ["Morterone"]="097055"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-banda-larga-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": "canvas",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='banda-larga'] to be visible",
    "click element [data-tab='banda-larga']",
    "wait for element #pane-banda-larga to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab Banda larga (AGCOM Broadband Map) - standard: $STANDARD"
echo "Base URL: $BASE_URL"
echo "Config: $CONFIG_FILE"
echo "======================================================================"

for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"
  URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  TOTAL=$((TOTAL + 1))

  echo ""
  echo "--- $NAME ($ISTAT) ---"
  echo "URL: $URL"

  $PA11Y \
    --config "$CONFIG_FILE" \
    --reporter cli \
    "$URL"

  EXIT_CODE=$?
  if [ $EXIT_CODE -ne 0 ]; then
    FAILED=$((FAILED + 1))
    echo "  ⚠ $NAME ha FALLITO (exit $EXIT_CODE)"
  else
    echo "  ✓ $NAME OK"
  fi
done

echo ""
echo "======================================================================"
echo "Riepilogo: $((TOTAL - FAILED))/$TOTAL OK"
if [ $FAILED -gt 0 ]; then
  echo "FAILED: $FAILED comuni con errori WCAG"
  exit 1
fi
echo "Tutti i comuni hanno superato $STANDARD."
echo "======================================================================"
