#!/usr/bin/env bash
# Pa11y test della tab "Distributori e prezzi" (CARBURANTI) su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 058091 Roma         → tab densa (~936 distributori, cluster denso, chart confronto prezzi)
#   - 015146 Milano       → tab densa (~241 distributori, cluster + chart)
#   - 077014 Matera       → tab semplice (pochi impianti, ~25)
#   - 097055 Morterone    → edge case (comune piccolo senza distributori, carburanti: null)
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-carburanti.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-carburanti.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-carburanti.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-carburanti.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-carburanti.sh"
  exit 1
fi

declare -A TESTS=(
  ["Roma"]="058091"
  ["Milano"]="015146"
  ["Matera"]="077014"
  ["Morterone"]="097055"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-carburanti-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, canvas",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='carburanti'] to be visible",
    "click element [data-tab='carburanti']",
    "wait for element #pane-carburanti to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab CARBURANTI (Distributori e prezzi) - standard: $STANDARD"
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
