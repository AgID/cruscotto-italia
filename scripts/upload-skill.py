#!/usr/bin/env python3
"""Upload una skill .zip dal repo (docs/skills/) al prefisso R2 'skills/'.

Pattern coerente con l'ETL: usa etl.lib.r2 con le credenziali R2 dell'env
(R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET).

Le skill caricate qui sono servite dal Worker via GET /skills/<name>.zip
(handler in worker/src/http.ts: pass-through R2 con cache 24h).

Uso:
    python scripts/upload-skill.py docs/skills/cruscotto-italia-workflow-v1.4.zip
    python scripts/upload-skill.py docs/skills/cruscotto-italia-workflow-v1.4.zip --key skills/custom-name.zip
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Aggiungi root repo a sys.path per import etl.lib
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from etl.lib import r2  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload skill zip a R2 prefix 'skills/'."
    )
    parser.add_argument("local_path", type=Path,
                        help="Percorso del file .zip locale "
                             "(es. docs/skills/cruscotto-italia-workflow-v1.4.zip)")
    parser.add_argument("--key", type=str, default=None,
                        help="Override della chiave remota su R2. "
                             "Default: 'skills/<basename del file locale>'.")
    args = parser.parse_args()

    if not args.local_path.exists():
        print(f"ERRORE: file non trovato: {args.local_path}", file=sys.stderr)
        return 1

    if not args.local_path.suffix == ".zip":
        print(f"ATTENZIONE: il file non è uno .zip ({args.local_path.suffix})",
              file=sys.stderr)

    key = args.key or f"skills/{args.local_path.name}"

    size = args.local_path.stat().st_size
    print(f"Caricamento {args.local_path} ({size:,} bytes) -> R2: {key}")
    r2.upload_file(args.local_path, key, content_type="application/zip")

    # Verifica via HEAD
    head = r2.head(key)
    if head:
        print(f"OK: ETag={head.get('ETag')} ContentLength={head.get('ContentLength')}")
    else:
        print("ERRORE: HEAD su R2 fallita dopo l'upload", file=sys.stderr)
        return 1

    print(f"\nServito ora da: https://cruscotto-italia-mcp.piersoftckan.biz/{key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
