#!/usr/bin/env bash
# Pa11y test della tab "Meteo e Aria" su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce      → meteo completo, nessuna stazione ISPRA-SNPA (aria_empty)
#   - 077014 Matera     → meteo completo, nessuna stazione ISPRA-SNPA
#   - 058091 Roma       → meteo completo + sezione aria completa (stazioni ISPRA,
#                         chart trend decennale, mappa stazioni Leaflet)
#   - 097055 Morterone  → edge case: meteo ok, nessuna stazione
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
#
# Uso:
#   bash scripts/pa11y-aria.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-aria.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-aria.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-aria.sh
set -uo pipefail
BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-aria.sh"
  exit 1
fi
declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Roma"]="058091"
  ["Morterone"]="097055"
)
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-aria-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT
cat > "$CONFIG_FILE" <<JSONEOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": "canvas, .leaflet-tile-container, .leaflet-marker-icon, .leaflet-control-attribution, .leaflet-control-zoom, .aria-cluster, .meteo-divider",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='aria'] to be visible",
    "click element [data-tab='aria']",
    "wait for element #pane-aria to be visible"
  ]
}
JSONEOF
TOTAL=0
FAILED=0
echo "======================================================================"
echo "Pa11y test tab Meteo e Aria - standard: $STANDARD"
echo "Base URL: $BASE_URL"
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
