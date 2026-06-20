#!/usr/bin/env bash
# Pa11y test della tab Contratti (ANAC) su 4 comuni rappresentativi.
#   - 075035 Lecce      → 37 categorie CPV, tabella filtrabile completa
#   - 077014 Matera     → stazione appaltante media, Sud
#   - 015146 Milano     → grande buyer, molte categorie CPV
#   - 097051 Morterone  → micro-comune, anac assente (tabella non renderizzata)
# Verifica WCAG 2.1 AA con runner HTMLCS. Focus: input search con aria-label,
# heading e contrasto della tabella .cpvt-*.
#
# Uso:
#   bash scripts/pa11y-anac.sh
#   PA11Y='npx --yes pa11y' bash scripts/pa11y-anac.sh

set -uo pipefail
BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Milano"]="015146"
  ["Morterone"]="097051"
)

CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-anac-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 3500,
  "runners": ["htmlcs"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, canvas, .leaflet-zoom-animated, .leaflet-overlay-pane svg",
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element .tab[data-tab='contratti'] to be visible",
    "click element .tab[data-tab='contratti']",
    "wait for element #pane-contratti to be visible"
  ]
}
EOF

TOTAL=0; FAILED=0
echo "===================================================================="
echo "Pa11y tab Contratti (ANAC) - standard: $STANDARD - $BASE_URL"
echo "===================================================================="
for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"; URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  TOTAL=$((TOTAL + 1))
  echo ""; echo "--- $NAME ($ISTAT) ---"; echo "URL: $URL"
  $PA11Y --config "$CONFIG_FILE" --reporter cli "$URL"
  if [ $? -ne 0 ]; then FAILED=$((FAILED + 1)); echo "  ⚠ $NAME FALLITO"; else echo "  ✓ $NAME OK"; fi
done
echo ""; echo "Riepilogo: $((TOTAL - FAILED))/$TOTAL OK"
[ $FAILED -gt 0 ] && exit 1
echo "Tutti i comuni hanno superato $STANDARD."
