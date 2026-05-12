#!/usr/bin/env bash
# Pa11y test della tab ANNCSU su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce      → tab ANNCSU completa (47k civici, cartografia+GPS+altro)
#   - 077014 Matera     → tab ANNCSU con quote altimetriche
#   - 021008 Bolzano    → tab ANNCSU con strade bilingui IT/DE
#   - 007003 Aosta      → tab ANNCSU edge case (0% geo-ref, niente mappa né chart)
#
# Verifica WCAG 2.1 livello AA con regole HTMLCS + ax-core (default Pa11y).
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y  (oppure npx pa11y)
#   - Frontend deployato e raggiungibile (default cruscotto-italia.piersoftckan.biz)
#
# Uso:
#   bash scripts/pa11y-anncsu.sh              # usa default base URL
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-anncsu.sh  # locale

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

# Action sequence: click sulla tab ANNCSU + attesa mappa Leaflet caricata.
# I selettori sono testati su screenshot Lecce 2026-05-12.
WAIT_MS="${WAIT_MS:-3500}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1; then
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

TOTAL=0
FAILED=0
ERRORS_TOTAL=0

echo "======================================================================"
echo "Pa11y test tab ANNCSU - standard: $STANDARD"
echo "Base URL: $BASE_URL"
echo "Wait dopo tab click: ${WAIT_MS}ms (mappa Leaflet + enrich full)"
echo "======================================================================"

for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"
  URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  TOTAL=$((TOTAL + 1))

  echo ""
  echo "--- $NAME ($ISTAT) ---"
  echo "URL: $URL"

  # Pa11y con sequenza: carica → click tab ANNCSU → attendi mappa → audit
  $PA11Y \
    --standard "$STANDARD" \
    --timeout 60000 \
    --wait 2000 \
    --runner htmlcs \
    --runner axe \
    --include-warnings \
    --reporter cli \
    --hide-elements ".leaflet-tile-container, .leaflet-marker-icon, canvas" \
    --action "wait for element [data-tab='anncsu'] to be visible" \
    --action "click element [data-tab='anncsu']" \
    --action "wait for ${WAIT_MS}" \
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
