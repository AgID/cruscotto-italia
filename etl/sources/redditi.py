"""
ETL MEF Redditi IRPEF su base comunale
Fonte: Dipartimento delle Finanze - finanze.gov.it
Licenza: CC 3.0 - citazione obbligatoria "Dipartimento delle Finanze - MEF"

Serie storica: 5 anni (2020-2024) di anno imposta.
Schema CSV varia: 2020-2022 hanno 50 colonne ('Bonus spettante', no tot),
2023-2024 hanno 52-53 colonne ('Trattamento spettante', tot esplicito).

Output shards: redditi/<istat>.json su R2 (~7897 file).
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import Any

import boto3
import requests
from botocore.client import Config

# -----------------------------------------------------------------------------
# Configurazione
# -----------------------------------------------------------------------------

SUPPORTED_YEARS = [2020, 2021, 2022, 2023, 2024]

MEF_BASE_URL = (
    "https://www1.finanze.gov.it/finanze3/analisi_stat/"
    "v_4_0_0/contenuti/"
    "Redditi_e_principali_variabili_IRPEF_su_base_comunale_CSV_{year}.zip"
    "?d=1615465800"
)

MEF_REFERER = (
    "https://www1.finanze.gov.it/finanze3/analisi_stat/"
    "index.php?search_class%5B0%5D=cCOMUNE&opendata=yes"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# R2
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")

# Prefissi R2
R2_RAW_PREFIX = "raw/mef/"
R2_SHARDS_PREFIX = "redditi/"

# Locale (cache temporanea)
LOCAL_CACHE_DIR = Path("/tmp/mef_cache")
LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Parallelismo upload (pattern standard Cruscotto)
MAX_WORKERS_UPLOAD = 24

# Numero comuni atteso (per logging)
EXPECTED_COMUNI = 7897

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("redditi")


# -----------------------------------------------------------------------------
# Mapping colonne canoniche
# -----------------------------------------------------------------------------

# Le chiavi sono i nomi canonici interni Cruscotto.
# I valori sono liste di possibili nomi colonna nel CSV MEF (header strippato).
# Più nomi = stessa variabile con naming diverso tra anni (es. Bonus->Trattamento).

COLUMN_MAP: dict[str, list[str]] = {
    # Metadati comune
    "anno": ["Anno di imposta"],
    "cod_catastale": ["Codice catastale"],
    "istat_comune": ["Codice Istat Comune"],
    "denominazione": ["Denominazione Comune"],
    "sigla_provincia": ["Sigla Provincia"],
    "regione": ["Regione"],
    "istat_regione": ["Codice Istat Regione"],
    # Contribuenti
    "contribuenti": ["Numero contribuenti"],
    # Tipologie reddito (freq + tot)
    "fabbricati_freq": ["Reddito da fabbricati - Frequenza"],
    "fabbricati_tot": ["Reddito da fabbricati - Ammontare in euro"],
    "dipendente_freq": ["Reddito da lavoro dipendente e assimilati - Frequenza"],
    "dipendente_tot": [
        "Reddito da lavoro dipendente e assimilati - Ammontare in euro"
    ],
    "pensione_freq": ["Reddito da pensione - Frequenza"],
    "pensione_tot": ["Reddito da pensione - Ammontare in euro"],
    "autonomo_freq": [
        "Reddito da lavoro autonomo (comprensivo dei valori nulli) - Frequenza"
    ],
    "autonomo_tot": [
        "Reddito da lavoro autonomo (comprensivo dei valori nulli) - Ammontare in euro"
    ],
    # Imposte
    "reddito_imponibile_freq": ["Reddito imponibile - Frequenza"],
    "reddito_imponibile_tot": ["Reddito imponibile - Ammontare in euro"],
    "imposta_netta_freq": ["Imposta netta - Frequenza"],
    "imposta_netta_tot": ["Imposta netta - Ammontare in euro"],
    # Bonus/Trattamento (cambia nome 2022->2023)
    "trattamento_freq": [
        "Trattamento spettante - Frequenza",
        "Bonus spettante - Frequenza",
    ],
    "trattamento_tot": [
        "Trattamento spettante - Ammontare in euro",
        "Bonus spettante - Ammontare in euro",
    ],
    # Addizionali
    "add_regionale_freq": ["Addizionale regionale dovuta - Frequenza"],
    "add_regionale_tot": ["Addizionale regionale dovuta - Ammontare in euro"],
    "add_comunale_freq": ["Addizionale comunale dovuta - Frequenza"],
    "add_comunale_tot": ["Addizionale comunale dovuta - Ammontare in euro"],
    # Reddito complessivo totale (solo 2023+, per 2020-22 si deriva)
    "reddito_complessivo_freq": ["Reddito complessivo - Frequenza"],
    "reddito_complessivo_tot": ["Reddito complessivo - Ammontare in euro"],
    # Fasce di reddito (8 fasce, freq + tot ciascuna)
    "f0_freq": ["Reddito complessivo minore o uguale a zero euro - Frequenza"],
    "f0_tot": [
        "Reddito complessivo minore o uguale a zero euro - Ammontare in euro"
    ],
    "f1_freq": ["Reddito complessivo da 0 a 10000 euro - Frequenza"],
    "f1_tot": ["Reddito complessivo da 0 a 10000 euro - Ammontare in euro"],
    "f2_freq": ["Reddito complessivo da 10000 a 15000 euro - Frequenza"],
    "f2_tot": ["Reddito complessivo da 10000 a 15000 euro - Ammontare in euro"],
    "f3_freq": ["Reddito complessivo da 15000 a 26000 euro - Frequenza"],
    "f3_tot": ["Reddito complessivo da 15000 a 26000 euro - Ammontare in euro"],
    "f4_freq": ["Reddito complessivo da 26000 a 55000 euro - Frequenza"],
    "f4_tot": ["Reddito complessivo da 26000 a 55000 euro - Ammontare in euro"],
    "f5_freq": ["Reddito complessivo da 55000 a 75000 euro - Frequenza"],
    "f5_tot": ["Reddito complessivo da 55000 a 75000 euro - Ammontare in euro"],
    "f6_freq": ["Reddito complessivo da 75000 a 120000 euro - Frequenza"],
    "f6_tot": ["Reddito complessivo da 75000 a 120000 euro - Ammontare in euro"],
    "f7_freq": ["Reddito complessivo oltre 120000 euro - Frequenza"],
    "f7_tot": ["Reddito complessivo oltre 120000 euro - Ammontare in euro"],
}

# Label leggibili per le 8 fasce (per il frontend)
FASCE_LABELS = {
    "f0": "≤ 0",
    "f1": "0 - 10k",
    "f2": "10k - 15k",
    "f3": "15k - 26k",
    "f4": "26k - 55k",
    "f5": "55k - 75k",
    "f6": "75k - 120k",
    "f7": "> 120k",
}


# -----------------------------------------------------------------------------
# Client R2
# -----------------------------------------------------------------------------


def get_r2_client():
    if not all([R2_ACCESS_KEY, R2_SECRET_KEY, R2_ACCOUNT_ID, R2_BUCKET]):
        raise RuntimeError(
            "Credenziali R2 mancanti: setta R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID, R2_BUCKET"
        )
    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


# -----------------------------------------------------------------------------
# Download + cache raw
# -----------------------------------------------------------------------------


def download_year_zip(year: int, force: bool = False) -> bytes:
    """Scarica lo ZIP dell'anno indicato. Cache R2 raw/mef/redditi_<year>.zip."""
    raw_key = f"{R2_RAW_PREFIX}redditi_{year}.zip"
    local_path = LOCAL_CACHE_DIR / f"redditi_{year}.zip"
    r2 = get_r2_client()

    # Prova cache R2
    if not force:
        try:
            obj = r2.get_object(Bucket=R2_BUCKET, Key=raw_key)
            data = obj["Body"].read()
            log.info("anno %d: usando cache R2 (%s, %d bytes)", year, raw_key, len(data))
            local_path.write_bytes(data)
            return data
        except r2.exceptions.NoSuchKey:
            log.info("anno %d: cache R2 assente, scarico da MEF", year)
        except Exception as e:
            log.warning("anno %d: errore lettura cache R2 (%s), scarico", year, e)

    # Download fresco
    url = MEF_BASE_URL.format(year=year)
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": MEF_REFERER,
        "Accept": "application/zip,*/*",
        "Accept-Language": "it-IT,it;q=0.9",
    }
    log.info("anno %d: GET %s", year, url)
    r = requests.get(url, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.content
    log.info("anno %d: scaricati %d bytes", year, len(data))

    # Salva cache R2 (best-effort)
    try:
        r2.put_object(
            Bucket=R2_BUCKET,
            Key=raw_key,
            Body=data,
            ContentType="application/zip",
        )
        log.info("anno %d: cache R2 aggiornata (%s)", year, raw_key)
    except Exception as e:
        log.warning("anno %d: impossibile salvare cache R2: %s", year, e)

    local_path.write_bytes(data)
    return data


# -----------------------------------------------------------------------------
# Parse CSV
# -----------------------------------------------------------------------------


def _resolve_columns(header: list[str]) -> dict[str, int | None]:
    """Per ogni chiave canonica, trova l'indice colonna nel CSV (o None)."""
    # Normalizza header (strip whitespace, NBSP, doppi spazi)
    norm = [(i, h.strip().replace("\xa0", " ")) for i, h in enumerate(header)]
    norm_by_name = {h: i for i, h in norm}

    resolved: dict[str, int | None] = {}
    for canonical, candidates in COLUMN_MAP.items():
        idx = None
        for cand in candidates:
            cand_norm = cand.strip()
            if cand_norm in norm_by_name:
                idx = norm_by_name[cand_norm]
                break
        resolved[canonical] = idx
    return resolved


def _to_int(s: str) -> int:
    if not s or s.strip() == "":
        return 0
    try:
        return int(s.strip())
    except ValueError:
        # MEF a volte usa il punto come migliaia (raro nei CSV ma copriamo)
        try:
            return int(s.strip().replace(".", "").replace(",", ""))
        except ValueError:
            return 0


def _safe_div(num: int, den: int) -> int | None:
    """Divisione protetta: ritorna None se denominatore 0, arrotondato a int €."""
    if den <= 0:
        return None
    return round(num / den)


def parse_year_csv(zip_bytes: bytes, year: int) -> dict[str, dict[str, Any]]:
    """
    Estrae il CSV dallo ZIP e produce un dict {istat: row_anno}
    dove row_anno è la struttura "anno" da inserire nello shard.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError(f"anno {year}: nessun CSV nello ZIP")
        csv_name = names[0]
        with zf.open(csv_name) as fh:
            raw = fh.read()

    # Encoding: il file è ASCII puro nei test, ma per sicurezza usiamo
    # latin1 come fallback (i nomi comuni possono avere accentate)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin1")

    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    if not rows:
        raise RuntimeError(f"anno {year}: CSV vuoto")

    header = rows[0]
    cols = _resolve_columns(header)

    # Check chiavi indispensabili
    required = ["istat_comune", "contribuenti", "denominazione"]
    missing = [k for k in required if cols.get(k) is None]
    if missing:
        raise RuntimeError(
            f"anno {year}: colonne obbligatorie mancanti: {missing}"
        )

    log.info(
        "anno %d: header OK, %d colonne canoniche mappate su %d totali",
        year,
        sum(1 for v in cols.values() if v is not None),
        len(header),
    )

    out: dict[str, dict[str, Any]] = {}

    for raw_row in rows[1:]:
        if not raw_row or len(raw_row) < 8:
            continue

        def g(key: str) -> str:
            idx = cols.get(key)
            if idx is None or idx >= len(raw_row):
                return ""
            return raw_row[idx]

        istat_raw = g("istat_comune").strip()
        if not istat_raw:
            continue

        # Normalizza ISTAT a 6 cifre (zero-pad)
        istat = istat_raw.zfill(6)[-6:]

        # Filtra riga aggregata "Mancante/errata" (no domicilio fiscale)
        if istat == "000000":
            continue
        denom_raw = g("denominazione").strip()
        if denom_raw in ("0", "", "Mancante/errata"):
            continue

        contribuenti = _to_int(g("contribuenti"))
        if contribuenti == 0:
            # Comune con zero contribuenti: salta
            continue

        # Tipologie reddito
        tipologie = {}
        for tipo in ("dipendente", "pensione", "autonomo", "fabbricati"):
            freq = _to_int(g(f"{tipo}_freq"))
            tot = _to_int(g(f"{tipo}_tot"))
            tipologie[tipo] = {
                "freq": freq,
                "tot": tot,
                "medio": _safe_div(tot, freq),
            }

        # Fasce di reddito (per derivare il totale se manca)
        fasce: dict[str, dict[str, Any]] = {}
        somma_fasce_tot = 0
        somma_fasce_freq = 0
        for i in range(8):
            fk = f"f{i}"
            freq = _to_int(g(f"{fk}_freq"))
            tot = _to_int(g(f"{fk}_tot"))
            fasce[fk] = {
                "label": FASCE_LABELS[fk],
                "freq": freq,
                "tot": tot,
            }
            somma_fasce_tot += tot
            somma_fasce_freq += freq

        # Reddito complessivo: usa colonna esplicita se disponibile (2023+),
        # altrimenti deriva da somma fasce (2020-22)
        rc_freq_col = _to_int(g("reddito_complessivo_freq"))
        rc_tot_col = _to_int(g("reddito_complessivo_tot"))
        if rc_tot_col > 0:
            reddito_complessivo_freq = rc_freq_col or somma_fasce_freq
            reddito_complessivo_tot = rc_tot_col
            reddito_complessivo_derivato = False
        else:
            reddito_complessivo_freq = somma_fasce_freq
            reddito_complessivo_tot = somma_fasce_tot
            reddito_complessivo_derivato = True

        # Imposta netta
        imp_netta_freq = _to_int(g("imposta_netta_freq"))
        imp_netta_tot = _to_int(g("imposta_netta_tot"))

        # Addizionale comunale
        add_com_freq = _to_int(g("add_comunale_freq"))
        add_com_tot = _to_int(g("add_comunale_tot"))

        # Addizionale regionale
        add_reg_freq = _to_int(g("add_regionale_freq"))
        add_reg_tot = _to_int(g("add_regionale_tot"))

        # Trattamento/Bonus
        tratt_freq = _to_int(g("trattamento_freq"))
        tratt_tot = _to_int(g("trattamento_tot"))

        # Calcoli pre-aggregati (frontend leggero)
        anno_data: dict[str, Any] = {
            "contribuenti": contribuenti,
            "reddito_complessivo": {
                "freq": reddito_complessivo_freq,
                "tot": reddito_complessivo_tot,
                "medio": _safe_div(reddito_complessivo_tot, contribuenti),
                "medio_per_dichiarante": _safe_div(
                    reddito_complessivo_tot, reddito_complessivo_freq
                ),
                "derivato_da_fasce": reddito_complessivo_derivato,
            },
            "imposta_netta": {
                "freq": imp_netta_freq,
                "tot": imp_netta_tot,
                "medio": _safe_div(imp_netta_tot, imp_netta_freq),
            },
            "addizionale_comunale": {
                "freq": add_com_freq,
                "tot": add_com_tot,
                "medio": _safe_div(add_com_tot, add_com_freq),
            },
            "addizionale_regionale": {
                "freq": add_reg_freq,
                "tot": add_reg_tot,
                "medio": _safe_div(add_reg_tot, add_reg_freq),
            },
            "trattamento": {
                "freq": tratt_freq,
                "tot": tratt_tot,
                "medio": _safe_div(tratt_tot, tratt_freq),
            },
            "tipologie": tipologie,
            "fasce": fasce,
        }

        # Tieni anche metadati per il primo anno valido (verranno consolidati dopo)
        meta = {
            "denominazione": g("denominazione").strip(),
            "sigla_provincia": g("sigla_provincia").strip(),
            "regione": g("regione").strip(),
            "cod_catastale": g("cod_catastale").strip(),
        }

        out[istat] = {"anno_data": anno_data, "meta": meta}

    log.info("anno %d: parsati %d comuni", year, len(out))
    return out


# -----------------------------------------------------------------------------
# Build shards multi-anno
# -----------------------------------------------------------------------------


def build_shards(years_data: dict[int, dict[str, dict]]) -> dict[str, dict]:
    """
    years_data: {anno: {istat: {anno_data, meta}}}
    Output: {istat: shard_dict pronto per scrittura}

    Baseline: solo comuni presenti nell'anno PIÙ RECENTE (allineamento con
    altri shard Cruscotto). Comuni soppressi prima dell'ultimo anno vengono
    esclusi anche se hanno dati storici.
    """
    if not years_data:
        return {}

    latest_year = max(years_data.keys())
    baseline_istat: set[str] = set(years_data[latest_year].keys())

    # Conteggio comuni "fantasma" esclusi (per logging)
    all_istat: set[str] = set()
    for y in years_data.values():
        all_istat.update(y.keys())
    ghost = all_istat - baseline_istat
    if ghost:
        log.info(
            "Esclusi %d comuni soppressi prima del %d (es. %s)",
            len(ghost),
            latest_year,
            ", ".join(sorted(ghost)[:5]),
        )

    shards: dict[str, dict] = {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for istat in sorted(baseline_istat):
        # Trova metadati dal primo anno disponibile (preferenza al più recente)
        meta = {}
        for yr in sorted(years_data.keys(), reverse=True):
            if istat in years_data[yr]:
                meta = years_data[yr][istat]["meta"]
                break

        anni_obj = {}
        for yr in sorted(years_data.keys()):
            if istat in years_data[yr]:
                anni_obj[str(yr)] = years_data[yr][istat]["anno_data"]

        # Trend: lista di {anno, reddito_medio, contribuenti}
        # utile per il line chart frontend
        trend = []
        for yr_str in sorted(anni_obj.keys()):
            d = anni_obj[yr_str]
            trend.append(
                {
                    "anno": int(yr_str),
                    "reddito_medio": d["reddito_complessivo"]["medio"],
                    "contribuenti": d["contribuenti"],
                    "imposta_media": d["imposta_netta"]["medio"],
                    "add_comunale_media": d["addizionale_comunale"]["medio"],
                }
            )

        shard = {
            "istat_comune": istat,
            "comune": meta.get("denominazione", ""),
            "sigla_provincia": meta.get("sigla_provincia", ""),
            "regione": meta.get("regione", ""),
            "cod_catastale": meta.get("cod_catastale", ""),
            "anni_disponibili": sorted(int(y) for y in anni_obj.keys()),
            "anni": anni_obj,
            "trend": trend,
            "fonte": "Dipartimento delle Finanze - MEF",
            "licenza": "CC BY 3.0",
            "url_fonte": (
                "https://www.finanze.gov.it/it/statistiche-fiscali/"
                "open-data-comunale-principali-variabili-irpef/"
            ),
            "last_update": now_iso,
        }
        shards[istat] = shard

    log.info("build_shards: %d shards costruiti", len(shards))
    return shards


# -----------------------------------------------------------------------------
# Push R2 (pattern standard Cruscotto)
# -----------------------------------------------------------------------------


def _list_existing_etags(r2, prefix: str) -> dict[str, str]:
    """Una sola list paginata, ritorna dict {filename: etag_stripped}."""
    etags: dict[str, str] = {}
    paginator = r2.get_paginator("list_objects_v2")
    page_iter = paginator.paginate(Bucket=R2_BUCKET, Prefix=prefix)
    for page in page_iter:
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            fname = key[len(prefix):]
            etag = obj["ETag"].strip('"')
            etags[fname] = etag
    return etags


def push_shards_r2(shards: dict[str, dict], force: bool = False) -> tuple[int, int]:
    """
    Pattern standard Cruscotto: list paginata + diff md5 + ThreadPool upload.
    Ritorna (uploaded, skipped).
    """
    r2 = get_r2_client()

    log.info("R2: list existing etags su prefix '%s'...", R2_SHARDS_PREFIX)
    t0 = time.time()
    remote_etags = _list_existing_etags(r2, R2_SHARDS_PREFIX)
    log.info(
        "R2: %d oggetti remoti elencati in %.1fs",
        len(remote_etags),
        time.time() - t0,
    )

    # Prepara payload + diff
    to_upload: list[tuple[str, bytes]] = []  # (filename, body)
    skipped = 0
    for istat, shard in shards.items():
        body = json.dumps(shard, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        local_md5 = md5(body).hexdigest()
        fname = f"{istat}.json"
        if not force and remote_etags.get(fname) == local_md5:
            skipped += 1
            continue
        to_upload.append((fname, body))

    log.info(
        "R2: da uploadare %d, skip %d (cache md5)",
        len(to_upload),
        skipped,
    )

    if not to_upload:
        return 0, skipped

    uploaded = 0
    t0 = time.time()

    def _put(item: tuple[str, bytes]) -> str:
        fname, body = item
        key = f"{R2_SHARDS_PREFIX}{fname}"
        r2.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return fname

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_UPLOAD) as ex:
        futures = [ex.submit(_put, item) for item in to_upload]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
                uploaded += 1
            except Exception as e:
                log.error("upload errore: %s", e)
            if i % 200 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(to_upload) - i) / rate if rate > 0 else 0
                log.info(
                    "R2 upload: %d/%d (%.0f/s, ETA %.0fs)",
                    i,
                    len(to_upload),
                    rate,
                    eta,
                )

    log.info(
        "R2: %d uploaded in %.1fs (%.1f/s), %d skip",
        uploaded,
        time.time() - t0,
        uploaded / max(time.time() - t0, 0.001),
        skipped,
    )
    return uploaded, skipped


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------


def run(
    years: list[int],
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    log.info("=" * 60)
    log.info("ETL MEF Redditi IRPEF")
    log.info("Anni: %s", years)
    log.info("Force: %s | Limit: %s | Dry-run: %s", force, limit, dry_run)
    log.info("=" * 60)

    years_data: dict[int, dict[str, dict]] = {}
    for yr in years:
        zip_bytes = download_year_zip(yr, force=force)
        parsed = parse_year_csv(zip_bytes, yr)
        if len(parsed) < EXPECTED_COMUNI - 100:
            log.warning(
                "anno %d: parsati solo %d comuni (atteso ~%d)",
                yr,
                len(parsed),
                EXPECTED_COMUNI,
            )
        years_data[yr] = parsed

    log.info("Costruisco shards multi-anno...")
    shards = build_shards(years_data)
    log.info("Totale shards costruiti: %d", len(shards))

    if limit:
        keys = sorted(shards.keys())[:limit]
        shards = {k: shards[k] for k in keys}
        log.info("Limit applicato: %d shards", len(shards))

    if dry_run:
        sample_key = next(iter(shards.keys()))
        log.info("DRY-RUN: shard di esempio (%s):", sample_key)
        print(json.dumps(shards[sample_key], ensure_ascii=False, indent=2))
        return

    uploaded, skipped = push_shards_r2(shards, force=force)
    log.info("FINE: uploaded=%d, skipped=%d", uploaded, skipped)


def parse_args():
    p = argparse.ArgumentParser(description="ETL MEF Redditi IRPEF")
    p.add_argument(
        "--anni",
        type=str,
        default=",".join(str(y) for y in SUPPORTED_YEARS),
        help=f"Anni da processare, comma-separated (default: {SUPPORTED_YEARS})",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignora cache R2 e md5 diff",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Processa solo N shards (per test)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Non scrive su R2, stampa solo uno shard di esempio",
    )
    return p.parse_args()


def main():
    args = parse_args()
    try:
        years = [int(y.strip()) for y in args.anni.split(",") if y.strip()]
    except ValueError:
        log.error("--anni invalido: %s", args.anni)
        sys.exit(2)

    unsupported = [y for y in years if y not in SUPPORTED_YEARS]
    if unsupported:
        log.error(
            "Anni non supportati: %s. Supportati: %s",
            unsupported,
            SUPPORTED_YEARS,
        )
        sys.exit(2)

    run(years=years, force=args.force, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
