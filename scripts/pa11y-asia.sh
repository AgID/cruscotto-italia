#!/usr/bin/env bash
# Pa11y test della tab "Imprese e addetti" (ASIA UL) su 4 comuni
# rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce        → tab piena (~11.900 UL, mix 95/4/0.7/0.08 classi,
#                            top settori ordinati per UL desc, +0.86% YoY verde,
#                            tabella ATECO dettaglio ~88 righe)
#   - 058091 Roma         → tab densa (metropoli, alto numero UL, doughnut con
#                            quote più equilibrate tra classi dimensionali)
#   - 015146 Milano       → tab densa (centro industriale, tessuto economico
#                            forte di settori finanziari e industriali)
#   - 097055 Morterone    → edge case (3 UL, mostra '(poco significativo)'
#                            su YoY -25%, chart con sola 1-3 voci, doughnut
#                            100% W0_9, fallback rendering robusto)
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
# Particolare attenzione a:
#   - Chart.js canvas (nascosto da Pa11y via hideElements)
#   - Tabella ATECO×classe (thead semantico con colgroup/scope, hover row)
#   - Details/summary collapsable (semantica nativa HTML5, focus-visible)
#   - KPI card YoY (color positive/negative — verifica contrasto sufficiente)
#   - Link esterni con target=_blank + rel=noopener
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-asia.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-asia.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-asia.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-asia.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-asia.sh"
  exit 1
fi

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Roma"]="058091"
  ["Milano"]="015146"
  ["Morterone"]="097055"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-asia-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": "canvas, .leaflet-tile-container, .leaflet-marker-icon",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='asia'] to be visible",
    "click element [data-tab='asia']",
    "wait for element #pane-asia to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab ASIA UL (Imprese e addetti) - standard: $STANDARD"
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
