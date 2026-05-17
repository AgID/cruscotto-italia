"""Cloudflare R2 client wrapper — DEPRECATO.

[2026-05-17] Cruscotto Italia su account AgID non usa piu' Cloudflare R2.
L'account AgID non puo' creare bucket R2 (richiede carta di credito). Tutta
l'infrastruttura dati e' local-first:

    /var/www/cruscotto-italia/data/
    ├── lookup/{comuni-bundle,anac-aggregato,bdap-aggregato}.json
    ├── <source>/<istat>.json    (shard per comune)
    └── manifest.json            (catalogo dati)

Il Worker MCP AgID legge questi file via HTTPS tramite DATA_BASE_URL
(transitoriamente Aruba, post-cutover https://cruscotto-italia.dati.gov.it/data).

Questo modulo e' mantenuto solo come kill-switch: ogni funzione esposta
solleva RuntimeError immediatamente per garantire che nessun chiamante
residuo possa accidentalmente fare richieste verso R2 piersoft.

Per le operazioni che prima andavano su R2, usare etl/lib/local_lookup.py:
    - load_comuni_bundle / load_anac_aggregato / load_bdap_aggregato
    - save_lookup, save_meta, load_meta
    - save_shard, save_bytes

Questo file verra' cancellato fisicamente nell'Ondata 2 (post-cutover stabile).
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()


_DEPRECATION_MSG = (
    "etl.lib.r2 e' DEPRECATO. Cruscotto Italia AgID non usa piu' Cloudflare R2. "
    "Usa etl.lib.local_lookup per lookup/meta/shard locali. "
    "Vedi HANDOFF_ETL_LOCALFIRST.md per la migrazione."
)


def _killswitch(fn_name: str):
    log.error("r2_killswitch_triggered", function=fn_name)
    raise RuntimeError(f"{_DEPRECATION_MSG} (chiamata: {fn_name})")


def get_r2_client():
    """DEPRECATO: solleva RuntimeError. Usa etl.lib.local_lookup."""
    _killswitch("get_r2_client")


def get_bucket() -> str:
    """DEPRECATO: solleva RuntimeError."""
    _killswitch("get_bucket")


def upload_file(local_path: Path | str, key: str, content_type: str | None = None) -> None:
    """DEPRECATO: solleva RuntimeError. Usa local_lookup.save_shard / save_bytes."""
    _killswitch("upload_file")


def upload_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> None:
    """DEPRECATO: solleva RuntimeError. Usa local_lookup.save_bytes."""
    _killswitch("upload_bytes")


def download_file(key: str, local_path: Path | str) -> None:
    """DEPRECATO: solleva RuntimeError. I dati sono local; leggi direttamente da DATA_DIR."""
    _killswitch("download_file")


def head(key: str) -> dict | None:
    """DEPRECATO: solleva RuntimeError. Usa pathlib.Path(...).exists() su DATA_DIR."""
    _killswitch("head")


def list_keys(prefix: str = "") -> list[str]:
    """DEPRECATO: solleva RuntimeError. Usa pathlib.Path(...).glob() su DATA_DIR."""
    _killswitch("list_keys")
