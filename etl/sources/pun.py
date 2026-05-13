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
  python -m etl.sources.pun --target=local
  python -m etl.sources.pun --target=r2
  python -m etl.sources.pun --target=r2 --force

Cadenza: giornaliera (file rigenerato lato GSE ~03:00 UTC).
Skip automatico se LastModified S3 è invariato rispetto all'ultima run
(persistito in pun/_meta.json su R2).
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
from typing import Optional

import boto3
import requests
import structlog
from botocore import UNSIGNED
from botocore.config import Config

from etl.lib import manifest, r2

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

# R2 destinazione
R2_PREFIX = "pun"          # → R2: pun/<istat>.json
R2_META_KEY = "pun/_meta.json"

# Output locale
DATA_DIR = Path("data/pun")
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
                  by_np: dict, by_name: dict) -> Optional[str]:
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


def _clean(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().strip('"').strip()
    return s if s and s != " " else None


def _to_int(v) -> Optional[int]:
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
    for istat, sh in shards.items():
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


# ──────────────────────── SKIP LOGIC (R2 meta) ─────────────────────

def read_last_known_lm() -> Optional[str]:
    try:
        client = r2.get_r2_client()
        obj = client.get_object(Bucket=r2.get_bucket(), Key=R2_META_KEY)
        return json.loads(obj["Body"].read())["last_modified"]
    except Exception:
        return None


def write_last_known_lm(last_modified: datetime) -> None:
    body = json.dumps(
        {"last_modified": last_modified.isoformat(),
         "checked_at": datetime.now(timezone.utc).isoformat()},
        indent=2,
    ).encode("utf-8")
    r2.upload_bytes(body, R2_META_KEY, content_type="application/json")


# ──────────────────────── PUSH R2 / WRITE LOCAL ────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def push_shards_r2(shards: dict[str, dict], force: bool = False) -> tuple[int, int]:
    """Push shard su R2 con skip-by-md5 (pattern aria.py/veicoli.py).

    1) UNA list_objects_v2 paginata per leggere TUTTI gli ETag remoti
    2) calcolo md5 locale e diff
    3) upload paralleli con ThreadPoolExecutor(24)
    4) progress log ogni 200 upload
    """
    total = len(shards)
    log.info("shards_r2_start", total=total, force=force, prefix=R2_PREFIX)
    t0 = time.time()

    # 1) Lista oggetti remoti
    remote_etag: dict[str, str] = {}
    if not force:
        try:
            client = r2.get_r2_client()
            pag = client.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=r2.get_bucket(), Prefix=f"{R2_PREFIX}/"):
                for o in page.get("Contents", []):
                    name = o["Key"].split("/")[-1]
                    if name.startswith("_"):  # skip _meta.json
                        continue
                    etag = (o.get("ETag") or "").strip('"').lower()
                    remote_etag[name] = etag
            log.info("shards_r2_remote_listed", count=len(remote_etag))
        except Exception as e:
            log.warning("shards_r2_list_fail", err=str(e))

    # 2) Diff
    bodies: dict[str, bytes] = {}
    md5s: dict[str, str] = {}
    for istat, shard in shards.items():
        body = json.dumps(shard, ensure_ascii=False).encode("utf-8")
        bodies[istat] = body
        md5s[istat] = _md5_of_bytes(body)

    to_upload: list[str] = []
    n_same = 0
    if force:
        to_upload = list(shards.keys())
        log.info("shards_r2_force_all", n=len(to_upload))
    else:
        for istat, md5 in md5s.items():
            rmd5 = remote_etag.get(f"{istat}.json")
            if rmd5 is None or rmd5 != md5:
                to_upload.append(istat)
            else:
                n_same += 1
        log.info("shards_r2_md5_compared", total=total,
                 unchanged=n_same, to_upload=len(to_upload))

    # 3) Upload paralleli
    files_for_manifest: list[dict] = []

    def _upload_one(istat: str) -> tuple[str, int, str]:
        key = f"{R2_PREFIX}/{istat}.json"
        body = bodies[istat]
        r2.upload_bytes(body, key, content_type="application/json")
        return (key, len(body), md5s[istat])

    uploaded = 0
    if to_upload:
        with ThreadPoolExecutor(max_workers=24) as ex:
            futs = {ex.submit(_upload_one, istat): istat for istat in to_upload}
            for f in as_completed(futs):
                try:
                    key, size, md5 = f.result()
                    files_for_manifest.append({"key": key, "size": size, "md5": md5})
                    uploaded += 1
                    if uploaded % 200 == 0:
                        elapsed = time.time() - t0
                        rate = uploaded / elapsed if elapsed > 0 else 0
                        eta = (len(to_upload) - uploaded) / rate if rate > 0 else 0
                        log.info("shards_r2_progress",
                                 uploaded=uploaded, to_upload=len(to_upload),
                                 elapsed_s=round(elapsed, 1),
                                 eta_s=round(eta, 1),
                                 rate=round(rate, 1))
                except Exception as e:
                    log.error("shards_r2_upload_fail", err=str(e))

    elapsed = round(time.time() - t0, 1)
    log.info("shards_r2_done", uploaded=uploaded, skipped=n_same,
             total=total, elapsed_s=elapsed)
    if files_for_manifest:
        manifest.update_source("pun", files_for_manifest, status="ok")
    return uploaded, n_same


def write_shards_local(shards: dict[str, dict]) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, payload in shards.items():
        (DATA_DIR / f"{istat}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
    log.info("pun_local_done", written=n, dir=str(DATA_DIR))
    return n


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="ETL PUN — punti di ricarica veicoli elettrici")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--force", action="store_true",
                        help="Forza esecuzione anche se LastModified non è cambiato")
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza ridownload del CSV ISTAT comuni")
    args = parser.parse_args()

    log.info("etl_pun_start", target=args.target, version=ETL_VERSION)

    # Skip check (solo target=r2)
    if args.target == "r2" and not args.force:
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

    # Write
    if args.target == "local":
        write_shards_local(shards)
    else:
        push_shards_r2(shards, force=args.force)
        write_last_known_lm(last_modified)

    log.info("etl_pun_done", target=args.target, comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
