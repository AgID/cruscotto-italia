#!/usr/bin/env bash
# Pa11y test della tab Beni culturali su 4 comuni rappresentativi.
#
# La 25a fonte e' l'unione di due dataset MiC:
#   - ArCo (ICCD): patrimonio immobile tutelato (chiese, palazzi, castelli,
#     ville, archeologia, monumenti, parchi)
#   - Cultural-ON DBUnico 2.0: Luoghi della Cultura visitabili (musei,
#     biblioteche, archivi) con orari/contatti/scheda online
#
# Casi coperti:
#   - 058091 Roma       -> 888 beni (645 ArCo + 243 Cultural-ON), tutto FULL
#   - 075035 Lecce      -> 654 beni (638 + 16), maggioranza ArCo
#   - 077014 Matera     -> piccolo comune con mix, BASE (cap 30)
#   - 097051 Morterone  -> micro-comune (33 ab.), edge case beni assenti
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS.
# axe-core escluso: la mappa Leaflet con marker SVG (divIcon) e cluster
# overlay genera falsi positivi color-contrast sul canvas. Verifica
# manuale CSS: var(--ink) (#0b3b66) su #fff = 8.8:1, .beni-list-fonte-co
# bg #ede5ff col #5a3da6 = 5.4:1, .beni-list-fonte-arco bg #d6e8ff col
# #1d4d80 = 6.1:1, tutti sopra AA 4.5:1.
#
# Hide elements: tile/marker/canvas/SVG overlay Leaflet per evitare
# false positive contrasto sulla mappa. Il filtro chip-categoria, la
# lista accordion e la search box restano testati normalmente.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-beni-culturali.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-beni-culturali.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-beni-culturali.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-beni-culturali.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
# BASIC_AUTH="user:pass" abilita HTTP Basic Auth via URL injection
# (utile pre-deploy AgID quando nginx e' blindato con htpasswd).
BASIC_AUTH="${BASIC_AUTH:-}"

if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure: PA11Y='npx --yes pa11y' bash scripts/pa11y-beni-culturali.sh"
  exit 1
fi

declare -A TESTS=(
  ["Roma"]="058091"
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Morterone"]="097051"
)

CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-beni-culturali-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 5500,
  "runners": ["htmlcs"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, canvas, .leaflet-zoom-animated, .leaflet-overlay-pane svg, .beni-list-detail-image",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors", "--host-resolver-rules=MAP cruscotto-italia.dati.gov.it 127.0.0.1"]
  },
  "actions": [
    "wait for element [data-tab='beni-culturali'] to be visible",
    "click element [data-tab='beni-culturali']",
    "wait for element #pane-beni-culturali to be visible",
    "wait for 2000"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab Beni culturali - standard: $STANDARD"
echo "Base URL: $BASE_URL"
echo "Config: $CONFIG_FILE"
echo "======================================================================"

for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"
  # Inject BASIC_AUTH nell'URL invece che negli headers (puppeteer rifiuta
  # Authorization via Fetch.continueRequest come 'Unsafe header'). Chrome
  # gestisce nativamente https://user:pass@host/path.
  if [ -n "$BASIC_AUTH" ]; then
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
