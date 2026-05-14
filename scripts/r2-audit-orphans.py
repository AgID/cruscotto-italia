#!/usr/bin/env python3
"""
Audit shard orfani in R2.

Lista tutti gli oggetti del bucket R2 con pattern `<sezione>/<istat>.json`
e identifica quelli con ISTAT non più presente nell'anagrafica vigente
(7896 comuni italiani al 2026). Output: lista chiavi orfane + dimensione
recuperabile + comando per cancellarle.

Modalità default: DRY-RUN (mostra cosa cancellerebbe ma non agisce).
Per cancellare davvero: --delete.

Uso:
  # Credenziali R2 in env (vedi etl/lib/r2.py per dettagli)
  python3 scripts/r2-audit-orphans.py

  # Modalità delete (richiede conferma interattiva)
  python3 scripts/r2-audit-orphans.py --delete

  # Salva report in JSON
  python3 scripts/r2-audit-orphans.py --report /tmp/r2-audit.json

Prefix gestiti (estratti automaticamente da pattern <prefix>/<6-digit>.json):
  anac/, aria/, bdap/, bdap_mop/, bdap_siope/, carburanti/, dashboard/,
  demografia/, immobili_pa/, istat_profilo/, istat_turismo/, pnrr/,
  pun/, sanita/, scuole/, territorio/, veicoli/, anncsu/, anncsu_full/,
  agcom/, ...

Prefix NON gestiti (saltati silenziosamente):
  skills/, _metadata/, file aggregati non-comune (es. mef-redditi-aggregato.json)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Aggiungo etl/ al path per importare lib.r2
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.lib import r2  # noqa: E402

# Pattern: <prefix>/<6 cifre>.json
SHARD_KEY_PATTERN = re.compile(r"^([a-z_]+)/(\d{6})\.json$")


def load_anagrafica_istat(veicoli_dir: Path) -> set[str]:
    """
    Carica il set di codici ISTAT vigenti leggendo i file in output/veicoli/.
    Ritorna ~7896 codici 6-digit.
    """
    if not veicoli_dir.exists():
        raise FileNotFoundError(
            f"Directory {veicoli_dir} non trovata. "
            "Eseguire prima ETL veicoli con --target=local."
        )
    istat_set = set()
    for f in veicoli_dir.glob("*.json"):
        # Estraggo il codice ISTAT dal nome file (più veloce che parsing JSON)
        stem = f.stem
        if stem.isdigit() and len(stem) == 6:
            istat_set.add(stem)
    return istat_set


def list_all_shards(client, bucket: str) -> list[dict]:
    """
    Lista tutti gli oggetti del bucket che matchano il pattern shard.
    Ritorna list di dict {key, size, prefix, istat}.
    """
    shards = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            m = SHARD_KEY_PATTERN.match(key)
            if not m:
                continue  # skip non-shard (skills/, aggregati, etc.)
            shards.append({
                "key": key,
                "size": obj["Size"],
                "prefix": m.group(1),
                "istat": m.group(2),
            })
    return shards


def find_orphans(shards: list[dict], vigenti: set[str]) -> list[dict]:
    """Filtra solo gli shard con ISTAT non più vigente."""
    return [s for s in shards if s["istat"] not in vigenti]


def group_by_prefix(orphans: list[dict]) -> dict:
    """Aggrega orfani per prefix (per report leggibile)."""
    by_prefix: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0, "istat": set()})
    for o in orphans:
        by_prefix[o["prefix"]]["count"] += 1
        by_prefix[o["prefix"]]["size"] += o["size"]
        by_prefix[o["prefix"]]["istat"].add(o["istat"])
    # Convert set → list per JSON-serializable
    return {p: {**v, "istat": sorted(v["istat"])} for p, v in by_prefix.items()}


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def delete_orphans(client, bucket: str, orphans: list[dict],
                   batch_size: int = 1000) -> int:
    """Cancella gli orfani in batch (max 1000 per chiamata S3 delete_objects)."""
    deleted = 0
    for i in range(0, len(orphans), batch_size):
        batch = orphans[i:i + batch_size]
        resp = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": o["key"]} for o in batch]},
        )
        deleted += len(resp.get("Deleted", []))
        errors = resp.get("Errors", [])
        if errors:
            print(f"✗ {len(errors)} errori in batch {i // batch_size}:",
                  file=sys.stderr)
            for e in errors[:5]:
                print(f"  - {e.get('Key')}: {e.get('Message')}", file=sys.stderr)
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veicoli-dir", type=Path,
                    default=Path("output/veicoli"),
                    help="Directory shard veicoli da cui estrarre anagrafica ISTAT "
                         "(default: output/veicoli)")
    ap.add_argument("--delete", action="store_true",
                    help="Cancella effettivamente gli orfani (default: dry-run)")
    ap.add_argument("--yes", action="store_true",
                    help="Salta la conferma interattiva (richiede --delete)")
    ap.add_argument("--report", type=Path,
                    help="Salva report JSON in questo path")
    args = ap.parse_args()

    print("→ Carico anagrafica ISTAT vigente...")
    vigenti = load_anagrafica_istat(args.veicoli_dir)
    print(f"✓ Anagrafica: {len(vigenti):,} comuni vigenti")
    if len(vigenti) < 7000:
        print(f"✗ Anagrafica sospetta (< 7000): controllo {args.veicoli_dir}",
              file=sys.stderr)
        return 2

    print("→ Connessione R2...")
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    print(f"✓ Bucket: {bucket}")

    print("→ Listo tutti gli shard R2 (può richiedere 1-2 minuti)...")
    shards = list_all_shards(client, bucket)
    print(f"✓ Shard totali: {len(shards):,}")

    by_prefix_all = defaultdict(lambda: {"count": 0, "size": 0})
    for s in shards:
        by_prefix_all[s["prefix"]]["count"] += 1
        by_prefix_all[s["prefix"]]["size"] += s["size"]

    print("\n📊 Distribuzione shard per prefix:")
    for p, v in sorted(by_prefix_all.items(), key=lambda x: -x[1]["count"]):
        print(f"  {p:20s} {v['count']:>6,} shard, {human_bytes(v['size']):>10s}")

    orphans = find_orphans(shards, vigenti)
    total_size = sum(o["size"] for o in orphans)

    print(f"\n🔍 Shard orfani trovati: {len(orphans):,} ({human_bytes(total_size)})")

    if not orphans:
        print("✓ Nessun orfano. Storage pulito.")
        return 0

    by_prefix_orphans = group_by_prefix(orphans)
    print("\n📋 Orfani per prefix:")
    for p, v in sorted(by_prefix_orphans.items(), key=lambda x: -x[1]["count"]):
        istat_sample = ", ".join(v["istat"][:5])
        if len(v["istat"]) > 5:
            istat_sample += f", ...+{len(v['istat']) - 5}"
        print(f"  {p:20s} {v['count']:>4,} shard, {human_bytes(v['size']):>10s}  "
              f"ISTAT: {istat_sample}")

    if args.report:
        report = {
            "vigenti_count": len(vigenti),
            "shards_total": len(shards),
            "orphans_count": len(orphans),
            "orphans_size_bytes": total_size,
            "orphans_size_human": human_bytes(total_size),
            "by_prefix": by_prefix_orphans,
            "orphan_keys": [o["key"] for o in orphans],
        }
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n💾 Report scritto in {args.report}")

    if not args.delete:
        print(f"\n💡 Dry-run mode. Per cancellare: rilancia con --delete")
        print(f"   Recupero stimato: {human_bytes(total_size)}")
        return 0

    if not args.yes:
        print(f"\n⚠️  Stai per cancellare {len(orphans):,} oggetti R2 "
              f"({human_bytes(total_size)}). Procedere? [yes/N] ", end="")
        confirm = input().strip().lower()
        if confirm != "yes":
            print("✗ Annullato.")
            return 1

    print(f"\n→ Cancellazione in corso ({len(orphans)} oggetti)...")
    deleted = delete_orphans(client, bucket, orphans)
    print(f"✓ Cancellati {deleted:,} oggetti, recuperati {human_bytes(total_size)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
