#!/usr/bin/env bash
# Pa11y test della cartografia CATASTO AGE su 4 comuni rappresentativi.
#
# Il catasto NON e' un tab separato: vive dentro il tab ANNCSU, attivato
# tramite i checkbox "Fogli" e "Particelle" del pannello "Livelli mappa".
# Lo script apre il tab ANNCSU, attiva entrambi i layer catastali e attende
# il rendering (fetch + decompressione pako + disegno Leaflet/canvas).
#
# Casi coperti:
#   - 075035 Lecce      -> catasto completo (68k particelle, monolitico)
#   - 077014 Matera     -> catasto medio
#   - 072006 Bari       -> catasto grande monolitico (molte particelle)
#   - 022139 Morterone  -> edge case comune minuscolo (poche particelle)
#
# NB: lo script attiva solo il layer FOGLI (poche geometrie). Le PARTICELLE
# non vengono attivate di proposito: su comuni grandi (anche monolitici come
# Bari) il rendering di decine di migliaia di poligoni su canvas satura Chrome
# headless e lo blocca. Per la conformita' WCAG conta il pannello di controllo
# accessibile (i 3 checkbox, label, switch, status, hint), NON il canvas delle
# particelle, che pa11y esclude comunque via hideElements. Attivare i Fogli e'
# sufficiente a far comparire status/hint del catasto e testare il pannello.
#
# Verifica WCAG 2.1 livello AA con runner HTMLCS + axe-core.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y
#
# Uso:
#   bash scripts/pa11y-catasto.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-catasto.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-catasto.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-catasto.sh

set -uo pipefail

BASE_URL="${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
PA11Y="${PA11Y:-pa11y}"
STANDARD="${STANDARD:-WCAG2AA}"
# Basic auth (blindatura htpasswd VM). Formato: "utente:password".
BASIC_AUTH="${BASIC_AUTH:-piersoft:agidtest}"
# Path al binario Chrome di puppeteer (necessario quando si gira come root:
# il chromeLaunchConfig.args applica --no-sandbox). Default = cache root.
CHROME_PATH="${PA11Y_CHROME_PATH:-/root/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome}"

# Verifica pa11y installato
if ! command -v "$PA11Y" >/dev/null 2>&1 && [[ "$PA11Y" != npx* ]]; then
  echo "ERRORE: $PA11Y non trovato. Installa con: npm install -g pa11y"
  echo "Oppure usa npx:  PA11Y='npx pa11y' bash scripts/pa11y-catasto.sh"
  exit 1
fi

declare -A TESTS=(
  ["Lecce"]="075035"
  ["Matera"]="077014"
  ["Bari"]="072006"
  ["Morterone"]="022139"
)

# Pa11y non supporta --action sulla CLI: usare un file config JSON.
# Le actions: apre tab ANNCSU, spunta Fogli e Particelle, attende il
# rendering del catasto (lo status diventa visibile a fine fetch/disegno).
CONFIG_FILE="$(mktemp --suffix=.json /tmp/pa11y-catasto-config.XXXXXX)"
trap "rm -f $CONFIG_FILE" EXIT

# NB: l'auth basic va messa NELL'URL (puppeteer ignora l'header Authorization
# sulla navigazione). Vedi costruzione URL piu' sotto.
# executablePath: usa il Chrome di puppeteer con --no-sandbox (necessario root).

cat > "$CONFIG_FILE" <<EOF
{
  "standard": "$STANDARD",
  "timeout": 90000,
  "wait": 5000,
  "runners": ["htmlcs", "axe"],
  "includeWarnings": true,
  "hideElements": ".leaflet-tile-container, .leaflet-marker-icon, .leaflet-overlay-pane, canvas",
  "chromeLaunchConfig": {
    "executablePath": "$CHROME_PATH",
    "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"]
  },
  "actions": [
    "wait for element [data-tab='anncsu'] to be visible",
    "click element [data-tab='anncsu']",
    "wait for element #pane-anncsu to be visible",
    "wait for element #anncsu-catasto-fogli to be visible",
    "click element #anncsu-catasto-fogli"
  ]
}
EOF

TOTAL=0
FAILED=0

echo "======================================================================"
echo "Pa11y test CATASTO AGE - standard: $STANDARD"
echo "Base URL: $BASE_URL"
echo "Config: $CONFIG_FILE"
echo "======================================================================"

for NAME in "${!TESTS[@]}"; do
  ISTAT="${TESTS[$NAME]}"
  # Auth inline nell'URL: https://user:pass@host/...
  if [ -n "$BASIC_AUTH" ]; then
    URL="$(echo "$BASE_URL" | sed "s#://#://${BASIC_AUTH}@#")/comune.html?istat=${ISTAT}"
  else
    URL="${BASE_URL}/comune.html?istat=${ISTAT}"
  fi
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
