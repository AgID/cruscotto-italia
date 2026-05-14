"""ETL AGCOM Broadband Map — copertura banda larga per comune.

Sorgente
--------
AGCOM (Autorità per le Garanzie nelle Comunicazioni) — BBmap, sistema di
mappatura nazionale delle reti di accesso a Internet ex art. 22 Codice
delle Comunicazioni Elettroniche.

Pagina indice "AI-Ready": https://geo.agcom.it/reportistica/ai/index.html

Acquisizione
------------
Singolo CSV nazionale a livello comunale, scaricabile da ArcGIS sharing:

  https://geo.agcom.it/arcgis/sharing/rest/content/items/
    6c0b48a9a06c44059656b987d85acb63/data

Aggiornamento trimestrale. Dato corrente al 31/12/2025, rilascio 10/02/2026.
Encoding effettivo CP1252 (la pagina dichiara UTF-8 ma il file è Latin-1).
Separatore ';'. 7896 righe = 7896/7896 comuni italiani (100% coverage).
La chiave 'pro_com' è il codice ISTAT comune (può avere 4/5/6 cifre, va
pad-left a 6).

Licenza
-------
CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) — principio "open data by
default": dati PA senza licenza espressa = CC-BY 4.0 con attribuzione al
titolare (AGCOM). Riferimenti AGCOM: https://www.agcom.it/termini-e-condizioni

Schema agcom_bbmap/<istat>.json
-------------------------------
{
  "_etl_version": "0.1.0",
  "_source": "AGCOM - Broadband Map",
  "_source_url": "https://geo.agcom.it/reportistica/",
  "_license": "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)",
  "_data_period": "31/12/2025",
  "_generated_at": "ISO-8601",
  "kpi": {
    "famiglie_residenti": int,
    "famiglie_ftth": int,
    "famiglie_ftth_20m": int,
    "copertura_ftth_desi_pct": float,    # %
    "copertura_ftth_20m_pct": float,     # %
    "confidenza_desi_pct": float,        # %
    "celle_20m_raggiunte": int,
    "celle_20m_ftth": int,
    "celle_20m_fttc": int,
    "punti_dichiarati": int,
    "punti_dichiarati_ftth": int,
    "punti_geo_distinti": int,
    "punti_geo_distinti_ftth": int,
    "indirizzi_postali_distinti": int,
    "indirizzi_postali_distinti_ftth": int
  },
  "mappa_ufficiale": {
    "url": "https://geo.agcom.it/agcomapps/BB4/...?center=lon,lat&level=14",
    "level": 14
  }
}

Nota: lo shard NON include geometria. La rete dettagliata (polilinee
strade FTTH/rame) è esposta dal FeatureServer ArcGIS ufficiale AGCOM,
e nella tab "Connettività" del frontend viene linkata come deep-link
alla mappa ufficiale AGCOM. Il volume del FeatureServer (192k+ segmenti
solo per Roma) rende impraticabile l'ingestion massiva.

Uso
---
  python -m etl.sources.agcom_bbmap --target=local
  python -m etl.sources.agcom_bbmap --target=r2
  python -m etl.sources.agcom_bbmap --target=r2 --force

Cadenza: trimestrale (allinearsi al rilascio AGCOM, tipicamente fine
quartile + 30-45 giorni). Skip automatico se hash del CSV è invariato
rispetto all'ultima run (persistito in agcom_bbmap/_meta.json su R2).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "AGCOM - Broadband Map"
SOURCE_URL = "https://geo.agcom.it/reportistica/"
LICENSE_STR = "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)"

# ArcGIS sharing item id del CSV comunale (versione 31/12/2025 → 10/02/2026)
AGCOM_CSV_URL = (
    "https://geo.agcom.it/arcgis/sharing/rest/content/items/"
    "6c0b48a9a06c44059656b987d85acb63/data"
)
DATA_PERIOD = "31/12/2025"   # aggiornare ad ogni rilascio trimestrale

# Mappa ufficiale AGCOM (Web AppBuilder 2.15)
# Pattern: ?center=lon,lat&level=N (WGS84, level 13-15 per scala comunale)
AGCOM_MAP_BASE = (
    "https://geo.agcom.it/agcomapps/BB4/BB4_BBwired_na_app16_4/"
)

# R2 destinazione
R2_PREFIX = "agcom_bbmap"   # → R2: agcom_bbmap/<istat>.json
R2_META_KEY = "agcom_bbmap/_meta.json"

# Output locale
DATA_DIR = Path("data/agcom_bbmap")
CACHE_DIR = Path(".cache/agcom_bbmap")

USER_AGENT = (
    "cruscotto-italia-etl/0.1 "
    "(+https://github.com/piersoft/cruscotto-italia)"
)

# Anagrafica ISTAT per ricavare centroide lat/lon (per deep-link mappa).
# Riutilizziamo gli shard 'anagrafica/<istat>.json' già presenti su R2
# quando target=r2; in target=local proviamo prima il file in
# data/anagrafica/, altrimenti fallback a None (deep-link senza zoom).
ANAGRAFICA_DIR_LOCAL = Path("data/anagrafica")
R2_ANAGRAFICA_PREFIX = "anagrafica"


# ──────────────────────── FETCH CSV AGCOM ──────────────────────────

def fetch_agcom_csv() -> tuple[bytes, str]:
    """Scarica il CSV AGCOM comunale.

    Returns (body_bytes, sha256_hex).
    """
    log.info("agcom_fetch_start", url=AGCOM_CSV_URL)
    r = requests.get(
        AGCOM_CSV_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"},
        timeout=120,
    )
    r.raise_for_status()
    body = r.content
    sha = hashlib.sha256(body).hexdigest()
    log.info("agcom_fetch_done", bytes=len(body), sha256=sha[:16])
    return body, sha


# ──────────────────────── PARSE CSV ────────────────────────────────

def _parse_pct(s: str) -> float | None:
    """'97%' -> 97.0, '' -> None."""
    if not s:
        return None
    s = s.strip().rstrip("%").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> int | None:
    """'1234' -> 1234, '' -> None."""
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", "")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_csv(body: bytes) -> list[dict]:
    """Parsa il CSV AGCOM (encoding CP1252, separatore ';').

    Header multiriga: i nomi delle colonne contengono '\\n', vengono
    normalizzati posizionalmente (19 colonne fisse).
    """
    # Encoding effettivo verificato sperimentalmente: CP1252
    try:
        text = body.decode("cp1252")
    except UnicodeDecodeError:
        text = body.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader)
    if len(header) != 19:
        log.warning("agcom_csv_unexpected_cols",
                    expected=19, got=len(header))
    rows: list[dict] = []
    skipped_no_procom = 0
    for raw in reader:
        if len(raw) < 19:
            # estendi con stringhe vuote per evitare IndexError
            raw = raw + [""] * (19 - len(raw))
        pro_com = raw[3].strip()
        if not pro_com:
            skipped_no_procom += 1
            continue
        # Pad-left a 6 cifre per match ISTAT canonico
        istat6 = pro_com.zfill(6)
        rows.append({
            "istat": istat6,
            "regione":  raw[0].strip(),
            "provincia": raw[1].strip(),
            "comune":   raw[2].strip(),
            "celle_20m_raggiunte":          _parse_int(raw[4]),
            "punti_dichiarati":             _parse_int(raw[5]),
            "punti_geo_distinti":           _parse_int(raw[6]),
            "indirizzi_postali_distinti":   _parse_int(raw[7]),
            "celle_20m_ftth":               _parse_int(raw[8]),
            "punti_dichiarati_ftth":        _parse_int(raw[9]),
            "punti_geo_distinti_ftth":      _parse_int(raw[10]),
            "indirizzi_postali_distinti_ftth": _parse_int(raw[11]),
            "celle_20m_fttc":               _parse_int(raw[12]),
            "famiglie_residenti":           _parse_int(raw[13]),
            "famiglie_ftth":                _parse_int(raw[14]),
            "famiglie_ftth_20m":            _parse_int(raw[15]),
            "copertura_ftth_desi_pct":      _parse_pct(raw[16]),
            "confidenza_desi_pct":          _parse_pct(raw[17]),
            "copertura_ftth_20m_pct":       _parse_pct(raw[18]),
        })
    log.info("agcom_csv_parsed",
             rows=len(rows), skipped_no_procom=skipped_no_procom)
    return rows


# ──────────────────────── CENTROIDE COMUNE ─────────────────────────

def load_anagrafica_centroids(target: str) -> dict[str, tuple[float, float]]:
    """Carica un mapping istat6 -> (lat, lon) dal dataset anagrafica.

    Strategia:
      - target=r2:    legge da R2 (pattern: list_objects su anagrafica/,
                      get_object per ciascuno è troppo costoso → 7896 GET).
                      Sfrutta il fatto che 'dashboard/<istat>.json' contiene
                      già anagrafica.coordinate. In alternativa, accetta
                      mancanza centroide (deep-link senza zoom).
      - target=local: legge file in data/anagrafica/*.json se esistono.

    Per evitare 7896 GET su R2, in modalità r2 NON popoliamo i centroidi
    da remoto: il frontend stesso ha le coordinate dalla dashboard A1 e
    può costruire l'URL deep-link client-side senza salvarlo nello shard.
    """
    centroids: dict[str, tuple[float, float]] = {}
    if target != "local":
        log.info("agcom_centroids_skip_r2",
                 reason="frontend will build deep-link client-side")
        return centroids
    if not ANAGRAFICA_DIR_LOCAL.exists():
        log.warning("agcom_centroids_no_dir",
                    path=str(ANAGRAFICA_DIR_LOCAL))
        return centroids
    n = 0
    for p in ANAGRAFICA_DIR_LOCAL.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            coord = d.get("coordinate") or d.get("kpi", {}).get("coordinate")
            if coord and "lat" in coord and "lon" in coord:
                centroids[p.stem] = (float(coord["lat"]), float(coord["lon"]))
                n += 1
        except Exception:
            continue
    log.info("agcom_centroids_loaded", n=n)
    return centroids


def build_deeplink(lat: float | None, lon: float | None,
                   level: int = 14) -> dict:
    """Costruisce il dict mappa_ufficiale per lo shard.

    Se lat/lon disponibili, include URL deep-link con center+level.
    Altrimenti URL base senza parametri (l'utente vedrà la mappa Italia
    completa).
    """
    if lat is None or lon is None:
        return {"url": AGCOM_MAP_BASE, "level": None}
    # WAB 2.15 accetta center=lon,lat (geographic coords)
    url = f"{AGCOM_MAP_BASE}?center={lon:.6f},{lat:.6f}&level={level}"
    return {"url": url, "level": level}


# ──────────────────────── BUILD SHARDS ─────────────────────────────

def _choose_level(famiglie_residenti: int | None) -> int:
    """Scegli zoom level AGCOM in base alla taglia del comune.

    AGCOM WebAppBuilder usa scale ESRI:
      level 12 ≈ 1:73k   (Roma, Milano: si vede tutto il comune)
      level 13 ≈ 1:36k   (capoluoghi medi)
      level 14 ≈ 1:18k   (comuni medi)
      level 15 ≈ 1:9k    (comuni piccoli)
    """
    if famiglie_residenti is None:
        return 14
    if famiglie_residenti >= 200_000:
        return 12
    if famiglie_residenti >= 30_000:
        return 13
    if famiglie_residenti >= 3_000:
        return 14
    return 15


def build_shards(rows: list[dict],
                 centroids: dict[str, tuple[float, float]],
                 now_iso: str) -> dict[str, dict]:
    """Costruisce dict istat6 -> payload shard."""
    shards: dict[str, dict] = {}
    for r in rows:
        istat = r["istat"]
        fam_res = r["famiglie_residenti"]
        level = _choose_level(fam_res)
        lat_lon = centroids.get(istat)
        if lat_lon:
            mappa = build_deeplink(lat_lon[0], lat_lon[1], level=level)
        else:
            mappa = build_deeplink(None, None, level=level)
        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_LABEL,
            "_source_url": SOURCE_URL,
            "_license": LICENSE_STR,
            "_data_period": DATA_PERIOD,
            "_generated_at": now_iso,
            "kpi": {
                "famiglie_residenti":        fam_res,
                "famiglie_ftth":             r["famiglie_ftth"],
                "famiglie_ftth_20m":         r["famiglie_ftth_20m"],
                "copertura_ftth_desi_pct":   r["copertura_ftth_desi_pct"],
                "copertura_ftth_20m_pct":    r["copertura_ftth_20m_pct"],
                "confidenza_desi_pct":       r["confidenza_desi_pct"],
                "celle_20m_raggiunte":       r["celle_20m_raggiunte"],
                "celle_20m_ftth":            r["celle_20m_ftth"],
                "celle_20m_fttc":            r["celle_20m_fttc"],
                "punti_dichiarati":          r["punti_dichiarati"],
                "punti_dichiarati_ftth":     r["punti_dichiarati_ftth"],
                "punti_geo_distinti":        r["punti_geo_distinti"],
                "punti_geo_distinti_ftth":   r["punti_geo_distinti_ftth"],
                "indirizzi_postali_distinti": r["indirizzi_postali_distinti"],
                "indirizzi_postali_distinti_ftth": r["indirizzi_postali_distinti_ftth"],
            },
            "anagrafica_locale": {
                "regione":   r["regione"],
                "provincia": r["provincia"],
                "comune":    r["comune"],
            },
            "mappa_ufficiale": mappa,
        }
    log.info("agcom_shards_built", n=len(shards))
    return shards


# ──────────────────────── META / SKIP CHECK ─────────────────────────

def read_last_known_sha() -> str | None:
    try:
        client = r2.get_r2_client()
        obj = client.get_object(Bucket=r2.get_bucket(), Key=R2_META_KEY)
        meta = json.loads(obj["Body"].read().decode("utf-8"))
        return meta.get("sha256")
    except Exception:
        return None


def write_meta(sha: str, n_shards: int) -> None:
    body = json.dumps({
        "sha256": sha,
        "n_shards": n_shards,
        "data_period": DATA_PERIOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "etl_version": ETL_VERSION,
    }, indent=2).encode("utf-8")
    r2.upload_bytes(body, R2_META_KEY, content_type="application/json")


# ──────────────────────── PUSH R2 / LOCAL ──────────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def push_shards_r2(shards: dict[str, dict],
                   force: bool = False) -> tuple[int, int]:
    """Push shard su R2 con skip-by-md5 (pattern pun.py/aria.py)."""
    total = len(shards)
    log.info("shards_r2_start", total=total, force=force, prefix=R2_PREFIX)
    t0 = time.time()

    # 1) Lista oggetti remoti
    remote_etag: dict[str, str] = {}
    if not force:
        try:
            client = r2.get_r2_client()
            pag = client.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=r2.get_bucket(),
                                     Prefix=f"{R2_PREFIX}/"):
                for o in page.get("Contents", []):
                    name = o["Key"].split("/")[-1]
                    if name.startswith("_"):
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
            futs = {ex.submit(_upload_one, istat): istat
                    for istat in to_upload}
            for f in as_completed(futs):
                try:
                    key, size, md5 = f.result()
                    files_for_manifest.append(
                        {"key": key, "size": size, "md5": md5})
                    uploaded += 1
                    if uploaded % 200 == 0:
                        elapsed = time.time() - t0
                        rate = uploaded / elapsed if elapsed > 0 else 0
                        eta = (len(to_upload) - uploaded) / rate if rate > 0 else 0
                        log.info("shards_r2_progress",
                                 uploaded=uploaded,
                                 to_upload=len(to_upload),
                                 elapsed_s=round(elapsed, 1),
                                 eta_s=round(eta, 1),
                                 rate=round(rate, 1))
                except Exception as e:
                    log.error("shards_r2_upload_fail", err=str(e))

    elapsed = round(time.time() - t0, 1)
    log.info("shards_r2_done", uploaded=uploaded, skipped=n_same,
             total=total, elapsed_s=elapsed)
    if files_for_manifest:
        manifest.update_source("agcom_bbmap", files_for_manifest,
                               status="ok")
    return uploaded, n_same


def write_shards_local(shards: dict[str, dict]) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, payload in shards.items():
        (DATA_DIR / f"{istat}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        n += 1
    log.info("agcom_local_done", written=n, dir=str(DATA_DIR))
    return n


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL AGCOM Broadband Map — copertura banda larga comunale")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--force", action="store_true",
                        help="Forza upload anche se SHA del CSV invariato")
    args = parser.parse_args()

    log.info("etl_agcom_bbmap_start",
             target=args.target, version=ETL_VERSION)

    # Fetch + sha
    body, sha = fetch_agcom_csv()

    # Skip check (solo target=r2)
    if args.target == "r2" and not args.force:
        last = read_last_known_sha()
        if last == sha:
            log.info("agcom_skip", reason="sha_unchanged", sha=sha[:16])
            return 0
        log.info("agcom_run", last_sha=(last or "")[:16], current_sha=sha[:16])

    # Parse
    rows = parse_csv(body)
    if len(rows) < 7000:
        log.error("agcom_too_few_rows", got=len(rows),
                  expected_min=7000)
        return 2

    # Centroidi (solo per target=local, vedi load_anagrafica_centroids)
    centroids = load_anagrafica_centroids(args.target)

    # Build shards
    now_iso = datetime.now(timezone.utc).isoformat()
    shards = build_shards(rows, centroids, now_iso)

    # Sample log (comuni notevoli)
    for sample in ["058091", "015146", "077014", "075035", "097055", "007003"]:
        sh = shards.get(sample)
        if sh:
            kpi = sh["kpi"]
            log.info("sample", istat=sample,
                     nome=sh["anagrafica_locale"]["comune"],
                     ftth_desi=kpi["copertura_ftth_desi_pct"],
                     ftth_20m=kpi["copertura_ftth_20m_pct"],
                     fam_res=kpi["famiglie_residenti"],
                     fam_ftth=kpi["famiglie_ftth"])
        else:
            log.info("sample_no_data", istat=sample)

    # Write
    if args.target == "local":
        write_shards_local(shards)
    else:
        push_shards_r2(shards, force=args.force)
        write_meta(sha, len(shards))

    log.info("etl_agcom_bbmap_done", target=args.target,
             comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
