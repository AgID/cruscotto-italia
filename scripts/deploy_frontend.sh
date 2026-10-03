#!/bin/bash
# Cruscotto Italia - deploy del frontend statico sulla VM AgID.
#
# Nasce da due problemi trovati il 03/10/2026, entrambi dovuti al deploy
# fatto a mano:
#
#  1) la root del vhost nginx e' /var/www/cruscotto-italia/frontend/, non la
#     cartella padre. Copiando in /var/www/cruscotto-italia/ il server
#     continuava a servire la versione precedente SENZA alcun errore: la
#     pagina rispondeva 200, semplicemente era quella vecchia. In quella
#     cartella sono stati trovati residui di about.html e index.html fermi
#     al 23 maggio, dello stesso errore fatto mesi prima.
#
#  2) la stringa di versione nel footer era cablata in quattro file e ferma
#     a v2026.05.07.1, mentre l'ultimo commit che aveva toccato quella riga
#     era del 9 agosto. Una versione che mente e' peggio di nessuna versione.
#
# Qui la versione viene DERIVATA dal commit: data dell'ultimo commit che ha
# toccato frontend/, piu' hash breve. La sostituzione avviene sulle copie in
# /var/www, mai nel repo, che resta la fonte di verita' con il suo
# placeholder storico.
#
# Uso:
#     scripts/deploy_frontend.sh              # tutti i file html + indice
#     scripts/deploy_frontend.sh comune.html  # un file solo
#     DRY_RUN=1 scripts/deploy_frontend.sh    # mostra senza copiare
set -euo pipefail

REPO="/home/ubuntu/cruscotto-italia"
SRC="${REPO}/frontend"
DEST="/var/www/cruscotto-italia/frontend"
DATA_DEST="/var/www/cruscotto-italia/data"
DRY_RUN="${DRY_RUN:-0}"

# Pagine servite dal vhost. L'indice dei contenuti NON sta qui: va in
# data/, dove CICO lo legge da filesystem (vedi lessico_indice.py).
PAGINE=(index.html comune.html about.html accessibilita.html)

cd "$REPO"

if [ ! -d "$DEST" ]; then
  echo "ERRORE: $DEST non esiste. La root del vhost e' cambiata?" >&2
  exit 1
fi

# ── versione dal commit ───────────────────────────────────────────────────
HASH=$(git log -1 --format=%h -- frontend/ 2>/dev/null || echo "nogit")
DATA=$(git log -1 --format=%cd --date=format:%Y.%m.%d -- frontend/ 2>/dev/null || date +%Y.%m.%d)
VERSIONE="v${DATA}.${HASH}"

# Stringa cablata nei file: si aggiorna qui se un giorno cambia formato.
VECCHIA='v2026\.05\.07\.1'

echo "repo:     $REPO"
echo "versione: $VERSIONE"
echo "destinaz: $DEST"
[ "$DRY_RUN" = "1" ] && echo "(DRY RUN: nessuna copia)"
echo

# ── scelta dei file ───────────────────────────────────────────────────────
if [ $# -gt 0 ]; then
  PAGINE=("$@")
fi

for f in "${PAGINE[@]}"; do
  if [ ! -f "${SRC}/${f}" ]; then
    echo "  SALTO ${f}: non esiste in ${SRC}"
    continue
  fi
  N=$(grep -c "$VECCHIA" "${SRC}/${f}" || true)
  if [ "$DRY_RUN" = "1" ]; then
    echo "  ${f}: ${N} occorrenze di versione da sostituire"
    continue
  fi
  TMP=$(mktemp)
  sed "s/${VECCHIA}/${VERSIONE}/g" "${SRC}/${f}" > "$TMP"
  sudo install -o www-data -g www-data -m 644 "$TMP" "${DEST}/${f}"
  rm -f "$TMP"
  echo "  ${f}: copiato (${N} occorrenze di versione aggiornate)"
done

# ── indice dei contenuti: destinazione diversa, nessuna sostituzione ──────
if [ $# -eq 0 ] && [ -f "${SRC}/indice_contenuti.json" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "  indice_contenuti.json -> ${DATA_DEST}/"
  else
    sudo install -o ubuntu -g www-data -m 644 \
      "${SRC}/indice_contenuti.json" "${DATA_DEST}/indice_contenuti.json"
    echo "  indice_contenuti.json: copiato in data/"
  fi
fi

[ "$DRY_RUN" = "1" ] && exit 0

# ── verifica dal web: che il server serva davvero quel che abbiamo copiato ─
echo
echo "verifica:"
for f in "${PAGINE[@]}"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://cruscotto-italia.dati.gov.it/${f}" || echo "000")
  echo "  ${f}: HTTP ${CODE}"
done
SERVITA=$(curl -s "https://cruscotto-italia.dati.gov.it/index.html" \
  | grep -oE 'v20[0-9]{2}\.[0-9]{2}\.[0-9]{2}\.[0-9a-f]+' | head -1 || true)
echo "  versione servita: ${SERVITA:-non trovata} (attesa: ${VERSIONE})"
