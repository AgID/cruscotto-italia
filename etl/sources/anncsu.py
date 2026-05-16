"""ETL ANNCSU - Archivio Nazionale Numeri Civici e Strade Urbane.

Fonte istituzionale: Agenzia delle Entrate + ISTAT (DPCM 12/05/2016).
Licenza: open data ai sensi del Regolamento UE 2023/138 (recepimento
Direttiva UE 2019/1024 sul riuso di dati ad alto valore).

URL pattern (endpoint Akamai geo-IT, richiede User-Agent + Referer):
  https://anncsu.open.agenziaentrate.gov.it/age-inspire/opendata/anncsu/
    getds.php?<KIND>_<REGIONE>

  KIND: STRAD (stradario, ~17MB totali) | INDIR (indirizzario, ~310MB totali)
  REGIONE: 20 codici (ABRU, BASI, ..., VENE)

ZIP regionale contiene 1 CSV: <KIND>_<REGIONE>_<YYYYMMDD>.csv
Encoding: UTF-8. Separatore: ';'. Decimali: ',' (per COORD_X/Y/QUOTA).

Schema STRAD (10 col): CODICE_COMUNE; CODICE_ISTAT; PROGRESSIVO_NAZIONALE;
  CODICE_COMUNALE; ODONIMO; LOCALITA'; TOTALE_ACCESSI;
  DIZIONE_LINGUA1; DIZIONE_LINGUA2; (trailing empty col)

Schema INDIR (19 col): tutte le col di STRAD MENO TOTALE_ACCESSI,
  PIU' PROGRESSIVO_ACCESSO; CODICE_COMUNALE_ACCESSO; CIVICO; ESPONENTE;
  SPECIFICITA (ROSSO/NERO per FI/GE); METRICO; PROGRESSIVO_SNC;
  COORD_X_COMUNE (lon WGS84); COORD_Y_COMUNE (lat WGS84); QUOTA; METODO (1-4).

Pattern di esecuzione:
  1) download_all() → 40 file in cache locale + R2 raw/anncsu/_latest
     skip download se cache locale del giorno esiste e R2 matcha md5
  2) parse_strad_all() → dict[istat] = {n_strade, top10, bilingue_count, ...}
  3) parse_indir_all() → dict[istat] = {n_civici, geo_ref, punti, quota, ...}
  4) build_shards() → 7891 file anncsu/<istat>.json (sample 1000 punti)
  5) push_to_r2() → list_objects_v2 + md5 diff + ThreadPool max_workers=24

Schema output shard:
{
  "_etl_version": "0.1.0",
  "_source": "ANNCSU - Agenzia delle Entrate + Istat",
  "_snapshot_date": "2026-05-04",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_strade": 773,
    "n_strade_bilingui": 0,         (omesso se 0)
    "n_civici": 17705,
    "n_civici_geo_ref": 17234,
    "pct_geo_ref": 97.3,
    "n_civici_metrici": 12,         (omesso se 0)
    "n_civici_rosso": 0,            (omesso se 0)
    "n_civici_nero": 0,             (omesso se 0)
    "n_civici_bis": 234,
    "quota_min_m": 89,
    "quota_max_m": 467,
    "quota_mean_m": 312.4,
    "bbox": {"lat_min": 40.6, "lat_max": 40.7,
             "lon_min": 16.55, "lon_max": 16.65},
    "metodi_georef": {"gps": 3445, "catasto": 6700,
                      "ortofoto": 5000, "cartografia": 2089},
    "top_10_strade": [
      {"odo": "VIA LUCANA", "acc": 330},
      ...
    ]
  },
  "punti": [
    {"lat": 40.6712, "lon": 16.6041, "odo": "VIA LUCANA",
     "civ": "10", "esp": "A", "quota": 320, "met": 1},
    ...    # max 1000 stratificato
  ]
}

Usage:
  python -m etl.sources.anncsu --target=r2
  python -m etl.sources.anncsu --target=local --outdir=dist/anncsu
  python -m etl.sources.anncsu --regioni=BASI,PUGL  (subset test)
  python -m etl.sources.anncsu --skip-download (riusa cache /tmp esistente)
"""

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import r2

log = structlog.get_logger()

ETL_VERSION = "0.1.0"
SOURCE_LABEL = "ANNCSU - Agenzia delle Entrate + Istat"

# 20 codici regionali ufficiali ANNCSU
REGIONS = [
    "ABRU", "BASI", "CALA", "CAMP", "EMIL", "FRIU", "LAZI", "LIGU",
    "LOMB", "MARC", "MOLI", "PIEM", "PUGL", "SARD", "SICI", "TOSC",
    "TREN", "UMBR", "VALL", "VENE",
]

BASE_URL = "https://anncsu.open.agenziaentrate.gov.it/age-inspire/opendata/anncsu/getds.php"
REFERER = "https://www.anncsu.gov.it/it/consultazione-dellarchivio/open-data/Accedi-ai-servizi-di-dowload-massivo-in-Open-data/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

