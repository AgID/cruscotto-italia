#!/bin/bash
# Cruscotto Italia - Archivio locale dei file mensili BDNCP di ANAC
# (dataset cig, aggiudicazioni, aggiudicatari)
#
# Perche: ANAC espone i file mensili AAAAMM01-<dataset>_csv.zip solo per una
# finestra mobile di ~7 mesi (verificato il 19/10/2026: 202304-202603 in 404
# anche se elencati nel log). Ogni mese il file piu vecchio sparisce e non si
# recupera piu. L'ETL ANAC legge da questo archivio, non da ANAC a ogni run.
#
# Gira TUTTI I GIORNI: per ogni dataset prova i nomi dei mesi della finestra
# con una GET a range (2 byte) e scarica solo i file nuovi. Idempotente.
#
# I nomi dei file sono prevedibili e si provano direttamente: le pagine e i
# TTL DCAT del portale non sono raggiungibili con lo User-Agent dichiarato,
# i download si. Si usa SOLO lo User-Agent di etl/lib/http_ua.py (quello
# dichiarato agli enti per le whitelist). Niente HEAD: il firewall risponde
# 200 con una pagina di blocco. Un file e valido solo se inizia con la firma
# zip "PK" e passa unzip -t.
#
# Stato in data/_diagnostics/anac_archivio.json (non pubblico), letto dal
# freshness check.
set -euo pipefail

REPO="/home/ubuntu/cruscotto-italia"
ARCH="/home/ubuntu/anac_bdncp_archivio"
LOGDIR="/var/log/cruscotto-etl"
LOGFILE="${LOGDIR}/anac-archivio.log"
LOCK="/var/lock/cruscotto-anac-archivio.lock"
STATO="/var/www/cruscotto-italia/data/_diagnostics/anac_archivio.json"
DATASETS="cig aggiudicazioni aggiudicatari"
FINESTRA=9      # mesi provati all'indietro dal mese corrente (ANAC ne tiene ~7)

BASE_DL="https://dati.anticorruzione.it/opendata/download/dataset"
UA=$(cd "$REPO" && python3 -c 'from etl.lib.http_ua import USER_AGENT; print(USER_AGENT)')
H=(-A "$UA")

mkdir -p "$LOGDIR" "$ARCH" "$(dirname "$STATO")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

exec 9>"$LOCK"
if ! flock -n 9; then
  log "archiviazione gia in corso (lock attivo): esco"
  exit 0
fi

ERRORI=0
NUOVI=0
: > "$TMP/stato.tsv"     # dataset \t esito \t nota

# 0 se all'URL c'e uno zip (primi 2 byte = PK), 1 altrimenti
esiste_zip() {
  local inizio
  inizio=$(curl -s -m 60 "${H[@]}" -r 0-1 "$1" | head -c 2 || true)
  [ "$inizio" = "PK" ]
}

# Scarica $1 (url) in $2 (path finale): .part, firma PK, unzip -t, md5.
scarica_zip() {
  local url="$1" dest="$2" part="$2.part"
  rm -f "$part"
  if ! curl -s -f -m 1800 "${H[@]}" -o "$part" "$url"; then
    log "ERRORE download: $url"; rm -f "$part"; return 1
  fi
  if [ "$(head -c 2 "$part")" != "PK" ]; then
    log "ERRORE non zip ($(stat -c%s "$part") byte): $url"; rm -f "$part"; return 1
  fi
  if ! unzip -tqq "$part" >/dev/null 2>&1; then
    log "ERRORE zip corrotto: $url"; rm -f "$part"; return 1
  fi
  mv "$part" "$dest"
  echo "$(md5sum "$dest" | cut -c1-32)  $(basename "$dest")" >> "$(dirname "$dest")/MD5SUMS"
  log "archiviato $(basename "$dest") $(stat -c%s "$dest") byte"
}

# Mesi della finestra, dal piu vecchio al corrente (AAAAMM)
MESI=$(python3 - "$FINESTRA" <<'PY'
import sys
from datetime import date
n = int(sys.argv[1]); y, m = date.today().year, date.today().month
out = []
for _ in range(n):
    out.append(f"{y}{m:02d}")
    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
print(" ".join(reversed(out)))
PY
)

