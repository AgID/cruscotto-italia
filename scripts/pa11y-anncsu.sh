#!/usr/bin/env bash
# Pa11y test della tab ANNCSU su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce      → tab completa (47k civici, cartografia+GPS+altro)
#   - 077014 Matera     → tab con quote altimetriche
#   - 021008 Bolzano    → tab con strade bilingui IT/DE
#   - 007003 Aosta      → edge case 0% geo-ref (no mappa, no chart)
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-anncsu.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-anncsu.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-anncsu.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-anncsu.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
WAIT_MS="${WAIT_MS:-3500}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-anncsu.sh"
  exit 1
fi

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Bolzano"]="021008"
  ["Aosta"]="007003"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-anncsu-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 2000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, canvas",
  "actions": [
    "wait for element [data-tab='anncsu'] to be visible",
    "click element [data-tab='anncsu']",
    "wait for ${WAIT_MS}"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab ANNCSU - standard: $STANDARD"
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