CACHE_DIR = Path("/tmp/cruscotto-anncsu-cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_SLEEP_SEC = 1.0       # Sleep tra request (benchmark: 0 errori a 1s)
DOWNLOAD_TIMEOUT_SEC = 120
SAMPLE_PER_COMUNE = 1000       # Sample stratificato per mappa
TOP_STRADE_LIMIT = 10

# Metodi georef ANNCSU (codifica ufficiale 1-4 da DPCM 12/05/2016;
# valore 5 osservato nei dati reali, non documentato — probabilmente
# introdotto dopo l'attivazione dell'archivio)
METODO_LABEL = {
    "1": "gps",
    "2": "catasto",
    "3": "ortofoto",
    "4": "cartografia",
    "5": "altro",
}


# ----------------------------------------------------------------------
# FASE 1 - DOWNLOAD
# ----------------------------------------------------------------------

def _r2_latest_key(kind: str, region: str) -> str:
    return f"raw/anncsu/{kind}_{region}_latest.zip"


def _md5_file(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _r2_md5(key: str) -> str | None:
    """ETag R2 == md5 per upload single-part (i nostri ZIP <50MB lo sono)."""
    meta = r2.head(key)
    if meta is None:
        return None
    return (meta.get("ETag") or "").strip('"').lower()


def download_one(kind: str, region: str, push_r2: bool = True) -> Path:
    """Scarica un singolo file ZIP.

    Strategia:
      1. Se cache locale esiste e md5 matcha R2 _latest → skip
      2. Altrimenti GET endpoint, salva in cache locale
      3. Se push_r2: upload _latest.zip su R2 (overwrite)
    """
    local_path = CACHE_DIR / f"{kind}_{region}.zip"
    r2_key = _r2_latest_key(kind, region)

    # Step 1: cache locale + R2 match?
    if local_path.exists() and push_r2:
        local_md5 = _md5_file(local_path)
        remote_md5 = _r2_md5(r2_key)
        if remote_md5 and local_md5 == remote_md5:
            log.info("anncsu_skip_cached", kind=kind, region=region,
                     size=local_path.stat().st_size)
            return local_path

    # Step 2: download
    url = f"{BASE_URL}?{kind}_{region}"
    log.info("anncsu_downloading", kind=kind, region=region, url=url)
    t0 = time.time()
    resp = requests.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT_SEC,
                        stream=True)
    resp.raise_for_status()
    with local_path.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    elapsed = time.time() - t0
    size = local_path.stat().st_size
    log.info("anncsu_downloaded", kind=kind, region=region,
             size=size, elapsed_s=round(elapsed, 1))

    # Sanity check: deve essere uno ZIP valido
    if not zipfile.is_zipfile(local_path):
        raise RuntimeError(
            f"Downloaded file is not a valid ZIP: {kind}_{region} "
            f"({size} bytes)"
        )

    # Step 3: push R2 _latest
    if push_r2:
        r2.upload_file(local_path, r2_key, content_type="application/zip")
        log.info("anncsu_pushed_r2", key=r2_key, size=size)

    return local_path


def download_all(regions: list[str], push_r2: bool = True) -> dict:
    """Scarica tutti i 2*N file ZIP, ritorna mapping a path locali."""
    paths: dict[tuple[str, str], Path] = {}
    t0 = time.time()
    total = len(regions) * 2
    done = 0

    for region in regions:
        for kind in ("STRAD", "INDIR"):
            try:
                paths[(kind, region)] = download_one(kind, region, push_r2=push_r2)
            except Exception as e:
                log.error("anncsu_download_failed", kind=kind, region=region,
                          error=str(e))
                raise
            done += 1
            if done < total:
                time.sleep(DOWNLOAD_SLEEP_SEC)

    elapsed = time.time() - t0
    log.info("anncsu_download_phase_done", files=len(paths),
             elapsed_s=round(elapsed, 1))
    return paths


# ----------------------------------------------------------------------
# FASE 2 - PARSE STRAD (stradari)
# ----------------------------------------------------------------------

def _read_csv_from_zip(zip_path: Path) -> tuple[str, list[dict]]:
    """Estrae il singolo CSV dello ZIP e lo legge come list of dict.

    Returns:
        (csv_filename, rows)  -- csv_filename utile per estrarre snapshot date.
    """
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist()
                 if i.filename.lower().endswith(".csv")]
        if len(infos) != 1:
            raise RuntimeError(
                f"Expected exactly 1 CSV in {zip_path.name}, "
                f"found {len(infos)}: {[i.filename for i in infos]}"
            )
        info = infos[0]
        with zf.open(info.filename) as fh:
            text = fh.read().decode("utf-8", errors="replace")

    # csv.DictReader gestisce il trailing ; correttamente
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    rows = list(reader)
    return info.filename, rows


def _extract_snapshot_date(csv_filename: str) -> str:
    """Da 'STRAD_BASI_20260504.csv' → '2026-05-04'."""
    # Pattern: <KIND>_<REG>_YYYYMMDD.csv
    parts = csv_filename.replace(".csv", "").split("_")
    for p in parts:
        if len(p) == 8 and p.isdigit():
            return f"{p[:4]}-{p[4:6]}-{p[6:8]}"
    return ""


