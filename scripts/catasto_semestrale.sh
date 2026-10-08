#!/bin/bash
# Cruscotto Italia - Sentinella e aggiornamento catasto AGE (Agenzia delle Entrate)
#
# Gira TUTTI I GIORNI. Il controllo costa una richiesta Range da 128 byte per
# regione (19) piu' il warmup: confronta la dimensione remota dello ZIP
# regionale con quella dello ZIP locale. Se nessuna regione e' cambiata esce.
# Se l'Agenzia ha pubblicato un rilascio nuovo scarica solo le regioni
# cambiate, verifica gli ZIP e rigenera i comuni (catasto_age.py --force).
#
# La fonte e' semestrale ma senza data fissa: il controllo giornaliero serve a
# non dipendere dalla puntualita' dell'Agenzia. Stessa logica di
# omi_semestrale.sh.
#
# Stato: data/_diagnostics/catasto_release.json (ultimo controllo, esito,
# rilascio remoto e rilascio in produzione per regione). _diagnostics NON e'
# servita da nginx: un esito di errore non deve essere leggibile dall'esterno.
# Lo legge scripts/etl/freshness_check.py.
#
# Errori mai silenziosi: ogni condizione anomala scrive ERRORE o ATTENZIONE
# nel log, registra l'esito nello stato ed esce con codice 1.
#
# CHECK_ONLY=1: solo controllo, senza download ne' build.
set -euo pipefail

REPO="/home/ubuntu/cruscotto-italia"
PIPELINE="${REPO}/etl/sources/catasto_age.py"
ZIPDIR="/home/ubuntu/catasto_test"
OUTDIR="/var/www/cruscotto-italia/data/catasto_full"
DIAGDIR="/var/www/cruscotto-italia/data/_diagnostics"
STATE="${DIAGDIR}/catasto_release.json"
LOGDIR="/var/log/cruscotto-etl"
LOGFILE="${LOGDIR}/catasto-semestrale.log"
LOCK="/var/lock/cruscotto-catasto.lock"
WORKERS=13

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
PORTALE="https://www.agenziaentrate.gov.it/portale/"
PAGINA="https://www.agenziaentrate.gov.it/portale/accedi-al-servizio-cartografici"
PAGINA_DL="https://www.agenziaentrate.gov.it/portale/download-massivo-cartografia-catastale"
# URL storico: risponde 301 verso l'host di download corrente, seguito con -L.
# Va interrogato in GET: la HEAD viene bloccata da Akamai (403).
SRC="https://wfs.cartografia.agenziaentrate.gov.it/inspire/wfs/GetDataset.php?dataset="

HDRS=(-A "$UA"
  -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate"
  -H 'Sec-Ch-Ua: "Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
  -H 'Sec-Ch-Ua-Mobile: ?0' -H 'Sec-Ch-Ua-Platform: "macOS"')

REGIONS=(
  ABRUZZO BASILICATA CALABRIA CAMPANIA EMILIA-ROMAGNA FRIULI-VENEZIA-GIULIA
  LAZIO LIGURIA LOMBARDIA MARCHE MOLISE PIEMONTE PUGLIA SARDEGNA SICILIA
  TOSCANA UMBRIA VALLE-AOSTA VENETO
)

mkdir -p "$LOGDIR" "$DIAGDIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

# ── lock: se un aggiornamento precedente e' ancora vivo, non si parte ──────
exec 9>"$LOCK"
if ! flock -n 9; then
  log "aggiornamento gia' in corso (lock attivo): esco"
  exit 0
fi

