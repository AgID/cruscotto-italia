#!/usr/bin/env bash
# Pa11y test della tab Pendolarismo su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 072006 Bari       → attrattore netto (+40522), 16 sezioni complete
#   - 077014 Matera     → centro storico, attrattore (+5715), Sud
#   - 015146 Milano     → grande attrattore (+286k), gigante metropolitano
#   - 097051 Morterone  → micro-comune (33 ab.), edge case dati assenti
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS.
# axe-core escluso: riporta falsi positivi color-contrast sugli elementi
# dentro #pane-pendolarismo perche' la mappa Leaflet+CanvasFlowmapLayer
# introduce un canvas overlay che confonde il calcolo del background.
# Verifica manuale: i CSS .section-h-small (#17324d su #fff) e
# .pendolarismo-tab-muted (#455A72 su #fff) hanno contrasto 12:1 e 6.56:1
# rispettivamente, ampiamente sopra il limite AA 4.5:1.
# Hide canvas/marker per evitare false positive sulla mappa Leaflet+Flowmap.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-pendolarismo.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-pendolarismo.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-pendolarismo.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-pendolarismo.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure: PA11Y='npx pa11y' bash scripts/pa11y-pendolarismo.sh"
  exit 1
fi

declare -A TESTS=(
  ["Bari"]="072006"
  ["Matera"]="077014"
  ["Milano"]="015146"
  ["Morterone"]="097051"
)

CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-pendolarismo-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4500,
  "runners": ["htmlcs"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, canvas, .leaflet-zoom-animated, .leaflet-overlay-pane svg",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='pendolarismo'] to be visible",
    "click element [data-tab='pendolarismo']",
    "wait for element #pane-pendolarismo to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab Pendolarismo - standard: $STANDARD"
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