def load_canonical_istat() -> set[str]:
    """Carica il set di tutti gli ISTAT validi (~7896) dal bundle R2.

    Usato per filtrare ghost ISTAT presenti in ANNCSU ma soppressi/fusi
    (es. 047023 Vergemoli fuso nel 2014 in Fabbriche di Vergemoli).
    """
    log.info("anncsu_loading_canonical_istat")
    client = r2.get_r2_client()
    obj = client.get_object(Bucket=r2.get_bucket(),
                            Key="lookup/comuni-bundle.json")
    bundle = json.loads(obj["Body"].read())["comuni"]
    canonical = set(bundle.keys())
    log.info("anncsu_canonical_loaded", n_comuni=len(canonical))
    return canonical


def _is_bilingual_odonimo(odo: str) -> bool:
    """Detect odonimi bilingui dal pattern testuale dell'ODONIMO.

    ANNCSU non popola DIZIONE_LINGUA1/2 in modo uniforme:
    - Cogne (VALL) usa correttamente DIZIONE_LINGUA1 = francese
    - Bolzano (TREN) NON popola le colonne lingua: il bilinguismo
      tedesco è codificato direttamente nell'ODONIMO con separatore.

    Pattern bilingue tipici:
      "VIA ROMA / ROMSTRASSE"
      "VIA ROMA - ROMSTRASSE"
      "PIAZZA SAN MARCO/SAN MARCOPLATZ"
    """
    if not odo or len(odo) < 8:
        return False
    # Separator ' - ' o ' / ' o '/' standalone (con almeno 3 char per parte)
    for sep in (" - ", " / ", "/"):
        if sep in odo:
            parts = [p.strip() for p in odo.split(sep) if p.strip()]
            if len(parts) >= 2 and all(len(p) >= 3 for p in parts):
                return True
    return False


def parse_strad_region(zip_path: Path) -> tuple[dict, str]:
    """Parsa lo stradario regionale.

    Returns:
        (per_istat: dict[istat → strad_data], snapshot_date)
    """
    csv_fname, rows = _read_csv_from_zip(zip_path)
    snapshot = _extract_snapshot_date(csv_fname)

    # Aggregazione per CODICE_ISTAT
    # per_istat[istat] = {
    #     "n_strade": int,
    #     "n_strade_bilingui": int,
    #     "top_strade_acc": [(odonimo, accessi), ...],   # ordinato desc, top N
    #     "strade_by_prog": {progressivo_nazionale: odonimo},  # per join indir
    # }
    per_istat: dict[str, dict] = defaultdict(lambda: {
        "n_strade": 0,
        "n_strade_bilingui": 0,
        "top_strade_acc": [],
        "strade_by_prog": {},
    })

    for row in rows:
        istat = (row.get("CODICE_ISTAT") or "").strip()
        if not istat or len(istat) != 6:
            continue
        odo = (row.get("ODONIMO") or "").strip()
        if not odo:
            continue
        prog = (row.get("PROGRESSIVO_NAZIONALE") or "").strip()
        try:
            acc = int(row.get("TOTALE_ACCESSI") or "0")
        except (ValueError, TypeError):
            acc = 0
        ling1 = (row.get("DIZIONE_LINGUA1") or "").strip()
        ling2 = (row.get("DIZIONE_LINGUA2") or "").strip()
        # Bilingue se ha LINGUA1/2 popolata (Cogne, Friuli) O se l'ODONIMO
        # contiene un separatore bilingue (Bolzano, Glorenza)
        is_bilingual = bool(ling1 or ling2) or _is_bilingual_odonimo(odo)

        d = per_istat[istat]
        d["n_strade"] += 1
        if is_bilingual:
            d["n_strade_bilingui"] += 1
        d["top_strade_acc"].append((odo, acc))
        if prog:
            d["strade_by_prog"][prog] = odo

    # Tronca top_strade
    for _istat, d in per_istat.items():
        d["top_strade_acc"].sort(key=lambda t: t[1], reverse=True)
        d["top_strade_acc"] = d["top_strade_acc"][:TOP_STRADE_LIMIT]

    return dict(per_istat), snapshot


def parse_strad_all(strad_paths: dict[str, Path]) -> tuple[dict, str]:
    """Aggrega tutti gli stradari regionali in un unico dict per ISTAT.

    Args:
        strad_paths: {region_code: zip_path}

    Returns:
        (per_istat: dict[istat → strad_data], snapshot_date_max)
    """
    merged: dict[str, dict] = {}
    snapshot_max = ""
    for region, path in strad_paths.items():
        log.info("anncsu_parse_strad", region=region, file=path.name)
        per_istat, snap = parse_strad_region(path)
        if snap > snapshot_max:
            snapshot_max = snap
        for istat, data in per_istat.items():
            if istat in merged:
                # Edge case: regione duplicata? Non dovrebbe succedere
                # ma somma in difensiva
                merged[istat]["n_strade"] += data["n_strade"]
                merged[istat]["n_strade_bilingui"] += data["n_strade_bilingui"]
                merged[istat]["top_strade_acc"] = sorted(
                    merged[istat]["top_strade_acc"] + data["top_strade_acc"],
                    key=lambda t: t[1], reverse=True
                )[:TOP_STRADE_LIMIT]
                merged[istat]["strade_by_prog"].update(data["strade_by_prog"])
            else:
                merged[istat] = data
    log.info("anncsu_strad_merged", istat_count=len(merged),
             snapshot=snapshot_max)
    return merged, snapshot_max


