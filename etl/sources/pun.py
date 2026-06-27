"""ETL Piattaforma Unica Nazionale dei punti di ricarica (PUN / GSE).

Sorgente
--------
GSE — Piattaforma Unica Nazionale dei punti di ricarica per veicoli
elettrici (MASE/GSE), https://www.piattaformaunicanazionale.it/idr

Acquisizione
------------
Il bottone "Esporta dati" e l'endpoint S3 sono stati disabilitati da GSE
(giugno 2026). I dati sono ora accessibili esclusivamente via API REST
del portale PUN, autenticata con credenziali AWS Cognito guest/unauthenticated.
L'IdentityPoolId è pubblicato in chiaro dal sito stesso in /config.json:

  AWS_REGION:           eu-south-1
  AWS_IDENTITY_POOL_ID: eu-south-1:e3b2ab05-2046-43dd-8ed0-c0f14c69d507

Flusso (2 step):
  1. Cognito GetId → GetCredentialsForIdentity (guest, senza login)
     → SigV4 credentials temporanee (1h)
  2. POST v1/chargepoints/public/map/search (paginato, 1000/pag)
     → lista evse_id (69.730 ~)
  3. POST v1/chargepoints/group (batch 100)
     → dati completi per ogni EVSE: CPO, indirizzo, connettori,
       potenza, capabilities, stato, orario

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
  "_etl_version": "0.2.0",
  "_source": "GSE - Piattaforma Unica Nazionale (PUN)",
  "_source_url": "https://www.piattaformaunicanazionale.it/idr",
  "_license": "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_totale": int,
    "n_attivi": int, "n_non_attivi": int,
    "pct_attivi": float,
    "n_ac": int, "n_dc": int,
    "potenza_tot_kw": float,
    "n_ricarica_rapida": int,         # DC + potenza >= 50kW
    "n_hpc": int,                     # >= 150kW
    "n_ultra_fast": int,              # >= 350kW
    "cpo_list": [str],                # CPO presenti nel comune
    "mix_connettori": {"IEC_62196_T2": .., "CCS": .., ..}
  },
  "punti": [
    {"id_evse", "lat", "lon", "indirizzo", "cap", "stato",
     "stato_raw", "cpo", "party_id", "tipo_parcheggio",
     "open_24h7", "potenza_w", "corrente", "standard_connettore",
     "capabilities", "real_time", "publication_status"}
  ]
}

Uso
---
  python -m etl.sources.pun
  python -m etl.sources.pun --force

Cadenza: giornaliera (08:00 UTC).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog
from requests_aws4auth import AWS4Auth

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.2.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "GSE - Piattaforma Unica Nazionale (PUN)"
SOURCE_URL   = "https://www.piattaformaunicanazionale.it/idr"
LICENSE_STR  = "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)"

# Cognito — valori pubblicati in https://www.piattaformaunicanazionale.it/config.json
AWS_REGION        = "eu-south-1"
COGNITO_IDP_ID    = "eu-south-1:e3b2ab05-2046-43dd-8ed0-c0f14c69d507"
API_BASE          = "https://api.pun.piattaformaunicanazionale.it"
COGNITO_ENDPOINT  = f"https://cognito-identity.{AWS_REGION}.amazonaws.com/"

# Paginazione
MAP_SEARCH_PAGE_SIZE = 1000   # max testato sull'endpoint
GROUP_BATCH_SIZE     = 100    # limite lato JS: slice(0,100)
CRED_RENEW_EVERY     = 600    # batch → rinnovo creds ogni ~1h

SHARD_PREFIX     = "pun"
DEFAULT_OUTDIR   = Path("/var/www/cruscotto-italia/data/pun")
CACHE_DIR        = Path(".cache/pun")

ISTAT_COMUNI_URL = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
USER_AGENT       = "cruscotto-italia-etl/0.2 (+https://github.com/AgID/cruscotto-italia)"

BBOX_LAT_MIN, BBOX_LAT_MAX = 35.0, 47.5
BBOX_LON_MIN, BBOX_LON_MAX = 6.0, 19.0

ENGLISH_EXONYMS = {
    "rome": "roma", "milan": "milano", "naples": "napoli", "turin": "torino",
    "florence": "firenze", "genoa": "genova", "padua": "padova",
    "venice": "venezia", "raguse": "ragusa", "syracuse": "siracusa",
    "leghorn": "livorno", "mantua": "mantova",
}
DENOM_ALIASES = {
    "reggio emilia": "reggio nell emilia",
    "reggio calabria": "reggio di calabria",
}

# Mapping status API → etichetta leggibile (compatibile con vecchio schema)
STATUS_MAP = {
    "AVAILABLE":   "Attivo",
    "CHARGING":    "Attivo",
    "RESERVED":    "Attivo",
    "PLANNED":     "Non Attivo",
    "OUTOFORDER":  "Non Attivo",
    "INOPERATIVE": "Non Attivo",
    "BLOCKED":     "Non Attivo",
    "REMOVED":     "Non Attivo",
    "UNKNOWN":     "Non Attivo",
}

# Mapping standard connettore → tipo corrente
AC_STANDARDS = {"IEC_62196_T1", "IEC_62196_T2", "IEC_62196_T3A", "IEC_62196_T3C",
                "DOMESTIC_A","DOMESTIC_B","DOMESTIC_C","DOMESTIC_D","DOMESTIC_E",
                "DOMESTIC_F","DOMESTIC_G","DOMESTIC_H","DOMESTIC_I","DOMESTIC_J",
                "DOMESTIC_K","DOMESTIC_L", "IEC_60309_2_single_16"}
DC_STANDARDS  = {"CHADEMO", "IEC_62196_T2_COMBO", "TESLA_R", "TESLA_S",
                 "IEC_62196_T1_COMBO", "NACS"}


# ──────────────────────── NORMALIZE / RESOLVE ──────────────────────

def normalize(s: str) -> str:
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


# ──────────────────────── POINT-IN-POLYGON ─────────────────────────

class _PipChecker:
    """Point-in-polygon su GeoJSON sezioni censimento per verificare
    se una coordinata cade dentro il territorio comunale.
    Usa ray-casting stdlib (nessuna dipendenza esterna).
    """

    def __init__(self, censimento_full_dir: Path):
        self._dir   = censimento_full_dir
        self._cache: dict[str, list] = {}

    def _carica_sezioni(self, istat: str) -> list:
        if istat in self._cache:
            return self._cache[istat]
        path = self._dir / f"{istat}.geojson"
        if not path.exists():
            self._cache[istat] = []
            return []
        try:
            fc = json.loads(path.read_text(encoding="utf-8"))
            feats = fc.get("features", [])
            self._cache[istat] = feats
            return feats
        except Exception:
            self._cache[istat] = []
            return []

    @staticmethod
    def _pip(pt: tuple, ring: list) -> bool:
        x, y = pt
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i
        return inside

    def _in_feature(self, pt: tuple, geom: dict) -> bool:
        if geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            polys = [geom["coordinates"]]
        for poly in polys:
            outer = poly[0]
            holes = poly[1:]
            if self._pip(pt, outer) and not any(self._pip(pt, h) for h in holes):
                return True
        return False

    def fuori_comune(self, istat: str, lon: float, lat: float) -> bool:
        """Ritorna True se (lon, lat) NON cade in nessuna sezione del comune."""
        feats = self._carica_sezioni(istat)
        if not feats:
            return False   # nessun dato → non filtrare
        return not any(self._in_feature((lon, lat), f["geometry"]) for f in feats)


DEFAULT_CENSIMENTO_DIR = Path("/var/www/cruscotto-italia/data/censimento_full")


def pull_istat_comuni(workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "istat-comuni.csv"
    if out.exists() and out.stat().st_size > 500_000:
        log.info("istat_comuni_cache_hit", path=str(out))
        return out
    log.info("pulling_istat_comuni", url=ISTAT_COMUNI_URL)
    r = requests.get(ISTAT_COMUNI_URL, headers={"User-Agent": USER_AGENT}, timeout=120)
    r.raise_for_status()
    try:
        text = r.content.decode("latin-1")
    except UnicodeDecodeError:
        text = r.content.decode("utf-8", errors="replace")
    out.write_text(text, encoding="utf-8")
    log.info("istat_comuni_saved", path=str(out), bytes=out.stat().st_size)
    return out


def build_istat_index(csv_path: Path) -> tuple[dict, dict]:
    by_np: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = defaultdict(set)
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            if len(row) < 16:
                continue
            istat6    = row[4].strip()
            nome_full = row[5].strip()
            nome_it   = row[6].strip()
            nome_alt  = row[7].strip()
            prov      = row[11].strip()
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


# ──────────────────────── AUTH COGNITO GUEST ───────────────────────

def _cognito_post(target: str, payload: dict) -> dict:
    r = requests.post(
        COGNITO_ENDPOINT,
        headers={
            "Content-Type":  "application/x-amz-json-1.1",
            "X-Amz-Target":  target,
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_cognito_auth() -> tuple[AWS4Auth, float]:
    """Ottieni SigV4 auth con credential Cognito guest (unauthenticated).
    Ritorna (auth, expiration_unix_ts).
    """
    iid  = _cognito_post(
        "AWSCognitoIdentityService.GetId",
        {"IdentityPoolId": COGNITO_IDP_ID},
    )["IdentityId"]
    cred = _cognito_post(
        "AWSCognitoIdentityService.GetCredentialsForIdentity",
        {"IdentityId": iid},
    )["Credentials"]
    auth = AWS4Auth(
        cred["AccessKeyId"],
        cred["SecretKey"],
        AWS_REGION,
        "execute-api",
        session_token=cred["SessionToken"],
    )
    log.info("cognito_auth_ok", expiration=cred["Expiration"])
    return auth, cred["Expiration"]


# ──────────────────────── FETCH EVSE IDs ───────────────────────────

def fetch_all_evse_ids(auth: AWS4Auth) -> list[str]:
    """Step 1: scarica tutti gli evse_id via public/map/search paginato."""
    evse_ids: list[str] = []
    page = 0
    total_pages = None

    log.info("pun_map_search_start")
    while True:
        r = requests.post(
            f"{API_BASE}/v1/chargepoints/public/map/search",
            auth=auth,
            json={"page": page, "size": MAP_SEARCH_PAGE_SIZE},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()

        if total_pages is None:
            total_pages  = d.get("totalPages", 0)
            total_el     = d.get("totalElements", 0)
            log.info("pun_map_search_info",
                     total_elements=total_el, total_pages=total_pages)

        content = d.get("content", [])
        evse_ids.extend(item["evse_id"] for item in content if item.get("evse_id"))

        if d.get("last", True) or not content:
            break
        page += 1

    log.info("pun_map_search_done", n_evse=len(evse_ids))
    return evse_ids


# ──────────────────────── FETCH DETTAGLI (group) ───────────────────

def fetch_group_batch(auth: AWS4Auth, batch: list[str]) -> list[dict]:
    """POST /chargepoints/group con lista evse_id → dati completi."""
    r = requests.post(
        f"{API_BASE}/v1/chargepoints/group",
        auth=auth,
        json=batch,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if r.status_code == 401:
        return []   # segnale al chiamante di rinnovare le creds
    r.raise_for_status()
    return r.json()


def fetch_all_details(evse_ids: list[str]) -> list[dict]:
    """Step 2: scarica i dettagli completi in batch da GROUP_BATCH_SIZE.
    Rinnova le credenziali ogni CRED_RENEW_EVERY batch.
    """
    batches = [
        evse_ids[i:i + GROUP_BATCH_SIZE]
        for i in range(0, len(evse_ids), GROUP_BATCH_SIZE)
    ]
    total   = len(batches)
    records = []

    auth, _ = get_cognito_auth()

    for i, batch in enumerate(batches):
        if i > 0 and i % CRED_RENEW_EVERY == 0:
            log.info("pun_cred_renew", batch=i, total=total)
            auth, _ = get_cognito_auth()

        result = fetch_group_batch(auth, batch)
        if not result and len(batch) > 0:
            # 401 → rinnova e riprova
            log.info("pun_401_renew", batch=i)
            auth, _ = get_cognito_auth()
            result = fetch_group_batch(auth, batch)

        records.extend(result)

        if (i + 1) % 100 == 0 or (i + 1) == total:
            log.info("pun_group_progress",
                     batch=i + 1, total=total,
                     records=len(records))
        time.sleep(0.03)

    log.info("pun_group_done", total_records=len(records))
    return records


# ──────────────────────── PARSE RECORD → RIGA ──────────────────────

def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _connector_current(standard: str) -> str:
    """Determina AC/DC dal nome dello standard connettore."""
    if standard in DC_STANDARDS:
        return "DC"
    return "AC"


def _potenza_categoria(power_w: int | None) -> str | None:
    """Classifica la potenza (allineata al vecchio schema PUN):
      Slow      < 7.4 kW
      Quick     7.4 – < 22 kW
      Fast      22 – < 50 kW
      HPC       50 – < 150 kW
      Ultra fast >= 150 kW (nel vecchio schema era >= 350, ma la PUN
                             usa 150 come soglia HPC/Ultra fast)
    """
    if not power_w:
        return None
    kw = power_w / 1000
    if kw < 7.4:
        return "Slow"
    if kw < 22:
        return "Quick"
    if kw < 50:
        return "Fast"
    if kw < 150:
        return "HPC"
    return "Ultra fast"


def parse_record(rec: dict) -> dict | None:
    """Converte un record /group in dict interno (schema punti)."""
    loc    = rec.get("location", {})
    coords = rec.get("coordinates", {})
    try:
        lat = float(coords.get("latitude", 0))
        lon = float(coords.get("longitude", 0))
    except (TypeError, ValueError):
        return None
    if not in_italy(lat, lon):
        return None

    conns      = rec.get("connectors", [])
    # Prendi il connettore con potenza massima come rappresentativo
    main_conn  = max(conns, key=lambda c: c.get("max_electric_power", 0), default={})
    standard   = _clean(main_conn.get("standard"))
    power_w    = main_conn.get("max_electric_power")
    corrente   = _connector_current(standard) if standard else None

    status_raw = _clean(rec.get("status"))
    stato      = STATUS_MAP.get(status_raw, "Non Attivo")

    opening    = loc.get("opening_times", {})
    open_247   = opening.get("twentyfourseven", False)

    capabilities = "|".join(rec.get("capabilities", []))

    return {
        "id_evse":            _clean(rec.get("evse_id")),
        "lat":                round(lat, 6),
        "lon":                round(lon, 6),
        "indirizzo":          _clean(loc.get("address")),
        "cap":                _clean(loc.get("postal_code")),
        "stato":              stato,
        "stato_raw":          status_raw,
        "cpo":                _clean(rec.get("businessName")),
        "party_id":           _clean(loc.get("party_id")),
        "tipo_parcheggio":    _clean(loc.get("parking_type")),
        "open_24h7":          bool(open_247),
        "potenza_w":          int(power_w) if power_w else None,
        "potenza_categoria":  _potenza_categoria(power_w),
        "corrente":           corrente,
        "standard_connettore": standard,
        "n_connettori":       len(conns),
        "capabilities":       capabilities if capabilities else None,
        "real_time":          bool(rec.get("realTime")),
        "publication_status": _clean(rec.get("publicationStatus")),
    }


# ──────────────────────── BUILD SHARDS ─────────────────────────────

def build_shards(records: list[dict],
                 by_np: dict,
                 by_name: dict,
                 pip_checker: _PipChecker | None = None) -> dict[str, dict]:
    """Aggrega record per ISTAT con KPI per comune."""
    shards: dict[str, dict] = {}
    stats  = Counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    sample_unmatched: dict[str, list] = defaultdict(list)

    for raw in records:
        loc   = raw.get("location", {})
        coords = raw.get("coordinates", {})
        try:
            lat = float(coords.get("latitude", 0))
            lon = float(coords.get("longitude", 0))
        except (TypeError, ValueError):
            stats["bad_coords"] += 1
            continue

        city  = _clean(loc.get("city"))  or ""
        state = _clean(loc.get("state")) or ""

        istat = resolve_istat(city, state, lat, lon, by_np, by_name)
        if not istat:
            if not in_italy(lat, lon):
                stats["outside_italy"] += 1
                if len(sample_unmatched["outside_italy"]) < 5:
                    sample_unmatched["outside_italy"].append((city, state))
            else:
                stats["unmatched"] += 1
                if len(sample_unmatched["unmatched"]) < 10:
                    sample_unmatched["unmatched"].append((city, state))
            continue

        punto = parse_record(raw)
        if punto is None:
            stats["parse_error"] += 1
            continue

        # Point-in-polygon: scarta punti fuori dal territorio comunale
        if pip_checker and pip_checker.fuori_comune(istat, punto["lon"], punto["lat"]):
            stats["fuori_comune"] += 1
            continue

        stats["matched"] += 1

        if istat not in shards:
            shards[istat] = {
                "_etl_version":  ETL_VERSION,
                "_source":       SOURCE_LABEL,
                "_source_url":   SOURCE_URL,
                "_license":      LICENSE_STR,
                "_generated_at": now_iso,
                "punti":         [],
            }
        shards[istat]["punti"].append(punto)

    # KPI per shard
    for _istat, sh in shards.items():
        punti  = sh["punti"]
        n_tot  = len(punti)
        n_att  = sum(1 for p in punti if p["stato"] == "Attivo")
        n_dc   = sum(1 for p in punti if p["corrente"] == "DC")
        pot_tot_w = sum(p["potenza_w"] or 0 for p in punti)

        n_rapida   = sum(1 for p in punti
                         if p["corrente"] == "DC" and (p["potenza_w"] or 0) >= 50_000)
        n_hpc      = sum(1 for p in punti if (p["potenza_w"] or 0) >= 150_000)
        n_ultra    = sum(1 for p in punti if (p["potenza_w"] or 0) >= 350_000)

        mix_conn   = Counter(p["standard_connettore"] for p in punti
                             if p["standard_connettore"])
        mix_cat    = Counter(p["potenza_categoria"] for p in punti
                             if p["potenza_categoria"])
        cpo_list   = sorted({p["cpo"] for p in punti if p["cpo"]})

        sh["kpi"] = {
            "n_totale":         n_tot,
            "n_attivi":         n_att,
            "n_non_attivi":     n_tot - n_att,
            "pct_attivi":       round(n_att / n_tot * 100, 1) if n_tot else 0.0,
            "n_ac":             n_tot - n_dc,
            "n_dc":             n_dc,
            "potenza_tot_kw":   round(pot_tot_w / 1000, 1),
            "n_ricarica_rapida": n_rapida,
            "n_hpc":            n_hpc,
            "n_ultra_fast":     n_ultra,
            "cpo_list":         cpo_list,
            "mix_potenza":      dict(mix_cat),
            "mix_connettori":   dict(mix_conn),
        }

    log.info("pun_aggregate_done",
             matched=stats["matched"],
             unmatched=stats["unmatched"],
             outside_italy=stats["outside_italy"],
             bad_coords=stats["bad_coords"],
             parse_errors=stats["parse_error"],
             fuori_comune=stats["fuori_comune"],
             comuni_shard=len(shards),
             sample_unmatched=dict(sample_unmatched))
    return shards


# ──────────────────────── WRITE LOCAL ──────────────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def write_shards_local(shards: dict[str, dict],
                       outdir: Path,
                       force: bool = False) -> tuple[int, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    total   = len(shards)
    written = 0
    skipped = 0
    files_for_manifest: list[dict] = []
    t0 = time.time()

    for istat, shard in shards.items():
        body    = json.dumps(shard, ensure_ascii=False).encode("utf-8")
        new_md5 = _md5_of_bytes(body)
        target  = outdir / f"{istat}.json"

        if not force and target.exists():
            try:
                if _md5_of_bytes(target.read_bytes()) == new_md5:
                    skipped += 1
                    files_for_manifest.append(
                        {"key": f"{SHARD_PREFIX}/{istat}.json",
                         "size": len(body), "md5": new_md5})
                    continue
            except OSError:
                pass

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        written += 1
        files_for_manifest.append(
            {"key": f"{SHARD_PREFIX}/{istat}.json",
             "size": len(body), "md5": new_md5})

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
    parser = argparse.ArgumentParser(
        description="ETL PUN — punti di ricarica veicoli elettrici (v0.2, API REST)"
    )
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output dir (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--force", action="store_true",
                        help="Forza riesecuzione + riscrittura totale")
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza ridownload del CSV ISTAT comuni")
    parser.add_argument("--censimento-full-dir", type=Path,
                        default=DEFAULT_CENSIMENTO_DIR,
                        help=f"Dir con GeoJSON sezioni censimento per PiP (default: {DEFAULT_CENSIMENTO_DIR})")
    args = parser.parse_args()

    log.info("etl_pun_start", version=ETL_VERSION, outdir=str(args.outdir))

    # Step 1 — ottieni evse_id
    auth, _ = get_cognito_auth()
    evse_ids = fetch_all_evse_ids(auth)
    if not evse_ids:
        log.error("pun_no_evse_ids")
        return 1

    # Step 2 — scarica dettagli (rinnovo creds gestito internamente)
    records = fetch_all_details(evse_ids)
    if not records:
        log.error("pun_no_records")
        return 1

    # Index ISTAT
    if args.no_cache and CACHE_DIR.exists():
        for p in CACHE_DIR.glob("istat-comuni.csv"):
            p.unlink()
    istat_csv = pull_istat_comuni(CACHE_DIR)
    by_np, by_name = build_istat_index(istat_csv)

    # Aggregazione per comune
    pip_checker = None
    if args.censimento_full_dir.exists():
        pip_checker = _PipChecker(args.censimento_full_dir)
        log.info("pun_pip_checker_enabled", source=str(args.censimento_full_dir))
    else:
        log.warning("pun_pip_checker_disabled", path=str(args.censimento_full_dir))

    shards = build_shards(records, by_np, by_name, pip_checker)

    # Sample log comuni notevoli
    for sample in ["058091", "015146", "077014", "075035", "097055"]:
        sh = shards.get(sample)
        if sh:
            log.info("sample", istat=sample, **sh["kpi"])
        else:
            log.info("sample_no_data", istat=sample)

    # Write locale
    write_shards_local(shards, args.outdir, force=args.force)

    log.info("etl_pun_done", comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
