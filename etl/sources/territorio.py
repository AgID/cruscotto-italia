"""ETL Tab Territorio: 3 fonti ISPRA unificate per shard comunale.

Fonti:
- ISPRA Consumo di Suolo (XLSX 2025, anni 2006-2024)
  https://www.isprambiente.gov.it/.../consumo_di_suolo_estratto_dati_2025_anni_2006_2024.xlsx
- ISPRA IdroGEO PIR API REST (Pericolosita' e Indicatori di Rischio)
  https://idrogeo.isprambiente.it/api/pir/comuni/{istat_no_pad}
- ISPRA Catasto Nazionale Rifiuti (CSV per anno, 2010-2024)
  https://www.catasto-rifiuti.isprambiente.it/get/getDettaglioComunale.csv.php?aa={ANNO}

Licenza: tutte le fonti CC-BY 4.0

Output:
  territorio/<istat>.json per ogni comune del bundle anagrafica.
  Schema: kpi (sintesi), suolo (stock + serie storica), rischio_idrogeologico
  (alluvioni + frane), rifiuti (ultimo anno + serie storica % RD), geo (osmid + extent).

Strategia ETL:
  Fase A — Suolo: download XLSX 1.7MB + parse openpyxl
  Fase B — IdroGEO: 7896 chiamate API parallele (20 thread, ~3min)
  Fase C — Rifiuti: 15 download CSV (45MB totali) + parse + aggregazione storica
  Fase D — Merge & shard: 1 file JSON per comune del bundle anagrafica

Usage:
  python -m etl.sources.territorio --target=local
  python -m etl.sources.territorio --target=r2
  python -m etl.sources.territorio --target=local --no-cache
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# URLs e costanti
# ---------------------------------------------------------------------------
ISPRA_SUOLO_URL = (
    "https://www.isprambiente.gov.it/it/attivita/suolo-e-territorio/suolo/"
    "il-consumo-di-suolo/consumo_di_suolo_estratto_dati_2025_anni_2006_2024.xlsx"
)
IDROGEO_API_BASE = "https://idrogeo.isprambiente.it/api/pir/comuni"
RIFIUTI_CSV_URL = (
    "https://www.catasto-rifiuti.isprambiente.it/get/"
    "getDettaglioComunale.csv.php?aa={anno}"
)
RIFIUTI_ANNO_MIN = 2010
RIFIUTI_ANNO_MAX = 2024

# Bundle anagrafica (compatibilita con altri ETL)
ANAGRAFICA_BUNDLE_KEY = "lookup/comuni-bundle.json"

# Limiti operativi
IDROGEO_PARALLEL = 20  # thread paralleli per chiamate IdroGEO
IDROGEO_TIMEOUT = 15
HTTP_USER_AGENT = (
    "cruscotto-italia-etl/0.1 (+https://github.com/piersoft/cruscotto-italia)"
)


# ---------------------------------------------------------------------------
# Helper: parsing numerico italiano (1.234,56 -> 1234.56)
# ---------------------------------------------------------------------------
def parse_it_num(s: str | None) -> float | None:
    """Parse numero con formato italiano: '1.234,56' -> 1234.56.

    Restituisce None per stringhe vuote, '-', 'n.d.', ecc.
    """
    if s is None:
        return None
    s = s.strip()
    if s in ("", "-", "n.d.", "N.D.", "ND"):
        return None
    s = s.replace("%", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def round2(x: float | None) -> float | None:
    """Arrotonda a 2 decimali se non None (gestisce float di precisione XLSX)."""
    return round(x, 2) if x is not None else None


# ---------------------------------------------------------------------------
# Bundle anagrafica
# ---------------------------------------------------------------------------
def load_nome_to_istat() -> dict:
    """Carica anagrafica bundle da R2.

    Schema bundle: {"comuni": {istat: {denominazione, provincia, regione, ...}}}
    Ritorna dict {istat_code: row_dict} (passthrough del dict comuni).
    """
    log.info("anagrafica_loading")
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    obj = client.get_object(Bucket=bucket, Key=ANAGRAFICA_BUNDLE_KEY)
    bundle = json.loads(obj["Body"].read())
    comuni = bundle.get("comuni", {})
    log.info("anagrafica_loaded", n_comuni=len(comuni))
    return comuni


# ===========================================================================
# FASE A — ISPRA Consumo di Suolo (XLSX)
# ===========================================================================
def download_suolo_xlsx(cache_dir: Path, force: bool = False) -> Path:
    """Download dell'XLSX ISPRA Suolo (1.7 MB). Cache locale per ripartibilita."""
    out = cache_dir / "ispra_suolo_2025.xlsx"
    if out.exists() and not force:
        log.info("suolo_xlsx_cached", path=str(out), size=out.stat().st_size)
        return out

    log.info("suolo_xlsx_download_start", url=ISPRA_SUOLO_URL)
    req = urllib.request.Request(
        ISPRA_SUOLO_URL, headers={"User-Agent": HTTP_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(out, "wb") as f:
            f.write(resp.read())
    log.info("suolo_xlsx_download_done", size=out.stat().st_size)
    return out


def parse_suolo_xlsx(xlsx_path: Path) -> dict:
    """Parse XLSX foglio 'Comuni_2006_2024' -> dict {istat: suolo_data}.

    Schema output per comune:
      {
        "stock_2024": { "ha": float, "pct": float },
        "serie_storica": [
          {"intervallo": "2006-2012", "netto_ha": float, "ripristino_ha": float},
          ... (11 intervalli)
        ]
      }

    NB: PRO_COM nell'XLSX e' int senza padding (es. 75035), zero-padded a 6 cifre
    qui per allinearsi a istat_code del cruscotto (075035).
    """
    import openpyxl

    log.info("suolo_xlsx_parse_start", path=str(xlsx_path))
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb["Comuni_2006_2024"]

    rows = ws.iter_rows(values_only=True)
    header = next(rows)

    # Mappa intervalli 2006-2012 .. 2023-2024 (11 intervalli)
    # Schema header: PRO_COM, Nome_Comune, Nome_Regione, Nome_Provincia,
    # poi triple [netto, lordo, ripristino] per ogni intervallo,
    # poi "Suolo consumato 2024 [ettari]" e "[%]"
    intervalli = []
    for i, h in enumerate(header):
        if h and str(h).startswith("Incremento netto "):
            # estrai "2006-2012" da "Incremento netto 2006-2012 [ettari]"
            label = str(h).replace("Incremento netto ", "").split(" [")[0]
            intervalli.append((label, i, i + 2))  # idx netto, idx ripristino

    log.info("suolo_intervalli_detected", intervalli=[i[0] for i in intervalli])

    stock_ha_idx = None
    stock_pct_idx = None
    for i, h in enumerate(header):
        if h and str(h).startswith("Suolo consumato 2024 [ettari]"):
            stock_ha_idx = i
        elif h and str(h).startswith("Suolo consumato 2024 [%]"):
            stock_pct_idx = i

    if stock_ha_idx is None or stock_pct_idx is None:
        log.error("suolo_stock_columns_not_found")
        return {}

    suolo_by_istat = {}
    n_rows = 0
    for row in rows:
        if not row or row[0] is None:
            continue
        try:
            pro_com = int(row[0])
        except (ValueError, TypeError):
            continue
        istat = f"{pro_com:06d}"
        n_rows += 1

        serie = []
        for label, netto_i, ripr_i in intervalli:
            netto = row[netto_i] if netto_i < len(row) else None
            ripr = row[ripr_i] if ripr_i < len(row) else None
            serie.append({
                "intervallo": label,
                "netto_ha": round2(netto) if isinstance(netto, (int, float)) else None,
                "ripristino_ha": round2(ripr) if isinstance(ripr, (int, float)) else None,
            })

        stock_ha = row[stock_ha_idx] if stock_ha_idx < len(row) else None
        stock_pct = row[stock_pct_idx] if stock_pct_idx < len(row) else None

        suolo_by_istat[istat] = {
            "stock_2024": {
                "ha": round2(stock_ha) if isinstance(stock_ha, (int, float)) else None,
                "pct": round2(stock_pct) if isinstance(stock_pct, (int, float)) else None,
            },
            "serie_storica": serie,
        }

    log.info("suolo_xlsx_parse_done", n_comuni=len(suolo_by_istat), n_rows=n_rows)
    return suolo_by_istat


# ===========================================================================
# FASE B — ISPRA IdroGEO (API REST)
# ===========================================================================
def fetch_idrogeo_one(pro_com: int) -> dict | None:
    """Singola chiamata API IdroGEO. Ritorna None per HTTP 404 o errori."""
    url = f"{IDROGEO_API_BASE}/{pro_com}"
    req = urllib.request.Request(
        url, headers={"User-Agent": HTTP_USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=IDROGEO_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        log.warning("idrogeo_http_error", pro_com=pro_com, code=e.code)
        return None
    except Exception as e:
        log.warning("idrogeo_fetch_error", pro_com=pro_com, error=str(e)[:60])
        return None


def shape_idrogeo_payload(raw: dict) -> dict:
    """Riformatta payload IdroGEO 134 campi -> shape leggibile per il frontend.

    Mantiene solo i campi narrativamente utili: alluvioni P1-P3, frane P1-P4+AA,
    popolazione/famiglie/edifici esposti, percentuali. Demografia di base e
    osmid/extent vanno in sezioni separate.
    """
    g = raw.get  # shortcut
    return {
        "_disclaimer": (
            "Mosaicatura ISPRA pericolosita' v5.0 (2020); "
            "Censimento Popolazione e Abitazioni ISTAT 2021"
        ),
        "alluvioni": {
            # area in km2 + percentuale per scenario P3 (frequenti, T<=50anni),
            # P2 (poco frequenti, T<=200), P1 (rare, T<=500)
            "ar_p3_kmq": g("ar_id_p3"),
            "ar_p2_kmq": g("ar_id_p2"),
            "ar_p1_kmq": g("ar_id_p1"),
            "ar_p3_pct": g("aridp3_p"),
            "ar_p2_pct": g("aridp2_p"),
            "ar_p1_pct": g("aridp1_p"),
            "pop_p3": g("pop_idr_p3"),
            "pop_p2": g("pop_idr_p2"),
            "pop_p1": g("pop_idr_p1"),
            "pop_p3_pct": g("popidp3_p"),
            "pop_p2_pct": g("popidp2_p"),
            "pop_p1_pct": g("popidp1_p"),
            "fam_p3": g("fam_idr_p3"),
            "fam_p2": g("fam_idr_p2"),
            "fam_p1": g("fam_idr_p1"),
            "ed_p3": g("ed_idr_p3"),
            "ed_p2": g("ed_idr_p2"),
            "ed_p1": g("ed_idr_p1"),
            "im_p3": g("im_idr_p3"),  # imprese
            "im_p2": g("im_idr_p2"),
            "im_p1": g("im_idr_p1"),
            "bbcc_p3": g("bbcc_id_p3"),  # beni culturali
            "bbcc_p2": g("bbcc_id_p2"),
            "bbcc_p1": g("bbcc_id_p1"),
        },
        "frane": {
            # P4 (molto elevata), P3 (elevata), P2 (media), P1 (moderata),
            # AA (aree attenzione). Aggregato P3+P4 come "rischio significativo"
            "ar_p4_kmq": g("ar_fr_p4"),
            "ar_p3_kmq": g("ar_fr_p3"),
            "ar_p2_kmq": g("ar_fr_p2"),
            "ar_p1_kmq": g("ar_fr_p1"),
            "ar_aa_kmq": g("ar_fr_aa"),
            "ar_p3p4_kmq": g("ar_fr_p3p4"),
            "ar_p4_pct": g("ar_frp4_p"),
            "ar_p3_pct": g("ar_frp3_p"),
            "ar_p2_pct": g("ar_frp2_p"),
            "ar_p1_pct": g("ar_frp1_p"),
            "ar_aa_pct": g("ar_fraa_p"),
            "ar_p3p4_pct": g("ar_frp3p4p"),
            "pop_p4": g("pop_fr_p4"),
            "pop_p3": g("pop_fr_p3"),
            "pop_p2": g("pop_fr_p2"),
            "pop_p3p4": g("popfr_p3p4"),
            "pop_p4_pct": g("popfrp4_p"),
            "pop_p3_pct": g("popfrp3_p"),
            "pop_p3p4_pct": g("popfrp3p4p"),
            "fam_p3p4": g("famfr_p3p4"),
            "fam_p3p4_pct": g("famfrp3p4p"),
            "ed_p3p4": g("ed_fr_p3p4"),
            "ed_p3p4_pct": g("edfrp3p4p"),
            "im_p3p4": g("imfr_p3p4"),
            "im_p3p4_pct": g("imfrp3p4p"),
            "bbcc_p3p4": g("bbccfrp3p4"),
        },
        "_demografia_idrogeo": {
            # Dato Censimento ISTAT 2021 fornito da IdroGEO.
            # Tenuto separato per evitare collisione con DemograficaTab (POSAS).
            "pop_res_2011": g("pop_res011"),
            "pop_res_2021": g("pop_res021"),
            "fam_tot_2021": g("fam_tot"),
            "ed_tot_2021": g("ed_tot"),
            "im_tot_2021": g("im_tot"),
        },
    }


def shape_idrogeo_geo(raw: dict) -> dict:
    """Estrae geo (ar_kmq + osmid + extent) dal payload IdroGEO."""
    return {
        "ar_kmq": raw.get("ar_kmq"),
        "osmid": raw.get("osmid"),
        "extent": raw.get("extent"),
    }


def fetch_idrogeo_all(istat_codes: list[str]) -> dict:
    """Fetch parallelo IdroGEO per tutti i comuni. Ritorna dict {istat: shape}.

    istat_codes: lista di codici 6-cifre zero-padded (es. '075035').
    Internamente convertiti a int per l'API (es. 75035).
    """
    log.info("idrogeo_fetch_start", n_comuni=len(istat_codes),
             parallel=IDROGEO_PARALLEL)

    rischio_by_istat = {}
    geo_by_istat = {}

    def _job(istat: str):
        pro_com = int(istat)  # rimuove zero-padding
        raw = fetch_idrogeo_one(pro_com)
        if raw is None:
            return istat, None, None
        return istat, shape_idrogeo_payload(raw), shape_idrogeo_geo(raw)

    completed = 0
    with ThreadPoolExecutor(max_workers=IDROGEO_PARALLEL) as ex:
        futures = {ex.submit(_job, istat): istat for istat in istat_codes}
        for fut in as_completed(futures):
            try:
                istat, rischio, geo = fut.result()
                if rischio is not None:
                    rischio_by_istat[istat] = rischio
                if geo is not None:
                    geo_by_istat[istat] = geo
            except Exception as e:
                log.warning("idrogeo_job_failed", error=str(e)[:60])
            completed += 1
            if completed % 1000 == 0:
                log.info("idrogeo_fetch_progress", completed=completed,
                         total=len(istat_codes))

    log.info("idrogeo_fetch_done",
             rischio=len(rischio_by_istat), geo=len(geo_by_istat))
    return {"rischio": rischio_by_istat, "geo": geo_by_istat}


# ===========================================================================
# FASE C — ISPRA Catasto Rifiuti (15 CSV anno per anno)
# ===========================================================================
def download_rifiuti_anno(anno: int, cache_dir: Path,
                          force: bool = False) -> Path | None:
    """Download CSV Catasto rifiuti anno. Cache locale."""
    out = cache_dir / f"rifiuti_{anno}.csv"
    if out.exists() and not force:
        log.debug("rifiuti_csv_cached", anno=anno, size=out.stat().st_size)
        return out

    url = RIFIUTI_CSV_URL.format(anno=anno)
    log.info("rifiuti_csv_download_start", anno=anno, url=url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(out, "wb") as f:
                f.write(resp.read())
        log.info("rifiuti_csv_download_done", anno=anno, size=out.stat().st_size)
        return out
    except Exception as e:
        log.warning("rifiuti_csv_download_failed", anno=anno, error=str(e))
        return None


def parse_rifiuti_csv_anno(csv_path: Path, anno: int) -> dict:
    """Parse CSV rifiuti per un anno -> dict {istat: row_dict}.

    Schema row_dict:
      anno, popolazione, ru_t, rd_t, rd_pct, kg_ab, dato_riferito_a, aggregato_nome
    Per righe 'Vedi aggregazione: X', dato_riferito_a = 'Vedi aggregazione',
    aggregato_nome = X (i valori numerici sono spesso vuoti, ma li lasciamo None).
    """
    out = {}
    n_total = 0
    n_aggregato_vede = 0
    n_aggregato_puro = 0

    with open(csv_path, encoding="utf-8") as f:
        f.readline()  # skip riga 1 (titolo descrittivo)
        reader = csv.reader(f, delimiter=";")
        try:
            next(reader)  # skip riga 2 (header colonne)
        except StopIteration:
            log.warning("rifiuti_csv_empty", anno=anno)
            return out

        for row in reader:
            # Le righe "Vedi aggregazione" hanno meno colonne (frazioni non riempite),
            # ma servono comunque per associare il comune al suo capofila.
            # Filtro minimo: serve almeno il codice + anagrafica + 'dato riferito a'
            if len(row) < 6:
                continue
            cod_raw = row[0].strip()
            # Skip note finali e righe con codice non numerico
            if not cod_raw or not cod_raw[0].isdigit() or len(cod_raw) < 8:
                continue
            n_total += 1

            # Pattern codice: RR + IIIIII (8 cifre)
            istat = cod_raw[2:8] if len(cod_raw) >= 8 else None
            if not istat:
                continue

            dato_rif = row[5].strip()

            # Classificazione (NB: classificazione PRIMA dei valori numerici,
            # perche' kg_ab dipende dal ruolo).
            # "Aggregazione: X" = comune capofila aggregato, dato consolidato
            # "Vedi aggregazione: X" = comune membro, rimanda al capofila
            aggregato_nome = None
            aggregato_ruolo = None
            if dato_rif.startswith("Aggregazione:"):
                aggregato_nome = dato_rif.replace("Aggregazione:", "").strip()
                aggregato_ruolo = "capofila"
                n_aggregato_puro += 1
            elif dato_rif.startswith("Vedi aggregazione:"):
                aggregato_nome = dato_rif.replace("Vedi aggregazione:", "").strip()
                aggregato_ruolo = "membro"
                n_aggregato_vede += 1

            # safe access per le colonne numeriche
            def col(idx, row=row):
                return row[idx] if idx < len(row) else ''
            ru_t = parse_it_num(col(23))
            rd_t = parse_it_num(col(20))
            rd_pct = parse_it_num(col(24))
            popolazione = parse_it_num(col(4))
            # kg_ab non calcolabile per capofila (numeratore aggregato, denominatore comune)
            if aggregato_ruolo == "capofila":
                kg_ab = None
            else:
                kg_ab = (ru_t * 1000 / popolazione) if (ru_t and popolazione) else None

            out[istat] = {
                "anno": anno,
                "popolazione": int(popolazione) if popolazione else None,
                "ru_t": round2(ru_t),
                "rd_t": round2(rd_t),
                "rd_pct": round2(rd_pct),
                "kg_ab": round(kg_ab) if kg_ab else None,
                "dato_riferito_a": dato_rif,
                "aggregato_nome": aggregato_nome,
                "aggregato_ruolo": aggregato_ruolo,  # 'capofila' | 'membro' | None
            }

    log.info("rifiuti_csv_parse_done", anno=anno, n_records=len(out),
             n_processed=n_total, n_aggr_vede=n_aggregato_vede,
             n_aggr_puro=n_aggregato_puro)
    return out


def parse_rifiuti_all_anni(cache_dir: Path, force: bool = False) -> dict:
    """Parse di tutti i CSV anno -> dict {istat: {serie_storica, ultimo}}.

    Cache: i CSV gia' scaricati non vengono riscaricati (a meno di --no-cache).
    """
    rifiuti_by_istat = defaultdict(lambda: {
        "ultimo_anno": None,
        "ultimo": None,
        "serie_storica": [],
        "_aggregato": None,
        "_aggregato_ruolo": None,
    })

    for anno in range(RIFIUTI_ANNO_MIN, RIFIUTI_ANNO_MAX + 1):
        csv_path = download_rifiuti_anno(anno, cache_dir, force=force)
        if csv_path is None:
            continue
        anno_data = parse_rifiuti_csv_anno(csv_path, anno)

        for istat, row in anno_data.items():
            entry = rifiuti_by_istat[istat]
            # Serie storica: 4 metriche leggere
            entry["serie_storica"].append({
                "anno": row["anno"],
                "rd_pct": row["rd_pct"],
                "ru_t": row["ru_t"],
                "rd_t": row["rd_t"],
                "kg_ab": row["kg_ab"],
            })
            # Aggregazione: tieni il nome dell'ULTIMO anno con valore (piu' aggiornato).
            # Un comune puo' cambiare aggregazione nel tempo (vedi Allein: Grand Combin
            # 2010-2023 -> Mont-Emilius 2024).
            if row["aggregato_nome"]:
                entry["_aggregato"] = row["aggregato_nome"]
                entry["_aggregato_ruolo"] = row["aggregato_ruolo"]
            # Ultimo anno: tieni il piu' recente con dato non None
            if row["rd_pct"] is not None or row["ru_t"] is not None:
                if entry["ultimo_anno"] is None or row["anno"] > entry["ultimo_anno"]:
                    entry["ultimo_anno"] = row["anno"]
                    entry["ultimo"] = {
                        "popolazione": row["popolazione"],
                        "ru_t": row["ru_t"],
                        "rd_t": row["rd_t"],
                        "rd_pct": row["rd_pct"],
                        "kg_ab": row["kg_ab"],
                    }

    # Ordina serie storica per anno crescente
    for entry in rifiuti_by_istat.values():
        entry["serie_storica"].sort(key=lambda r: r["anno"])

    log.info("rifiuti_all_done", n_comuni=len(rifiuti_by_istat))
    return dict(rifiuti_by_istat)


# ===========================================================================
# FASE D — Merge & shard
# ===========================================================================
def build_kpi(suolo: dict | None, rischio: dict | None,
              rifiuti: dict | None, idrogeo_raw: dict | None = None) -> dict:
    """Costruisce KPI strip riassuntivi per il frontend.

    ar_kmq (superficie comunale) viene da IdroGEO raw se presente, perche'
    e' un dato anagrafico utile anche se l'utente non si interessa al rischio.
    """
    kpi = {
        "ar_kmq": None,
        "suolo_consumato_2024_pct": None,
        "incremento_2024_ha": None,
        "popolazione_frane_p3p4_pct": None,
        "rd_pct_ultimo_anno": None,
        "rd_ultimo_anno": None,
        "kg_per_abitante_ultimo_anno": None,
    }

    if idrogeo_raw:
        kpi["ar_kmq"] = idrogeo_raw.get("ar_kmq")

    if suolo:
        kpi["suolo_consumato_2024_pct"] = suolo["stock_2024"]["pct"]
        if suolo["serie_storica"]:
            kpi["incremento_2024_ha"] = suolo["serie_storica"][-1].get("netto_ha")

    if rischio:
        kpi["popolazione_frane_p3p4_pct"] = rischio["frane"].get("pop_p3p4_pct")

    if rifiuti and rifiuti.get("ultimo"):
        kpi["rd_pct_ultimo_anno"] = rifiuti["ultimo"]["rd_pct"]
        kpi["kg_per_abitante_ultimo_anno"] = rifiuti["ultimo"]["kg_ab"]
        kpi["rd_ultimo_anno"] = rifiuti.get("ultimo_anno")

    return kpi


def build_territorio_shards(
    suolo_by_istat: dict,
    idrogeo_data: dict,
    rifiuti_by_istat: dict,
    anagrafica: dict,
    output_dir: Path,
) -> tuple[Path, dict]:
    """Compone shard territorio/<istat>.json per ogni comune del bundle.

    Ritorna (shard_dir, stats).
    """
    shard_dir = output_dir / "territorio"
    shard_dir.mkdir(parents=True, exist_ok=True)

    rischio_by_istat = idrogeo_data.get("rischio", {})
    geo_by_istat = idrogeo_data.get("geo", {})

    n_full = 0  # comuni con tutte e 3 le fonti
    n_partial = 0  # almeno una fonte
    n_empty = 0  # nessuna fonte
    written = 0

    for istat, anag in anagrafica.items():
        suolo = suolo_by_istat.get(istat)
        rischio = rischio_by_istat.get(istat)
        rifiuti = rifiuti_by_istat.get(istat)
        geo = geo_by_istat.get(istat)

        # Skip se NESSUNA fonte ha dati per questo comune
        if not any((suolo, rischio, rifiuti)):
            n_empty += 1
            continue

        if suolo and rischio and rifiuti:
            n_full += 1
        else:
            n_partial += 1

        kpi = build_kpi(suolo, rischio, rifiuti, idrogeo_raw=geo)

        shard = {
            "_source": (
                "ISPRA Consumo di Suolo (Rapporto SNPA 2025) + "
                "ISPRA IdroGEO PIR + "
                "ISPRA Catasto Nazionale Rifiuti"
            ),
            "_license": "CC-BY 4.0",
            "_extracted_at": "2026-05-08",
            "istat_code": istat,
            "denominazione": anag.get("denominazione"),
            "provincia": anag.get("provincia"),
            "regione": anag.get("regione"),
            "kpi": kpi,
        }

        if suolo:
            shard["suolo"] = suolo
        if rischio:
            shard["rischio_idrogeologico"] = rischio
        if rifiuti:
            shard["rifiuti"] = rifiuti
        if geo:
            shard["geo"] = geo

        out_path = shard_dir / f"{istat}.json"
        out_path.write_text(
            json.dumps(shard, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        written += 1

    stats = {
        "n_full": n_full,  # tutte e 3 le fonti
        "n_partial": n_partial,
        "n_empty": n_empty,
        "written": written,
    }
    log.info("territorio_shards_built", **stats)
    return shard_dir, stats


# ===========================================================================
# Push su R2
# ===========================================================================
def push_to_r2_parallel(shard_dir: Path) -> int:
    """Carica shard JSON su R2 sotto prefisso 'territorio/'."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    shard_files = sorted(shard_dir.glob("*.json"))

    log.info("territorio_push_start", n_files=len(shard_files), bucket=bucket)

    def _upload_one(sf: Path):
        key = f"territorio/{sf.name}"
        client.upload_file(
            str(sf),
            bucket,
            key,
            ExtraArgs={"ContentType": "application/json"},
        )

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in shard_files}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 1000 == 0:
                    log.info("territorio_push_progress",
                             uploaded=uploaded, total=len(shard_files))
            except Exception as e:
                log.error("territorio_upload_failed", error=str(e))
    log.info("territorio_push_done", uploaded=uploaded)
    return uploaded


# ===========================================================================
# Main
# ===========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ETL Tab Territorio: ISPRA Suolo + IdroGEO + Catasto Rifiuti"
        ),
    )
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("/tmp/cruscotto-territorio-cache"))
    parser.add_argument("--outdir", type=Path, default=Path("/var/www/cruscotto-italia/data"))
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza re-download di XLSX e CSV rifiuti")
    parser.add_argument("--skip-idrogeo", action="store_true",
                        help="Skip fase IdroGEO (per test rapidi)")
    parser.add_argument("--skip-rifiuti", action="store_true",
                        help="Skip fase Catasto Rifiuti (per test rapidi)")
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    output_dir = args.outdir
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target,
             cache_dir=str(cache_dir), output_dir=str(output_dir))

    try:
        # 0) Carico anagrafica
        anagrafica = load_nome_to_istat()

        # FASE A — Suolo (XLSX)
        log.info("fase_a_suolo_start")
        xlsx_path = download_suolo_xlsx(cache_dir, force=args.no_cache)
        suolo_by_istat = parse_suolo_xlsx(xlsx_path)

        # FASE B — IdroGEO (API)
        if args.skip_idrogeo:
            log.info("fase_b_idrogeo_skip")
            idrogeo_data = {"rischio": {}, "geo": {}}
        else:
            log.info("fase_b_idrogeo_start")
            idrogeo_data = fetch_idrogeo_all(list(anagrafica.keys()))

        # FASE C — Rifiuti (15 CSV)
        if args.skip_rifiuti:
            log.info("fase_c_rifiuti_skip")
            rifiuti_by_istat = {}
        else:
            log.info("fase_c_rifiuti_start")
            rifiuti_by_istat = parse_rifiuti_all_anni(cache_dir,
                                                     force=args.no_cache)

        # FASE D — Merge & shard
        log.info("fase_d_merge_start")
        shard_dir, stats = build_territorio_shards(
            suolo_by_istat, idrogeo_data, rifiuti_by_istat,
            anagrafica, output_dir,
        )

        # 5) Upload su R2 (solo se target=r2)
        if args.target == "r2":
            uploaded = push_to_r2_parallel(shard_dir)
            manifest.update_source(
                "territorio",
                [{"key": "territorio/*", "count": uploaded}],
                status="ok",
            )
            log.info("manifest_updated")

        log.info("etl_done", **stats)
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
