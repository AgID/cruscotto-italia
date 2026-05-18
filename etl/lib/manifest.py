"""Manifest management — local-first.

manifest.json e' la source of truth per Worker MCP AgID:
quali dataset sono disponibili, quando sono stati generati, dimensioni.
Worker lo legge via HTTPS:  fetch(`${DATA_BASE_URL}/manifest.json`)
  - in produzione AgID: https://cruscotto-italia.dati.gov.it/data/manifest.json

Storage: filesystem locale, default /var/www/cruscotto-italia/data/manifest.json
(percorso configurabile via env DATA_DIR di local_lookup).

Schema:
{
  "generated_at": "2026-05-17T08:00:00+00:00",
  "etl_version": "0.1.0",
  "sources": {
    "anac": {
      "last_run": "2026-05-17T07:30:00+00:00",
      "status": "ok",
      "files": [
        {"key": "anac/2024/awards.parquet", "size": 134217728, "row_count": 1234567}
      ]
    },
    ...
  }
}

NOTE storiche:
- Pre 2026-05-17 il manifest era su R2 piersoft. Adesso e' local-first
  perche' l'account AgID non puo' creare bucket R2.
- L'API (load/save/update_source) e' invariata: chiamanti esistenti
  continuano a funzionare senza modifiche.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from . import local_lookup

log = structlog.get_logger()

ETL_VERSION_DEFAULT = "0.1.0"


def _manifest_path() -> Path:
    """Percorso del manifest sul filesystem locale."""
    return local_lookup.get_data_dir() / "manifest.json"


def load() -> dict[str, Any]:
    """Carica il manifest da disco; ritorna skeleton vuoto se assente."""
    f = _manifest_path()
    if not f.exists():
        return {
            "generated_at": None,
            "etl_version": ETL_VERSION_DEFAULT,
            "sources": {},
        }
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.error("manifest_corrupt", path=str(f), error=str(e))
        # File corrotto -> ricomincia da skeleton (preserve safety)
        return {
            "generated_at": None,
            "etl_version": ETL_VERSION_DEFAULT,
            "sources": {},
        }


def save(manifest: dict[str, Any]) -> None:
    """Scrive il manifest atomic (tmp + rename) sul filesystem locale."""
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    f = _manifest_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(f)
    log.info("manifest_saved", path=str(f),
             sources=len(manifest.get("sources", {})))


def update_source(source: str, files: list[dict], status: str = "ok") -> None:
    """Aggiorna l'entry di una singola sorgente nel manifest."""
    m = load()
    m.setdefault("sources", {})
    m["sources"][source] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "files": files,
    }
    save(m)
