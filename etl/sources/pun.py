"""ETL Piattaforma Unica Nazionale dei punti di ricarica (PUN / GSE).

Sorgente
--------
GSE — Piattaforma Unica Nazionale dei punti di ricarica per veicoli
elettrici (MASE/GSE), https://www.piattaformaunicanazionale.it/idr

Acquisizione
------------
Esposizione pubblica via bottone "Esporta dati" della tab Lista IDR.
Lato implementativo, il portale fa firmare un URL S3 lato browser usando
credenziali AWS Cognito *guest/unauthenticated*. L'IdentityPoolId è
pubblicato in chiaro dal sito stesso in /config.json:

  AWS_REGION:           eu-south-1
  AWS_IDENTITY_POOL_ID: eu-south-1:e3b2ab05-2046-43dd-8ed0-c0f14c69d507
  Bucket:               gse-pun-prod-documents
  Key:                  PA/infrastrutture.csv

L'ETL replica esattamente questo flusso (Cognito GetId →
GetCredentialsForIdentity → s3:GetObject), senza alcun bypass di
autenticazione. È la stessa azione che fa il browser di qualunque
utente che clicca sul bottone "Esporta dati".

Licenza
-------
CC BY 4.0 ex art. 52, comma 2, D.Lgs 82/2005 (CAD) — principio "open
data by default": i dati pubblicati dalla PA senza licenza espressa si
intendono come dati di tipo aperto, e le Linee Guida Open Data AgID
(Determinazione 183/2023) attribuiscono in tali casi la licenza CC-BY 4.0
con attribuzione al titolare (GSE).

Schema pun/<istat>.json
-----------------------
{
  "_etl_version": "0.1.0",
  "_source": "GSE - Piattaforma Unica Nazionale (PUN)",
  "_source_url": "https://www.piattaformaunicanazionale.it/idr",
  "_license": "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)",
  "_data_last_modified": "ISO-8601",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_totale": int,                   # numero PdR totali nel comune
    "n_attivi": int, "n_non_attivi": int,
    "pct_attivi": float,
    "n_ac": int, "n_dc": int,
    "potenza_tot_kw": float,
    "mix_potenza": {"Slow": .., "Quick": .., "Fast": .., "HPC": .., "Ultra fast": ..}
  },
  "punti": [
    {"id_evse", "lat", "lon", "indirizzo", "cap", "stato",
     "tipo_parcheggio", "potenza_categoria", "potenza_w", "corrente",
     "restrizioni", "servizi_vicini", "orario"}
  ]
}

Uso
---
  python -m etl.sources.pun
  python -m etl.sources.pun --force

Cadenza: giornaliera (file rigenerato lato GSE ~03:00 UTC).
Skip automatico se LastModified S3 è invariato rispetto all'ultima run
(persistito in DATA_DIR/pun/_meta.json local).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests
import structlog
from botocore import UNSIGNED
from botocore.config import Config

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "GSE - Piattaforma Unica Nazionale (PUN)"
SOURCE_URL = "https://www.piattaformaunicanazionale.it/idr"
LICENSE_STR = "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)"

# Cognito + S3 — valori pubblicati in https://www.piattaformaunicanazionale.it/config.json
AWS_REGION = "eu-south-1"
COGNITO_IDP_ID = "eu-south-1:e3b2ab05-2046-43dd-8ed0-c0f14c69d507"
SOURCE_BUCKET = "gse-pun-prod-documents"
SOURCE_KEY = "PA/infrastrutture.csv"

# Output prefix (locale, sotto DATA_DIR/pun/)
SHARD_PREFIX = "pun"

# Output locale (default produzione VM AgID; override via --outdir)
DEFAULT_OUTDIR = Path("/var/www/cruscotto-italia/data/pun")
CACHE_DIR = Path(".cache/pun")

# Elenco comuni ISTAT (riusa pattern di anagrafica.py)
ISTAT_COMUNI_URL = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
USER_AGENT = "cruscotto-italia-etl/0.1 (+https://github.com/piersoft/cruscotto-italia)"

# Bounding box Italia (con isole) — esclude geocoding errato
BBOX_LAT_MIN, BBOX_LAT_MAX = 35.0, 47.5
BBOX_LON_MIN, BBOX_LON_MAX = 6.0, 19.0

# Esonimi inglesi (CPO usano spesso "Rome", "Milan", ecc.)
ENGLISH_EXONYMS = {
    "rome": "roma", "milan": "milano", "naples": "napoli", "turin": "torino",
    "florence": "firenze", "genoa": "genova", "padua": "padova",
    "venice": "venezia", "raguse": "ragusa", "syracuse": "siracusa",
    "leghorn": "livorno", "mantua": "mantova",
}
# Denominazioni colloquiali → ufficiali ISTAT
DENOM_ALIASES = {
    "reggio emilia": "reggio nell emilia",
    "reggio calabria": "reggio di calabria",
}


# ──────────────────────── NORMALIZE / RESOLVE ──────────────────────

def normalize(s: str) -> str:
    """Normalizzazione comuni: lowercase, no accenti, no apostrofi/trattini,
    slash → spazio. Allineata a pnrr_progetti.normalize() ma in lowercase.
    """
    if not s:
        return ""
    s = s.strip().lower().replace("\u2019", "'").replace("`", "'")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"['\-]", " ", s)
    s = re.sub(r"/", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def in_italy(lat: float, lon: float) -> bool:
    return BBOX_LAT_MIN <= lat <= BBOX_LAT_MAX and BBOX_LON_MIN <= lon <= BBOX_LON_MAX


def pull_istat_comuni(workdir: Path) -> Path:
    """Scarica CSV ISTAT comuni (pattern preso da anagrafica.pull_istat_comuni)."""
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "istat-comuni.csv"
    if out.exists() and out.stat().st_size > 500_000:
        log.info("istat_comuni_cache_hit", path=str(out))
        return out
    log.info("pulling_istat_comuni", url=ISTAT_COMUNI_URL)
    r = requests.get(ISTAT_COMUNI_URL,
                     headers={"User-Agent": USER_AGENT},
                     timeout=120)
    r.raise_for_status()
    try:
        text = r.content.decode("latin-1")
    except UnicodeDecodeError:
        text = r.content.decode("utf-8", errors="replace")
    out.write_text(text, encoding="utf-8")
    log.info("istat_comuni_saved", path=str(out), bytes=out.stat().st_size)
    return out


def build_istat_index(csv_path: Path) -> tuple[dict, dict]:
    """Costruisce due indici:
      - by_np: (nome_norm, prov_norm) -> ISTAT6        (match preferito)
      - by_name: nome_norm -> set(ISTAT6)              (fallback)
    Provincia attesa per esteso (es. "Torino", non "TO").
    """
    by_np: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = defaultdict(set)
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            if len(row) < 16:
                continue
            istat6 = row[4].strip()
            nome_full = row[5].strip()
            nome_it = row[6].strip()
            nome_alt = row[7].strip()
            prov = row[11].strip()
            cands = {nome_full, nome_it}
            if nome_alt:
                cands.add(nome_alt)
            if "/" in nome_full:
                for p in nome_full.split("/"):
                    cands.add(p.strip())
            p_norm = normalize(prov)
            for nm in cands:
                if not nm:
                    continue
                n_norm = normalize(nm)
                by_np[(n_norm, p_norm)] = istat6
                by_name[n_norm].add(istat6)
    log.info("istat_index_built", n_np=len(by_np), n_names=len(by_name))
    return by_np, by_name


def resolve_istat(citta: str, prov: str, lat: float, lon: float,
                  by_np: dict, by_name: dict) -> str | None:
    """Risolve (Città, Provincia, lat, lon) → ISTAT6 o None.

    Strategia:
      0. Filtra out coordinate fuori bbox Italia
      1. Applica esonimi inglesi e alias denominazione
      2. Match exact (nome, prov)
      3. Match name_only (se univoco)
      4. Swap city↔province (alcuni CPO li invertono)
      5. Fallback: provincia come nome (cap. di prov.)
    """
    c = normalize(citta)
    p = normalize(prov)
    c = DENOM_ALIASES.get(c, ENGLISH_EXONYMS.get(c, c))
    p = DENOM_ALIASES.get(p, ENGLISH_EXONYMS.get(p, p))
    if not c or not in_italy(lat, lon):
        return None
    if (c, p) in by_np:
        return by_np[(c, p)]
    cands = by_name.get(c, set())
    if len(cands) == 1:
        return next(iter(cands))
    if (p, c) in by_np:
        return by_np[(p, c)]
    if p in by_name and len(by_name[p]) == 1:
        return next(iter(by_name[p]))
    return None


# ──────────────────────── ACQUISIZIONE S3 PUBBLICO ────────────────

def _get_pun_s3_client():
    """Ottieni client S3 con credenziali Cognito guest (unauthenticated)."""
    cog = boto3.client("cognito-identity", region_name=AWS_REGION,
                       config=Config(signature_version=UNSIGNED))
    identity_id = cog.get_id(IdentityPoolId=COGNITO_IDP_ID)["IdentityId"]
    creds = cog.get_credentials_for_identity(IdentityId=identity_id)["Credentials"]
    return boto3.client(
        "s3", region_name=AWS_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"],
    )


def head_pun_last_modified() -> datetime:
    """HEAD-only (no download) per lo skip check."""
    s3 = _get_pun_s3_client()
    h = s3.head_object(Bucket=SOURCE_BUCKET, Key=SOURCE_KEY)
    return h["LastModified"]


def fetch_pun_csv() -> tuple[bytes, datetime]:
    """Scarica infrastrutture.csv. Ritorna (body_bytes, last_modified_dt)."""
    log.info("pun_fetch_start", bucket=SOURCE_BUCKET, key=SOURCE_KEY)
    s3 = _get_pun_s3_client()
    obj = s3.get_object(Bucket=SOURCE_BUCKET, Key=SOURCE_KEY)
    body = obj["Body"].read()
    lm = obj["LastModified"]
    log.info("pun_fetch_ok", bytes=len(body), last_modified=lm.isoformat())
    return body, lm


# ──────────────────────── PARSE & AGGREGATE ────────────────────────

def parse_csv(body: bytes) -> list[dict]:
    """UTF-8 BOM, separatore ';', CRLF. 17 colonne."""
    text = body.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    return rows


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().strip('"').strip()
    return s if s and s != " " else None


def _to_int(v) -> int | None:
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except (ValueError, AttributeError, TypeError):
        return None


def build_shards(rows: list[dict], by_np: dict, by_name: dict,
                 last_modified: datetime) -> dict[str, dict]:
    """Aggrega righe PdR per ISTAT con KPI per comune."""
    shards: dict[str, dict] = {}
    stats = Counter()
    sample_unmatched: dict[str, list[tuple[str, str]]] = defaultdict(list)
    now_iso = datetime.now(timezone.utc).isoformat()

    for r in rows:
        try:
            lat = float(r["Latitudine"])
            lon = float(r["Longitudine"])
        except (ValueError, KeyError, TypeError):
            stats["bad_coords"] += 1
            continue
        istat = resolve_istat(r["Citta'"], r["Provincia"], lat, lon, by_np, by_name)
        if not istat:
            if not in_italy(lat, lon):
                stats["outside_italy"] += 1
                if len(sample_unmatched["outside_italy"]) < 5:
                    sample_unmatched["outside_italy"].append((r["Citta'"], r["Provincia"]))
            else:
                stats["unmatched"] += 1
                if len(sample_unmatched["unmatched"]) < 10:
                    sample_unmatched["unmatched"].append((r["Citta'"], r["Provincia"]))
            continue
        stats["matched"] += 1
        if istat not in shards:
            shards[istat] = {
                "_etl_version": ETL_VERSION,
                "_source": SOURCE_LABEL,
                "_source_url": SOURCE_URL,
                "_license": LICENSE_STR,
                "_data_last_modified": last_modified.isoformat(),
                "_generated_at": now_iso,
                "punti": [],
            }
        shards[istat]["punti"].append({
            "id_evse": _clean(r["ID EVSE"]),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "indirizzo": _clean(r["Indirizzo"]),
            "cap": _clean(r["Codice postale"]),
            "stato": _clean(r["Stato"]),                          # Attivo / Non Attivo
            "tipo_parcheggio": _clean(r["Tipologia parcheggio"]),
            "potenza_categoria": _clean(r["Potenza Erogabile"]),  # Slow/Quick/Fast/HPC/Ultra fast
            "potenza_w": _to_int(r["Potenza massima(W)"]),
            "corrente": _clean(r["Tipologia di corrente"]),        # AC / DC
            "restrizioni": _clean(r["Restrizioni parcheggio"]),
            "servizi_vicini": _clean(r["Servizi nelle vicinanze"]),
            "orario": _clean(r["Orario d'apertura"]),
        })

    # KPI per shard
    for _istat, sh in shards.items():
        punti = sh["punti"]
        n_tot = len(punti)
        n_att = sum(1 for p in punti if p["stato"] == "Attivo")
        n_dc = sum(1 for p in punti if p["corrente"] == "DC")
        pot_tot_w = sum(p["potenza_w"] or 0 for p in punti)
        mix_cat = Counter(p["potenza_categoria"] for p in punti if p["potenza_categoria"])
        sh["kpi"] = {
            "n_totale": n_tot,
            "n_attivi": n_att,
            "n_non_attivi": n_tot - n_att,
            "pct_attivi": round(n_att / n_tot * 100, 1) if n_tot else 0.0,
            "n_ac": n_tot - n_dc,
            "n_dc": n_dc,
            "potenza_tot_kw": round(pot_tot_w / 1000, 1),
            "mix_potenza": dict(mix_cat),
        }

    log.info("pun_aggregate_done",
             matched=stats["matched"],
             unmatched=stats["unmatched"],
             outside_italy=stats["outside_italy"],
             bad_coords=stats["bad_coords"],
             comuni_shard=len(shards),
             sample_unmatched=dict(sample_unmatched))
    return shards


# ──────────────────────── SKIP LOGIC (meta locale) ─────────────────

def read_last_known_lm() -> str | None:
    """Legge il last_modified S3 dell'ultima run da DATA_DIR/pun/_meta.json."""
    meta = local_lookup.load_meta(SHARD_PREFIX)
    return meta.get("last_modified") if meta else None


