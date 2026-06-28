#!/usr/bin/env bash
# Pa11y master runner: esegue tutti i test mirati WCAG 2.1 AA sulle tab
# integrate in sequenza, con report aggregato finale.
#
# Tab testate (10):
#   1. ANNCSU              - civici e strade (Agenzia Entrate + ISTAT)
#   2. Banda larga         - copertura FTTH/FTTC (AGCOM Broadband Map)
#   3. PUN                 - punti di ricarica veicoli elettrici (GSE/MASE)
#   4. Sanita'             - farmacie + parafarmacie + ospedali (Min. Salute)
#   5. Carburanti          - distributori e prezzi (MIMIT)
#   6. Terzo Settore       - enti RUNTS ODV/APS/EF/IS/SMS/ETS (Min. Lavoro)
#   7. Imprese ASIA        - unita locali e addetti ATECO (ISTAT ASIA UL)
#   8. Pendolarismo        - matrice OD lavoro 2021 (ISTAT Censimento perm.)
#   9. Censimento 2021     - Basi Territoriali + 119 vars (ISTAT)
#  10. Beni culturali      - MiC ArCo + Cultural-ON DBUnico 2.0
#
# Ogni script testa 4 comuni rappresentativi (Roma/Milano/Matera/Morterone +
# eventuali edge case specifici), totale 28+ pagine.
#
# Prerequisiti:
#   - Node.js >= 18
#   - npm install -g pa11y  (oppure usa PA11Y='npx pa11y')
#
# Uso:
#   bash scripts/pa11y-all.sh
#   BASE_URL=http://localhost:8080 bash scripts/pa11y-all.sh
#   STANDARD=WCAG2AAA bash scripts/pa11y-all.sh
#   PA11Y='npx pa11y' bash scripts/pa11y-all.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TESTS=(
  "ANNCSU:pa11y-anncsu.sh"
  "Banda larga:pa11y-banda-larga.sh"
  "PUN:pa11y-pun.sh"
  "Sanita:pa11y-sanita.sh"
  "Carburanti:pa11y-carburanti.sh"
  "Terzo Settore:pa11y-runts.sh"
  "Imprese ASIA:pa11y-asia.sh"
  "Pendolarismo:pa11y-pendolarismo.sh"
  "Censimento 2021:pa11y-censimento.sh"
  "Beni culturali:pa11y-beni-culturali.sh"
  "Meteo e Aria:pa11y-aria.sh"
)

echo "######################################################################"
echo "#  Pa11y master runner — Cruscotto Italia"
echo "#  Standard: ${STANDARD:-WCAG2AA}"
echo "#  Base URL: ${BASE_URL:-https://cruscotto-italia.dati.gov.it}"
echo "######################################################################"
echo ""

TOTAL_SUITES=${#TESTS[@]}
PASSED_SUITES=0
FAILED_SUITES=()

START_TIME=$(date +%s)

for entry in "${TESTS[@]}"; do
  NAME="${entry%%:*}"
  SCRIPT="${entry##*:}"
  SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT"

  if [ ! -f "$SCRIPT_PATH" ]; then
    echo "⚠ SKIP $NAME: script $SCRIPT non trovato"
    FAILED_SUITES+=("$NAME (script mancante)")
    continue
  fi

  echo ""
  echo "######################################################################"
  echo "#  Suite: $NAME ($SCRIPT)"
  echo "######################################################################"

  if bash "$SCRIPT_PATH"; then
    PASSED_SUITES=$((PASSED_SUITES + 1))
    echo ""
    echo "✓ Suite $NAME PASSED"
  else
    FAILED_SUITES+=("$NAME")
    echo ""
    echo "✗ Suite $NAME FAILED"
  fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "######################################################################"
echo "#  RIEPILOGO FINALE"
echo "######################################################################"
echo "Suite eseguite:  $TOTAL_SUITES"
echo "Suite passate:   $PASSED_SUITES"
echo "Suite fallite:   ${#FAILED_SUITES[@]}"
echo "Tempo totale:    ${ELAPSED}s"
echo ""

if [ ${#FAILED_SUITES[@]} -gt 0 ]; then
  echo "Suite con errori:"
  for s in "${FAILED_SUITES[@]}"; do
    echo "  - $s"
  done
  echo ""
  echo "Esamina i log sopra per il dettaglio degli errori WCAG."
  exit 1
fi

echo "✓ Tutte le ${TOTAL_SUITES} suite hanno superato lo standard ${STANDARD:-WCAG2AA}."
echo "######################################################################"