log "==== archivio ANAC BDNCP - start (UA dichiarato, mesi provati: $MESI) ===="
for N in $DATASETS; do
  D="${ARCH}/${N}"; mkdir -p "$D"
  ERR_N=0; PUB=""

  # 1. per ogni mese della finestra: gia in archivio, nuovo da scaricare, o assente
  for M in $MESI; do
    F="${M}01-${N}_csv.zip"
    U="${BASE_DL}/${N}/filesystem/${F}"
    if [ -s "$D/$F" ]; then PUB="$PUB $M"; continue; fi
    if esiste_zip "$U"; then
      PUB="$PUB $M"
      if scarica_zip "$U" "$D/$F"; then NUOVI=$((NUOVI+1)); else ERR_N=$((ERR_N+1)); fi
    fi
    sleep 1
  done
  PUB_MAX=$(echo $PUB | tr ' ' '\n' | tail -1)
  if [ -z "$PUB_MAX" ]; then
    log "ERRORE $N: nessun file mensile trovato nella finestra (firewall o cambio di nomi?)"
    printf '%s\terrore\tnessun file mensile nella finestra\n' "$N" >> "$TMP/stato.tsv"
    ERRORI=$((ERRORI+1)); continue
  fi

  # 2. buchi: mesi tra il primo archiviato e l'ultimo che mancano
  python3 - "$D" "$N" > "$TMP/$N.buchi" <<'PY'
import sys, glob, os
d, n = sys.argv[1], sys.argv[2]
mesi = sorted(os.path.basename(p)[:6] for p in glob.glob(f"{d}/[0-9]*-{n}_csv.zip"))
if mesi:
    y, m = int(mesi[0][:4]), int(mesi[0][4:])
    fine = mesi[-1]
    while f"{y}{m:02d}" <= fine:
        if f"{y}{m:02d}" not in mesi:
            print(f"{y}{m:02d}")
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
PY
  if [ -s "$TMP/$N.buchi" ]; then
    log "ATTENZIONE $N: mesi mancanti in archivio: $(tr '\n' ' ' < "$TMP/$N.buchi")"
  fi

  # 3. storico: si riscarica solo se cambia dimensione (nuovo nome datato)
  if [ "$N" != "cig" ]; then
    TOT=$(curl -s -m 30 "${H[@]}" -r 0-0 -D - -o /dev/null "${BASE_DL}/${N}/filesystem/${N}_csv.zip" \
          | tr -d '\r' | sed -n 's#^[Cc]ontent-[Rr]ange: bytes 0-0/##p' || true)
    LOC=$(ls -1 "$D"/${N}_csv*.zip 2>/dev/null | tail -1 || true)
    LOC_S=$( [ -n "$LOC" ] && stat -c%s "$LOC" || echo 0 )
    if [ -n "$TOT" ] && [ "$TOT" != "$LOC_S" ]; then
      log "$N: storico remoto $TOT byte, locale $LOC_S: scarico"
      if scarica_zip "${BASE_DL}/${N}/filesystem/${N}_csv.zip" "$D/${N}_csv_$(date +%Y%m%d).zip"; then
        NUOVI=$((NUOVI+1)); else ERR_N=$((ERR_N+1)); fi
    elif [ -z "$TOT" ]; then
      log "ATTENZIONE $N: dimensione dello storico non leggibile"
    fi
  fi

  ARC_MAX=$(ls -1 "$D"/[0-9]*-${N}_csv.zip 2>/dev/null | tail -1 | xargs -r basename | cut -c1-6 || true)
  ARC_N=$(ls -1 "$D"/[0-9]*-${N}_csv.zip 2>/dev/null | wc -l || true)
  ESITO=ok; [ "$ERR_N" -gt 0 ] && ESITO=errore
  [ "$ARC_MAX" != "$PUB_MAX" ] && ESITO=errore
  printf '%s\t%s\tpubblicati nella finestra:%s; archiviati %s (ultimo %s); buchi %s\n' \
    "$N" "$ESITO" "$PUB" "$ARC_N" "$ARC_MAX" "$(tr '\n' ' ' < "$TMP/$N.buchi")" >> "$TMP/stato.tsv"
  ERRORI=$((ERRORI+ERR_N))
  log "$N: pubblicati nella finestra:$PUB | archiviati $ARC_N, ultimo $ARC_MAX, esito $ESITO"
done

# 4. stato per il freshness check (scrittura atomica)
python3 - "$TMP/stato.tsv" "$STATO" "$ARCH" <<'PY'
import sys, json, os, glob
from datetime import datetime, timezone
tsv, out, arch = sys.argv[1:4]
ds = {}
for riga in open(tsv, encoding="utf-8"):
    n, esito, nota = riga.rstrip("\n").split("\t")
    mesi = sorted(os.path.basename(p)[:8] for p in glob.glob(f"{arch}/{n}/[0-9]*-{n}_csv.zip"))
    ds[n] = {"esito": esito, "nota": nota, "ultimo_mese": mesi[-1] if mesi else None, "n_mesi": len(mesi)}
stato = {"controllato_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "esito": "ok" if ds and all(v["esito"] == "ok" for v in ds.values()) else "errore",
         "dataset": ds}
tmp = out + ".part"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(stato, f, ensure_ascii=False, indent=1)
os.replace(tmp, out)
PY

log "==== archivio ANAC BDNCP - end (nuovi $NUOVI, errori $ERRORI, totale $(du -sh "$ARCH" | cut -f1)) ===="
[ "$ERRORI" -eq 0 ]
