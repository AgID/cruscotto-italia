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

BASE_URL="${BASE_URL:-https://127.0.0.1}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
# BASIC_AUTH="user:pass" abilita HTTP Basic Auth via header Authorization
# (utile pre-deploy AgID quando nginx blindato con htpasswd).
BASIC_AUTH="${BASIC_AUTH:-}"
# HOST_HEADER opzionale: se valorizzato, aggiunge header Host (utile solo
# per bypass DNS via 127.0.0.1). Quando si usa il dominio reale (con
# /etc/hosts o DNS pubblico) DEVE restare vuoto, altrimenti puppeteer
# rigetta: 'Unsafe header: host' (header riservato CDP).
HOST_HEADER="${HOST_HEADER:-}"

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

CONFIG_FILE="$(mktemp /tmp/pa11y-censimento-config.XXXXXX).json"
# mktemp crea file senza .json; il file finale e' quello col .json (creato da cat sotto).
# Trap rimuove entrambi.
trap "rm -f $CONFIG_FILE \"${CONFIG_FILE%.json}\"" EXIT

# Costruisce blocco "headers" JSON. Solo Host (se HOST_HEADER set, per
# bypass DNS via 127.0.0.1). BASIC_AUTH viene iniettata direttamente
# nell'URL (https://user:pass@host/...) per evitare l'header
# Authorization via puppeteer Fetch.continueRequest che incappa nel
# blocco 'Unsafe header'.
HEADERS_PARTS=()
if [ -n "$HOST_HEADER" ]; then
  HEADERS_PARTS+=("\"Host\": \"$HOST_HEADER\"")
fi
HEADERS_JSON=""
if [ ${#HEADERS_PARTS[@]} -gt 0 ]; then
  IFS=', '
  HEADERS_JSON="\"headers\": {${HEADERS_PARTS[*]}},"
  unset IFS
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
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--host-resolver-rules=MAP cruscotto-italia.dati.gov.it 127.0.0.1, MAP chatbot.dati.gov.it 127.0.0.1"]
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
  # Inject BASIC_AUTH nell'URL invece che negli headers (puppeteer rifiuta
  # Authorization via Fetch.continueRequest come 'Unsafe header'). Chrome
  # gestisce nativamente https://user:pass@host/path.
  if [ -n "$BASIC_AUTH" ]; then
    # Estrai schema (https://) e resto dell'URL
    SCHEMA="${BASE_URL%%://*}"
    REST="${BASE_URL#*://}"
    URL="${SCHEMA}://${BASIC_AUTH}@${REST}/comune.html?istat=${ISTAT}"
  else
    URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  fi
  TOTAL=$((TOTAL + 1))
  echo ""
  echo "--- $NAME ($ISTAT) ---"
  # Mostra URL senza credenziali nel log
  echo "URL: ${BASE_URL}/comune.html?istat=${ISTAT}"
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
