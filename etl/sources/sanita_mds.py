"""ETL Sanita' territoriale - Ministero della Salute (Open Data IODL v2.0).

Integrazione 15a fonte di Cruscotto Italia. Bundle 3 dataset:
  1. Farmacie       (FRM_FARMA_5_<YYYYMMDD>.csv, ~58.5k righe storiche, ~20.8k attive)
  2. Parafarmacie   (FRM_PFARMA_7_<YYYYMMDD>.csv, ~13.8k storiche, ~7.2k attive)
  3. Posti letto    (Posti letto per stabilimento ospedaliero e disciplina_2023_0.csv,
                     ~11.6k righe disciplina, ~1.260 stabilimenti)

Discovery URL: NON sono hardcoded. Sono parsati da
  https://www.dati.salute.gov.it/page-data/index/page-data.json
selezionando i drupal_internal__nid:
  70785  Farmacie
  70776  Parafarmacie
  175018 Posti letto stabilimento ospedaliero 2023

Cadenza upstream:
  - Farmacie / Parafarmacie: aggiornamento QUOTIDIANO (URL cambia ogni giorno con suffisso _YYYYMMDD)
  - Posti letto: aggiornamento ANNUALE (lug 2025 per anno 2023)

Particolarita' parsing:
  - Farmacie/Parafarmacie: UTF-8, delim=';', decimali coordinate con ','
  - Posti letto: ISO-8859-1, delim=';', header con spazi (es 'Codice Comune')
  - `cod_comune` / `codice_comune` / `Codice Comune` sono gia' ISTAT 6-digit nativi
    (no fuzzy match, no normalize necessario)
  - data_inizio_validita / data_fine_validita filtrano lo storico:
    attiva = end vuota o '-' o futura rispetto a OGGI

Outlier coordinate:
  - 0% farmacie fuori bbox Italia (35-47 lat, 6-19 lon)
  - ~0.24% farmacie con coord fuori bbox provincia (~2/834 a Roma, errori MEF/MdS)
  - Strategia: keep i punti, conteggio in kpi.n_outlier_coordinate

Schema output per shard sanita_mds/<istat>.json:
{
  "_etl_version": "0.1.0",
  "_source": "Ministero della Salute - Open Data",
  "_license": "IODL v2.0",
  "_generated_at": "ISO-8601",
  "_fonti": {
    "farmacie":     {"url", "data_riferimento", "aggiornamento"},
    "parafarmacie": {"url", "data_riferimento", "aggiornamento"},
    "ospedali":     {"url", "anno_dati", "aggiornamento"}
  },
  "istat_code": "058091",
  "comune":     "ROMA",
  "provincia":  "RM",
  "regione":    "LAZIO",
  "farmacie":     {"kpi": {...}, "punti": [...]}     | null,
  "parafarmacie": {"kpi": {...}, "punti": [...]}     | null,
  "ospedali":     {"kpi": {...}, "stabilimenti": [...]} | null
}

Usage:
  python -m etl.sources.sanita_mds --target=local
  python -m etl.sources.sanita_mds --target=r2 --force-shard-upload
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import requests
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# ============================================================================
# COSTANTI / CONFIG
# ============================================================================

ETL_VERSION = "0.1.0"
SOURCE_LABEL = "Ministero della Salute - Open Data"
SOURCE_LICENSE = "IODL v2.0"

DISCOVERY_URL = "https://www.dati.salute.gov.it/page-data/index/page-data.json"
PORTAL_BASE = "https://www.dati.salute.gov.it"

# Drupal node IDs (verificati 2026-05-13)
TARGETS = {
    "farmacie":     {"nid": 70785, "label": "Farmacie"},
    "parafarmacie": {"nid": 70776, "label": "Parafarmacie"},
    "ospedali":     {"nid": 175018, "label": "Posti letto stab. ospedaliero 2023"},
}

# Riferimento URL pubblici per `_fonti`
DATASET_PUBLIC_URLS = {
    "farmacie":     f"{PORTAL_BASE}/dataset/farmacie",
    "parafarmacie": f"{PORTAL_BASE}/dataset/parafarmacie",
    "ospedali":     f"{PORTAL_BASE}/dataset/posti-letto-stabilimento-ospedaliero-e-disciplina",
}

ANNO_OSPEDALI = 2023

# HTTP fetch (richiede UA browser-like per WAF MdS)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/json,*/*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Referer": "https://www.dati.salute.gov.it/",
}
HTTP_TIMEOUT_SEC = 180
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_SLEEP = 5

# Bbox Italia per check macroscopico (lat 35-47, lon 6-19)
BBOX_ITALIA_LAT = (35.0, 47.5)
BBOX_ITALIA_LON = (6.0, 19.0)


# ============================================================================
# DISCOVERY (parse page-data.json per estrarre URL freschi)
# ============================================================================

def fetch_discovery() -> dict:
    """Scarica il page-data.json del portale dati.salute.gov.it.

    Returns:
        {label: {"url": "...", "filename": "...", "filesize": int,
                 "data_aggiornamento": "YYYY-MM-DD"}}
        per ognuna delle 3 chiavi in TARGETS.
    """
    log.info("sanita_mds_discovery_start", url=DISCOVERY_URL)
    resp = requests.get(DISCOVERY_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    nodes = (
        data.get("result", {})
            .get("data", {})
            .get("allNodeDataset", {})
            .get("nodes", [])
    )
    log.info("sanita_mds_discovery_nodes", total=len(nodes))

    result: dict[str, dict] = {}
    for key, target in TARGETS.items():
        nid = target["nid"]
        for n in nodes:
            if n.get("drupal_internal__nid") != nid:
                continue
            files = n.get("relationships", {}).get("field_listafile", []) or []
            if not files:
                log.warning("sanita_mds_no_files_in_node", key=key, nid=nid)
                continue
            # Primo file utile (CSV preferito)
            chosen = None
            for f in files:
                fname = (f.get("filename") or "").lower()
                if fname.endswith(".csv"):
                    chosen = f
                    break
            if chosen is None:
                chosen = files[0]

            url = chosen.get("url", "")
            if url and not url.startswith("http"):
                url = PORTAL_BASE + url

            result[key] = {
                "url": url,
                "filename": chosen.get("filename"),
                "filesize": chosen.get("filesize"),
                "data_aggiornamento": (
                    n.get("field_dataultimoaggiornamento")
                    or n.get("changed")
                    or None
                ),
            }
            break
        else:
            log.error("sanita_mds_target_not_found", key=key, nid=nid)
            raise RuntimeError(f"Discovery: target {key} (nid={nid}) non trovato nel feed")

    log.info("sanita_mds_discovery_done",
             targets=list(result.keys()),
             total_bytes=sum((v.get("filesize") or 0) for v in result.values()))
    return result


def download_csv(url: str, cache_path: Path, force: bool = False) -> Path:
    """Scarica un CSV con retry, salva in cache_path."""
    if cache_path.exists() and not force:
        log.info("sanita_mds_cache_hit", path=str(cache_path),
                 size=cache_path.stat().st_size)
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            log.info("sanita_mds_download_start", url=url, attempt=attempt)
            with requests.get(url, headers=HTTP_HEADERS, stream=True,
                              timeout=HTTP_TIMEOUT_SEC) as r:
                r.raise_for_status()
                tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                with open(tmp, "wb") as out:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        out.write(chunk)
                tmp.replace(cache_path)
            log.info("sanita_mds_download_done", path=str(cache_path),
                     size=cache_path.stat().st_size)
            return cache_path
        except Exception as e:
            last_err = e
            log.warning("sanita_mds_download_retry", attempt=attempt,
                        error=str(e))
            time.sleep(DOWNLOAD_RETRY_SLEEP * attempt)

    raise RuntimeError(f"Download fallito dopo {DOWNLOAD_RETRIES} tentativi: {last_err}")


# ============================================================================
# PARSING (filtri attivita' + normalizzazione coordinate)
# ============================================================================

def _today() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_date_it(s: str) -> Optional[datetime]:
    """Parsa 'DD/MM/YYYY' italiana, None su errore."""
    s = (s or "").strip()
    if not s or s == "-":
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y")
    except ValueError:
        return None


def _is_active(end_str: str, ref_date: datetime) -> bool:
    """Una farmacia/parafarmacia e' attiva se end e' vuota/'-' o futura."""
    end = _parse_date_it(end_str)
    if end is None:
        # vuota o '-' => attiva
        return (end_str or "").strip() in ("", "-")
    return end >= ref_date


def _norm_coord(s: str) -> Optional[float]:
    """Converte '45,066215' o '45.066215' in float; None se vuoto/non parseable."""
    s = (s or "").strip()
    if not s or s == "-":
        return None
    s = s.replace(",", ".")
    try:
        v = float(s)
        return v
    except (ValueError, TypeError):
        return None


def _norm_int(s: str) -> int:
    try:
        return int((s or "0").strip() or 0)
    except ValueError:
        return 0


def _in_bbox_italia(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    return (BBOX_ITALIA_LAT[0] <= lat <= BBOX_ITALIA_LAT[1] and
            BBOX_ITALIA_LON[0] <= lon <= BBOX_ITALIA_LON[1])


def parse_farmacie(csv_path: Path, ref_date: datetime) -> dict[str, list[dict]]:
    """Ritorna {istat: [punto, ...]} solo per farmacie ATTIVE.

    Anagrafica regione/provincia tenuta a parte (lookup esterno).
    """
    by_istat: dict[str, list[dict]] = defaultdict(list)
    n_tot = 0
    n_active = 0
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            n_tot += 1
            if not _is_active(row.get("data_fine_validita", ""), ref_date):
                continue
            n_active += 1
            istat = (row.get("cod_comune") or "").strip()
            if not istat or len(istat) != 6:
                continue

            lat = _norm_coord(row.get("latitudine"))
            lon = _norm_coord(row.get("longitudine"))
            tipo = (row.get("descrizione_tipologia") or "").strip()
            # Normalizzazione case-mix "Dispensario stagionale" / "Stagionale"
            if tipo:
                tipo = tipo.strip()
                # Capitalizza solo la prima lettera, rispetta " " interno
                tipo_norm = tipo[0].upper() + tipo[1:].lower() if tipo else tipo
            else:
                tipo_norm = "Non specificata"

            by_istat[istat].append({
                "nome":     (row.get("descrizione_farmacia") or "").strip()[:140],
                "tipo":     tipo_norm,
                "indirizzo": (row.get("indirizzo") or "").strip(),
                "cap":      (row.get("cap") or "").strip(),
                "lat":      round(lat, 6) if lat is not None else None,
                "lon":      round(lon, 6) if lon is not None else None,
                # anagrafica per arricchimento aggregato
                "_provincia": (row.get("sigla_provincia") or "").strip(),
                "_regione":   (row.get("regione") or "").strip(),
                "_comune":    (row.get("comune") or "").strip(),
            })

    log.info("sanita_mds_farmacie_parsed",
             tot=n_tot, attive=n_active, comuni=len(by_istat))
    return dict(by_istat)


def parse_parafarmacie(csv_path: Path, ref_date: datetime) -> dict[str, list[dict]]:
    """Ritorna {istat: [punto, ...]} solo per parafarmacie ATTIVE."""
    by_istat: dict[str, list[dict]] = defaultdict(list)
    n_tot = 0
    n_active = 0
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            n_tot += 1
            if not _is_active(row.get("data_fine_validita", ""), ref_date):
                continue
            n_active += 1
            istat = (row.get("codice_comune") or "").strip()
            if not istat or len(istat) != 6:
                continue

            lat = _norm_coord(row.get("latitudine"))
            lon = _norm_coord(row.get("longitudine"))

            by_istat[istat].append({
                "nome":     (row.get("sito_logistico") or "").strip()[:140],
                "indirizzo": (row.get("indirizzo") or "").strip(),
                "cap":      (row.get("cap") or "").strip(),
                "lat":      round(lat, 6) if lat is not None else None,
                "lon":      round(lon, 6) if lon is not None else None,
                "_provincia": (row.get("sigla_provincia") or "").strip(),
                "_regione":   (row.get("regione") or "").strip(),
                "_comune":    (row.get("comune") or "").strip(),
            })

    log.info("sanita_mds_parafarmacie_parsed",
             tot=n_tot, attive=n_active, comuni=len(by_istat))
    return dict(by_istat)


def parse_ospedali(csv_path: Path) -> dict[str, dict]:
    """Ritorna {istat: {stabilimenti: {key: stab_dict}, anag: {...}}}

    Aggrega per (codice_struttura, subcodice). Encoding ISO-8859-1.
    Trim su valori (il dataset MdS ha padding a lunghezza fissa).
    """
    by_istat: dict[str, dict] = {}
    n_rows = 0
    with open(csv_path, encoding="iso-8859-1", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            n_rows += 1
            istat = (row.get("Codice Comune") or "").strip()
            if not istat or len(istat) != 6:
                continue

            cs = (row.get("Codice struttura") or "").strip()
            sc = (row.get("Subcodice") or "").strip()
            key = f"{cs}_{sc}"

            if istat not in by_istat:
                by_istat[istat] = {
                    "stabilimenti": {},
                    "anag": {
                        "regione":   (row.get("Descrizione Regione") or "").strip(),
                        "provincia": (row.get("Sigla Provincia") or "").strip(),
                        "comune":    (row.get("Comune") or "").strip(),
                    },
                }

            stab = by_istat[istat]["stabilimenti"].setdefault(key, {
                "codice_struttura": cs,
                "subcodice":        sc,
                "denominazione":    (row.get("Denominazione Struttura/Stabilimento") or "").strip(),
                "tipo_struttura":   (row.get("Descrizione tipo struttura") or "").strip(),
                "tipo_azienda_cod": (row.get("Tipo Azienda") or "").strip(),
                "indirizzo":        (row.get("Indirizzo") or "").strip(),
                "discipline":       [],
                "totale_posti_letto": 0,
            })

            ord_pl = _norm_int(row.get("Posti letto degenza ordinaria"))
            pag_pl = _norm_int(row.get("Posti letto degenza a pagamento"))
            dh_pl  = _norm_int(row.get("Posti letto Day Hospital"))
            ds_pl  = _norm_int(row.get("Posti letto Day Surgery"))
            tot_pl = _norm_int(row.get("Totale posti letto"))

            stab["totale_posti_letto"] += tot_pl
            stab["discipline"].append({
                "codice":      (row.get("Codice disciplina") or "").strip(),
                "descrizione": (row.get("Descrizione disciplina") or "").strip(),
                "tipo":        (row.get("Tipo di Disciplina") or "").strip(),
                "n_reparti":   _norm_int(row.get("N\u00b0 Reparti") or row.get("N° Reparti")),
                "ord": ord_pl, "pag": pag_pl, "dh": dh_pl, "ds": ds_pl,
                "totale": tot_pl,
            })

    log.info("sanita_mds_ospedali_parsed",
             rows=n_rows, comuni=len(by_istat),
             stabilimenti=sum(len(v["stabilimenti"]) for v in by_istat.values()))
    return by_istat


# ============================================================================
# BUILD SHARDS (composizione finale per comune)
# ============================================================================

def _filter_outlier_coords(punti: list[dict]) -> tuple[list[dict], int]:
    """Filtra outlier statistici delle coordinate.

    Le coordinate del MdS hanno un tasso di errore osservato del ~5-15%
    (osservazione Roma 2026-05-13: punti del comune 058091 con lat/lon
    sparse in tutto il Lazio - Viterbo, Frosinone, Latina, ecc.).
    Il filtro `cod_comune` da' record corretti, ma le `lat`/`lon` di
    quei record sono inserite male a monte.

    Strategia: per ogni gruppo (gia' filtrato per istat):
      1. Calcolo centroide robust (mediana lat/lon dei punti geo-referenziati)
      2. Soglia distanza:
         - N>50 punti (es. capoluoghi grandi): 0.3 gradi (~33 km)
         - N<=50 (resto):                       0.15 gradi (~16 km)
         (Roma capitale e' radius ~30km dal centro, le soglie sono ampie
         per non eliminare frazioni periferiche legittime.)
      3. Punti oltre soglia: lat/lon nullificati (record resta, perdono
         solo le coordinate). Il record sopravvive in 'punti' con nome,
         indirizzo, CAP visibili nel popup/elenco.

    Ritorna (punti_modificati, n_droppati).
    """
    if not punti:
        return punti, 0

    coords = [(p["lat"], p["lon"]) for p in punti
              if p.get("lat") is not None and p.get("lon") is not None]
    if len(coords) < 5:
        # Troppi pochi punti per calcolare mediana robusta: niente filtro.
        return punti, 0

    lats_sorted = sorted(c[0] for c in coords)
    lons_sorted = sorted(c[1] for c in coords)
    med_lat = lats_sorted[len(lats_sorted) // 2]
    med_lon = lons_sorted[len(lons_sorted) // 2]

    soglia = 0.3 if len(coords) > 50 else 0.15

    n_droppati = 0
    out = []
    for p in punti:
        if p.get("lat") is None or p.get("lon") is None:
            out.append(p)
            continue
        d_lat = abs(p["lat"] - med_lat)
        d_lon = abs(p["lon"] - med_lon)
        if d_lat > soglia or d_lon > soglia:
            # Outlier: nullifico coord ma tengo il record
            new_p = dict(p)
            new_p["lat"] = None
            new_p["lon"] = None
            new_p["_coord_dropped"] = True  # privato, non finisce nel JSON
            out.append(new_p)
            n_droppati += 1
        else:
            out.append(p)
    return out, n_droppati


def _build_farmacie_section(punti: list[dict]) -> dict:
    """KPI + punti per farmacie. Tiene tutti i punti (no sampling).

    Pre-filtro outlier coordinate: vedi _filter_outlier_coords.
    """
    punti, n_droppati = _filter_outlier_coords(punti)
    n_tot = len(punti)
    n_geo = sum(1 for p in punti if p["lat"] is not None and p["lon"] is not None)
    n_outlier_bbox = sum(
        1 for p in punti
        if p["lat"] is not None and p["lon"] is not None
        and not _in_bbox_italia(p["lat"], p["lon"])
    )
    mix = Counter(p["tipo"] or "Non specificata" for p in punti)
    return {
        "kpi": {
            "n_totale":            n_tot,
            "n_geo_referenziate":  n_geo,
            "pct_geo_referenziate": round(100 * n_geo / n_tot, 1) if n_tot else 0.0,
            "mix_tipologia":       dict(sorted(mix.items(), key=lambda kv: -kv[1])),
            "n_outlier_coordinate":   n_outlier_bbox,
            "n_coordinate_droppate":  n_droppati,
        },
        "punti": [
            {k: v for k, v in p.items() if not k.startswith("_")}
            for p in sorted(punti, key=lambda x: x["nome"])
        ],
    }


def _build_parafarmacie_section(punti: list[dict]) -> dict:
    """KPI + punti per parafarmacie. Pre-filtro outlier come per farmacie."""
    punti, n_droppati = _filter_outlier_coords(punti)
    n_tot = len(punti)
    n_geo = sum(1 for p in punti if p["lat"] is not None and p["lon"] is not None)
    n_outlier_bbox = sum(
        1 for p in punti
        if p["lat"] is not None and p["lon"] is not None
        and not _in_bbox_italia(p["lat"], p["lon"])
    )
    return {
        "kpi": {
            "n_totale":            n_tot,
            "n_geo_referenziate":  n_geo,
            "pct_geo_referenziate": round(100 * n_geo / n_tot, 1) if n_tot else 0.0,
            "n_outlier_coordinate":   n_outlier_bbox,
            "n_coordinate_droppate":  n_droppati,
        },
        "punti": [
            {k: v for k, v in p.items() if not k.startswith("_")}
            for p in sorted(punti, key=lambda x: x["nome"])
        ],
    }


def _build_ospedali_section(stab_map: dict) -> dict:
    stabs = list(stab_map.values())
    stabs.sort(key=lambda s: -s["totale_posti_letto"])

    n_stab = len(stabs)
    n_reparti = sum(d["n_reparti"] for s in stabs for d in s["discipline"])
    pl_ord = sum(d["ord"]    for s in stabs for d in s["discipline"])
    pl_pag = sum(d["pag"]    for s in stabs for d in s["discipline"])
    pl_dh  = sum(d["dh"]     for s in stabs for d in s["discipline"])
    pl_ds  = sum(d["ds"]     for s in stabs for d in s["discipline"])
    pl_tot = sum(d["totale"] for s in stabs for d in s["discipline"])

    # mix discipline aggregato per comune
    mix_disc: Counter = Counter()
    for s in stabs:
        for d in s["discipline"]:
            desc = d["descrizione"] or "(non specificata)"
            mix_disc[desc] += d["totale"]
    mix_disc_sorted = dict(sorted(mix_disc.items(), key=lambda kv: -kv[1]))

    return {
        "kpi": {
            "n_stabilimenti":           n_stab,
            "n_reparti_totali":         n_reparti,
            "posti_letto_totali":       pl_tot,
            "posti_letto_ordinaria":    pl_ord,
            "posti_letto_pagamento":    pl_pag,
            "posti_letto_day_hospital": pl_dh,
            "posti_letto_day_surgery":  pl_ds,
            "mix_discipline":           mix_disc_sorted,
        },
        "stabilimenti": stabs,
    }


def build_shards(
    farmacie_by_istat: dict[str, list[dict]],
    parafarmacie_by_istat: dict[str, list[dict]],
    ospedali_by_istat: dict[str, dict],
    discovery: dict,
) -> dict[str, dict]:
    """Costruisce shard per ogni comune che ha almeno una sezione popolata."""
    all_istats = (
        set(farmacie_by_istat.keys())
        | set(parafarmacie_by_istat.keys())
        | set(ospedali_by_istat.keys())
    )
    log.info("sanita_mds_build_shards_start", n_comuni=len(all_istats))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # _fonti structurato
    fonti = {
        "farmacie": {
            "url":      DATASET_PUBLIC_URLS["farmacie"],
            "data_riferimento": discovery["farmacie"].get("data_aggiornamento"),
            "aggiornamento":    "quotidiano",
        },
        "parafarmacie": {
            "url":      DATASET_PUBLIC_URLS["parafarmacie"],
            "data_riferimento": discovery["parafarmacie"].get("data_aggiornamento"),
            "aggiornamento":    "quotidiano",
        },
        "ospedali": {
            "url":         DATASET_PUBLIC_URLS["ospedali"],
            "anno_dati":   ANNO_OSPEDALI,
            "aggiornamento": "annuale (luglio dell'anno N+1)",
        },
    }

    shards: dict[str, dict] = {}
    for istat in all_istats:
        f_punti = farmacie_by_istat.get(istat) or []
        pf_punti = parafarmacie_by_istat.get(istat) or []
        osp_blob = ospedali_by_istat.get(istat) or {}

        # Anagrafica: preferisco quella di farmacie (sempre presente nei comuni
        # con farmacie); fallback parafarmacie, fallback ospedali
        anag: dict[str, str] = {}
        if f_punti:
            r = f_punti[0]
            anag = {"regione": r["_regione"], "provincia": r["_provincia"],
                    "comune":  r["_comune"]}
        elif pf_punti:
            r = pf_punti[0]
            anag = {"regione": r["_regione"], "provincia": r["_provincia"],
                    "comune":  r["_comune"]}
        elif osp_blob:
            anag = osp_blob["anag"]

        shard = {
            "_etl_version":   ETL_VERSION,
            "_source":        SOURCE_LABEL,
            "_license":       SOURCE_LICENSE,
            "_generated_at":  generated_at,
            "_fonti":         fonti,
            "istat_code":     istat,
            "comune":         anag.get("comune"),
            "provincia":      anag.get("provincia"),
            "regione":        anag.get("regione"),
            "farmacie":       _build_farmacie_section(f_punti) if f_punti else None,
            "parafarmacie":   _build_parafarmacie_section(pf_punti) if pf_punti else None,
            "ospedali":       _build_ospedali_section(osp_blob["stabilimenti"])
                              if osp_blob else None,
        }
        shards[istat] = shard

    log.info("sanita_mds_build_shards_done", n_shards=len(shards))
    return shards


# ============================================================================
# OUTPUT (local | R2 push)
# ============================================================================

def write_local(shards: dict[str, dict], outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    total_bytes = 0
    max_size = 0
    max_istat = None
    for istat, data in shards.items():
        path = outdir / f"{istat}.json"
        payload = json.dumps(data, ensure_ascii=False)
        path.write_text(payload, encoding="utf-8")
        sz = len(payload.encode("utf-8"))
        total_bytes += sz
        if sz > max_size:
            max_size = sz
            max_istat = istat
        n += 1
    log.info("sanita_mds_local_done",
             n=n, total_bytes=total_bytes,
             max_size=max_size, max_istat=max_istat,
             outdir=str(outdir))
    return n


def _md5_file(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def push_shards_to_r2(shard_dir: Path, force_upload: bool = False) -> dict:
    """Push paralleli su prefix sanita_mds/ con skip via md5/ETag.

    Pattern allineato a anncsu.py / aria.py: una list_objects_v2 paginata,
    diff md5 locale vs ETag remoto, upload paralleli con ThreadPoolExecutor.
    """
    if not shard_dir.exists():
        log.warning("sanita_mds_no_shard_dir", path=str(shard_dir))
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
        pag = _client.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=r2.get_bucket(), Prefix="sanita_mds/"):
            for o in page.get("Contents", []):
                name = o["Key"].split("/")[-1]
                etag = (o.get("ETag") or "").strip('"').lower()
                remote_etag[name] = etag
        log.info("sanita_mds_remote_listed", count=len(remote_etag))
    except Exception as e:
        log.warning("sanita_mds_list_failed", error=str(e))

    if force_upload:
        to_upload = list(shard_files)
        log.info("sanita_mds_force_upload", count=len(to_upload))
    else:
        to_upload = []
        n_same = 0
        for sf in shard_files:
            rmd5 = remote_etag.get(sf.name)
            if rmd5 is None or _md5_file(sf) != rmd5:
                to_upload.append(sf)
            else:
                n_same += 1
        log.info("sanita_mds_md5_compared",
                 total=len(shard_files), unchanged=n_same,
                 to_upload=len(to_upload))

    def _upload_one(sf: Path) -> str:
        r2.upload_file(sf, f"sanita_mds/{sf.name}",
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
                    log.info("sanita_mds_push_progress",
                             uploaded=uploaded, total=len(to_upload))
            except Exception as e:
                errors += 1
                log.error("sanita_mds_upload_failed", error=str(e))

    log.info("sanita_mds_push_done",
             uploaded=uploaded,
             unchanged=len(shard_files) - len(to_upload),
             errors=errors)
    return {
        "uploaded": uploaded,
        "unchanged": len(shard_files) - len(to_upload),
        "errors": errors,
    }


def push_aggregato_to_r2(aggr_path: Path) -> None:
    """Push lookup aggregato su sanita_mds-lookup.json (radice bucket)."""
    if not aggr_path.exists():
        log.warning("sanita_mds_no_aggregato", path=str(aggr_path))
        return
    r2.upload_file(aggr_path, "sanita_mds-lookup.json",
                   content_type="application/json")
    log.info("sanita_mds_aggregato_pushed", path=str(aggr_path))


# ============================================================================
# AGGREGATO (sanita_mds-lookup.json - per home/about, statistiche nazionali)
# ============================================================================

def build_aggregato(shards: dict[str, dict], discovery: dict) -> dict:
    n_farmacie_tot = 0
    n_parafarm_tot = 0
    n_ospedali_stab = 0
    n_pl_tot = 0
    comuni_con_farmacie = 0
    comuni_con_parafarm = 0
    comuni_con_ospedale = 0

    for istat, s in shards.items():
        if s.get("farmacie"):
            n_farmacie_tot += s["farmacie"]["kpi"]["n_totale"]
            comuni_con_farmacie += 1
        if s.get("parafarmacie"):
            n_parafarm_tot += s["parafarmacie"]["kpi"]["n_totale"]
            comuni_con_parafarm += 1
        if s.get("ospedali"):
            n_ospedali_stab += s["ospedali"]["kpi"]["n_stabilimenti"]
            n_pl_tot += s["ospedali"]["kpi"]["posti_letto_totali"]
            comuni_con_ospedale += 1

    return {
        "_etl_version":  ETL_VERSION,
        "_source":       SOURCE_LABEL,
        "_license":      SOURCE_LICENSE,
        "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovery":     discovery,
        "totali": {
            "farmacie":     n_farmacie_tot,
            "parafarmacie": n_parafarm_tot,
            "stabilimenti_ospedalieri": n_ospedali_stab,
            "posti_letto_totali":       n_pl_tot,
        },
        "copertura_comunale": {
            "farmacie":     comuni_con_farmacie,
            "parafarmacie": comuni_con_parafarm,
            "ospedali":     comuni_con_ospedale,
            "totale_comuni_italia": 7896,
        },
        "n_shards": len(shards),
    }


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Sanita' territoriale - Ministero della Salute"
    )
    parser.add_argument("--target", choices=["local", "r2"], default="local",
                        help="Output target: local files or R2 push")
    parser.add_argument("--output-dir", default="output/sanita_mds",
                        help="Local output directory")
    parser.add_argument("--cache-dir", default="cache/sanita_mds",
                        help="Cache directory for downloaded CSVs")
    parser.add_argument("--no-cache", action="store_true",
                        help="Re-download CSV ignorando la cache locale")
    parser.add_argument("--force-shard-upload", action="store_true",
                        help="Bypass md5 check, upload tutti gli shard (target=r2)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    shard_dir = output_dir / "shards"
    cache_dir = Path(args.cache_dir)
    aggr_path = output_dir / "sanita_mds-lookup.json"

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        log.info("sanita_mds_etl_start", target=args.target)

        # 1. Discovery URL freschi
        discovery = fetch_discovery()

        # 2. Download dei 3 CSV
        csv_paths: dict[str, Path] = {}
        for key, info in discovery.items():
            url = info["url"]
            fname = info.get("filename") or f"{key}.csv"
            cache_path = cache_dir / fname
            csv_paths[key] = download_csv(url, cache_path, force=args.no_cache)

        # 3. Parse
        ref_date = _today()
        log.info("sanita_mds_parse_start", ref_date=ref_date.isoformat())
        farmacie = parse_farmacie(csv_paths["farmacie"], ref_date)
        parafarm = parse_parafarmacie(csv_paths["parafarmacie"], ref_date)
        ospedali = parse_ospedali(csv_paths["ospedali"])

        # 4. Build shards
        shards = build_shards(farmacie, parafarm, ospedali, discovery)

        # 5. Write local
        n_written = write_local(shards, shard_dir)

        # 6. Build + write aggregato
        aggr = build_aggregato(shards, discovery)
        aggr_path.write_text(json.dumps(aggr, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        log.info("sanita_mds_aggregato_written",
                 path=str(aggr_path),
                 totals=aggr["totali"])

        # 7. Push R2 se richiesto
        if args.target == "r2":
            result = push_shards_to_r2(shard_dir,
                                       force_upload=args.force_shard_upload)
            push_aggregato_to_r2(aggr_path)
            uploaded_keys = (
                [f"sanita_mds/{k}.json" for k in shards.keys()]
                + ["sanita_mds-lookup.json"]
            )
            try:
                manifest.update_source("sanita_mds", uploaded_keys, status="ok")
            except Exception as e:
                log.warning("sanita_mds_manifest_update_failed", error=str(e))

            log.info("sanita_mds_r2_done", **result)

        log.info("sanita_mds_etl_done",
                 n_shards=n_written,
                 totali=aggr["totali"],
                 copertura=aggr["copertura_comunale"])
        return 0

    except Exception as e:
        log.error("sanita_mds_etl_failed",
                  error=str(e), error_type=type(e).__name__)
        if args.target == "r2":
            try:
                manifest.update_source("sanita_mds", [], status="failed")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    sys.exit(main())
