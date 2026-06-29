#!/usr/bin/env bash
# Pa11y test della sezione "Morfologia" (dentro tab Territorio) su 4 comuni.
#
# Casi coperti:
#   - 077014 Matera     → morfologia completa (versanti, geomorfologia, mappa)
#   - 007003 Aosta      → morfologia alpina (quota elevata, pendenza alta)
#   - 058091 Roma       → morfologia pianura/collina
#   - 059018 Ponza      → edge case: isola senza dati morfologia (slot vuoto)
set -uo pipefail
BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato."
  exit 1
fi
declare -A TESTS=(
  ["Matera"]="077014"
  ["Aosta"]="007003"
  ["Roma"]="058091"
  ["Ponza"]="059018"
)
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-morfologia-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT
cat > "$CONFIG_FILE" << JSONEOF
{
  "standard": "$STANDARD",
  "timeout": 60000,
  "wait": 4000,
  "runners": ["htmlcs", "axe"],
  "chromeLaunchConfig": {
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
  "actions": [
    "wait for element [data-tab='territorio'] to be visible",
    "click element [data-tab='territorio']",
    "wait for element #pane-territorio to be visible"
  ]
,
  "ignore": ["color-contrast"]
}
JSONEOF
TOTAL=0
FAILED=0
echo "======================================================================"
echo "Pa11y test sezione Morfologia (tab Territorio) - standard: $STANDARD"
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
    "$URL" \
    && echo "✓ PASS $NAME" \
    || { echo "✗ FAIL $NAME"; FAILED=$((FAILED + 1)); }
done
echo ""
echo "======================================================================"
echo "Risultato: $((TOTAL - FAILED))/$TOTAL PASS"
echo "======================================================================"
[ $FAILED -eq 0 ]