# ----------------------------------------------------------------------
# FASE 3 - PARSE INDIR (indirizzari)
# ----------------------------------------------------------------------

def _parse_coord(s: str) -> float | None:
    """Converte coordinata ANNCSU (es. '15,810056') → float o None.

    None se vuoto, 0, o non parsabile.
    """
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
        # Filtro coordinate fasulle (es. 0.0)
        if v == 0.0:
            return None
        return v
    except ValueError:
        return None


def parse_indir_region(zip_path: Path,
                       strade_by_prog_by_istat: dict[str, dict[str, str]]
                       ) -> dict:
    """Parsa l'indirizzario regionale e aggrega per ISTAT.

    Args:
        zip_path: zip dell'indirizzario regionale
        strade_by_prog_by_istat: {istat: {progressivo_naz: odonimo}}
            usato per arricchire i punti con il nome via senza riparser STRAD.

    Returns:
        dict[istat → indir_data]:
          {
            "n_civici": int,
            "n_civici_geo_ref": int,
            "n_civici_metrici": int,
            "n_civici_rosso": int,
            "n_civici_nero": int,
            "n_civici_bis": int,    # con esponente non vuoto
            "quote": [float, ...],  # per stats dopo merge
            "punti_geo": [          # max SAMPLE_PER_COMUNE per istat (raccolto qui poi sampled in build_shards)
              {"lat":..., "lon":..., "odo":..., "civ":...,
               "esp":..., "quota":..., "met":int}
            ],
            "bbox": (lat_min, lat_max, lon_min, lon_max),
            "metodi": {"1": int, "2": int, "3": int, "4": int}
          }
    """
    _csv_fname, rows = _read_csv_from_zip(zip_path)

    per_istat: dict[str, dict] = defaultdict(lambda: {
        "n_civici": 0,
        "n_civici_geo_ref": 0,
        "n_civici_metrici": 0,
        "n_civici_rosso": 0,
        "n_civici_nero": 0,
        "n_civici_bis": 0,
        "quote": [],
        "punti_geo": [],
        "lat_min": None, "lat_max": None,
        "lon_min": None, "lon_max": None,
        "metodi": defaultdict(int),
    })

    for row in rows:
        istat = (row.get("CODICE_ISTAT") or "").strip()
        if not istat or len(istat) != 6:
            continue
        d = per_istat[istat]
        d["n_civici"] += 1

        civico = (row.get("CIVICO") or "").strip()
        esponente = (row.get("ESPONENTE") or "").strip()
        specificita = (row.get("SPECIFICITA") or "").strip().upper()
        metrico = (row.get("METRICO") or "").strip()
        metodo = (row.get("METODO") or "").strip()

        if esponente:
            d["n_civici_bis"] += 1
        # SPECIFICITA: nei dati reali ANNCSU è codificata come 'N'/'R'
        # (singola lettera), non 'NERO'/'ROSSO' come da metadata storica
        if specificita in ("ROSSO", "R"):
            d["n_civici_rosso"] += 1
        elif specificita in ("NERO", "N"):
            d["n_civici_nero"] += 1
        if metrico and metrico != "0":
            d["n_civici_metrici"] += 1
        if metodo:
            d["metodi"][metodo] += 1

        lon = _parse_coord(row.get("COORD_X_COMUNE", ""))
        lat = _parse_coord(row.get("COORD_Y_COMUNE", ""))
        if lat is not None and lon is not None:
            d["n_civici_geo_ref"] += 1
            # Bbox update
            if d["lat_min"] is None or lat < d["lat_min"]:
                d["lat_min"] = lat
            if d["lat_max"] is None or lat > d["lat_max"]:
                d["lat_max"] = lat
            if d["lon_min"] is None or lon < d["lon_min"]:
                d["lon_min"] = lon
            if d["lon_max"] is None or lon > d["lon_max"]:
                d["lon_max"] = lon

            # Quota
            quota_raw = (row.get("QUOTA") or "").strip().replace(",", ".")
            try:
                quota = float(quota_raw) if quota_raw else None
                if quota is not None and quota != 0.0:
                    d["quote"].append(quota)
            except ValueError:
                quota = None

            # Punto per la mappa: odonimo via lookup STRAD
            prog = (row.get("PROGRESSIVO_NAZIONALE") or "").strip()
            odo = strade_by_prog_by_istat.get(istat, {}).get(prog, "")
            try:
                met_int = int(metodo) if metodo else 0
            except ValueError:
                met_int = 0
            d["punti_geo"].append({
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "odo": odo,
                "civ": civico,
                "esp": esponente,
                "quota": int(quota) if quota else None,
                "met": met_int,
            })

    return dict(per_istat)


