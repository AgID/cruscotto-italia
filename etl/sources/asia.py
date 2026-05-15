"""ETL ISTAT ASIA UL - Archivio Statistico Imprese Attive (Unità Locali).

Fonte: ISTAT esploradati.istat.it (SDMX REST API).
Licenza: CC BY 3.0 IT (standard ISTAT).
URL: https://esploradati.istat.it/databrowser/#/it/dw/categories/IT1,Z0500DICA,1.0/DICA_ASIA/DICA_ASIAULP

Dataflow utilizzato: 183_1163_DF_DICA_ASIAULP_TERRIFDATA_7
  Granularità: COMUNE × ATECO 2 cifre × Classe addetti × Anno (2018-2023).
  Aggiornamento upstream: annuale, ~Q4 (LAST_UPDATE 2026-04-17, copre fino al 2023).

Schema SDMX (5 dimensioni + TIME_PERIOD + OBS_VALUE):
  FREQ.REF_AREA.DATA_TYPE.ECON_ACTIVITY_NACE_2007.PERS_EMPL_SIZE_CLASS.TIME_PERIOD

Misure (DATA_TYPE):
  - LU       = Stock unità locali attive (numero)
  - LUEMPDAA = Addetti delle unità locali, media annua (numero)

Classi addetti (PERS_EMPL_SIZE_CLASS):
  - TOTAL, W0_9 (micro), W10_49 (piccole), W50_249 (medie), W_GE250 (grandi)

ATECO (NACE Rev.2 2 cifre): ~88 divisioni economiche.
  Codice "0010" = TOTALE attività economiche (riga aggregata).

Strategia download:
  Bulk CSV per anno (6 chiamate × ~150 MB cad.) anziché bulk completo singolo
  (~900 MB, timeout-sensibile). CSV concatenato in CACHE_DIR/asia_<year>.csv.
  Retry su 503 con backoff esponenziale (ISTAT rate-limita dopo richieste pesanti).

Output:
  asia/<istat>.json per ciascun comune (~25-40 KB ciascuno, totale ~250-400 MB R2).

Schema shard asia/<istat>.json:
{
  "_etl_version": "0.1.0",
  "_source": "ISTAT - Archivio Statistico Imprese Attive (ASIA UL)",
  "_source_url": "https://esploradati.istat.it/databrowser/...",
  "_license": "CC BY 3.0 IT",
  "_generated_at": "ISO-8601",
  "_years_available": [2018, 2019, 2020, 2021, 2022, 2023],
  "_latest_year": 2023,

  "kpi": {
    "ul_totali": 11896,               // UL stock anno più recente
    "addetti_totali": 45123.5,        // Addetti media annua anno più recente
    "addetti_per_ul": 3.79,           // Dimensione media UL (addetti/UL)
    "ul_yoy_pct": 0.86,               // Variazione % UL vs anno precedente

    "mix_classe_addetti": {           // % UL per classe dimensionale (anno più recente)
      "W0_9": 92.3,
      "W10_49": 6.8,
      "W50_249": 0.8,
      "W_GE250": 0.1
    },

    "top_settori_ul": [               // Top-10 ATECO per UL (anno più recente)
      {"code": "47", "label": "Commercio dettaglio", "ul": 2103, "addetti": 4521.3},
      ...
    ],
    "top_settori_addetti": [...]      // Top-10 ATECO per addetti
  },

  "serie_storica": {                  // Serie 2018-2023 (totale comune)
    "anni": [2018, 2019, 2020, 2021, 2022, 2023],
    "ul": [11500, 11650, 11700, 11750, 11794, 11896],
    "addetti": [44000, 44500, 43200, 43800, 44900, 45123.5]
  },

  "ateco_dettaglio": {                // Tutti i settori × tutte le classi, anno più recente
    "2023": {
      "47": {"TOTAL": {"ul": 2103, "addetti": 4521.3},
             "W0_9": {"ul": 1980, "addetti": 3200.1},
             "W10_49": {"ul": 110, "addetti": 850.2},
             ...},
      "10": {...}
    }
  }
}

Usage:
  python -m etl.sources.asia --target=local --limit=5     # smoke 5 comuni
  python -m etl.sources.asia --target=local               # full local
  python -m etl.sources.asia --target=r2                  # full + R2 push
  python -m etl.sources.asia --target=r2 --skip-download  # riusa cache CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# === Configurazione SDMX ===
SDMX_BASE = "https://esploradati.istat.it/SDMXWS/rest"
SDMX_AGENCY = "IT1"
SDMX_VERSION = "1.0"
DATAFLOW_ID = "183_1163_DF_DICA_ASIAULP_TERRIFDATA_7"
UA = "cruscotto-italia/1.0 (+https://cruscotto-italia.piersoftckan.biz)"

CANONICAL_URL = (
    "https://esploradati.istat.it/databrowser/#/it/dw/categories/"
    "IT1,Z0500DICA,1.0/DICA_ASIA/DICA_ASIAULP/"
    "183_1163_DF_DICA_ASIAULP_TERRIFDATA_7"
)
LICENSE = "CC BY 3.0 IT"

# Anni serie storica (verificato 2026-05-15: dataflow contiene 2018-2023)
YEARS = [2018, 2019, 2020, 2021, 2022, 2023]
LATEST_YEAR = max(YEARS)

# Cache locale CSV bulk
CACHE_DIR = Path("/tmp/cruscotto_asia")

# Codici DATA_TYPE
DT_LU = "LU"             # numero unità locali
DT_ADD = "LUEMPDAA"      # addetti media annua

# Codice ATECO "totale attività"
ATECO_TOTAL = "0010"

# Classi addetti
SIZE_TOTAL = "TOTAL"
SIZE_CLASSES = ["W0_9", "W10_49", "W50_249", "W_GE250"]  # esclude TOTAL

# Labels italiani ATECO 2 cifre (NACE Rev.2 sezione livello DIVISIONE)
ATECO_LABELS = {
    "01": "Agricoltura e produzione animale",
    "02": "Silvicoltura",
    "03": "Pesca e acquacoltura",
    "05": "Estrazione carbone",
    "06": "Estrazione petrolio e gas",
    "07": "Estrazione minerali metalliferi",
    "08": "Altre attività estrattive",
    "09": "Servizi di supporto estrazione",
    "10": "Industrie alimentari",
    "11": "Industria bevande",
    "12": "Industria tabacco",
    "13": "Industrie tessili",
    "14": "Confezione articoli abbigliamento",
    "15": "Pelli e calzature",
    "16": "Industria legno",
    "17": "Carta",
    "18": "Stampa e supporti registrati",
    "19": "Coke e prodotti petroliferi",
    "20": "Prodotti chimici",
    "21": "Prodotti farmaceutici",
    "22": "Articoli in gomma e plastica",
    "23": "Lavorazione minerali non metalliferi",
    "24": "Metallurgia",
    "25": "Prodotti in metallo",
    "26": "Computer, elettronica, ottica",
    "27": "Apparecchiature elettriche",
    "28": "Macchinari",
    "29": "Autoveicoli",
    "30": "Altri mezzi di trasporto",
    "31": "Mobili",
    "32": "Altre industrie manifatturiere",
    "33": "Riparazione e installazione macchinari",
    "35": "Energia elettrica, gas, vapore",
    "36": "Raccolta e trattamento acque",
    "37": "Gestione reti fognarie",
    "38": "Rifiuti",
    "39": "Risanamento e bonifica",
    "41": "Costruzione edifici",
    "42": "Ingegneria civile",
    "43": "Lavori di costruzione specializzati",
    "45": "Commercio e riparazione autoveicoli",
    "46": "Commercio all'ingrosso",
    "47": "Commercio al dettaglio",
    "49": "Trasporto terrestre",
    "50": "Trasporto marittimo",
    "51": "Trasporto aereo",
    "52": "Magazzinaggio e trasporto supporto",
    "53": "Servizi postali e corriere",
    "55": "Alloggio",
    "56": "Ristorazione",
    "58": "Editoria",
    "59": "Cinema, video, musica",
    "60": "Radio e TV",
    "61": "Telecomunicazioni",
    "62": "Informatica e software",
    "63": "Servizi informazione",
    "64": "Servizi finanziari (no assicurazioni)",
    "65": "Assicurazioni e fondi pensione",
    "66": "Servizi finanziari ausiliari",
    "68": "Attività immobiliari",
    "69": "Attività legali e contabili",
    "70": "Direzione aziendale e consulenza",
    "71": "Studi tecnici e architettura",
    "72": "Ricerca e sviluppo",
    "73": "Pubblicità e ricerche di mercato",
    "74": "Altre attività professionali",
    "75": "Veterinaria",
    "77": "Noleggio e leasing",
    "78": "Ricerca personale",
    "79": "Agenzie viaggio",
    "80": "Vigilanza e investigazioni",
    "81": "Servizi edifici e paesaggio",
    "82": "Supporto ufficio e altre B2B",
    "84": "Amministrazione pubblica e difesa",
    "85": "Istruzione",
    "86": "Assistenza sanitaria",
    "87": "Assistenza sociale residenziale",
    "88": "Assistenza sociale non residenziale",
    "90": "Attività creative e artistiche",
    "91": "Biblioteche, musei",
    "92": "Lotterie, scommesse, casino",
    "93": "Attività sportive e intrattenimento",
    "94": "Organizzazioni associative",
    "95": "Riparazione computer e beni personali",
    "96": "Altri servizi personali",
    "97": "Servizi domestici",
    "98": "Produzione beni per uso proprio",
    "99": "Organismi extraterritoriali",
}


# ═════════════════════════════════════════════════════════════════════════
# FASE 1 — Download CSV in chunk di N comuni × tutti gli anni in 1 chiamata
# ═════════════════════════════════════════════════════════════════════════
#
# Strategia validata 2026-05-15:
# - ISTAT esploradati.istat.it ha limite MAX 35 codici REF_AREA per chiamata
#   (40 → HTTP 400 "Bad Request - Invalid URL").
# - Il bulk full su 1 anno (~150 MB) e' instabile: in ~50% dei casi viene
#   troncato a 70-80 MB senza errore (server-side ConnectionReset).
# - Il bulk full 6 anni (~900 MB) e' totalmente non praticabile.
# - Parallelismo > 2 connessioni → HTTP 503 da rate-limit.
#
# Soluzione: 226 chiamate da 35 comuni × 6 anni = ~25 s/chiamata.
# Con parallelismo=2 + retry su 503: ~60 min/full pull annuale (CI).
# Per smoke test --limit (5 comuni): 1 sola chiamata, ~30 s.
#
# Cache: 1 file CSV per chunk (asia_chunk_<idx>.csv) in CACHE_DIR.

CHUNK_SIZE = 35  # max codici REF_AREA per chiamata SDMX ISTAT
PARALLEL_DOWNLOADS = 2  # ISTAT rate-limit a 503 oltre 2 connessioni concorrenti


def sdmx_data_url_chunk(istat_codes: list[str],
                        year_start: int, year_end: int) -> str:
    """URL SDMX per N comuni × range anni in 1 chiamata."""
    key_area = "+".join(istat_codes)
    return (f"{SDMX_BASE}/data/{SDMX_AGENCY},{DATAFLOW_ID},{SDMX_VERSION}/"
            f"A.{key_area}.....?startPeriod={year_start}&endPeriod={year_end}")


def download_chunk(istat_codes: list[str],
                   year_start: int, year_end: int,
                   out_path: Path,
                   max_retry: int = 4) -> Path:
    """Scarica 1 chunk (N comuni × range anni) con retry su 503."""
    url = sdmx_data_url_chunk(istat_codes, year_start, year_end)
    if len(istat_codes) > CHUNK_SIZE:
        raise ValueError(f"Chunk size {len(istat_codes)} > limite ISTAT {CHUNK_SIZE}")

    headers = {
        "Accept": "application/vnd.sdmx.data+csv;version=1.0.0",
        "User-Agent": UA,
        "Accept-Encoding": "identity",
    }

    for attempt in range(1, max_retry + 1):
        try:
            t0 = time.time()
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
            elapsed = time.time() - t0
            if len(data) < 1000:
                snippet = data[:200].decode("utf-8", errors="ignore")
                raise RuntimeError(f"Response too small: {len(data)} bytes | {snippet}")
            out_path.write_bytes(data)
            return out_path
        except urllib.error.HTTPError as e:
            if e.code in (503, 429) and attempt < max_retry:
                backoff = 15 * attempt
                log.warning("asia_chunk_503_backoff",
                            chunk_file=out_path.name,
                            attempt=attempt, backoff=backoff,
                            n_codes=len(istat_codes))
                time.sleep(backoff)
                continue
            log.error("asia_chunk_http_error",
                      chunk_file=out_path.name,
                      code=e.code, attempt=attempt,
                      first_codes=istat_codes[:3])
            raise
        except (urllib.error.URLError, RuntimeError, TimeoutError, OSError) as e:
            if attempt < max_retry:
                backoff = 15 * attempt
                log.warning("asia_chunk_retry",
                            chunk_file=out_path.name,
                            attempt=attempt, error=str(e), backoff=backoff)
                time.sleep(backoff)
                continue
            log.error("asia_chunk_failed", chunk_file=out_path.name,
                      err=str(e), attempts=attempt)
            raise
    raise RuntimeError(f"chunk failed after {max_retry} retries (file={out_path.name})")


def get_all_istat_codes() -> list[str]:
    """Ritorna lista ufficiale comuni ISTAT da manifest demografia (gia' su R2).

    Se non disponibile, fallback a probe SDMX (lento).
    """
    # Tentativo 1: manifest demografia se accessibile
    try:
        m = manifest.load()
        files = m.get("sources", {}).get("demografia", {}).get("files", [])
        codes = []
        for f in files:
            name = f.get("name", "")
            if name.endswith(".json"):
                codes.append(name[:-5])  # strip .json
        codes = sorted(set(c for c in codes if c.isdigit() and len(c) == 6))
        if len(codes) > 7000:
            log.info("asia_istat_codes_from_manifest", n=len(codes))
            return codes
    except Exception as e:
        log.warning("asia_manifest_unavailable", err=str(e))

    # Tentativo 2: codelist CL_ITTER107 da SDMX (lento, ~50 MB)
    log.info("asia_fetching_codelist_itter107")
    cl_url = (f"{SDMX_BASE}/codelist/{SDMX_AGENCY}/CL_ITTER107/1.0"
              "?detail=allstubs")
    req = urllib.request.Request(cl_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as resp:
        xml = resp.read().decode("utf-8")
    import re
    codes = sorted(set(re.findall(r'<structure:Code id="(\d{6})"', xml)))
    log.info("asia_istat_codes_from_codelist", n=len(codes))
    return codes


def download_all_in_chunks(cache_dir: Path,
                           year_start: int, year_end: int,
                           limit_istat: list[str] | None = None,
                           force: bool = False) -> list[Path]:
    """Itera chunked download di TUTTI i comuni ISTAT × range anni.

    Se limit_istat passato, scarica solo quelli (smoke test).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    if limit_istat:
        all_codes = sorted(set(limit_istat))
        log.info("asia_download_limit_mode", n_codes=len(all_codes))
    else:
        all_codes = get_all_istat_codes()
        log.info("asia_download_full_mode", n_codes=len(all_codes))

    # Chunk in gruppi di CHUNK_SIZE
    chunks = [all_codes[i:i + CHUNK_SIZE]
              for i in range(0, len(all_codes), CHUNK_SIZE)]
    log.info("asia_download_chunks_planned",
             n_chunks=len(chunks), chunk_size=CHUNK_SIZE,
             years=f"{year_start}-{year_end}",
             parallel=PARALLEL_DOWNLOADS)

    out_paths: list[Path] = []
    todo: list[tuple[int, list[str], Path]] = []
    for idx, chunk_codes in enumerate(chunks):
        out = cache_dir / f"asia_chunk_{idx:04d}.csv"
        if out.exists() and out.stat().st_size > 1000 and not force:
            out_paths.append(out)
            continue
        todo.append((idx, chunk_codes, out))

    log.info("asia_download_todo", todo=len(todo), cached=len(chunks) - len(todo))

    if not todo:
        return [cache_dir / f"asia_chunk_{idx:04d}.csv" for idx in range(len(chunks))]

    t0 = time.time()
    done = 0
    failed: list[int] = []

    def _worker(idx: int, codes: list[str], out: Path) -> tuple[int, bool]:
        try:
            download_chunk(codes, year_start, year_end, out)
            return idx, True
        except Exception as e:
            log.error("asia_chunk_giveup", idx=idx, err=str(e))
            return idx, False

    with ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as ex:
        futs = {ex.submit(_worker, idx, codes, out): idx
                for idx, codes, out in todo}
        for fut in as_completed(futs):
            idx, ok = fut.result()
            done += 1
            if not ok:
                failed.append(idx)
            if done % 10 == 0 or done == len(todo):
                elapsed = time.time() - t0
                eta = elapsed / done * (len(todo) - done) if done > 0 else 0
                log.info("asia_download_progress",
                         done=done, todo=len(todo),
                         failed=len(failed),
                         elapsed_s=round(elapsed, 1),
                         eta_s=round(eta, 1))

    if failed:
        log.error("asia_download_partial_failure",
                  failed_chunks=failed[:20], n_failed=len(failed))
        raise RuntimeError(f"{len(failed)} chunks failed (see log)")

    # Ritorna paths in ordine
    return [cache_dir / f"asia_chunk_{idx:04d}.csv" for idx in range(len(chunks))]


# ═════════════════════════════════════════════════════════════════════════
# FASE 2 — Load + Aggregate via DuckDB
# ═════════════════════════════════════════════════════════════════════════

def load_csvs_to_duckdb(csv_paths: list[Path], limit_istat: list[str] | None = None
                        ) -> duckdb.DuckDBPyConnection:
    """Carica tutti i CSV in DuckDB unica tabella `asia_raw`.

    limit_istat: se fornito, filtra solo questi codici (smoke test).
    """
    con = duckdb.connect()
    paths_str = "', '".join(str(p) for p in csv_paths)

    log.info("asia_loading_csvs", n_files=len(csv_paths))

    # Carica tutti i CSV in una sola SELECT con UNION via read_csv glob
    con.execute(f"""
        CREATE TABLE asia_raw AS
        SELECT
            REF_AREA                       AS istat,
            DATA_TYPE                      AS data_type,
            ECON_ACTIVITY_NACE_2007        AS ateco,
            PERS_EMPL_SIZE_CLASS           AS size_class,
            CAST(TIME_PERIOD AS INTEGER)   AS year,
            TRY_CAST(OBS_VALUE AS DOUBLE)  AS value
        FROM read_csv(['{paths_str}'],
                      header=true,
                      ignore_errors=true,
                      auto_detect=true,
                      null_padding=true)
        WHERE REF_AREA IS NOT NULL
          AND length(REF_AREA) = 6
          AND REF_AREA ~ '^[0-9]{{6}}$'
          AND OBS_VALUE IS NOT NULL
    """)

    if limit_istat:
        in_list = "', '".join(limit_istat)
        con.execute(f"DELETE FROM asia_raw WHERE istat NOT IN ('{in_list}')")
        log.info("asia_filter_limit", n_istat=len(limit_istat))

    n = con.execute("SELECT COUNT(*) FROM asia_raw").fetchone()[0]
    n_istat = con.execute("SELECT COUNT(DISTINCT istat) FROM asia_raw").fetchone()[0]
    n_ateco = con.execute("SELECT COUNT(DISTINCT ateco) FROM asia_raw").fetchone()[0]
    log.info("asia_loaded", rows=n, comuni=n_istat, ateco_codes=n_ateco)
    return con


# ═════════════════════════════════════════════════════════════════════════
# FASE 3 — Build shard per comune
# ═════════════════════════════════════════════════════════════════════════

def build_shard_for_istat(con: duckdb.DuckDBPyConnection, istat: str,
                          now_iso: str) -> dict:
    """Costruisce il dict shard per un singolo comune."""

    # ── Serie storica totale (ateco=0010, size=TOTAL) ────────────────
    serie_rows = con.execute("""
        SELECT year, data_type, value
        FROM asia_raw
        WHERE istat = ?
          AND ateco = ?
          AND size_class = ?
        ORDER BY year, data_type
    """, [istat, ATECO_TOTAL, SIZE_TOTAL]).fetchall()

    # Pivot serie
    serie_ul = {y: None for y in YEARS}
    serie_add = {y: None for y in YEARS}
    for year, dt, val in serie_rows:
        if dt == DT_LU:
            serie_ul[year] = int(val) if val is not None else None
        elif dt == DT_ADD:
            serie_add[year] = round(val, 2) if val is not None else None

    ul_latest = serie_ul.get(LATEST_YEAR)
    add_latest = serie_add.get(LATEST_YEAR)
    ul_prev = serie_ul.get(LATEST_YEAR - 1)

    # ── KPI ──────────────────────────────────────────────────────────
    addetti_per_ul = None
    if ul_latest and add_latest and ul_latest > 0:
        addetti_per_ul = round(add_latest / ul_latest, 2)

    ul_yoy_pct = None
    if ul_latest is not None and ul_prev not in (None, 0):
        ul_yoy_pct = round((ul_latest - ul_prev) / ul_prev * 100, 2)

    # ── Mix classi addetti (anno più recente, LU su 0010) ────────────
    mix_rows = con.execute("""
        SELECT size_class, value
        FROM asia_raw
        WHERE istat = ? AND ateco = ? AND data_type = ? AND year = ?
          AND size_class IN ('W0_9','W10_49','W50_249','W_GE250')
    """, [istat, ATECO_TOTAL, DT_LU, LATEST_YEAR]).fetchall()
    mix_classe = {}
    if mix_rows and ul_latest and ul_latest > 0:
        for sc, v in mix_rows:
            mix_classe[sc] = round((v / ul_latest) * 100, 2) if v else 0.0

    # ── Top settori per UL (esclusi codici aggregati '0010' e altre) ─
    top_ul_rows = con.execute("""
        SELECT ateco, value AS ul, COALESCE(add_val.value, 0) AS addetti
        FROM asia_raw AS ul_t
        LEFT JOIN asia_raw AS add_val
          ON ul_t.istat = add_val.istat AND ul_t.ateco = add_val.ateco
         AND ul_t.year = add_val.year AND add_val.data_type = ?
         AND add_val.size_class = ?
        WHERE ul_t.istat = ?
          AND ul_t.data_type = ?
          AND ul_t.size_class = ?
          AND ul_t.year = ?
          AND ul_t.ateco != ?
          AND length(ul_t.ateco) = 2
        ORDER BY ul_t.value DESC
        LIMIT 10
    """, [DT_ADD, SIZE_TOTAL, istat, DT_LU, SIZE_TOTAL, LATEST_YEAR, ATECO_TOTAL]).fetchall()
    top_settori_ul = [
        {
            "code": code,
            "label": ATECO_LABELS.get(code, f"ATECO {code}"),
            "ul": int(ul),
            "addetti": round(addetti, 2) if addetti else 0.0,
        }
        for code, ul, addetti in top_ul_rows
    ]

    # ── Top settori per addetti ──────────────────────────────────────
    top_add_rows = con.execute("""
        SELECT add_t.ateco, COALESCE(ul_val.value, 0) AS ul, add_t.value AS addetti
        FROM asia_raw AS add_t
        LEFT JOIN asia_raw AS ul_val
          ON add_t.istat = ul_val.istat AND add_t.ateco = ul_val.ateco
         AND add_t.year = ul_val.year AND ul_val.data_type = ?
         AND ul_val.size_class = ?
        WHERE add_t.istat = ?
          AND add_t.data_type = ?
          AND add_t.size_class = ?
          AND add_t.year = ?
          AND add_t.ateco != ?
          AND length(add_t.ateco) = 2
        ORDER BY add_t.value DESC
        LIMIT 10
    """, [DT_LU, SIZE_TOTAL, istat, DT_ADD, SIZE_TOTAL, LATEST_YEAR, ATECO_TOTAL]).fetchall()
    top_settori_addetti = [
        {
            "code": code,
            "label": ATECO_LABELS.get(code, f"ATECO {code}"),
            "ul": int(ul) if ul else 0,
            "addetti": round(addetti, 2),
        }
        for code, ul, addetti in top_add_rows
    ]

    # ── ATECO dettaglio (anno più recente, tutti i settori × tutte le classi) ─
    detail_rows = con.execute("""
        SELECT ateco, data_type, size_class, value
        FROM asia_raw
        WHERE istat = ?
          AND year = ?
          AND length(ateco) = 2
        ORDER BY ateco, size_class, data_type
    """, [istat, LATEST_YEAR]).fetchall()
    ateco_dettaglio = {}
    for code, dt, sc, val in detail_rows:
        if code not in ateco_dettaglio:
            ateco_dettaglio[code] = {}
        if sc not in ateco_dettaglio[code]:
            ateco_dettaglio[code][sc] = {}
        if dt == DT_LU:
            ateco_dettaglio[code][sc]["ul"] = int(val) if val else 0
        elif dt == DT_ADD:
            ateco_dettaglio[code][sc]["addetti"] = round(val, 2) if val else 0.0

    # ── Compose shard ───────────────────────────────────────────────
    shard = {
        "_etl_version": "0.1.0",
        "_source": "ISTAT - Archivio Statistico Imprese Attive (ASIA UL)",
        "_source_url": CANONICAL_URL,
        "_license": LICENSE,
        "_generated_at": now_iso,
        "_years_available": YEARS,
        "_latest_year": LATEST_YEAR,

        "kpi": {
            "ul_totali": ul_latest,
            "addetti_totali": add_latest,
            "addetti_per_ul": addetti_per_ul,
            "ul_yoy_pct": ul_yoy_pct,
            "mix_classe_addetti": mix_classe,
            "top_settori_ul": top_settori_ul,
            "top_settori_addetti": top_settori_addetti,
        },

        "serie_storica": {
            "anni": YEARS,
            "ul": [serie_ul[y] for y in YEARS],
            "addetti": [serie_add[y] for y in YEARS],
        },

        "ateco_dettaglio": {
            str(LATEST_YEAR): ateco_dettaglio,
        },
    }
    return shard


def build_all_shards(con: duckdb.DuckDBPyConnection, output_dir: Path) -> Path:
    """Genera 1 shard JSON per ciascun comune presente in asia_raw."""
    shard_dir = output_dir / "asia"
    shard_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    istat_codes = [r[0] for r in con.execute(
        "SELECT DISTINCT istat FROM asia_raw ORDER BY istat"
    ).fetchall()]
    log.info("asia_building_shards", n_comuni=len(istat_codes))

    written = 0
    t0 = time.time()
    for i, istat in enumerate(istat_codes, 1):
        shard = build_shard_for_istat(con, istat, now_iso)
        out_path = shard_dir / f"{istat}.json"
        out_path.write_text(json.dumps(shard, ensure_ascii=False,
                                       separators=(",", ":")),
                            encoding="utf-8")
        written += 1
        if i % 200 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(istat_codes) - i)
            log.info("asia_build_progress",
                     done=i, total=len(istat_codes),
                     elapsed_s=round(elapsed, 1), eta_s=round(eta, 1))
    log.info("asia_build_done", written=written, elapsed_s=round(time.time() - t0, 1))
    return shard_dir


