#!/bin/bash
# Cruscotto Italia - Sentinella e raccolta semestrale OMI (Agenzia delle Entrate)
#
# Gira TUTTI I GIORNI ma costa una sola richiesta: interroga l'elenco dei
# semestri pubblicati e confronta con quello gia' in produzione. Se coincidono
# esce subito. Se l'Agenzia ha pubblicato un semestre nuovo, avvia la catena
# completa di raccolta (~12 ore) e poi gli ETL.
#
# Il controllo giornaliero non cambia la cadenza del dato, che resta
# semestrale: serve solo a non dipendere dalla puntualita' dell'Agenzia, che
# pubblica "entro" il 15 marzo e il 15 ottobre senza una data fissa.
# Stessa logica di catasto_semestrale.sh, che controlla mensilmente una
# fonte semestrale.
#
# Catena: sentinella -> perimetri (~10 min) -> quotazioni (~12 h)
#         -> etl.sources.omi -> rebuild dashboard
#
# Un lockfile impedisce che un secondo cron parta mentre la raccolta e'
# ancora in corso: con 12 ore di durata e un controllo al giorno, senza
# lock si sovrapporrebbero.
set -euo pipefail

REPO="/home/ubuntu/cruscotto-italia"
BUILD="/home/ubuntu/omi_build"
LOGDIR="/var/log/cruscotto-etl"
LOGFILE="${LOGDIR}/omi-semestrale.log"
LOCK="/var/lock/cruscotto-omi.lock"
SHARD_PROD="/var/www/cruscotto-italia/data/omi"

BASE="https://www1.agenziaentrate.gov.it/servizi/geopoi_omi"
UA="CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it; AgID)"

mkdir -p "$LOGDIR" "$BUILD"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

# ── lock: se la raccolta precedente e' ancora viva, non si parte ───────────
exec 9>"$LOCK"
if ! flock -n 9; then
  log "raccolta gia' in corso (lock attivo): esco"
  exit 0
fi

# ── 1. Sentinella: qual e' l'ultimo semestre pubblicato? ───────────────────
ULTIMO=$(curl -s --max-time 30 -A "$UA" \
  -H "Referer: ${BASE}/index.htm" \
  "${BASE}/zoneomi.php?richiesta=5" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["SEMESTRE"] if d else "")' \
  2>/dev/null || echo "")

if [ -z "$ULTIMO" ]; then
  log "ERRORE: elenco semestri non leggibile (servizio irraggiungibile?)"
  exit 1
fi

# semestre attualmente in produzione, letto da uno shard qualsiasi
IN_PROD=$(python3 - <<'PY' 2>/dev/null || echo ""
import glob, json
f = sorted(glob.glob("/var/www/cruscotto-italia/data/omi/*.json"))
if f:
    d = json.load(open(f[0]))
    p = d.get("_data_period", "")          # formato "2025/2"
    print(p.replace("/", "") if p else "")
PY
)

log "ultimo semestre pubblicato: ${ULTIMO} | in produzione: ${IN_PROD:-nessuno}"

if [ "$ULTIMO" = "$IN_PROD" ]; then
  log "nessun semestre nuovo: esco"
  exit 0
fi

log "==== raccolta OMI semestre ${ULTIMO} - start ===="

# Cartelle di lavoro pulite: i checkpoint del semestre precedente
# farebbero saltare comuni gia' presenti ma con dati vecchi.
rm -rf "${BUILD}/geojson" "${BUILD}/shard"
mkdir -p "${BUILD}/geojson" "${BUILD}/shard"

cd "$REPO"

# ── 2. Perimetri (~10 minuti) ─────────────────────────────────────────────
log "fase 1/4: perimetri"
python3 -u scripts/etl/omi_fetch_perimetri.py \
  --semestre "$ULTIMO" --outdir "${BUILD}/geojson" 2>&1 | tee -a "$LOGFILE"

# ── 3. Quotazioni (~12 ore) ───────────────────────────────────────────────
log "fase 2/4: quotazioni (richiede diverse ore)"
python3 -u scripts/etl/omi_fetch_quotazioni.py \
  --semestre "$ULTIMO" --outdir "${BUILD}/shard" \
  --geodir "${BUILD}/geojson" 2>&1 | tee -a "$LOGFILE"

N_SHARD=$(find "${BUILD}/shard" -name '*.json' | wc -l)
log "shard grezzi prodotti: ${N_SHARD}"
if [ "$N_SHARD" -lt 7000 ]; then
  log "ERRORE: raccolta incompleta (${N_SHARD} < 7000), la produzione non viene toccata"
  exit 1
fi

# ── 4. ETL: grezzo -> shard di produzione ─────────────────────────────────
log "fase 3/4: etl.sources.omi"
python3 -u -m etl.sources.omi \
  --rawdir "${BUILD}/shard" --geodir "${BUILD}/geojson" 2>&1 | tee -a "$LOGFILE"

# ── 5. Rebuild dashboard ──────────────────────────────────────────────────
log "fase 4/4: rebuild dashboard"
python3 -u -m etl.sources.dashboard --target local 2>&1 | tee -a "$LOGFILE"

N_PROD=$(find "$SHARD_PROD" -name '*.json' | wc -l)
log "==== raccolta OMI semestre ${ULTIMO} - end (${N_PROD} shard in produzione) ===="