def write_last_known_lm(last_modified: datetime) -> None:
    """Salva last_modified S3 in DATA_DIR/pun/_meta.json."""
    local_lookup.save_meta(SHARD_PREFIX, {
        "last_modified": last_modified.isoformat(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })


# ──────────────────────── WRITE LOCAL ──────────────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def write_shards_local(shards: dict[str, dict],
                       outdir: Path,
                       force: bool = False) -> tuple[int, int]:
    """Scrive shard local con skip-by-md5 per file invariati.

    Ritorna (written, skipped). Se il file esiste con lo stesso md5 del
    payload, non lo riscrive (riduce I/O e preserva mtime).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    total = len(shards)
    written = 0
    skipped = 0
    files_for_manifest: list[dict] = []
    t0 = time.time()

    for istat, shard in shards.items():
        body = json.dumps(shard, ensure_ascii=False).encode("utf-8")
        new_md5 = _md5_of_bytes(body)
        target = outdir / f"{istat}.json"

        if not force and target.exists():
            try:
                cur_md5 = _md5_of_bytes(target.read_bytes())
                if cur_md5 == new_md5:
                    skipped += 1
                    files_for_manifest.append(
                        {"key": f"{SHARD_PREFIX}/{istat}.json",
                         "size": len(body), "md5": new_md5}
                    )
                    continue
            except OSError:
                pass

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        written += 1
        files_for_manifest.append(
            {"key": f"{SHARD_PREFIX}/{istat}.json",
             "size": len(body), "md5": new_md5}
        )
        if (written + skipped) % 500 == 0:
            log.info("shards_local_progress",
                     written=written, skipped=skipped, total=total)

    elapsed = round(time.time() - t0, 1)
    log.info("shards_local_done", written=written, skipped=skipped,
             total=total, elapsed_s=elapsed, dir=str(outdir))

    if files_for_manifest:
        manifest.update_source(SHARD_PREFIX, files_for_manifest, status="ok")

    return written, skipped


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="ETL PUN — punti di ricarica veicoli elettrici")
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output dir (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--force", action="store_true",
                        help="Forza esecuzione anche se LastModified non è cambiato + riscrittura totale")
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza ridownload del CSV ISTAT comuni")
    args = parser.parse_args()

    log.info("etl_pun_start", version=ETL_VERSION, outdir=str(args.outdir))

    # Skip check su LastModified S3 vs meta locale (efficiente: evita di scaricare
    # un CSV grosso quando GSE non ha aggiornato il file)
    if not args.force:
        last_known = read_last_known_lm()
        current = head_pun_last_modified().isoformat()
        if last_known == current:
            log.info("pun_skip", reason="last_modified_unchanged",
                     last_modified=current)
            return 0
        log.info("pun_run", last_known=last_known, current=current)

    # Fetch + parse
    body, last_modified = fetch_pun_csv()
    rows = parse_csv(body)
    log.info("pun_parsed", rows=len(rows))

    # Index ISTAT
    if args.no_cache and CACHE_DIR.exists():
        for p in CACHE_DIR.glob("istat-comuni.csv"):
            p.unlink()
    istat_csv = pull_istat_comuni(CACHE_DIR)
    by_np, by_name = build_istat_index(istat_csv)

    # Build shards
    shards = build_shards(rows, by_np, by_name, last_modified)

    # Sample log (alcuni comuni notevoli)
    for sample in ["058091", "015146", "077014", "075035", "097055"]:
        sh = shards.get(sample)
        if sh:
            log.info("sample", istat=sample, **sh["kpi"])
        else:
            log.info("sample_no_data", istat=sample)

    # Write local + aggiorna meta per skip-check al prossimo run
    write_shards_local(shards, args.outdir, force=args.force)
    write_last_known_lm(last_modified)

    log.info("etl_pun_done", comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