# ═════════════════════════════════════════════════════════════════════════
# FASE 4 — R2 push parallelo
# ═════════════════════════════════════════════════════════════════════════

def push_to_r2_parallel(shard_dir: Path, force_upload: bool = False,
                        max_workers: int = 24) -> int:
    """Push parallelo dei shard su R2 con dedup MD5 vs ETag remoti.

    Pattern di riferimento: aria.py / veicoli.py.
    1) Lista oggetti remoti con prefisso 'asia/' → dict ETag.
    2) Calcola MD5 locale, salta se ETag corrisponde.
    3) Upload paralleli con ThreadPoolExecutor.
    """
    import hashlib

    shard_files = sorted(shard_dir.glob("*.json"))
    log.info("asia_r2_push_start", n_local=len(shard_files))

    # 1) Lista remota ETags
    remote_etags: dict[str, str] = {}
    if not force_upload:
        try:
            client = r2.get_r2_client()
            bucket = r2.get_bucket()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix="asia/"):
                for o in page.get("Contents", []):
                    remote_etags[o["Key"]] = o["ETag"].strip('"')
            log.info("asia_r2_remote_etag_listed", n_remote=len(remote_etags))
        except Exception as e:
            log.warning("asia_r2_list_failed", err=str(e))

    to_upload: list[Path] = []
    skipped = 0
    for sf in shard_files:
        key = f"asia/{sf.name}"
        if not force_upload and key in remote_etags:
            local_md5 = hashlib.md5(sf.read_bytes()).hexdigest()
            if local_md5 == remote_etags[key]:
                skipped += 1
                continue
        to_upload.append(sf)
    log.info("asia_r2_diff_done", to_upload=len(to_upload), skipped=skipped)

    if not to_upload:
        return 0

    uploaded = 0
    t0 = time.time()

    def _upload_one(sf: Path) -> bool:
        try:
            key = f"asia/{sf.name}"
            r2.upload_file(sf, key, content_type="application/json")
            return True
        except Exception as e:
            log.error("asia_r2_upload_failed", file=sf.name, err=str(e))
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
        for i, fut in enumerate(as_completed(futures), 1):
            ok = fut.result()
            if ok:
                uploaded += 1
            if i % 200 == 0:
                elapsed = time.time() - t0
                eta = elapsed / i * (len(to_upload) - i)
                log.info("asia_r2_push_progress",
                         done=i, total=len(to_upload),
                         elapsed_s=round(elapsed, 1), eta_s=round(eta, 1))
    log.info("asia_r2_push_done", uploaded=uploaded,
             skipped=skipped, elapsed_s=round(time.time() - t0, 1))
    return uploaded


# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="ETL ISTAT ASIA UL (Unità Locali + Addetti per comune)",
    )
    ap.add_argument("--target", choices=["local", "r2"], default="local",
                    help="local: scrive solo in --outdir; r2: scrive shard + push R2")
    ap.add_argument("--outdir", default="dist/asia",
                    help="Directory shard locali (default: dist/asia)")
    ap.add_argument("--skip-download", action="store_true",
                    help="Riusa cache CSV in /tmp/cruscotto_asia/")
    ap.add_argument("--force-download", action="store_true",
                    help="Re-download anche con cache presente")
    ap.add_argument("--force-upload", action="store_true",
                    help="R2 upload senza ETag check")
    ap.add_argument("--limit", default="",
                    help="Smoke test: lista comma-separated ISTAT (es. 075035,058091)")
    args = ap.parse_args()

    log.info("asia_etl_start", target=args.target)
    t_start = time.time()

    # FASE 1: download chunked
    limit = [c.strip() for c in args.limit.split(",") if c.strip()] if args.limit else None

    if args.skip_download:
        log.info("asia_skip_download")
        csv_paths = sorted(CACHE_DIR.glob("asia_chunk_*.csv"))
        if not csv_paths:
            log.error("asia_cache_missing",
                      cache_dir=str(CACHE_DIR),
                      hint="Run without --skip-download first")
            return 1
        log.info("asia_cache_files_found", n=len(csv_paths))
    else:
        csv_paths = download_all_in_chunks(
            CACHE_DIR,
            year_start=min(YEARS),
            year_end=max(YEARS),
            limit_istat=limit,
            force=args.force_download,
        )
    con = load_csvs_to_duckdb(csv_paths, limit_istat=limit)

    # FASE 3: shard build
    output_dir = Path(args.outdir)
    shard_dir = build_all_shards(con, output_dir.parent if output_dir.name == "asia" else output_dir)

    # FASE 4: R2 push (solo se target=r2)
    uploaded = 0
    if args.target == "r2":
        uploaded = push_to_r2_parallel(shard_dir, force_upload=args.force_upload)
        # Manifest update
        try:
            files = [{"name": f.name,
                      "size": f.stat().st_size,
                      "key": f"asia/{f.name}"}
                     for f in sorted(shard_dir.glob("*.json"))]
            manifest.update_source("asia", files, status="ok")
            log.info("asia_manifest_updated", n_files=len(files))
        except Exception as e:
            log.warning("asia_manifest_update_failed", err=str(e))

    elapsed_total = time.time() - t_start
    log.info("asia_etl_done",
             target=args.target,
             shards_dir=str(shard_dir),
             uploaded=uploaded,
             elapsed_s=round(elapsed_total, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
