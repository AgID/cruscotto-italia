#!/usr/bin/env bash
# Pa11y test della tab Sanità (sanita_mds) su 4 comuni rappresentativi.
#
# Casi coperti:
#   - 075035 Lecce      → caso completo (32 farm + 12 parafarm + 5 osp)
#   - 077014 Matera     → caso medio (17 farm + 14 parafarm + 1 osp)
#   - 058091 Roma       → caso massimo (834 farm, cluster attivo, disclaimer
#                         qualità coordinate attivo per 73 ricalcolate)
#   - 097055 Morterone  → edge case "Dati non disponibili" (no farm/parafarm/osp)
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-sanita.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-sanita.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-sanita.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-sanita.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.piersoftckan.biz}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-sanita.sh"
  exit 1
fi

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Roma"]="058091"
  ["Morterone"]="097055"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-sanita-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, .leaflet-control-attribution, .leaflet-control-zoom, .sanita-cluster, canvas",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='sanita'] to be visible",
    "click element [data-tab='sanita']",
    "wait for element #pane-sanita to be visible",
    "wait for element .sanita-kpis to be visible"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test tab Sanità - standard: $STANDARD"
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
