"""ETL ISTAT - Basi Territoriali + Variabili censuarie 2021.

Fonte: ISTAT - Censimento permanente popolazione 2021.
Licenza: CC BY 3.0 IT (standard ISTAT).
URL pagina: https://www.istat.it/notizia/basi-territoriali-e-variabili-censuarie/

Composizione fonte:
- 20 ZIP regionali shapefile sezioni di censimento 2021 (WGS84 UTM Zona 32N):
    https://www.istat.it/storage/cartografia/basi_territoriali/2021/R<NN>_21.zip
- 1 ZIP nazionale variabili censuarie sezioni 2021 (XLSX per regione):
    https://esploradati.istat.it/databrowser/DWL/PERMPOP/SUBCOM/Dati_regionali_2021.zip
- 1 ZIP aree subcomunali 2021 (solo ~43 capoluoghi, layer overlay opzionale):
    https://www.istat.it/wp-content/uploads/2025/04/ASC_21.zip

Edizione: dati definitivi pubblicati 14/05/2026 (pagina ISTAT aggiornata).

Copertura: 7896/7896 comuni (TN/BZ inclusi, a differenza del Catasto AdE).
Strategia: ETL local-first, output flat in data/censimento_full/<istat>.geojson
(geometrie + 122 variabili per sezione) + un solo file di aggregati
data/censimento/aggregati.json (dict {istat: kpi_comune+distribuzioni})
letto da dashboard.py per la sezione "censimento" nel comune A1.

Schema 122 variabili (dal file TRACCIATO_2021 ufficiale ISTAT):
  - P1-P3: popolazione totale + sesso
  - P14-P29: popolazione totale per fascia eta 5 anni
  - P30-P45: popolazione maschi per fascia eta 5 anni
  - P67-P82: popolazione femmine per fascia eta 5 anni
  - P83-P100: titolo di studio (totale, maschi, femmine x 5 livelli)
  - P101-P103: occupati 15-64 (totale, maschi, femmine)
  - IT1-IT12: italiani per fascia eta (0-14, 15-64, 65+) x sesso x occupati
  - ST1-ST33: stranieri (totali, UE/extra-UE, fasce eta, sesso, occupati)
  - PF1, PF3-PF8: famiglie per numero componenti
  - A2, A3, A8: abitazioni (occupate, vuote, totali)
  - E3: edifici residenziali

Output:
- data/censimento_full/<istat>.geojson : 7896 file, FeatureCollection con
  geometrie (Polygon EPSG:4326) + properties.vars (dict 122 variabili)
- data/censimento/aggregati.json : 1 file con dict aggregati comune-level
  per il dashboard A1 (kpi_comune + distribuzioni)

Aggiornamento: annuale, allineato al rilascio ISTAT (tipicamente aprile).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import structlog

from etl.lib import manifest

log = structlog.get_logger()

# ═════════════════════════════════════════════════════════════════════════
# Costanti fonte
# ═════════════════════════════════════════════════════════════════════════

SOURCE_LABEL = "ISTAT - Basi Territoriali + Variabili censuarie 2021"
SOURCE_PAGE = "https://www.istat.it/notizia/basi-territoriali-e-variabili-censuarie/"
LICENSE = "CC BY 3.0 IT"
ANNO = 2021
ETL_VERSION = "0.1.0"

# URL pattern shapefile regionali (R01-R20). R04 = Trentino-Alto Adige incluso.
URL_BT_REGION = (
    "https://www.istat.it/storage/cartografia/basi_territoriali/2021/R{:02d}_21.zip"
)

# Variabili censuarie sezioni: 1 ZIP nazionale con 20 XLSX regionali + TRACCIATO
URL_VARS = (
    "https://esploradati.istat.it/databrowser/DWL/PERMPOP/SUBCOM/"
    "Dati_regionali_2021.zip"
)

# Aree subcomunali (municipi/circoscrizioni/quartieri) - solo ~43 capoluoghi
URL_ASC = "https://www.istat.it/wp-content/uploads/2025/04/ASC_21.zip"

UA = "CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it)"

# ═════════════════════════════════════════════════════════════════════════
# Lista 122 variabili numeriche estratte per ogni sezione (dal TRACCIATO ISTAT)
# ═════════════════════════════════════════════════════════════════════════

# Popolazione totale e per sesso (3)
VARS_POP_BASE = ["P1", "P2", "P3"]

# Popolazione totale per fascia eta 5 anni (16): P14-P29
VARS_POP_ETA_TOT = [f"P{i}" for i in range(14, 30)]

# Popolazione maschi per fascia eta 5 anni (16): P30-P45
VARS_POP_ETA_M = [f"P{i}" for i in range(30, 46)]

# Popolazione femmine per fascia eta 5 anni (16): P67-P82
VARS_POP_ETA_F = [f"P{i}" for i in range(67, 83)]

# Titolo di studio (18): P83-P100 - totale/M/F x (totale, nessuno, elementare,
# media, diploma, terziario)
VARS_TITOLO = [f"P{i}" for i in range(83, 101)]

# Occupati 15-64 (3): P101-P103 totale/M/F
VARS_OCCUPATI = [f"P{i}" for i in range(101, 104)]

# Italiani (12): IT1-IT12 fasce eta + sesso + occupati
VARS_ITALIANI = [f"IT{i}" for i in range(1, 13)]

# Stranieri (24): ST1, ST2, ST2_B + ST3-ST5 + ST16-ST33
VARS_STRANIERI = (
    ["ST1", "ST2", "ST2_B"]
    + [f"ST{i}" for i in range(3, 6)]
    + [f"ST{i}" for i in range(16, 34)]
)

# Famiglie per numero componenti (7): PF1, PF3-PF8
VARS_FAMIGLIE = ["PF1"] + [f"PF{i}" for i in range(3, 9)]

# Abitazioni ed edifici (4)
VARS_ABITAZIONI = ["A2", "A3", "A8", "E3"]

# Unione totale: 110 codici numerici (i 12 anagrafici come PROCOM, REGIONE
# sono trattati separatamente come metadata della sezione, non in vars{}).
VARS_NUMERIC = (
    VARS_POP_BASE
    + VARS_POP_ETA_TOT
    + VARS_POP_ETA_M
    + VARS_POP_ETA_F
    + VARS_TITOLO
    + VARS_OCCUPATI
    + VARS_ITALIANI
    + VARS_STRANIERI
    + VARS_FAMIGLIE
    + VARS_ABITAZIONI
)
# Sanity check: deve essere 3 + 16 + 16 + 16 + 18 + 3 + 12 + 24 + 7 + 4 = 119
# (le "122 variabili" del titolo includono anche 3 voci anagrafica
#  contestuali: CODREG/CODPRO/CODCOM, gestite come metadata).
assert len(VARS_NUMERIC) == 119, f"Expected 119 vars, got {len(VARS_NUMERIC)}"

# ═════════════════════════════════════════════════════════════════════════
# Mapping dello shapefile TIPO_LOC (campo numerico) -> label leggibile
# ═════════════════════════════════════════════════════════════════════════

TIPO_LOC_MAP = {
    1: "centro_abitato",
    2: "nucleo_abitato",
    3: "case_sparse",
    4: "localita_produttiva",
}

CACHE_DIR = Path("/tmp/cruscotto_censimento")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Soglie minime per validare ZIP "non corrotti" (in byte):
# BT regionali piu' piccoli sono ~120KB (es. Molise small), quindi 50KB e' safe
# Variabili Dati_regionali_2021.zip e' ~250MB
MIN_ZIP_SIZE_BT = 50_000
MIN_ZIP_SIZE_VARS = 100_000_000  # 100 MB
MIN_ZIP_SIZE_ASC = 100_000

import time
import urllib.request
import urllib.error


# ═════════════════════════════════════════════════════════════════════════
# FASE 1 — Download fonti (con cache locale per riusabilita')
# ═════════════════════════════════════════════════════════════════════════

def _http_get(url: str, dest: Path, min_size: int, timeout: int = 600) -> Path:
    """Download HTTP con User-Agent + validazione magic bytes ZIP + size minima.

    Cache idempotente: se 'dest' esiste e supera min_size, lo riusa senza
    ri-scaricare. Per forzare il refresh, eliminare il file prima.
    """
    if dest.exists() and dest.stat().st_size >= min_size:
        log.info("censimento_zip_cached",
                 path=str(dest),
                 size_mb=round(dest.stat().st_size / 1024 / 1024, 1))
        return dest

    log.info("censimento_zip_download_start", url=url, dest=str(dest))
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} su {url}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error su {url}: {e.reason}")
    elapsed = time.time() - t0

    if len(data) < min_size:
        snippet = data[:200].decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"ZIP troppo piccolo ({len(data)} bytes < {min_size}), "
            f"possibile errore HTTP. Snippet: {snippet}"
        )
    if data[:2] != b"PK":
        raise RuntimeError(
            f"Magic bytes non ZIP su {url}: {data[:4].hex()}"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    log.info("censimento_zip_downloaded",
             url=url,
             size_mb=round(len(data) / 1024 / 1024, 1),
             elapsed_s=round(elapsed, 1))
    return dest


def download_dati_regionali(force: bool = False) -> Path:
    """Scarica Dati_regionali_2021.zip (~250MB) contenente 20 XLSX regionali
    di variabili censuarie + 1 XLSX TRACCIATO_2021 con il dizionario.

    Cache: /tmp/cruscotto_censimento/Dati_regionali_2021.zip
    """
    out = CACHE_DIR / "Dati_regionali_2021.zip"
    if force and out.exists():
        out.unlink()
    return _http_get(URL_VARS, out, MIN_ZIP_SIZE_VARS, timeout=900)


def download_bt_region(region: int, force: bool = False) -> Path:
    """Scarica R<NN>_21.zip (shapefile sezioni di censimento) per la regione
    indicata (1-20). R04 = Trentino-Alto Adige.

    Cache: /tmp/cruscotto_censimento/R<NN>_21.zip
    """
    if not 1 <= region <= 20:
        raise ValueError(f"region deve essere 1-20, ricevuto {region}")
    url = URL_BT_REGION.format(region)
    out = CACHE_DIR / f"R{region:02d}_21.zip"
    if force and out.exists():
        out.unlink()
    return _http_get(url, out, MIN_ZIP_SIZE_BT, timeout=300)


def download_asc(force: bool = False) -> Path:
    """Scarica ASC_21.zip (aree subcomunali ~43 capoluoghi, overlay opzionale).

    Cache: /tmp/cruscotto_censimento/ASC_21.zip
    """
    out = CACHE_DIR / "ASC_21.zip"
    if force and out.exists():
        out.unlink()
    return _http_get(URL_ASC, out, MIN_ZIP_SIZE_ASC, timeout=180)