def parse_indir_all(indir_paths: dict[str, Path],
                    strad_data: dict[str, dict]) -> dict:
    """Aggrega tutti gli indirizzari regionali.

    Args:
        indir_paths: {region_code: zip_path}
        strad_data: output di parse_strad_all (per lookup odonimi)

    Returns:
        dict[istat → indir_data] aggregato nazionale
    """
    # Estrai dict di lookup strade da strad_data
    strade_lookup: dict[str, dict[str, str]] = {
        istat: d.get("strade_by_prog", {}) for istat, d in strad_data.items()
    }

    merged: dict[str, dict] = {}
    for region, path in indir_paths.items():
        log.info("anncsu_parse_indir", region=region, file=path.name)
        per_istat = parse_indir_region(path, strade_lookup)
        for istat, data in per_istat.items():
            if istat in merged:
                # Aggrega (caso teorico, non dovrebbe succedere)
                m = merged[istat]
                m["n_civici"] += data["n_civici"]
                m["n_civici_geo_ref"] += data["n_civici_geo_ref"]
                m["n_civici_metrici"] += data["n_civici_metrici"]
                m["n_civici_rosso"] += data["n_civici_rosso"]
                m["n_civici_nero"] += data["n_civici_nero"]
                m["n_civici_bis"] += data["n_civici_bis"]
                m["quote"].extend(data["quote"])
                m["punti_geo"].extend(data["punti_geo"])
                for k, v in data["metodi"].items():
                    m["metodi"][k] += v
                # bbox merge
                for axis in ["lat_min", "lon_min"]:
                    if data[axis] is not None:
                        if m[axis] is None or data[axis] < m[axis]:
                            m[axis] = data[axis]
                for axis in ["lat_max", "lon_max"]:
                    if data[axis] is not None:
                        if m[axis] is None or data[axis] > m[axis]:
                            m[axis] = data[axis]
            else:
                merged[istat] = data

    log.info("anncsu_indir_merged", istat_count=len(merged))
    return merged


# ----------------------------------------------------------------------
# FASE 4 - BUILD SHARDS
# ----------------------------------------------------------------------

def _sample_punti(punti: list[dict], n: int = SAMPLE_PER_COMUNE,
                  seed: int = 42) -> list[dict]:
    """Campionamento random ma deterministico (seed) se >n punti."""
    if len(punti) <= n:
        return punti
    rng = random.Random(seed)
    return rng.sample(punti, n)


def _build_kpi(strad: dict, indir: dict) -> dict:
    """Costruisce il dict 'kpi' del singolo shard."""
    kpi: dict = {}

    # Strade
    kpi["n_strade"] = strad.get("n_strade", 0)
    if strad.get("n_strade_bilingui", 0) > 0:
        kpi["n_strade_bilingui"] = strad["n_strade_bilingui"]

    # Civici
    kpi["n_civici"] = indir.get("n_civici", 0)
    kpi["n_civici_geo_ref"] = indir.get("n_civici_geo_ref", 0)
    if kpi["n_civici"] > 0:
        kpi["pct_geo_ref"] = round(
            100.0 * kpi["n_civici_geo_ref"] / kpi["n_civici"], 1
        )
    else:
        kpi["pct_geo_ref"] = 0.0

    # Condizionali (omessi se 0)
    for key, source_key in [
        ("n_civici_metrici", "n_civici_metrici"),
        ("n_civici_rosso",   "n_civici_rosso"),
        ("n_civici_nero",    "n_civici_nero"),
    ]:
        v = indir.get(source_key, 0)
        if v > 0:
            kpi[key] = v

    # Bis (sempre presente)
    kpi["n_civici_bis"] = indir.get("n_civici_bis", 0)

    # Quota stats
    quote = indir.get("quote", [])
    if quote:
        kpi["quota_min_m"] = int(min(quote))
        kpi["quota_max_m"] = int(max(quote))
        kpi["quota_mean_m"] = round(sum(quote) / len(quote), 1)

    # Bbox
    if all(indir.get(k) is not None for k in ("lat_min", "lat_max",
                                              "lon_min", "lon_max")):
        kpi["bbox"] = {
            "lat_min": round(indir["lat_min"], 5),
            "lat_max": round(indir["lat_max"], 5),
            "lon_min": round(indir["lon_min"], 5),
            "lon_max": round(indir["lon_max"], 5),
        }

    # Metodi georef
    metodi = indir.get("metodi", {})
    metodi_label: dict = {}
    for code, n in metodi.items():
        label = METODO_LABEL.get(str(code), f"metodo_{code}")
        metodi_label[label] = metodi_label.get(label, 0) + int(n)
    if metodi_label:
        kpi["metodi_georef"] = metodi_label

    # Top 10 strade
    top = strad.get("top_strade_acc", [])
    if top:
        kpi["top_10_strade"] = [
            {"odo": odo, "acc": acc} for odo, acc in top
        ]

    return kpi


