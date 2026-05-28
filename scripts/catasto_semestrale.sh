#!/bin/bash
# Cruscotto Italia - Check mensile e re-fetch catasto AGE
set -euo pipefail

ZIPDIR="/home/ubuntu/catasto_test"
LOGFILE="/var/log/catasto-semestrale.log"
PIPELINE="/home/ubuntu/catasto_test/build_catasto.py"
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

REGIONS=(
  ABRUZZO BASILICATA CALABRIA CAMPANIA EMILIA-ROMAGNA FRIULI-VENEZIA-GIULIA
  LAZIO LIGURIA LOMBARDIA MARCHE MOLISE PIEMONTE PUGLIA SARDEGNA SICILIA
  TOSCANA UMBRIA VALLE-AOSTA VENETO
)

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

log "==== catasto_semestrale start ===="
cd "$ZIPDIR"

# 1. Warmup Akamai
log "Warmup Akamai..."
rm -f cookies.txt
curl -s -o /dev/null \
  -A "$UA" \
  -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate" \
  -H "Sec-Fetch-User: ?1" -H "Upgrade-Insecure-Requests: 1" \
  -H 'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"' \
  -H 'Sec-Ch-Ua-Mobile: ?0' -H 'Sec-Ch-Ua-Platform: "macOS"' \
  -c cookies.txt \
  "https://www.agenziaentrate.gov.it/portale/"
sleep 3
curl -s -o /dev/null \
  -A "$UA" \
  -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate" \
  -H "Sec-Fetch-Site: same-origin" -H "Sec-Fetch-User: ?1" \
  -H "Referer: https://www.agenziaentrate.gov.it/portale/" \
  -b cookies.txt -c cookies.txt \
  "https://www.agenziaentrate.gov.it/portale/accedi-al-servizio-cartografici"
sleep 3

# 2. Check per regione
UPDATED_REGIONS=()
for R in "${REGIONS[@]}"; do
  TARGET="${ZIPDIR}/${R}.zip"
  REMOTE_SZ=$(curl -sI \
    -A "$UA" \
    -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate" \
    -H "Sec-Fetch-Site: same-site" \
    -H "Referer: https://www.agenziaentrate.gov.it/portale/accedi-al-servizio-cartografici" \
    -H 'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"' \
    -H 'Sec-Ch-Ua-Mobile: ?0' -H 'Sec-Ch-Ua-Platform: "macOS"' \
    --range 0-127 \
    -b cookies.txt \
    "https://wfs.cartografia.agenziaentrate.gov.it/inspire/wfs/GetDataset.php?dataset=${R}.zip" \
    | grep -i "content-range" | sed -E 's/.*\/([0-9]+).*/\1/' | tr -d '\r')
  if [ -z "$REMOTE_SZ" ] || ! [[ "$REMOTE_SZ" =~ ^[0-9]+$ ]]; then
    log "[$R] WARN size remota non rilevata - skip"
    sleep 8
    continue
  fi
  LOCAL_SZ=0
  [ -f "$TARGET" ] && LOCAL_SZ=$(stat -c%s "$TARGET")
  if [ "$LOCAL_SZ" = "$REMOTE_SZ" ]; then
    log "[$R] OK ($LOCAL_SZ bytes, no update)"
  else
    log "[$R] AGGIORNATO (locale=$LOCAL_SZ remoto=$REMOTE_SZ)"
    UPDATED_REGIONS+=("$R")
  fi
  sleep 5
done

if [ "${#UPDATED_REGIONS[@]}" -eq 0 ]; then
  log "Nessun aggiornamento. Exit."
  log "==== catasto_semestrale end ===="
  exit 0
fi

log "Regioni da aggiornare: ${UPDATED_REGIONS[*]}"

# 3. Download regioni aggiornate
for R in "${UPDATED_REGIONS[@]}"; do
  TARGET="${ZIPDIR}/${R}.zip"
  log "[$R] Download in corso..."
  curl -L -s \
    -A "$UA" \
    -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate" \
    -H "Sec-Fetch-Site: same-site" \
    -H "Referer: https://www.agenziaentrate.gov.it/portale/download-massivo-cartografia-catastale" \
    -H 'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"' \
    -H 'Sec-Ch-Ua-Mobile: ?0' -H 'Sec-Ch-Ua-Platform: "macOS"' \
    -b cookies.txt \
    "https://wfs.cartografia.agenziaentrate.gov.it/inspire/wfs/GetDataset.php?dataset=${R}.zip" \
    -o "${TARGET}.part"
  NEW_SZ=$(stat -c%s "${TARGET}.part" 2>/dev/null || echo 0)
  if [ "$NEW_SZ" -gt 50000000 ]; then
    mv "${TARGET}.part" "${TARGET}"
    log "[$R] OK download $(du -h $TARGET | cut -f1)"
  else
    log "[$R] FAIL (size=$NEW_SZ)"
    rm -f "${TARGET}.part"
    continue
  fi
  sleep 5
done

# 4. Pipeline parziale --force
log "Avvio pipeline parziale (--force) su ${#UPDATED_REGIONS[@]} regioni..."
for R in "${UPDATED_REGIONS[@]}"; do
  log "[$R] Pipeline in corso..."
  python3 "$PIPELINE" "$R" --force --workers 13 2>&1 | tee -a "$LOGFILE"
done

log "==== catasto_semestrale end ===="
