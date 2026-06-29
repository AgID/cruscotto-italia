#!/usr/bin/env bash
# Pa11y test della tab "Beni culturali" su 4 comuni.
set -uo pipefail
BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato."; exit 1
fi
declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Roma"]="058091"
  ["Morterone"]="097055"
)
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-beni-config.XXXXXX)"
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
    "wait for element [data-tab='beni-culturali'] to be visible",
    "click element [data-tab='beni-culturali']",
    "wait for element #pane-beni-culturali to be visible",
    "wait for element .beni-list-row to be visible",
    "click element .beni-list-row",
    "wait for element .beni-list-detail to be visible"
  ],
  "ignore": ["color-contrast"]
}
JSONEOF
TOTAL=0
FAILED=0
echo "======================================================================"
echo "Pa11y test tab Beni culturali - standard: $STANDARD"
echo "Base URL: $BASE_URL"
echo "======================================================================"
for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"
  URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  TOTAL=$((TOTAL + 1))
  echo ""
  echo "--- $NAME ($ISTAT) ---"
  $PA11Y --config "$CONFIG_FILE" "$URL" \
    && echo "✓ PASS $NAME" \
    || { echo "✗ FAIL $NAME"; FAILED=$((FAILED + 1)); }
done
echo ""
echo "======================================================================"
echo "Risultato: $((TOTAL - FAILED))/$TOTAL PASS"
echo "======================================================================"
[ $FAILED -eq 0 ]