def build_shard(istat: str, strad: dict, indir: dict,
                snapshot_date: str) -> dict:
    """Costruisce il payload finale di un singolo shard."""
    kpi = _build_kpi(strad, indir)
    punti = _sample_punti(indir.get("punti_geo", []),
                          n=SAMPLE_PER_COMUNE)
    return {
        "_etl_version": ETL_VERSION,
        "_source": SOURCE_LABEL,
        "_snapshot_date": snapshot_date,
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "kpi": kpi,
        "punti": punti,
    }


def build_all_shards(strad_data: dict, indir_data: dict,
                     snapshot_date: str, out_dir: Path,
                     canonical_istat: set[str] | None = None) -> int:
    """Genera tutti i file shard locali. Ritorna numero scritti.

    Args:
        canonical_istat: se passato, filtra gli ISTAT che non sono nella
            lookup canonica (ghost da fusioni/soppressioni storiche).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Unione istat: STRAD U INDIR (un comune potrebbe teoricamente avere
    # solo strade ma 0 civici geo-ref)
    all_istat = set(strad_data.keys()) | set(indir_data.keys())

    # Filtro ghost (ISTAT soppressi non più in anagrafica canonica)
    if canonical_istat is not None:
        ghost = all_istat - canonical_istat
        if ghost:
            log.info("anncsu_ghost_istat_excluded",
                     count=len(ghost),
                     sample=sorted(ghost)[:10])
        all_istat = all_istat & canonical_istat

    log.info("anncsu_build_shards_start", istat_count=len(all_istat),
             out_dir=str(out_dir))

    written = 0
    for istat in sorted(all_istat):
        strad = strad_data.get(istat, {})
        indir = indir_data.get(istat, {})
        payload = build_shard(istat, strad, indir, snapshot_date)
        path = out_dir / f"{istat}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        written += 1
        if written % 500 == 0:
            log.info("anncsu_build_shards_progress", done=written,
                     total=len(all_istat))

    log.info("anncsu_build_shards_done", written=written)
    return written


# ----------------------------------------------------------------------
# FASE 4b - FULL SHARDS (opzione C: tutti i civici geo-ref, no sample)
# ----------------------------------------------------------------------
# Genera shard pesanti su prefisso anncsu_full/<istat>.json contenenti
# TUTTI i civici geo-ref del comune (non sample 1000). Lecce ~3.7MB JSON
# crudo, ~0.4MB gzip. Roma stimata ~50MB crudo, ~4.3MB gzip. Italia
# totale stimata ~0.23GB gzippato su R2.
#
# Schema slim per minimizzare peso:
#   {
#     "_etl_version": "0.1.0-fase-c",
#     "_source": "MEF DE - ANNCSU full",
#     "_snapshot_date": "YYYY-MM-DD",
#     "_generated_at": "ISO-8601",
#     "kpi": { ... }  # stessi KPI dello shard sample per backward compat
#     "punti": [      # TUTTI i civici geo-ref (no sample)
#       {"lat":..., "lon":..., "civ":..., "esp":..., "odo":...,
#        "met":int, "quota": int|null}
#     ]
#   }

def build_full_shard(istat: str, strad: dict, indir: dict,
                     snapshot_date: str) -> dict:
    """Costruisce shard FULL con TUTTI i civici geo-ref (no sample)."""
    kpi = _build_kpi(strad, indir)
    punti = indir.get("punti_geo", [])
    return {
        "_etl_version": "0.1.0-fase-c",
        "_source": SOURCE_LABEL + " (full)",
        "_snapshot_date": snapshot_date,
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_full": True,
        "kpi": kpi,
        "punti": punti,
    }


def build_all_full_shards(strad_data: dict, indir_data: dict,
                          snapshot_date: str, out_dir: Path,
                          canonical_istat: set[str] | None = None) -> int:
    """Genera shard FULL nel directory locale. Pattern identico a build_all_shards."""
    out_dir.mkdir(parents=True, exist_ok=True)
    all_istat = set(strad_data.keys()) | set(indir_data.keys())
    if canonical_istat is not None:
        ghost = all_istat - canonical_istat
        if ghost:
            log.info("anncsu_full_ghost_istat_excluded",
                     count=len(ghost), sample=sorted(ghost)[:10])
        all_istat = all_istat & canonical_istat

    log.info("anncsu_full_build_shards_start",
             istat_count=len(all_istat), out_dir=str(out_dir))
    written = 0
    skipped_no_geo = 0
    for istat in sorted(all_istat):
        strad = strad_data.get(istat, {})
        indir = indir_data.get(istat, {})
        # Skip comuni senza punti geo-ref (es. Aosta) per non sprecare R2
        punti = indir.get("punti_geo", [])
        if not punti:
            skipped_no_geo += 1
            continue
        payload = build_full_shard(istat, strad, indir, snapshot_date)
        path = out_dir / f"{istat}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        written += 1
        if written % 500 == 0:
            log.info("anncsu_full_build_shards_progress",
                     done=written, total=len(all_istat))

    log.info("anncsu_full_build_shards_done",
             written=written, skipped_no_geo=skipped_no_geo)
    return written


# ----------------------------------------------------------------------
# FASE 5 - PUSH R2 (pattern aria.py)
# ----------------------------------------------------------------------

def push_shards_to_r2(shard_dir: Path,
                      force_upload: bool = False) -> dict:
    """Push paralleli con skip via md5/ETag su prefix anncsu/."""
    if not shard_dir.exists():
        log.warning("anncsu_no_shard_dir_to_push", path=str(shard_dir))
        return {"uploaded": 0, "unchanged": 0, "errors": 0}

    import boto3 as _b3
    _client = _b3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )

    shard_files = sorted(shard_dir.glob("*.json"))

    remote_etag: dict[str, str] = {}
    try:
        _pag = _client.get_paginator("list_objects_v2")
        for _page in _pag.paginate(Bucket=r2.get_bucket(), Prefix="anncsu/"):
            for _o in _page.get("Contents", []):
                name = _o["Key"].split("/")[-1]
                etag = (_o.get("ETag") or "").strip('"').lower()
                remote_etag[name] = etag
        log.info("anncsu_shard_remote_listed", count=len(remote_etag))
    except Exception as e:
        log.warning("anncsu_shard_list_failed", error=str(e))

    to_upload: list[Path] = []
    if force_upload:
        to_upload = list(shard_files)
        log.info("anncsu_force_upload", count=len(to_upload))
    else:
        n_same = 0
        for sf in shard_files:
            rmd5 = remote_etag.get(sf.name)
            if rmd5 is None or _md5_file(sf) != rmd5:
                to_upload.append(sf)
            else:
                n_same += 1
        log.info("anncsu_md5_compared",
                 total=len(shard_files), unchanged=n_same,
                 to_upload=len(to_upload))

    def _upload_one(sf: Path) -> str:
        r2.upload_file(sf, f"anncsu/{sf.name}",
                       content_type="application/json")
        return sf.name

    uploaded = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
        for f in as_completed(futures):
            try:
                f.result()
                uploaded += 1
                if uploaded % 200 == 0:
                    log.info("anncsu_push_progress",
                             uploaded=uploaded, total=len(to_upload))
            except Exception as e:
                errors += 1
                log.error("anncsu_upload_failed", error=str(e))

    log.info("anncsu_push_done",
             uploaded=uploaded, unchanged=len(shard_files) - len(to_upload),
             errors=errors)
    return {
        "uploaded": uploaded,
        "unchanged": len(shard_files) - len(to_upload),
        "errors": errors,
    }


def push_full_shards_to_r2(shard_dir: Path,
                           force_upload: bool = False) -> dict:
    """Push paralleli su prefix anncsu_full/. Stesso pattern di push_shards_to_r2."""
    if not shard_dir.exists():
        log.warning("anncsu_full_no_shard_dir_to_push", path=str(shard_dir))
        return {"uploaded": 0, "unchanged": 0, "errors": 0}

    import boto3 as _b3
    _client = _b3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )

    shard_files = sorted(shard_dir.glob("*.json"))

    remote_etag: dict[str, str] = {}
    try:
        _pag = _client.get_paginator("list_objects_v2")
        for _page in _pag.paginate(Bucket=r2.get_bucket(), Prefix="anncsu_full/"):
            for _o in _page.get("Contents", []):
                name = _o["Key"].split("/")[-1]
                etag = (_o.get("ETag") or "").strip('"').lower()
                remote_etag[name] = etag
        log.info("anncsu_full_shard_remote_listed", count=len(remote_etag))
    except Exception as e:
        log.warning("anncsu_full_shard_list_failed", error=str(e))

    to_upload: list[Path] = []
    if force_upload:
        to_upload = list(shard_files)
        log.info("anncsu_full_force_upload", count=len(to_upload))
    else:
        n_same = 0
        for sf in shard_files:
            rmd5 = remote_etag.get(sf.name)
            if rmd5 is None or _md5_file(sf) != rmd5:
                to_upload.append(sf)
            else:
                n_same += 1
        log.info("anncsu_full_md5_compared",
                 total=len(shard_files), unchanged=n_same,
                 to_upload=len(to_upload))

    def _upload_one(sf: Path) -> str:
        r2.upload_file(sf, f"anncsu_full/{sf.name}",
                       content_type="application/json")
        return sf.name

    uploaded = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
        for f in as_completed(futures):
            try:
                f.result()
                uploaded += 1
                if uploaded % 200 == 0:
                    log.info("anncsu_full_push_progress",
                             uploaded=uploaded, total=len(to_upload))
            except Exception as e:
                errors += 1
                log.error("anncsu_full_upload_failed", error=str(e))

    log.info("anncsu_full_push_done",
             uploaded=uploaded, unchanged=len(shard_files) - len(to_upload),
             errors=errors)
    return {
        "uploaded": uploaded,
        "unchanged": len(shard_files) - len(to_upload),
        "errors": errors,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="ETL ANNCSU (toponomastica nazionale)")
    ap.add_argument("--target", choices=["local", "r2"], default="local",
                    help="local: scrive solo in --outdir; r2: scrive shard + push R2")
    ap.add_argument("--outdir", default="/var/www/cruscotto-italia/data/anncsu",
                    help="Directory shard locali (default: /var/www/cruscotto-italia/data/anncsu)")
    ap.add_argument("--regioni", default="",
                    help="Sottoinsieme regioni (CSV, es. BASI,PUGL). Default: tutte 20.")
    ap.add_argument("--skip-download", action="store_true",
                    help="Riusa cache locale /tmp esistente (non scarica)")
    ap.add_argument("--no-r2-cache", action="store_true",
                    help="Non pusha gli ZIP raw su R2 durante il download")
    ap.add_argument("--force-upload", action="store_true",
                    help="Push R2 di tutti gli shard senza md5 check")
    ap.add_argument("--no-canonical-filter", action="store_true",
                    help="Disabilita il filtro contro lookup/comuni-bundle.json "
                         "(rischia di scrivere shard per ISTAT soppressi)")
    ap.add_argument("--full-shards", action="store_true",
                    help="Genera anche shard FULL (anncsu_full/<istat>.json) "
                         "con tutti i civici geo-ref, non solo sample 1000. "
                         "Output peso: ~0.23GB gzippato totale nazionale.")
    ap.add_argument("--skip-sample", action="store_true",
                    help="Non genera/pusha gli shard sample (anncsu/). Utile "
                         "se --full-shards e gli sample sono già su R2.")
    ap.add_argument("--full-outdir", default="/var/www/cruscotto-italia/data/anncsu_full",
                    help="Directory shard FULL locali (default: dist/anncsu_full)")
    args = ap.parse_args()

    if args.regioni:
        regions = [r.strip().upper() for r in args.regioni.split(",")]
        invalid = [r for r in regions if r not in REGIONS]
        if invalid:
            log.error("invalid_regions", invalid=invalid,
                      valid=REGIONS)
            return 1
    else:
        regions = REGIONS

    log.info("anncsu_etl_start",
             regions=regions, target=args.target,
             skip_download=args.skip_download)
    t_start = time.time()

    # FASE 1: download
    if args.skip_download:
        paths = {}
        for region in regions:
            for kind in ("STRAD", "INDIR"):
                p = CACHE_DIR / f"{kind}_{region}.zip"
                if not p.exists():
                    log.error("anncsu_cache_missing", kind=kind, region=region,
                              path=str(p))
                    return 1
                paths[(kind, region)] = p
        log.info("anncsu_cache_reused", files=len(paths))
    else:
        paths = download_all(regions, push_r2=(not args.no_r2_cache))

    strad_paths = {reg: p for (k, reg), p in paths.items() if k == "STRAD"}
    indir_paths = {reg: p for (k, reg), p in paths.items() if k == "INDIR"}

    # FASE 2: parse STRAD
    log.info("anncsu_parse_strad_start", files=len(strad_paths))
    t0 = time.time()
    strad_data, snapshot = parse_strad_all(strad_paths)
    log.info("anncsu_parse_strad_done",
             elapsed_s=round(time.time() - t0, 1),
             istat=len(strad_data))

    # FASE 3: parse INDIR
    log.info("anncsu_parse_indir_start", files=len(indir_paths))
    t0 = time.time()
    indir_data = parse_indir_all(indir_paths, strad_data)
    log.info("anncsu_parse_indir_done",
             elapsed_s=round(time.time() - t0, 1),
             istat=len(indir_data))

    # FASE 4: build shard
    canonical = None
    if not args.no_canonical_filter:
        try:
            canonical = load_canonical_istat()
        except Exception as e:
            log.warning("anncsu_canonical_load_failed",
                        error=str(e),
                        note="proceeding without ghost ISTAT filter")

    n_written_sample = 0
    n_written_full = 0

    # FASE 4a: shard SAMPLE (anncsu/) — skippabile via --skip-sample
    if not args.skip_sample:
        out_dir = Path(args.outdir)
        n_written_sample = build_all_shards(strad_data, indir_data, snapshot,
                                            out_dir, canonical_istat=canonical)

    # FASE 4b: shard FULL (anncsu_full/) — solo se --full-shards
    if args.full_shards:
        full_out_dir = Path(args.full_outdir)
        n_written_full = build_all_full_shards(strad_data, indir_data, snapshot,
                                               full_out_dir,
                                               canonical_istat=canonical)

    # FASE 5: push R2
    if args.target == "r2":
        if not args.skip_sample:
            result = push_shards_to_r2(Path(args.outdir),
                                       force_upload=args.force_upload)
            log.info("anncsu_r2_push_sample_result", **result)
        if args.full_shards:
            result_full = push_full_shards_to_r2(Path(args.full_outdir),
                                                  force_upload=args.force_upload)
            log.info("anncsu_r2_push_full_result", **result_full)

    elapsed = time.time() - t_start
    log.info("anncsu_etl_done", elapsed_s=round(elapsed, 1),
             shards_sample=n_written_sample,
             shards_full=n_written_full,
             snapshot=snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
