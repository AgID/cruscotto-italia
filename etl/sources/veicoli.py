"""ETL Veicoli e incidenti per comune.

Fonti:
  1) ISTAT SDMX 41_993 (DCIS_VEICOLIPRA_COM) - Stock parco veicolare PRA
     per comune al 31/12/2024. Copertura: 7896 comuni.
     Dimensioni: VEHICLE_TYPE x TIME_PERIOD. DATA_TYPE = VEHICFLEET.
     Codelist CL_CATEGVEICOLI:
        1  autovetture
        2  autobus e filobus
        7  motocicli
        8  motocarri
        9  altri veicoli
       10  autocarri
       11  motrici
       12  rimorchi
       13-19  autovetture circolanti Euro 0..6

  2) ISTAT SDMX 41_983 - Incidenti, morti e feriti - comuni
     Anni 2020-2024. DATA_TYPE = KILLINJ (M=morti, F=feriti)
     e ROADACC (RESULT=9 totale incidenti). Copertura ~6339 comuni.

  3) ACI Linked Open Data (lod.aci.it) - Prime iscrizioni autovetture nuove
     per comune e alimentazione. Anni 2019-2024.

Schema veicoli/<istat>.json (v0.1.0):
  parco_veicoli: totale, autovetture, motocicli, ..., euro: {0..6, pct_inquinanti}
  incidenti: ultimo_anno + serie_storica
  iscrizioni: ultimo_anno + serie_storica

Uso:
  python -m etl.sources.veicoli --target=local
  python -m etl.sources.veicoli --target=r2
  python -m etl.sources.veicoli --target=local --no-cache

Cadenza: annuale.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

ETL_VERSION = "0.1.0"
UA = "Cruscotto-Italia-ETL/0.1 (+https://github.com/piersoft/cruscotto-italia)"

ISTAT_BASE = "https://esploradati.istat.it/SDMXWS/rest/data"
ISTAT_HEADERS = {
    "Accept": "application/vnd.sdmx.data+csv;version=1.0.0",
    "User-Agent": UA,
}

ANNO_PARCO = 2024
ANNI_INCIDENTI = [2020, 2021, 2022, 2023, 2024]
ANNI_ISCRIZIONI = [2019, 2020, 2021, 2022, 2023, 2024]

VEHICLE_CATS = {
    "1":  "autovetture",
    "2":  "autobus",
    "7":  "motocicli",
    "8":  "motocarri",
    "9":  "altri",
    "10": "autocarri",
    "11": "motrici",
    "12": "rimorchi",
}
EURO_CODES = {
    "13": "euro_0",
    "14": "euro_1",
    "15": "euro_2",
    "16": "euro_3",
    "17": "euro_4",
    "18": "euro_5",
    "19": "euro_6",
}

CACHE_DIR = Path("/tmp/cruscotto-veicoli-cache")
OUTPUT_DIR = Path("output/veicoli")

# Capoluoghi delle aree a statuto speciale con PRA autonomo (VdA, PA Trento,
# PA Bolzano): ISTAT 41_993 attribuisce a questi 3 comuni veicoli che in
# realtà sono registrati a livello provinciale (società/enti con sede nel
# capoluogo) generando tassi di motorizzazione irrealistici (1310-5047).
# Marcati come _unreliable nello shard; il frontend mostra "n/d" per il parco.
# Verificato 2026-05-11: gli altri ~253 comuni dei 3 territori sono OK.
PARCO_UNRELIABLE_ISTAT = {
    "007003",  # Aosta (tasso ISTAT 5047/1000 vs atteso ~750)
    "021008",  # Bolzano/Bozen (tasso ISTAT 1310/1000 vs atteso ~700)
    "022205",  # Trento (tasso ISTAT 4959/1000 vs atteso ~700)
}


def fetch_istat_parco(anno: int = ANNO_PARCO, use_cache: bool = True) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"istat_41_993_parco_{anno}.csv"
    if out.exists() and out.stat().st_size > 100_000 and use_cache:
        log.info("istat_parco_cache_hit", path=str(out), size=out.stat().st_size, anno=anno)
        return out
    url = (f"{ISTAT_BASE}/41_993/A..VEHICFLEET."
           f"?startPeriod={anno}&endPeriod={anno}")
    log.info("istat_parco_download_start", url=url, anno=anno)
    t0 = time.time()
    resp = requests.get(url, headers=ISTAT_HEADERS, timeout=300, stream=True)
    resp.raise_for_status()
    with open(out, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)
    elapsed = time.time() - t0
    log.info("istat_parco_downloaded", anno=anno, bytes=out.stat().st_size,
             elapsed_s=round(elapsed, 1), path=str(out))
    return out


def parse_istat_parco(csv_path: Path) -> dict[str, dict]:
    by_istat: dict[str, dict] = {}
    n_rows = 0
    n_skipped = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n_rows += 1
            ref_area = (row.get("REF_AREA") or "").strip()
            vt = (row.get("VEHICLE_TYPE") or "").strip()
            val_raw = (row.get("OBS_VALUE") or "").strip()
            if not re.fullmatch(r"\d{6}", ref_area):
                n_skipped += 1
                continue
            try:
                val = int(float(val_raw))
            except (TypeError, ValueError):
                n_skipped += 1
                continue
            comune = by_istat.setdefault(ref_area, {
                "autovetture": 0, "autobus": 0, "motocicli": 0,
                "motocarri": 0, "altri": 0, "autocarri": 0,
                "motrici": 0, "rimorchi": 0,
                "euro": {f"euro_{i}": 0 for i in range(7)},
            })
            if vt in VEHICLE_CATS:
                comune[VEHICLE_CATS[vt]] = val
            elif vt in EURO_CODES:
                comune["euro"][EURO_CODES[vt]] = val

    for _istat, c in by_istat.items():
        c["totale"] = (c["autovetture"] + c["autobus"] + c["motocicli"]
                       + c["motocarri"] + c["altri"] + c["autocarri"]
                       + c["motrici"] + c["rimorchi"])
        euro = c["euro"]
        inquinanti = euro["euro_0"] + euro["euro_1"] + euro["euro_2"] + euro["euro_3"]
        auto = c["autovetture"]
        euro["pct_inquinanti"] = (
            round(inquinanti / auto * 100, 1) if auto > 0 else None
        )

    log.info("istat_parco_parsed", n_rows_csv=n_rows, n_skipped=n_skipped,
             n_comuni=len(by_istat))
    return by_istat



# ----------------------------------------------------------------------------
# ISTAT 41_983 - Incidenti, morti, feriti per comune
# ----------------------------------------------------------------------------
def fetch_istat_incidenti(anni: list[int] | None = None, use_cache: bool = True) -> Path:
    """Scarica il CSV ISTAT 41_983 con incidenti, morti e feriti per comune
    sull'intervallo di anni indicato. Singola chiamata SDMX con range temporale.
    """
    if anni is None:
        anni = ANNI_INCIDENTI
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    a_min, a_max = min(anni), max(anni)
    out = CACHE_DIR / f"istat_41_983_incidenti_{a_min}_{a_max}.csv"
    if out.exists() and out.stat().st_size > 50_000 and use_cache:
        log.info("istat_incidenti_cache_hit", path=str(out),
                 size=out.stat().st_size, range=f"{a_min}-{a_max}")
        return out

    # Pattern dimensioni: FREQ.REF_AREA.DATA_TYPE.RESULT
    # Wildcard: tutti i comuni, tutti i DATA_TYPE, tutti i RESULT
    url = (f"{ISTAT_BASE}/41_983/A..."
           f"?startPeriod={a_min}&endPeriod={a_max}")
    log.info("istat_incidenti_download_start", url=url, range=f"{a_min}-{a_max}")
    t0 = time.time()
    resp = requests.get(url, headers=ISTAT_HEADERS, timeout=300, stream=True)
    resp.raise_for_status()
    with open(out, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)
    elapsed = time.time() - t0
    log.info("istat_incidenti_downloaded", bytes=out.stat().st_size,
             elapsed_s=round(elapsed, 1), path=str(out))
    return out


def parse_istat_incidenti(csv_path: Path) -> dict[str, dict]:
    """Legge il CSV ISTAT 41_983 e costruisce un dict istat_code -> blocco
    incidenti, con ultimo_anno (= anno piu' recente con dati) e serie_storica
    per gli anni disponibili.

    DATA_TYPE: KILLINJ (con RESULT=F feriti, M morti) | ROADACC (RESULT=9 incidenti)
    """
    # Struttura intermedia: by_istat[istat][anno] = {"incidenti":N, "morti":N, "feriti":N}
    raw: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: defaultdict(
        lambda: {"incidenti": 0, "morti": 0, "feriti": 0}
    ))
    n_rows = 0
    n_skipped = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n_rows += 1
            ref_area = (row.get("REF_AREA") or "").strip()
            dtype = (row.get("DATA_TYPE") or "").strip()
            result = (row.get("RESULT") or "").strip()
            period = (row.get("TIME_PERIOD") or "").strip()
            val_raw = (row.get("OBS_VALUE") or "").strip()
            if not re.fullmatch(r"\d{6}", ref_area):
                n_skipped += 1
                continue
            try:
                anno = int(period)
                val = int(float(val_raw))
            except (TypeError, ValueError):
                n_skipped += 1
                continue
            blk = raw[ref_area][anno]
            if dtype == "ROADACC" and result == "9":
                blk["incidenti"] = val
            elif dtype == "KILLINJ" and result == "M":
                blk["morti"] = val
            elif dtype == "KILLINJ" and result == "F":
                blk["feriti"] = val
            else:
                n_skipped += 1

    # Costruisci output finale: serie ordinata anno crescente + ultimo_anno
    by_istat: dict[str, dict] = {}
    for istat, per_anno in raw.items():
        anni_ord = sorted(per_anno.keys())
        if not anni_ord:
            continue
        ultimo = anni_ord[-1]
        u = per_anno[ultimo]
        by_istat[istat] = {
            "ultimo_anno": {
                "anno": ultimo,
                "incidenti": u["incidenti"],
                "morti": u["morti"],
                "feriti": u["feriti"],
            },
            "serie_storica": {
                "anni":     anni_ord,
                "incidenti": [per_anno[a]["incidenti"] for a in anni_ord],
                "morti":     [per_anno[a]["morti"]     for a in anni_ord],
                "feriti":    [per_anno[a]["feriti"]    for a in anni_ord],
            },
        }

    log.info("istat_incidenti_parsed", n_rows_csv=n_rows, n_skipped=n_skipped,
             n_comuni=len(by_istat))
    return by_istat



# ----------------------------------------------------------------------------
# ACI Linked Open Data - Prime iscrizioni autovetture per comune
# ----------------------------------------------------------------------------
ACI_BASE = "http://lod.aci.it"

# URL pagine dataset ACI per anno (slug stabile, percent-encoded en-dash U+2013)
ACI_DATASET_PAGE_TPL = (
    ACI_BASE
    + "/dataset/prime-iscrizioni-veicoli-nuovi-nel-{anno}-"
    + "%E2%80%93-autovetture-ente-territoriale-e-alimentazione"
)

# Categorie alimentazione ACI raggruppate in 5 macro-classi per il dashboard
# Mappa speciale nome ACI -> codice ISTAT per casi non risolvibili via
# anagrafica (nomi bilingui o post-fusione recente). Codici verificati
# dall'anagrafica unificata stessa il 2026-05-11.
SPECIAL_ACI = {
    # Comuni bilingui (nome ufficiale anagrafica include la variante linguistica)
    "DUINO AURISINA":         "032001",   # Duino-Aurisina/Devin Nabrezina (TS)
    "MONTAGNA":               "021053",   # Montagna sulla Strada del Vino/Montan (BZ)
    "SAN GIOVANNI DI FASSA":  "022250",   # Sen Jan di Fassa (TN, fus. 2018)
    "SALORNO":                "021076",   # Salorno sulla Strada del Vino (BZ)
    "PUEGNAGO SUL GARDA":     "017158",   # Puegnago del Garda (BS, rinominato)
    # Fusioni recenti: ACI usa ancora nome pre-fusione
    "BARDELLO":               "012144",   # in Bardello con Malgesso e Bregano (2024)
    "MALGESSO":               "012144",   # in Bardello con Malgesso e Bregano (2024)
    "CAMPOSPINOSO":           "018026",   # in Campospinoso Albaredo (2024)
    "CASORZO":                "005020",   # in Casorzo Monferrato (2023)
    "GRANA":                  "005056",   # in Grana Monferrato (2023)
    "MONTEMAGNO":             "005077",   # in Montemagno Monferrato (2023)
    "MORANSENGO":             "005122",   # in Moransengo-Tonengo (2023)
    "TONENGO":                "005122",   # in Moransengo-Tonengo (2023)
    "RONAGO":                 "013256",   # in Uggiate con Ronago (2024)
    "VENDROGNO":              "097008",   # in Bellano (2018)
    "MONTECICCARDO":          "041044",   # in Pesaro (2024)
    "POPOLI":                 "068033",   # in Popoli Terme (2024)
    "PUEGNAGO":               "017158",   # in Puegnago del Garda (rinominato)
    "VIGHIZZOLO DESTE":       "028037",   # in Este (PD, fus. 2024)
    # Fusioni Trentino: tutti in nuovi comuni unici
    "BREZ":                   "022253",   # in Novella (2020)
    "CAGNO":                  "022253",   # in Novella (2020) [in TN, non in CO]
    "CLOZ":                   "022253",   # in Novella (2020)
    "REVO":                   "022253",   # in Novella (2020)
    "ROMALLO":                "022253",   # in Novella (2020)
    "CARANO":                 "022254",   # in Ville di Fiemme (2020)
    "DAIANO":                 "022254",   # in Ville di Fiemme (2020)
    "VARENA":                 "022254",   # in Ville di Fiemme (2020)
    "CASTELFONDO":            "022252",   # in Borgo d'Anaunia (2020)
    "FONDO":                  "022252",   # in Borgo d'Anaunia (2020)
    "MALOSCO":                "022252",   # in Borgo d'Anaunia (2020)
    "FAEDO":                  "022167",   # in San Michele all'Adige (2020)
}

ACI_ALIMENTAZIONI = {
    "Benzina":                    "benzina",
    "Gasolio":                    "gasolio",
    "Elettrica":                  "elettriche",
    "Ibrido Benzina-Elettrico":   "ibride",
    "Ibrido Gasolio-Elettrico":   "ibride",
    "Benzina e Gas Liquido":      "gas_metano_gpl",
    "Benzina e Metano":           "gas_metano_gpl",
}


def _aci_discover_csv_url(anno: int) -> str | None:
    """Scrappa la pagina dataset ACI dell'anno e estrae l'URL del CSV.
    Pattern URL diversi tra 2019-2023 (titolo italiano) e 2024 (snake_case)."""
    # Shortcut: 2024 ha URL stabile
    if anno == 2024:
        return f"{ACI_BASE}/sites/default/files/statistiche_prime_iscrizioni_{anno}.csv"
    page_url = ACI_DATASET_PAGE_TPL.format(anno=anno)
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as e:
        log.warning("aci_page_fetch_fail", anno=anno, err=str(e))
        return None
    m = re.search(r'href="(http://lod\.aci\.it/sites/default/files/[^"]+\.csv)"', html)
    return m.group(1) if m else None


def fetch_aci_iscrizioni(anno: int, use_cache: bool = True) -> Path | None:
    """Scarica il CSV ACI prime iscrizioni per l'anno. Ritorna None su fallimento."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"aci_iscrizioni_{anno}.csv"
    if out.exists() and out.stat().st_size > 100_000 and use_cache:
        log.info("aci_cache_hit", anno=anno, path=str(out), size=out.stat().st_size)
        return out

    csv_url = _aci_discover_csv_url(anno)
    if not csv_url:
        log.warning("aci_csv_url_not_found", anno=anno)
        return None

    log.info("aci_download_start", anno=anno, url=csv_url)
    t0 = time.time()
    try:
        req = urllib.request.Request(csv_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp, open(out, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        log.error("aci_download_fail", anno=anno, err=str(e))
        return None
    elapsed = time.time() - t0
    log.info("aci_downloaded", anno=anno, bytes=out.stat().st_size,
             elapsed_s=round(elapsed, 1), path=str(out))
    return out


def parse_aci_iscrizioni(csv_path: Path, anno: int,
                        nome_to_istat: dict[str, str]) -> dict[str, dict]:
    """Parsa un CSV ACI e ritorna istat_code -> {totale, per_alimentazione...}.
    Filtra solo tipoEnteTerritoriale=='Comune' e risolve nome->ISTAT.
    """
    from etl.sources.pnrr_progetti import normalize

    by_istat: dict[str, dict] = {}
    n_rows = 0
    n_matched = 0
    n_unmatched = 0
    unmatched_names: set[str] = set()

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n_rows += 1
            if (row.get("tipoEnteTerritoriale") or "").strip() != "Comune":
                continue
            nome = (row.get("enteTerritoriale") or "").strip()
            alim = (row.get("alimentazione") or "").strip()
            try:
                val = int(row.get("primeIscrizioni") or 0)
            except (TypeError, ValueError):
                continue

            normalized = normalize(nome)
            istat = nome_to_istat.get(normalized) or SPECIAL_ACI.get(normalized)
            if not istat:
                n_unmatched += 1
                if len(unmatched_names) < 20:
                    unmatched_names.add(nome)
                continue
            n_matched += 1

            blk = by_istat.setdefault(istat, {
                "anno": anno,
                "totale": 0,
                "benzina": 0, "gasolio": 0, "elettriche": 0,
                "ibride": 0, "gas_metano_gpl": 0,
            })
            macro = ACI_ALIMENTAZIONI.get(alim)
            if macro:
                blk[macro] = blk.get(macro, 0) + val
            blk["totale"] += val

    # Calcolo % elettriche+ibride
    for _istat, c in by_istat.items():
        tot = c["totale"]
        if tot > 0:
            c["pct_elettriche_ibride"] = round(
                (c["elettriche"] + c["ibride"]) / tot * 100, 1
            )
        else:
            c["pct_elettriche_ibride"] = None

    log.info("aci_parsed", anno=anno, n_rows=n_rows, n_matched=n_matched,
             n_unmatched=n_unmatched, n_comuni=len(by_istat),
             sample_unmatched=sorted(unmatched_names)[:5])
    return by_istat



# ----------------------------------------------------------------------------
# Build shard + persistenza
# ----------------------------------------------------------------------------
def load_popolazione_from_dashboard() -> dict[str, int]:
    """Legge la popolazione da dashboard/<istat>.json su R2.
    Cache locale: /tmp/cruscotto-veicoli-cache/popolazione_from_dashboard.json
    Per evitare di scaricare 7896 file ad ogni run.
    """
    cache_f = CACHE_DIR / "popolazione_from_dashboard.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache_f.exists() and cache_f.stat().st_size > 10_000:
        try:
            data = json.loads(cache_f.read_text())
            log.info("popolazione_dashboard_cache_hit", n=len(data))
            return {k: int(v) for k, v in data.items()}
        except Exception:
            pass

    log.info("popolazione_dashboard_loading_start",
             info="reading all dashboard shards (slow first time)")
    out: dict[str, int] = {}
    keys = r2.list_keys("dashboard/")
    log.info("popolazione_dashboard_keys", n=len(keys))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def fetch_pop(key: str) -> tuple[str, int | None]:
        # extract istat from "dashboard/<istat>.json"
        istat = key.split("/")[-1].replace(".json", "")
        try:
            tmp = CACHE_DIR / f"_d_{istat}.json"
            r2.download_file(key, tmp)
            d = json.loads(tmp.read_text())
            tmp.unlink(missing_ok=True)
            pop = (d.get("demografia", {}).get("popolazione_totale")
                   or d.get("anagrafica", {}).get("kpi", {}).get("popolazione"))
            return (istat, int(pop) if pop else None)
        except Exception:
            return (istat, None)

    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = [ex.submit(fetch_pop, k) for k in keys]
        n_done = 0
        for fut in as_completed(futs):
            istat, pop = fut.result()
            if pop is not None:
                out[istat] = pop
            n_done += 1
            if n_done % 1000 == 0:
                log.info("popolazione_dashboard_progress", done=n_done, total=len(keys))
    log.info("popolazione_dashboard_done", n=len(out))
    cache_f.write_text(json.dumps(out))
    return out


def load_anagrafica_nomi_e_popolazione() -> tuple[dict[str, str], dict[str, int]]:
    """Carica nome + popolazione dall'anagrafica unificata.
    Cerca prima il parquet locale, poi scarica da R2 (lookup/anagrafica_unificata.parquet).
    Ritorna (nome_by_istat, pop_by_istat). Tollera mancanze.
    """
    nome_by_istat: dict[str, str] = {}
    pop_by_istat: dict[str, int] = {}
    try:
        import pandas as pd
        local = Path("output/anagrafica/anagrafica_unificata.parquet")
        if not local.exists():
            # Fallback: scarica da R2 in cache
            cached = CACHE_DIR / "anagrafica_unificata.parquet"
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if not cached.exists():
                log.info("anagrafica_r2_download", key="lookup/anagrafica_unificata.parquet")
                try:
                    r2.download_file("lookup/anagrafica_unificata.parquet", cached)
                except Exception as e:
                    log.warning("anagrafica_r2_fail", err=str(e))
                    return nome_by_istat, pop_by_istat
            local = cached
        df = pd.read_parquet(local)
        cols = {c.lower(): c for c in df.columns}
        istat_col = (cols.get("codice_istat") or cols.get("istat_code")
                     or cols.get("istat"))
        denom_col = cols.get("denominazione") or cols.get("nome")
        pop_col   = cols.get("popolazione") or cols.get("popolazione_totale")
        if istat_col and denom_col:
            for r in df[[istat_col, denom_col]].itertuples(index=False):
                if r[1]:
                    nome_by_istat[str(r[0])] = str(r[1])
        if istat_col and pop_col:
            import math
            for r in df[[istat_col, pop_col]].itertuples(index=False):
                try:
                    v = r[1]
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        continue
                    pop_by_istat[str(r[0])] = int(v)
                except (TypeError, ValueError):
                    pass
        log.info("anagrafica_caricata", n_nomi=len(nome_by_istat),
                 n_pop=len(pop_by_istat))
    except Exception as e:
        log.warning("anagrafica_load_fail", err=str(e))
    return nome_by_istat, pop_by_istat


def build_shard(istat: str, anag_nome: str | None,
                parco: dict | None, incid: dict | None,
                iscr: dict | None, pop: int | None) -> dict:
    out = {
        "_etl_version": ETL_VERSION,
        "_source": ("ISTAT 41_993 (parco PRA) + ISTAT 41_983 (incidenti) + "
                    "ACI LOD (iscrizioni)"),
        # Timestamp stabile a livello di GIORNO: evita re-upload di shard
        # identici quando l'ETL gira più volte lo stesso giorno. Cambia
        # automaticamente al run successivo se i dati cambiano (md5 diverso),
        # oppure al cambio di data anche se i dati sono identici (tollerabile).
        "_generated_at": datetime.now(timezone.utc).date().isoformat(),
        "_anno_dati_parco":       ANNO_PARCO,
        "_anno_dati_incidenti":   (incid or {}).get("ultimo_anno", {}).get("anno"),
        "_anno_dati_iscrizioni":  (iscr or {}).get("ultimo_anno", {}).get("anno"),
        "_aggiornamento_atteso": "annuale (parco dic, incidenti lug, iscrizioni gen)",
        "istat_code": istat,
        "denominazione": anag_nome,
        "popolazione": pop,
    }
    if parco:
        p = dict(parco)
        p["anno"] = ANNO_PARCO
        if pop and pop > 0 and p.get("autovetture"):
            p["tasso_motorizzazione_per_1000_ab"] = round(
                p["autovetture"] / pop * 1000, 1
            )
        else:
            p["tasso_motorizzazione_per_1000_ab"] = None
        # Marca come unreliable i 3 capoluoghi a PRA autonomo
        if istat in PARCO_UNRELIABLE_ISTAT:
            p["_unreliable"] = True
            p["_unreliable_reason"] = (
                "Per i capoluoghi di Valle d'Aosta, Provincia Autonoma di "
                "Trento e Provincia Autonoma di Bolzano, ISTAT 41_993 "
                "attribuisce al codice del capoluogo veicoli registrati a "
                "livello provinciale (società/enti). Il dato non riflette "
                "il parco circolante effettivo del solo comune ed è qui "
                "marcato come non confrontabile. Per il dato a livello "
                "provinciale fare riferimento ad ASTAT (BZ), ISPAT (TN) e "
                "Regione Valle d'Aosta."
            )
            # Annullo il tasso che sarebbe fuorviante; lascio i numeri assoluti
            # per trasparenza ma il frontend deve mostrarli come "n/d"
            p["tasso_motorizzazione_per_1000_ab"] = None
        out["parco_veicoli"] = p
    if incid:
        i = json.loads(json.dumps(incid))
        u = i["ultimo_anno"]
        if pop and pop > 0:
            i["ultimo_anno"]["morti_per_10k_ab"]  = round(u["morti"]  / pop * 10000, 2)
            i["ultimo_anno"]["feriti_per_10k_ab"] = round(u["feriti"] / pop * 10000, 2)
        else:
            i["ultimo_anno"]["morti_per_10k_ab"]  = None
            i["ultimo_anno"]["feriti_per_10k_ab"] = None
        out["incidenti"] = i
    if iscr:
        out["iscrizioni"] = iscr
    return out


def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def write_shards_local(shards: dict[str, dict]) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for istat, shard in shards.items():
        p = OUTPUT_DIR / f"{istat}.json"
        p.write_text(json.dumps(shard, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    log.info("shards_local_written", n=len(shards), dir=str(OUTPUT_DIR))
    return len(shards)


def push_shards_r2(shards: dict[str, dict], force: bool = False) -> tuple[int, int]:
    """Push shard su R2 con skip-by-md5. Pattern aria.py:
    - 1 list_objects_v2 paginato per leggere TUTTI gli ETag remoti in una volta
    - Upload paralleli con ThreadPoolExecutor
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(shards)
    log.info("shards_r2_start", total=total, force=force)
    t0 = time.time()

    # 1) Lista oggetti remoti correnti (1 chiamata paginata invece di 7913 HEAD)
    remote_etag: dict[str, str] = {}
    if not force:
        try:
            client = r2.get_r2_client()
            pag = client.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=r2.get_bucket(), Prefix="veicoli/"):
                for o in page.get("Contents", []):
                    name = o["Key"].split("/")[-1]
                    etag = (o.get("ETag") or "").strip('"').lower()
                    remote_etag[name] = etag
            log.info("shards_r2_remote_listed", count=len(remote_etag))
        except Exception as e:
            log.warning("shards_r2_list_fail", err=str(e))

    # 2) Calcola md5 locali, decide cosa caricare
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
        key = f"veicoli/{istat}.json"
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
        manifest.update_source("veicoli", files_for_manifest, status="ok")
    return uploaded, n_same


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL Veicoli e incidenti")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    log.info("etl_veicoli_start", target=args.target, version=ETL_VERSION)
    use_cache = not args.no_cache

    csv_parco = fetch_istat_parco(anno=ANNO_PARCO, use_cache=use_cache)
    parco_by_istat = parse_istat_parco(csv_parco)

    csv_incid = fetch_istat_incidenti(anni=ANNI_INCIDENTI, use_cache=use_cache)
    incidenti_by_istat = parse_istat_incidenti(csv_incid)

    # ACI - ciclo su tutti gli anni
    from etl.sources.pnrr_progetti import load_nome_to_istat
    nome_to_istat = load_nome_to_istat()
    log.info("aci_lookup_loaded", n_names=len(nome_to_istat))

    aci_per_anno: dict[int, dict[str, dict]] = {}
    for anno in ANNI_ISCRIZIONI:
        csv_p = fetch_aci_iscrizioni(anno, use_cache=use_cache)
        if csv_p is None:
            log.warning("aci_anno_skipped", anno=anno)
            continue
        aci_per_anno[anno] = parse_aci_iscrizioni(csv_p, anno, nome_to_istat)

    # Costruisco blocco iscrizioni per ogni comune: ultimo_anno + serie_storica
    iscrizioni_by_istat: dict[str, dict] = {}
    anni_disp = sorted(aci_per_anno.keys())
    if anni_disp:
        ultimo = anni_disp[-1]
        # Universo: tutti i comuni che compaiono in almeno un anno
        all_istat: set[str] = set()
        for a in anni_disp:
            all_istat.update(aci_per_anno[a].keys())
        for istat in all_istat:
            u = aci_per_anno.get(ultimo, {}).get(istat)
            ult_blk = u if u else {
                "anno": ultimo, "totale": 0, "benzina": 0, "gasolio": 0,
                "elettriche": 0, "ibride": 0, "gas_metano_gpl": 0,
                "pct_elettriche_ibride": None,
            }
            serie_tot = []
            serie_eh = []
            serie_el = []
            serie_ib = []
            for a in anni_disp:
                blk = aci_per_anno[a].get(istat, {})
                serie_tot.append(blk.get("totale", 0))
                serie_el.append(blk.get("elettriche", 0))
                serie_ib.append(blk.get("ibride", 0))
                serie_eh.append(blk.get("elettriche", 0) + blk.get("ibride", 0))
            iscrizioni_by_istat[istat] = {
                "ultimo_anno": ult_blk,
                "serie_storica": {
                    "anni": anni_disp,
                    "totale": serie_tot,
                    "elettriche": serie_el,
                    "ibride": serie_ib,
                    "elettriche_ibride": serie_eh,
                },
            }
    log.info("aci_iscrizioni_built", n_comuni=len(iscrizioni_by_istat),
             anni_disp=anni_disp)

    for sample_istat in ["016024", "058091", "072006", "075035"]:
        c = parco_by_istat.get(sample_istat)
        if c:
            log.info("sample", istat=sample_istat,
                     totale=c["totale"], autovetture=c["autovetture"],
                     euro_6=c["euro"]["euro_6"],
                     pct_inquinanti=c["euro"]["pct_inquinanti"])
        else:
            log.warning("sample_missing", istat=sample_istat)

    # Aggiungo sample incidenti
    for sample_istat in ["016024", "058091", "072006", "075035"]:
        i = incidenti_by_istat.get(sample_istat)
        if i:
            u = i["ultimo_anno"]
            log.info("sample_incidenti", istat=sample_istat,
                     anno=u["anno"], incidenti=u["incidenti"],
                     morti=u["morti"], feriti=u["feriti"])
        else:
            log.warning("sample_incidenti_missing", istat=sample_istat)

    for sample_istat in ["016024", "058091", "072006", "075035"]:
        i = iscrizioni_by_istat.get(sample_istat)
        if i:
            u = i["ultimo_anno"]
            s = i["serie_storica"]
            log.info("sample_aci_ts", istat=sample_istat, ultimo=u["anno"],
                     u_totale=u["totale"], u_eh_pct=u["pct_elettriche_ibride"],
                     serie_eh=s["elettriche_ibride"])
        else:
            log.warning("sample_aci_missing", istat=sample_istat)

    # === Carica anagrafica per nomi (parquet R2) + popolazione (dashboard R2) ===
    nome_by_istat, pop_by_istat = load_anagrafica_nomi_e_popolazione()
    if not pop_by_istat:
        log.info("popolazione_fallback_to_dashboard")
        pop_by_istat = load_popolazione_from_dashboard()

    # === Universo: union dei 3 dataset ===
    all_istat: set[str] = (set(parco_by_istat.keys())
                           | set(incidenti_by_istat.keys())
                           | set(iscrizioni_by_istat.keys()))
    log.info("shards_universe", n=len(all_istat))

    # === Build shards ===
    shards: dict[str, dict] = {}
    for istat in all_istat:
        shards[istat] = build_shard(
            istat=istat,
            anag_nome=nome_by_istat.get(istat),
            parco=parco_by_istat.get(istat),
            incid=incidenti_by_istat.get(istat),
            iscr=iscrizioni_by_istat.get(istat),
            pop=pop_by_istat.get(istat),
        )

    # === Sample dump ===
    for sample_istat in ["016024", "058091", "072006", "075035"]:
        s = shards.get(sample_istat)
        if not s:
            continue
        pv = s.get("parco_veicoli", {})
        inc = s.get("incidenti", {}).get("ultimo_anno", {})
        isc = s.get("iscrizioni", {}).get("ultimo_anno", {})
        log.info("sample_shard", istat=sample_istat,
                 denom=s.get("denominazione"), pop=s.get("popolazione"),
                 auto=pv.get("autovetture"),
                 tasso_motorizz=pv.get("tasso_motorizzazione_per_1000_ab"),
                 morti=inc.get("morti"),
                 morti_per_10k=inc.get("morti_per_10k_ab"),
                 pct_eh=isc.get("pct_elettriche_ibride"))

    # === Persistenza ===
    if args.target == "local":
        write_shards_local(shards)
    elif args.target == "r2":
        write_shards_local(shards)
        push_shards_r2(shards, force=False)

    log.info("etl_veicoli_done",
             n_shards=len(shards),
             n_parco=len(parco_by_istat),
             n_incidenti=len(incidenti_by_istat),
             n_iscrizioni=len(iscrizioni_by_istat),
             target=args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
