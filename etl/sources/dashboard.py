"""ETL Dashboard A1 - accorpa tutti gli shard di un comune in un unico file.

Strategia A1: invece di 6+ chiamate MCP separate (demografia, profilo, turismo,
pnrr, territorio, opere, anac), il frontend fa UNA chiamata sola che restituisce
dashboard/<istat>.json contenente tutto.

Benefici:
- 1 chiamata MCP per visita invece di 6+ (riduce alert Anthropic)
- Niente piu' dipendenza da BDAP OData live per SIOPE (TODO: pre-calcolato in
  un secondo step dopo questa baseline)
- Cache R2 unica, semplifica il worker

Sorgenti accorpate:
- demografia/<istat>.json        (POSAS ISTAT)
- profilo/<istat>.json           (Censimento ISTAT)
- turismo/<istat>.json           (ISTAT Turismo)
- pnrr/<istat>.json              (Italia Domani)
- territorio/<istat>.json        (ISPRA Suolo/Idro/Rifiuti)
- bdap/dettaglio/<istat>.json    (BDAP-MOP opere)
- siope/<istat>.json             (SIOPE Spese pre-calcolate)
- scuole/<istat>.json            (MIUR Anagrafe scuole statali)
- immobili_pa/<istat>.json       (MEF DE - Beni Immobili Pubblici 2022)
- anncsu/<istat>.json            (ANNCSU - civici e strade urbane)
- sanita_mds/<istat>.json        (Min. Salute - farmacie, parafarmacie, posti letto ospedalieri)
- pun/<istat>.json               (GSE/MASE - Piattaforma Unica Nazionale punti di ricarica)
- lookup/anac-aggregato.json     (filtrato per CF)
- lookup/comuni-bundle.json      (anagrafica)

Output:
- dashboard/<istat>.json per ogni comune in anagrafica (~7896 file)

Schema output:
  {
    "_etl_version": "0.1.0",
    "_generated_at": "ISO-8601",
    "_missing": ["lista shard non trovati"],
    "anagrafica":  { ... },   # da bundle.comuni[<istat>]
    "demografia":  { ... },   # null se _missing contiene "demografia"
    "profilo":     { ... },
    "turismo":     { ... },
    "pnrr":        { ... },
    "territorio":  { ... },
    "opere":       { ... },
    "anac":        { ... }    # null se CF non in lookup ANAC
  }

Usage:
  python -m etl.sources.dashboard --target=local
  python -m etl.sources.dashboard --target=r2
  python -m etl.sources.dashboard --target=r2 --limit=50    # smoke test
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# Shard da accorpare: (label nello schema output, key R2)
SHARDS = [
    ("demografia", "demografia/{istat}.json"),
    ("profilo",    "profilo/{istat}.json"),
    ("turismo",    "turismo/{istat}.json"),
    ("pnrr",       "pnrr/{istat}.json"),
    ("territorio", "territorio/{istat}.json"),
    ("opere",      "bdap/dettaglio/{istat}.json"),
    ("siope",      "siope/{istat}.json"),
    ("scuole",     "scuole/{istat}.json"),
    ("aria",       "aria/{istat}.json"),
    ("veicoli",    "veicoli/{istat}.json"),
    ("redditi",    "redditi/{istat}.json"),
    ("immobili_pa", "immobili_pa/{istat}.json"),
    ("anncsu",     "anncsu/{istat}.json"),
    ("sanita_mds", "sanita_mds/{istat}.json"),
    ("pun",        "pun/{istat}.json"),
]

ETL_VERSION = "0.1.0"


def fetch_json(client, bucket: str, key: str) -> dict | None:
    """Scarica un JSON da R2; ritorna None se non esiste."""
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        # 404 puo' arrivare anche come ClientError generico
        msg = str(e).lower()
        if "nosuchkey" in msg or "404" in msg or "not found" in msg:
            return None
        raise


def build_dashboard_for_comune(client, bucket: str, istat: str,
                                anagrafica: dict,
                                anac_data: dict,
                                bdap_data: dict) -> dict:
    """Costruisce il dashboard shard per un singolo comune.

    Ritorna dict con _etl_version, _generated_at, _missing, anagrafica, e
    una chiave per ogni shard (None se mancante).
    """
    missing: list[str] = []
    out: dict = {
        "_etl_version": ETL_VERSION,
        "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_missing": missing,
        "anagrafica": anagrafica,
    }

    # Shard fisici su R2
    for label, pat in SHARDS:
        data = fetch_json(client, bucket, pat.format(istat=istat))
        if data is None:
            missing.append(label)
            out[label] = None
        else:
            out[label] = data

    cf = anagrafica.get("codice_fiscale")

    # ANAC: lookup per codice fiscale
    if cf and cf in anac_data:
        out["anac"] = anac_data[cf]
    else:
        missing.append("anac")
        out["anac"] = None

    # BDAP KPI aggregato (per chart Opere finanz_*): lookup per CF.
    # NB: e\'  separato dallo shard "opere" (che e\' il dettaglio progetti).
    if cf and cf in bdap_data:
        out["bdap_kpi"] = bdap_data[cf]
    else:
        missing.append("bdap_kpi")
        out["bdap_kpi"] = None

    return out


def build_all_shards(output_dir: Path, limit: int | None = None) -> dict:
    """Costruisce tutti i dashboard shard nella output_dir locale.

    Ritorna stats: {processed, missing_per_shard, fully_complete}
    """
    client = r2.get_r2_client()
    bucket = r2.get_bucket()

    log.info("loading_lookups")
    bundle = json.loads(client.get_object(
        Bucket=bucket, Key="lookup/comuni-bundle.json"
    )["Body"].read())["comuni"]
    anac_full = json.loads(client.get_object(
        Bucket=bucket, Key="lookup/anac-aggregato.json"
    )["Body"].read())
    anac_data = anac_full.get("data", {})
    bdap_full = json.loads(client.get_object(
        Bucket=bucket, Key="lookup/bdap-aggregato.json"
    )["Body"].read())
    bdap_data = bdap_full.get("data", {})
    log.info("lookups_loaded",
             comuni=len(bundle),
             anac_enti=len(anac_data),
             bdap_enti=len(bdap_data))

    output_dir.mkdir(parents=True, exist_ok=True)

    istat_codes = sorted(bundle.keys())
    if limit:
        istat_codes = istat_codes[:limit]
        log.warning("limit_applied", limit=limit)

    stats = {
        "processed": 0,
        "missing_per_shard": {label: 0 for label, _ in SHARDS},
        "missing_anac": 0,
        "missing_bdap_kpi": 0,
        "fully_complete": 0,
    }

    def _build_one(istat: str) -> tuple[str, list[str]]:
        anagrafica = bundle[istat]
        out = build_dashboard_for_comune(
            client, bucket, istat, anagrafica, anac_data, bdap_data
        )
        # Scrivi su disco
        out_path = output_dir / f"{istat}.json"
        out_path.write_text(
            json.dumps(out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return istat, out["_missing"]

    # I/O bound (R2 GET): alta concorrenza
    with ThreadPoolExecutor(max_workers=32) as ex:
        futures = {ex.submit(_build_one, istat): istat for istat in istat_codes}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                istat, missing = fut.result()
                stats["processed"] += 1
                for label in missing:
                    if label == "anac":
                        stats["missing_anac"] += 1
                    elif label == "bdap_kpi":
                        stats["missing_bdap_kpi"] += 1
                    elif label in stats["missing_per_shard"]:
                        stats["missing_per_shard"][label] += 1
                if not missing:
                    stats["fully_complete"] += 1
                if i % 500 == 0:
                    log.info("build_progress",
                             done=i, total=len(istat_codes),
                             fully_complete=stats["fully_complete"])
            except Exception as e:
                log.error("build_failed",
                          istat=futures[fut], error=str(e))

    log.info("build_done", **stats)
    return stats


def push_to_r2_parallel(shard_dir: Path) -> int:
    """Upload parallelo degli shard sotto prefix 'dashboard/'."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()

    shard_files = sorted(shard_dir.glob("*.json"))
    log.info("dashboard_pushing", total=len(shard_files))

    def _upload_one(sf: Path) -> str:
        r2.upload_file(sf, f"dashboard/{sf.name}", content_type="application/json")
        return sf.name

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in shard_files}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 500 == 0:
                    log.info("dashboard_push_progress",
                             uploaded=uploaded, total=len(shard_files))
            except Exception as e:
                log.error("dashboard_upload_failed", error=str(e))

    log.info("dashboard_push_done", uploaded=uploaded)
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Dashboard A1 - accorpa shard comune in dashboard/<istat>.json"
    )
    parser.add_argument(
        "--target", choices=["local", "r2"], default="local",
        help="Dove pubblicare gli shard"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory output locale (default: tempdir)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limita a primi N comuni (smoke test)"
    )
    args = parser.parse_args()

    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="cruscotto-dashboard-"))
    log.info("etl_start", target=args.target, output_dir=str(output_dir),
             limit=args.limit)

    try:
        stats = build_all_shards(output_dir, limit=args.limit)

        if args.target == "r2":
            uploaded = push_to_r2_parallel(output_dir)
            manifest.update_source(
                "dashboard",
                [{"key": "dashboard/*", "count": uploaded}],
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
