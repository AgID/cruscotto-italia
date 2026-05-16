"""ETL SIOPE Spese Comunali - bulk download da BDAP CKAN, multi-anno.

Sostituisce la query OData live del worker comune_spese.ts con shard
pre-calcolati siope/<istat>.json. Risolve due problemi:
  1) BDAP OData live e' lento e instabile (down 2026-05-08)
  2) Frontend fa N chiamate MCP, una per comune visitato

Strategia:
  1) Per ognuna delle 20 regioni italiane e per ogni anno richiesto, scarica
     il CSV bulk pubblicato su dati.gov.it (CKAN), risorsa
     'spd_rnd_spe_sio_reg<XX>_01_<anno>'.
  2) Filtra solo righe Codice Tipologia Ente BDAP == 'CO' (comuni).
  3) Cache su R2: raw/siope/reg<XX>_<anno>_comuni.csv (CSV gia' filtrato,
     ~10-20% del raw originale). Riusato finche' il dataset upstream non
     viene aggiornato.
  4) Aggrega per (cod_istat_provincia + cod_istat_comune) e per codice
     gestionale, replicando la logica di worker/src/tools/comune_spese.ts:
     ordina voci per importo_cumulato decrescente, conserva mensili.
  5) Output: siope/<istat>.json per ognuno dei ~7896 comuni, con un blocco
     per_anno che racchiude tutti gli anni richiesti.

Input upstream: BDAP CKAN bulk CSV (CC-BY 4.0, RGS-MEF).

Output schema (v0.2.0, multi-anno):
{
  "_etl_version": "0.2.0",
  "_source": "BDAP CKAN bulk - SIOPE Movimenti cumulati mensili di Spesa",
  "_generated_at": "ISO-8601",
  "anni_disponibili": [2025, 2026],
  "anno_default": 2025,
  "per_anno": {
    "2025": {
      "_resource_id": "<uuid>",
      "anno": 2025,
      "parziale": false,
      "ente_siope": "COMUNE DI LECCE",
      "popolazione": 84543.0,
      "mesi_disponibili": ["2025/01", ..., "2025/12"],
      "ultimo_mese": "2025/12",
      "n_voci": 87,
      "totale_anno": 1234567.89,
      "voci": [
        {
          "codice_gestionale": "U1010101002",
          "desc_gestionale": "Voci stipendiali...",
          "codice_titolo": "U1000000000",
          "desc_titolo": "Spese correnti",
          "importo_cumulato": 1234.56,
          "ultimo_mese": "2025/12",
          "mensili": {"2025/01": 100.0, ..., "2025/12": 1234.56}
        }
      ]
    },
    "2026": {
      "_resource_id": "<uuid>",
      "anno": 2026,
      "parziale": true,
      "ente_siope": "COMUNE DI LECCE",
      "popolazione": 84543.0,
      "mesi_disponibili": ["2026/01", ..., "2026/04"],
      "ultimo_mese": "2026/04",
      "n_voci": 65,
      "totale_anno": 410000.00,
      "voci": [...]
    }
  }
}

Note: ogni shard ha "anno_default" = anno chiuso piu' recente disponibile;
se nessun anno chiuso e' presente, prende l'anno parziale piu' recente.

Usage:
  python -m etl.sources.siope --target=local                       # tutti gli anni supportati
  python -m etl.sources.siope --target=r2
  python -m etl.sources.siope --target=local --regioni=06          # solo FVG
  python -m etl.sources.siope --target=r2   --no-cache             # forza re-download
  python -m etl.sources.siope --target=local --anni=2026           # solo 2026
  python -m etl.sources.siope --target=local --anni=2025,2026      # entrambi

Cache:
  - R2: raw/siope/reg<XX>_<anno>_comuni.csv (CSV pre-filtrato CO, per anno)
  - Locale: nessuna (streaming CSV → aggregazione → output)

Note:
  - BDAP server richiede Accept-Encoding gzip + Referer header per servire i CSV
    a velocita' decente (altrimenti hangs in download).
  - CSV encoding: cp1252 (NON utf-8). Separatore ';', quote '"'.
  - L'ultima colonna del CSV e' vuota (trailing ';').
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from etl.lib import manifest, r2

log = structlog.get_logger()

ETL_VERSION = "0.2.0"

# Anni supportati. L'anno "default" e' l'ultimo chiuso (=ultimo anno per cui
# il dataset upstream copre tutti i 12 mesi). Gli anni "parziali" hanno
# mesi_disponibili < 12 e vengono marcati con flag parziale=true nello shard.
SUPPORTED_YEARS: list[int] = [2025, 2026]
DEFAULT_YEAR: int = 2025
# Anni considerati parziali (mesi disponibili < 12). Aggiornare quando l'anno
# passa "in chiusura" (tipicamente febbraio/marzo dell'anno N+2 quando RGS
# pubblica l'ultimo mese di N).
PARTIAL_YEARS: set[int] = {2026}

# Mappa codice regione SIOPE → CKAN resource_id del CSV bulk SIOPE Spese,
# per anno. Generata da package_show su dati.gov.it.
# Per rigenerare: ./scripts/find_siope_resources.py (TODO)
SIOPE_RESOURCE_IDS: dict[int, dict[str, str]] = {
    2025: {
        "01": "18270689-1897-4a5a-8fb8-b5a9a129593a",  # Piemonte
        "02": "44b6b335-d697-4997-8121-5eaa4c431aff",  # Valle d'Aosta
        "03": "54644001-abb4-4b21-ba8b-5a39622f6ecd",  # Lombardia
        "04": "e833081d-3f79-4d34-b921-6c38d956e4eb",  # Trentino-AA
        "05": "e3fe09cb-078b-4e27-9bce-8e34764fe899",  # Veneto
        "06": "1c572f0e-f80b-4ad7-902b-ebe492ec56dc",  # Friuli-VG
        "07": "afeb3b7f-b1c0-426c-b862-6f29af0a0358",  # Liguria
        "08": "a083eb86-44bf-400e-bd7e-fc6fa70f89e1",  # Emilia-Romagna
        "09": "74533d22-b1c2-4d89-b1b9-b98e6c9713ff",  # Toscana
        "10": "327259cc-9b32-4247-9611-3766815d8a58",  # Umbria
        "11": "a92cbcc0-b0a5-4ab9-b4be-98f255a6e22c",  # Marche
        "12": "33d82c62-46d3-4194-9484-1fb710cfef88",  # Lazio
        "13": "2f24c6fb-e500-4dab-963e-12cdad31ef8f",  # Abruzzo
        "14": "fe81de16-1502-4e1f-b0ec-adecc324c1d7",  # Molise
        "15": "4c30da4a-943c-4936-ba85-5c68182f678a",  # Campania
        "16": "0b3e4df5-8135-40c9-a91b-951f40de8ba2",  # Puglia
        "17": "a0987ac6-d2e4-4de3-b068-ffcfa553f731",  # Basilicata
        "18": "22ace8c3-f967-43fd-98d5-8deb6e704bca",  # Calabria
        "19": "8a740486-dec8-4566-a78e-cdd72d75813b",  # Sicilia
        "20": "75b2eb4b-a42b-42ae-9165-b24f1d2cc34b",  # Sardegna
    },
    # 2026: dataset osservati al 22/04/2026 (gen-apr, ~4 mesi).
    # Discovered via ckan_package_show su dati.gov.it il 2026-05-10.
    2026: {
        "01": "84ae6d4e-2885-4b35-9ee0-30759e84717a",  # Piemonte
        "02": "0dcce75b-26d8-4fba-841d-c82fae08b5c0",  # Valle d'Aosta
        "03": "e889c511-dada-49c1-bf1c-fc1cfe99b593",  # Lombardia
        "04": "da86b267-e8a9-4696-b404-bd3181bf9f02",  # Trentino-AA
        "05": "ca84752a-d25c-46c5-a1b3-c045f9ee61b2",  # Veneto
        "06": "aa4bd579-372d-44ba-84ea-8a592699b664",  # Friuli-VG
        "07": "8dae5843-3e77-442f-858a-1622d8938826",  # Liguria
        "08": "94c2a579-d68f-4ae8-a221-182d8bb2fd49",  # Emilia-Romagna
        "09": "c1f46e68-60ac-4ce3-a3c8-5642926a27ec",  # Toscana
        "10": "5d4c4cba-efc2-41ef-aa88-a59a846dd557",  # Umbria
        "11": "75661947-7478-4725-9b10-246ad3b17b2f",  # Marche
        "12": "165dc480-95c5-4b47-b300-07e4bb5cd313",  # Lazio
        "13": "6a1bf483-a4e9-4d19-9e0d-1e09c6177854",  # Abruzzo
        "14": "b18aaeda-dc39-4235-838a-50bdfc84c100",  # Molise
        "15": "e0dd93c9-5d92-4310-a082-207870baac6e",  # Campania
        "16": "8289e75a-9d24-4bc5-94bf-df2489da7843",  # Puglia
        "17": "7e5bed38-b703-41fa-a06c-2c6611a8e961",  # Basilicata
        "18": "ab0f9e49-8906-4217-9628-9d5f44242e5e",  # Calabria
        "19": "a06298aa-0c50-4917-9eb9-3836f4f25576",  # Sicilia
        "20": "94d4950c-b023-4d93-b310-dab2c14d3ced",  # Sardegna
    },
}


def get_resource_id(reg: str, anno: int) -> str:
    """Ritorna il resource_id CKAN per (regione, anno). Solleva KeyError se non mappato."""
    return SIOPE_RESOURCE_IDS[anno][reg]


# Backward-compat: alcuni moduli o script potrebbero referenziare la vecchia costante.
SIOPE_RESOURCE_IDS_2025 = SIOPE_RESOURCE_IDS[2025]

CSV_BASE_URL = "https://bdap-opendata.rgs.mef.gov.it/SpodCkanApi/api/3/datastore/dump"
CSV_ENCODING = "cp1252"
CSV_DELIM = ";"

# Headers necessari per non venire bloccati/rallentati da BDAP
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*;q=0.9",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://bdap-opendata.rgs.mef.gov.it/",
}

# Download tuning: BDAP a volte stalla senza inviare RST.
# Strategia: timeout (connect, read) + chunked iteration + retry con backoff.
HTTP_CONNECT_TIMEOUT = 30      # secondi per stabilire connessione
HTTP_READ_TIMEOUT = 60         # secondi tra un chunk e il successivo
DOWNLOAD_CHUNK_SIZE = 1 << 16  # 64 KB
MAX_DOWNLOAD_ATTEMPTS = 3      # tentativi per regione prima di skip
RETRY_BACKOFF_SECONDS = 10     # sleep tra tentativi


def _make_http_session() -> requests.Session:
    """Session con retry su connect/read errors transitori (non a livello chunk)."""
    s = requests.Session()
    retry = Retry(
        total=2, connect=2, read=0,  # read=0: gestiamo noi via chunk
        backoff_factor=2,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

# Colonne CSV BDAP (italian)
COL_PROV_ISTAT = "Codice istat provincia"
COL_COM_ISTAT = "Codice istat comune"
COL_TIPO_ENTE = "Codice Tipologia Ente BDAP"
COL_DESC_ENTE = "Descrizione Ente BDAP"
COL_ANNO_MESE = "Anno/Mese calendario"
COL_COD_TITOLO = "Codice Titolo CG"
COL_DESC_TITOLO = "Descrizione Titolo CG"
COL_COD_GEST = "Codice Gestionale Enti Locali"
COL_DESC_GEST = "Descrizione CG"
COL_POPOL = "Popolazione ISTAT"
COL_IMPORTO = "Importo cumulato"

TIPO_ENTE_COMUNE = "CO"


# ---------------------------------------------------------------------------
# Cache R2: raw/siope/reg<XX>_<anno>_comuni.csv (gia' filtrato)
# ---------------------------------------------------------------------------

def cache_key(reg: str, anno: int) -> str:
    return f"raw/siope/reg{reg}_{anno}_comuni.csv"


def cache_exists(reg: str, anno: int) -> bool:
    return r2.head(cache_key(reg, anno)) is not None


def cache_download(reg: str, anno: int) -> bytes:
    """Scarica CSV gia' filtrato dalla cache R2."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    obj = client.get_object(Bucket=bucket, Key=cache_key(reg, anno))
    return obj["Body"].read()


