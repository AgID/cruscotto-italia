"""Helper local-first per leggere lookup files e meta state da filesystem.

Sostituisce le letture da R2 piersoft (bucket cruscotto-italia-data,
prefix lookup/ e <source>/_meta.json) per gli ETL che girano con
--target=local sull'infrastruttura AgID.

Layout filesystem atteso (default su VM AgID / Aruba):

    /var/www/cruscotto-italia/data/
    ├── lookup/
    │   ├── comuni-bundle.json       (anagrafica 7896 comuni)
    │   ├── anac-aggregato.json      (mappa CF -> KPI contratti)
    │   └── bdap-aggregato.json      (mappa CF -> KPI opere)
    ├── carburanti/_meta.json
    ├── pun/_meta.json
    └── agcom_bbmap/_meta.json

Override per test/dev:
    DATA_DIR     -> base directory (default /var/www/cruscotto-italia/data)
    LOOKUP_DIR   -> directory lookup (default $DATA_DIR/lookup)

Ogni funzione load_* ritorna None o {} su file mancante (non solleva),
cosi' il chiamante puo' decidere fallback (es. fetch HTTP) o errore
controllato.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

log = structlog.get_logger()

DEFAULT_DATA_DIR = Path("/var/www/cruscotto-italia/data")


def get_data_dir() -> Path:
    """Base data dir; override via env DATA_DIR (utile in test/CI)."""
    return Path(os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)))


def get_lookup_dir() -> Path:
    """Lookup dir; override via env LOOKUP_DIR, default $DATA_DIR/lookup."""
    explicit = os.environ.get("LOOKUP_DIR")
    if explicit:
        return Path(explicit)
    return get_data_dir() / "lookup"


# ---------------------------------------------------------------------------
# Lookup files (comuni anagrafica + ANAC + BDAP aggregati)
# ---------------------------------------------------------------------------

def load_comuni_bundle() -> dict | None:
    """Ritorna il dict 'comuni' del bundle anagrafica o None se manca.

    Struttura attesa: {"comuni": {"077014": {...}, "075035": {...}, ...}}
    Restituisce direttamente il sotto-dict 'comuni' (chiavi = ISTAT 6 cifre).
    """
    f = get_lookup_dir() / "comuni-bundle.json"
    if not f.exists():
        log.warning("local_lookup_missing",
                    file="comuni-bundle.json",
                    path=str(f),
                    hint="rigenerare via anagrafica.py oppure rsync da Aruba")
        return None
    payload = json.loads(f.read_text(encoding="utf-8"))
    return payload.get("comuni", {})


def load_anac_aggregato() -> dict:
    """Ritorna il dict 'data' di anac-aggregato.json o {} se manca.

    Struttura attesa: {"data": {"<CF>": {...kpi...}, ...}, "_meta": {...}}
    """
    f = get_lookup_dir() / "anac-aggregato.json"
    if not f.exists():
        log.warning("local_lookup_missing", file="anac-aggregato.json", path=str(f))
        return {}
    payload = json.loads(f.read_text(encoding="utf-8"))
    return payload.get("data", {})


def load_bdap_aggregato() -> dict:
    """Ritorna il dict 'data' di bdap-aggregato.json o {} se manca.

    Struttura attesa: {"data": {"<CF>": {...kpi opere...}, ...}, "_meta": {...}}
    """
    f = get_lookup_dir() / "bdap-aggregato.json"
    if not f.exists():
        log.warning("local_lookup_missing", file="bdap-aggregato.json", path=str(f))
        return {}
    payload = json.loads(f.read_text(encoding="utf-8"))
    return payload.get("data", {})


def save_lookup(name: str, payload: dict) -> Path:
    """Scrive un file lookup (sovrascrive). Usata da anac.py/bdap.py target=local.

    name: nome file senza directory (es. 'anac-aggregato.json')
    Ritorna il Path scritto.
    """
    f = get_lookup_dir() / name
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    log.info("local_lookup_saved", file=name, path=str(f), bytes=f.stat().st_size)
    return f


# ---------------------------------------------------------------------------
# Meta state incrementale (carburanti, pun, agcom_bbmap)
# ---------------------------------------------------------------------------

def load_meta(source: str) -> dict:
    """Legge data/<source>/_meta.json o {} se non esiste.

    Usato dagli ETL incrementali (carburanti, pun, agcom_bbmap) per
    sapere quando hanno scaricato l'ultima volta.
    """
    f = get_data_dir() / source / "_meta.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def save_meta(source: str, data: dict) -> Path:
    """Scrive data/<source>/_meta.json atomic (tmp + rename)."""
    f = get_data_dir() / source / "_meta.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    log.info("local_meta_saved", source=source, path=str(f))
    return f


# ---------------------------------------------------------------------------
# Shard writers per gli ETL TIPO 3 (push) in modalita' --target=local
# ---------------------------------------------------------------------------

def save_shard(source: str, istat: str, payload: dict) -> Path:
    """Scrive uno shard per comune: data/<source>/<istat>.json (atomic).

    Sostituisce client.put_object(Bucket, Key='<source>/<istat>.json', Body=...)
    """
    f = get_data_dir() / source / f"{istat}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    return f


def save_bytes(key: str, data: bytes) -> Path:
    """Scrive bytes a percorso arbitrario sotto DATA_DIR. key e' relativo.

    Esempio: save_bytes('skills/cruscotto-workflow.zip', zipdata)
    -> /var/www/cruscotto-italia/data/skills/cruscotto-workflow.zip
    """
    f = get_data_dir() / key
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp") if f.suffix else Path(str(f) + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    return f