TMP=$(mktemp -d /tmp/catasto_sentinella.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
CK="${TMP}/cookies.txt"

declare -A RSZ RLM ZSZ BUILT FAILN
UPDATED=()
DOWNLOADED=()
N_WARN=0
N_ERR=0

# Scrive lo stato in _release.json. Le regioni non controllate in questo giro
# conservano il record precedente.
scrivi_stato() {
  local esito="$1"
  : > "${TMP}/regioni.tsv"
  for R in "${REGIONS[@]}"; do
    [ -n "${RSZ[$R]:-}" ] || continue
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$R" "${RSZ[$R]}" "${RLM[$R]:-}" \
      "${ZSZ[$R]:-0}" "${BUILT[$R]:-0}" "${FAILN[$R]:-}" >> "${TMP}/regioni.tsv"
  done
  ESITO="$esito" STATE="$STATE" TSV="${TMP}/regioni.tsv" python3 - <<'PY' || log "ERRORE: scrittura stato fallita"
import datetime, json, os
from email.utils import parsedate_to_datetime

state_path = os.environ["STATE"]
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def iso(lm):
    try:
        return parsedate_to_datetime(lm).isoformat()
    except Exception:
        return None

def num(x):
    return int(x) if x.isdigit() else None

try:
    prev = json.load(open(state_path))
except Exception:
    prev = {}
reg = dict(prev.get("regioni", {}))

for line in open(os.environ["TSV"]):
    p = line.rstrip("\n").split("\t")
    if len(p) < 6:
        continue
    r, rsz, rlm, zsz, built, fail = p[:6]
    prod = reg.get(r, {}).get("in_produzione")
    if built == "1":
        prod = {"last_modified": iso(rlm), "dimensione": num(rsz),
                "build": now, "comuni_falliti": num(fail)}
    elif prod is None and rsz.isdigit() and rsz == zsz:
        # primo giro: lo ZIP locale coincide con il remoto, ma la build
        # non e' stata fatta da questo script
        prod = {"last_modified": iso(rlm), "dimensione": num(rsz),
                "build": None, "origine": "allineamento_zip"}
    reg[r] = {"remoto": {"dimensione": num(rsz), "last_modified": iso(rlm)},
              "zip_locale": num(zsz), "in_produzione": prod}

lms = sorted(v["in_produzione"]["last_modified"] for v in reg.values()
             if v.get("in_produzione") and v["in_produzione"].get("last_modified"))
out = {
    "fonte": "Agenzia delle Entrate - cartografia catastale, download massivo",
    "ultimo_controllo": now,
    "esito": os.environ["ESITO"],
    "rilascio_in_produzione": {"min": lms[0] if lms else None,
                               "max": lms[-1] if lms else None},
    "regioni": dict(sorted(reg.items())),
}
tmp = state_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
os.chmod(tmp, 0o644)
os.replace(tmp, state_path)
PY
}

log "==== catasto sentinella start ===="

# ── 1. Warmup Akamai ──────────────────────────────────────────────────────
curl -s -o /dev/null --max-time 60 "${HDRS[@]}" -c "$CK" "$PORTALE" \
  || log "WARN warmup portale non riuscito"
sleep 3
curl -s -o /dev/null --max-time 60 "${HDRS[@]}" -H "Sec-Fetch-Site: same-origin" \
  -H "Referer: $PORTALE" -b "$CK" -c "$CK" "$PAGINA" \
  || log "WARN warmup pagina cartografia non riuscito"
sleep 3

# ── 2. Controllo per regione (GET Range 128 byte) ────────────────────────
for R in "${REGIONS[@]}"; do
  HDR=$(curl -s -L -D - -o /dev/null --max-time 60 "${HDRS[@]}" \
    -H "Sec-Fetch-Site: same-site" -H "Referer: $PAGINA" \
    --range 0-127 -b "$CK" "${SRC}${R}.zip" || true)
  SZ=$(printf '%s\n' "$HDR" | grep -i '^content-range:' | tail -1 \
    | sed -E 's/.*\/([0-9]+).*/\1/' | tr -d '\r' || true)
  LM=$(printf '%s\n' "$HDR" | grep -i '^last-modified:' | tail -1 \
    | cut -d' ' -f2- | tr -d '\r' || true)
  if ! [[ "$SZ" =~ ^[0-9]+$ ]]; then
    ST=$(printf '%s\n' "$HDR" | grep '^HTTP' | tail -1 | tr -d '\r' || true)
    log "[$R] WARN dimensione remota non rilevata (${ST:-nessuna risposta})"
    N_WARN=$((N_WARN+1))
    sleep 8
    continue
  fi
  RSZ[$R]=$SZ
  RLM[$R]=$LM
  LOC=0
  if [ -f "${ZIPDIR}/${R}.zip" ]; then LOC=$(stat -c%s "${ZIPDIR}/${R}.zip"); fi
  ZSZ[$R]=$LOC
  if [ "$LOC" = "$SZ" ]; then
    log "[$R] invariato ($SZ byte, rilascio ${LM:-?})"
  else
    log "[$R] NUOVO RILASCIO (locale=$LOC remoto=$SZ, rilascio ${LM:-?})"
    UPDATED+=("$R")
  fi
  sleep 5
done

if [ "$N_WARN" -eq "${#REGIONS[@]}" ]; then
  log "ERRORE: dimensione remota non rilevata per nessuna regione - controllo NON eseguito"
  scrivi_stato "errore"
  log "==== catasto sentinella end (ERRORE) ===="
  exit 1
fi
if [ "$N_WARN" -gt 0 ]; then
  log "ATTENZIONE: ${N_WARN} regioni non controllate in questo giro"
fi

if [ "${#UPDATED[@]}" -eq 0 ]; then
  if [ "$N_WARN" -gt 0 ]; then
    scrivi_stato "parziale"
    log "==== catasto sentinella end (nessun rilascio nuovo, controllo PARZIALE) ===="
    exit 1
  fi
  scrivi_stato "invariato"
  log "nessun rilascio nuovo: esco"
  log "==== catasto sentinella end ===="
  exit 0
fi

log "Regioni con rilascio nuovo: ${UPDATED[*]}"
if [ "${CHECK_ONLY:-0}" = "1" ]; then
  scrivi_stato "check_only"
  log "CHECK_ONLY=1: stop prima del download"
  exit 0
fi
scrivi_stato "aggiornamento_in_corso"

# ── 3. Download e verifica degli ZIP ─────────────────────────────────────
for R in "${UPDATED[@]}"; do
  TARGET="${ZIPDIR}/${R}.zip"
  PART="${TARGET}.part"
  log "[$R] download in corso..."
  if ! curl -L -s -f --max-time 3600 "${HDRS[@]}" \
      -H "Sec-Fetch-Site: same-site" -H "Referer: $PAGINA_DL" \
      -b "$CK" -o "$PART" "${SRC}${R}.zip"; then
    log "ERRORE [$R] download fallito"
    rm -f "$PART"; N_ERR=$((N_ERR+1)); continue
  fi
  NEW=$(stat -c%s "$PART")
  if [ "$NEW" != "${RSZ[$R]}" ]; then
    log "ERRORE [$R] scaricati $NEW byte invece di ${RSZ[$R]}"
    rm -f "$PART"; N_ERR=$((N_ERR+1)); continue
  fi
  if ! python3 -c 'import sys, zipfile; sys.exit(1 if zipfile.ZipFile(sys.argv[1]).testzip() else 0)' "$PART"; then
    log "ERRORE [$R] ZIP corrotto"
    rm -f "$PART"; N_ERR=$((N_ERR+1)); continue
  fi
  mv -f "$PART" "$TARGET"
  ZSZ[$R]=$NEW
  DOWNLOADED+=("$R")
  log "[$R] download OK ($(du -h "$TARGET" | cut -f1))"
  sleep 5
done

# ── 4. Build dei comuni (--force) ────────────────────────────────────────
for R in "${DOWNLOADED[@]}"; do
  log "[$R] build in corso..."
  RL="${TMP}/build_${R}.log"
  if ! python3 -u "$PIPELINE" "$R" --force --workers "$WORKERS" 2>&1 | tee -a "$LOGFILE" "$RL"; then
    log "ERRORE [$R] build terminata con errore"
    N_ERR=$((N_ERR+1)); continue
  fi
  F=$(grep -oE 'fail:[0-9]+' "$RL" | tail -1 | cut -d: -f2 || true)
  if [ -z "$F" ]; then
    log "ERRORE [$R] build senza riepilogo finale"
    N_ERR=$((N_ERR+1)); continue
  fi
  BUILT[$R]=1
  FAILN[$R]=$F
  if [ "$F" -gt 0 ]; then
    log "ATTENZIONE [$R] ${F} comuni non elaborati"
  fi
  log "[$R] build OK"
done

if [ "$N_ERR" -gt 0 ]; then
  scrivi_stato "errore"
  log "==== catasto sentinella end (ERRORE: ${N_ERR} regioni) ===="
  exit 1
fi
if [ "$N_WARN" -gt 0 ]; then
  scrivi_stato "parziale"
  log "==== catasto sentinella end (aggiornamento PARZIALE) ===="
  exit 1
fi
scrivi_stato "aggiornato"
log "==== catasto sentinella end (aggiornate: ${DOWNLOADED[*]}) ===="