def cache_upload(reg: str, anno: int, csv_bytes: bytes) -> None:
    """Salva CSV filtrato sulla cache R2."""
    r2.upload_bytes(csv_bytes, cache_key(reg, anno), content_type="text/csv")


# ---------------------------------------------------------------------------
# Download BDAP + filtro CO
# ---------------------------------------------------------------------------

def _download_with_retries(reg: str, url: str, log_ctx) -> bytes:
    """Scarica un URL in chunk con stall detection e retry esterno.

    Ritorna i bytes RAW (gia' decompressi se era gzip-Content-Encoding).
    Solleva RuntimeError se tutti i tentativi falliscono.
    """
    last_err = None
    session = _make_http_session()
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        t0 = time.time()
        try:
            log_ctx.info("download_attempt", reg=reg, attempt=attempt,
                         max=MAX_DOWNLOAD_ATTEMPTS)
            with session.get(
                url,
                headers=BROWSER_HEADERS,
                stream=True,
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                # Iter su chunk: requests applica HTTP_READ_TIMEOUT tra chunk,
                # quindi se BDAP smette di inviare byte solleva ReadTimeout.
                buf = bytearray()
                bytes_received = 0
                last_log_mb = 0
                for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        buf.extend(chunk)
                        bytes_received += len(chunk)
                        cur_mb = bytes_received // (5 * 1024 * 1024)  # log ogni 5 MB
                        if cur_mb > last_log_mb:
                            last_log_mb = cur_mb
                            log_ctx.info("download_progress",
                                         reg=reg,
                                         mb=round(bytes_received / 1024 / 1024, 1),
                                         elapsed_s=round(time.time() - t0, 1))
                # NB: requests ha gia' decodificato Content-Encoding: gzip
                # quando si itera con iter_content (raw=False di default).
                elapsed = time.time() - t0
                log_ctx.info("download_ok", reg=reg,
                             mb=round(len(buf) / 1024 / 1024, 1),
                             elapsed_s=round(elapsed, 1))
                return bytes(buf)
        except (requests.exceptions.RequestException,
                requests.exceptions.ChunkedEncodingError,
                ConnectionError,
                OSError) as e:
            last_err = e
            elapsed = time.time() - t0
            log_ctx.warning("download_failed",
                            reg=reg, attempt=attempt,
                            elapsed_s=round(elapsed, 1),
                            error=str(e)[:200])
            if attempt < MAX_DOWNLOAD_ATTEMPTS:
                sleep_s = RETRY_BACKOFF_SECONDS * attempt
                log_ctx.info("retry_sleep", reg=reg, sleep_s=sleep_s)
                time.sleep(sleep_s)

    raise RuntimeError(
        f"reg{reg}: all {MAX_DOWNLOAD_ATTEMPTS} download attempts failed; "
        f"last error: {last_err}"
    )


def download_and_filter_csv(reg: str, anno: int, log_ctx) -> bytes:
    """Scarica CSV regionale da BDAP, filtra solo righe Tipo CO, ritorna bytes
    del CSV filtrato (encoding cp1252, header preservato).
    """
    resource_id = get_resource_id(reg, anno)
    url = f"{CSV_BASE_URL}/{resource_id}.csv"
    log_ctx.info("downloading_csv", reg=reg, anno=anno, url=url)

    raw = _download_with_retries(reg, url, log_ctx)
    raw_size_mb = len(raw) / 1024 / 1024
    log_ctx.info("csv_downloaded", reg=reg, raw_mb=round(raw_size_mb, 1))

    # Decode CSV in cp1252, filtra righe CO, riencode in cp1252
    text = raw.decode(CSV_ENCODING)
    in_buf = io.StringIO(text)
    reader = csv.DictReader(in_buf, delimiter=CSV_DELIM, quotechar='"')

    out_buf = io.StringIO()
    fieldnames = reader.fieldnames
    if not fieldnames:
        raise RuntimeError(f"reg{reg}: CSV header missing")
    writer = csv.DictWriter(out_buf, fieldnames=fieldnames,
                            delimiter=CSV_DELIM, quotechar='"',
                            quoting=csv.QUOTE_ALL)
    writer.writeheader()

    n_total = 0
    n_kept = 0
    for row in reader:
        n_total += 1
        if row.get(COL_TIPO_ENTE, "").strip() == TIPO_ENTE_COMUNE:
            writer.writerow(row)
            n_kept += 1

    filtered_bytes = out_buf.getvalue().encode(CSV_ENCODING)
    filtered_mb = len(filtered_bytes) / 1024 / 1024

    log_ctx.info("csv_filtered", reg=reg,
                 rows_total=n_total, rows_kept=n_kept,
                 keep_pct=round(100 * n_kept / n_total, 1),
                 filtered_mb=round(filtered_mb, 1))

    return filtered_bytes


def get_filtered_csv(reg: str, anno: int, use_cache: bool, log_ctx) -> bytes:
    """Ritorna CSV filtrato dalla cache R2 o scarica+filtra+cacha."""
    if use_cache and cache_exists(reg, anno):
        log_ctx.info("cache_hit", reg=reg)
        return cache_download(reg, anno)

    csv_bytes = download_and_filter_csv(reg, anno, log_ctx)
    cache_upload(reg, anno, csv_bytes)
    log_ctx.info("cache_uploaded", reg=reg)
    return csv_bytes


# ---------------------------------------------------------------------------
# Aggregazione per comune
# ---------------------------------------------------------------------------

def to_float(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def aggregate_csv_to_shards(csv_bytes: bytes, anno: int,
                             reg: str, log_ctx) -> dict[str, dict]:
    """Aggrega un CSV regionale in year_block per-comune.

    Ritorna {istat_code: year_block_dict} dove year_block_dict ha lo schema
    del campo per_anno[<anno>] descritto in docstring del modulo.
    Il merging tra anni e l'aggiunta di campi top-level (anni_disponibili,
    anno_default, _generated_at) avvengono in build_final_shards().
    """
    text = csv_bytes.decode(CSV_ENCODING)
    reader = csv.DictReader(io.StringIO(text), delimiter=CSV_DELIM, quotechar='"')

    # Per ogni comune accumula:
    #   ente_siope (denominazione)
    #   popolazione (ultima vista)
    #   voci: dict[cod_gestionale -> {codice/desc/titolo/mensili/...}]
    #   mesi_disponibili: set
    per_comune: dict[str, dict] = defaultdict(lambda: {
        "ente_siope": None,
        "popolazione": None,
        "voci": {},
        "mesi": set(),
    })

    for row in reader:
        prov = row.get(COL_PROV_ISTAT, "").strip()
        com = row.get(COL_COM_ISTAT, "").strip()
        if not prov or not com:
            continue
        istat = prov + com

        anno_mese = row.get(COL_ANNO_MESE, "").strip()
        if not anno_mese:
            continue

        cg = row.get(COL_COD_GEST, "").strip()
        if not cg:
            continue

        c = per_comune[istat]
        if c["ente_siope"] is None:
            c["ente_siope"] = row.get(COL_DESC_ENTE, "").strip()
        c["popolazione"] = to_float(row.get(COL_POPOL))
        c["mesi"].add(anno_mese)

        voce = c["voci"].get(cg)
        if voce is None:
            voce = {
                "codice_gestionale": cg,
                "desc_gestionale": row.get(COL_DESC_GEST, "").strip(),
                "codice_titolo": row.get(COL_COD_TITOLO, "").strip(),
                "desc_titolo": row.get(COL_DESC_TITOLO, "").strip(),
                "importo_cumulato": 0.0,
                "ultimo_mese": "",
                "mensili": {},
            }
            c["voci"][cg] = voce

        importo = to_float(row.get(COL_IMPORTO))
        voce["mensili"][anno_mese] = importo
        if anno_mese > voce["ultimo_mese"]:
            voce["ultimo_mese"] = anno_mese
            voce["importo_cumulato"] = importo

    # Costruisci year_block per comune
    resource_id = get_resource_id(reg, anno)
    year_blocks: dict[str, dict] = {}
    for istat, c in per_comune.items():
        voci_list = sorted(
            c["voci"].values(),
            key=lambda v: v["importo_cumulato"],
            reverse=True,
        )
        totale = sum(v["importo_cumulato"] for v in voci_list)
        mesi_sorted = sorted(c["mesi"])
        year_blocks[istat] = {
            "_resource_id": resource_id,
            "anno": anno,
            "parziale": anno in PARTIAL_YEARS,
            "ente_siope": c["ente_siope"],
            "popolazione": c["popolazione"],
            "mesi_disponibili": mesi_sorted,
            "ultimo_mese": mesi_sorted[-1] if mesi_sorted else "",
            "n_voci": len(voci_list),
            "totale_anno": round(totale, 2),
            "voci": voci_list,
        }

    log_ctx.info("aggregated", reg=reg, anno=anno, comuni=len(year_blocks))
    return year_blocks


def build_final_shards(
    year_blocks_by_year: dict[int, dict[str, dict]],
) -> dict[str, dict]:
    """Unisce i year_block di piu' anni in shard finali per-comune.

    Input: {anno: {istat: year_block}, ...}
    Output: {istat: shard_finale_v0.2.0}

    Un comune compare nello shard finale se almeno UN anno ha dati per esso.
    """
    # Set di tutti gli istat visti in qualunque anno
    all_istat: set[str] = set()
    for blocks in year_blocks_by_year.values():
        all_istat.update(blocks.keys())

    anni_processati = sorted(year_blocks_by_year.keys())

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    shards: dict[str, dict] = {}
    for istat in all_istat:
        per_anno: dict[str, dict] = {}
        anni_disp_per_comune: list[int] = []
        for anno in anni_processati:
            yb = year_blocks_by_year.get(anno, {}).get(istat)
            if yb is not None:
                per_anno[str(anno)] = yb
                anni_disp_per_comune.append(anno)

        if not per_anno:
            continue

        # anno_default per il comune: scegliere l'ultimo anno chiuso DISPONIBILE
        # per quel comune; se nessun anno chiuso disponibile, l'ultimo parziale.
        chiusi_comune = [a for a in anni_disp_per_comune if a not in PARTIAL_YEARS]
        anno_default_comune = (
            max(chiusi_comune) if chiusi_comune else max(anni_disp_per_comune)
        )

        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": "BDAP CKAN bulk - SIOPE Movimenti cumulati mensili di Spesa",
            "_generated_at": now_iso,
            "anni_disponibili": anni_disp_per_comune,
            "anno_default": anno_default_comune,
            "per_anno": per_anno,
        }

    return shards


# ---------------------------------------------------------------------------
# Push R2
# ---------------------------------------------------------------------------

def write_shards_local(shards: dict[str, dict], output_dir: Path) -> int:
    """Scrive shard JSON in output_dir locale. Ritorna count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for istat, shard in shards.items():
        path = output_dir / f"{istat}.json"
        path.write_text(
            json.dumps(shard, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return len(shards)


def push_to_r2_parallel(shard_dir: Path) -> int:
    """Upload parallelo siope/<istat>.json su R2."""
    shard_files = sorted(shard_dir.glob("*.json"))
    log.info("siope_pushing", total=len(shard_files))

    def _upload_one(sf: Path) -> str:
        r2.upload_file(sf, f"siope/{sf.name}", content_type="application/json")
        return sf.name

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in shard_files}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 500 == 0:
                    log.info("siope_push_progress",
                             uploaded=uploaded, total=len(shard_files))
            except Exception as e:
                log.error("siope_upload_failed", error=str(e))

    log.info("siope_push_done", uploaded=uploaded)
    return uploaded


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL SIOPE Spese - bulk download CKAN + aggregazione comunale, multi-anno"
    )
    parser.add_argument(
        "--target", choices=["local", "r2"], default="local",
        help="Destinazione output shard"
    )
    parser.add_argument(
        "--outdir", type=Path,
        default=Path("/var/www/cruscotto-italia/data/siope"),
        help="Directory output locale (default: /var/www/cruscotto-italia/data/siope)"
    )
    parser.add_argument(
        "--regioni", type=str, default=None,
        help="Codici regione separati da virgola (es. '06,07'). Default: tutte le 20."
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Forza re-download del CSV ignorando la cache R2"
    )
    parser.add_argument(
        "--anni", type=str, default=None,
        help=(
            f"Anni separati da virgola, es. '2025,2026' o 'all'. "
            f"Default: tutti gli anni supportati ({SUPPORTED_YEARS})."
        ),
    )
    # Backward-compat: --anno (singolare) accettato come alias di --anni
    parser.add_argument(
        "--anno", type=int, default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    # Risolvi anni richiesti
    if args.anno is not None and args.anni is not None:
        log.error("anno_anni_conflict", hint="usa o --anno o --anni, non entrambi")
        return 1
    if args.anno is not None:
        anni = [args.anno]
    elif args.anni is None or args.anni.lower() == "all":
        anni = list(SUPPORTED_YEARS)
    else:
        try:
            anni = [int(a.strip()) for a in args.anni.split(",") if a.strip()]
        except ValueError as e:
            log.error("anni_parse_error", value=args.anni, error=str(e))
            return 1
    for a in anni:
        if a not in SUPPORTED_YEARS:
            log.error("anno_not_supported", anno=a, supported=SUPPORTED_YEARS)
            return 1

    # Risolvi regioni richieste
    all_regs = sorted(SIOPE_RESOURCE_IDS[SUPPORTED_YEARS[0]].keys())
    if args.regioni:
        regioni = [r.strip() for r in args.regioni.split(",")]
        for r in regioni:
            if r not in all_regs:
                log.error("regione_not_mapped", regione=r)
                return 1
    else:
        regioni = all_regs

    output_dir = args.outdir
    log.info("etl_start",
             target=args.target,
             regioni=regioni,
             anni=anni,
             use_cache=not args.no_cache,
             output_dir=str(output_dir))

    # Accumulatore: {anno: {istat: year_block}}
    year_blocks_by_year: dict[int, dict[str, dict]] = {a: {} for a in anni}
    failed_jobs: list[tuple[int, str, str]] = []  # (anno, reg, error)

    try:
        for anno in anni:
            log.info("year_start", anno=anno)
            for reg in regioni:
                log_ctx = log.bind(reg=reg, anno=anno)
                log_ctx.info("region_start")
                try:
                    csv_bytes = get_filtered_csv(
                        reg, anno,
                        use_cache=not args.no_cache,
                        log_ctx=log_ctx,
                    )
                    yb = aggregate_csv_to_shards(csv_bytes, anno, reg, log_ctx)
                    # Merge nel grande accumulatore
                    year_blocks_by_year[anno].update(yb)
                    log_ctx.info("region_done",
                                 comuni_anno_so_far=len(year_blocks_by_year[anno]))
                except Exception as e:
                    failed_jobs.append((anno, reg, str(e)[:200]))
                    log_ctx.error("region_failed", error=str(e)[:200])
            log.info("year_done", anno=anno,
                     comuni_anno=len(year_blocks_by_year[anno]))

        if failed_jobs:
            log.warning("jobs_skipped", count=len(failed_jobs))
            for anno, reg, err in failed_jobs:
                log.warning("skipped_detail", anno=anno, reg=reg, error=err)

        # Build shard finali (merge tra anni)
        shards = build_final_shards(year_blocks_by_year)
        total_shards = write_shards_local(shards, output_dir)

        log.info("aggregation_done",
                 total_shards=total_shards,
                 anni=anni,
                 jobs_skipped=len(failed_jobs),
                 output_dir=str(output_dir))

        if args.target == "r2":
            uploaded = push_to_r2_parallel(output_dir)
            manifest.update_source(
                "siope",
                [{"key": "siope/*", "count": uploaded}],
                status="ok",
            )
            log.info("manifest_updated", anni=anni)

        log.info("etl_done", total_shards=total_shards, anni=anni)
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
