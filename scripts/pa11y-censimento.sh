#!/usr/bin/env bash
# Pa11y test della tab Censimento 2021 (Basi Territoriali ISTAT) su
# 4 comuni rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce      → tab completa, ~440 sezioni, choropleth ricco
#                         con dropdown 12 indicatori + bar abitazioni
#                         + UE/Extra-UE
#   - 077014 Matera     → ~50-100 sezioni, Sud, presenza area UNESCO
#                         (alcune sezioni 'no_vars' in grigio)
#   - 015146 Milano     → metropoli con ~13.000 sezioni, stress test
#                         di rendering choropleth (geometrie grandi)
#   - 097055 Morterone  → micro-comune (33 ab.), edge case con ~1-2
#                         sezioni, dataset minimale
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS.
# axe-core escluso: come per pendolarismo, riporta falsi positivi
# color-contrast sugli elementi dentro #pane-censimento perche' la
# mappa Leaflet con poligoni colorati (choropleth) introduce overlay
# che confondono il calcolo del background. Verifica manuale:
#   - .section-h-small (#17324d su #fff)            contrasto 12:1
#   - .censimento-bar-legend / muted (#455A72 #fff)  contrasto 6.56:1
#   - .cns-var-table td (#0c1b3d su #fff)            contrasto >18:1
#   - .censimento-legend (var(--ink-mute) #fff)      contrasto >7:1
# Tutti ampiamente sopra il limite AA 4.5:1.
#
# Hide canvas/marker/tile per evitare false positive su mappa Leaflet
# (poligoni choropleth) + 4 chart Chart.js (canvas).
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-censimento.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-censimento.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-censimento.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-censimento.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
# BASIC_AUTH="user:pass" abilita HTTP Basic Auth via header Authorization
# (utile pre-deploy AgID quando nginx blindato con htpasswd).
BASIC_AUTH="${BASIC_AUTH:-}"

if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure: PA11Y='npx pa11y' bash scripts/pa11y-censimento.sh"
  exit 1
fi

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Milano"]="015146"
  ["Morterone"]="097055"
)

CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-censimento-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

# Costruisce blocco "headers" JSON se BASIC_AUTH e' fornito
HEADERS_JSON=""
if [ -n "$BASIC_AUTH" ]; then
  AUTH_B64="$(printf '%s' "$BASIC_AUTH" | base64 | tr -d '\n')"
  HEADERS_JSON='"headers": {"Authorization": "Basic '$AUTH_B64'"},'
fi

# wait piu' lungo (5500ms): il choropleth fa fetch lazy del geojson
# (3MB per Milano) + parse pyproj-free + render 13.000 poligoni.
cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 90000,
  "wait": 5500,
  "runners": ["htmlcs"],
  "includeWarnings": true,
  ${HEADERS_JSON}
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, .leaflet-overlay-pane svg, .leaflet-zoom-animated, canvas",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='censimento'] to be visible",
    "click element [data-tab='censimento']",
    "wait for element #pane-censimento to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab Censimento 2021 - standard: $STANDARD"
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
