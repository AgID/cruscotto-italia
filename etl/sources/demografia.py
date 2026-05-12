"""ETL Demografia ISTAT POSAS - matrice eta x sesso per comune.

Fonte: ISTAT POSAS (Popolazione e situazione anagrafica) gia scaricato.
Path: /tmp/posas-test/extracted/POSAS_2026_it_Comuni.csv
Schema: codice_istat, comune, eta, maschi, femmine, totale
        (eta=999 e' la riga di totale comune)

Output:
- demografia/<istat>.json per ogni comune (~3-5 KB)
  Contiene: KPI aggregati + matrice eta x sesso per piramide

KPI calcolati:
- popolazione_totale, maschi_totale, femmine_totale
- pct_0_14 (giovanissimi), pct_15_64 (eta lavorativa), pct_65_piu (anziani)
- pct_85_piu (grandi anziani)
- indice_vecchiaia = (65+) / (0-14) * 100
- indice_dipendenza = (0-14 + 65+) / (15-64) * 100
- eta_media (ponderata)

Usage:
  python -m etl.sources.demografia --target=local
  python -m etl.sources.demografia --target=r2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

POSAS_CSV = Path("/tmp/posas-test/extracted/POSAS_2026_it_Comuni.csv")


def build_demografia_shards(csv_path: Path, output_dir: Path) -> Path:
    """Genera 1 file JSON per comune con matrice eta x sesso + KPI."""
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = output_dir / "demografia"
    shard_dir.mkdir(parents=True, exist_ok=True)

    log.info("demografia_loading_csv", path=str(csv_path))
    con = duckdb.connect()

    # POSAS ha riga 1 con titolo, riga 2 header reale
    con.execute(f"""
        CREATE TABLE pop AS
        SELECT
            "Codice comune" AS istat,
            "Comune" AS comune,
            CAST("Età" AS INTEGER) AS eta,
            CAST("Totale maschi" AS INTEGER) AS m,
            CAST("Totale femmine" AS INTEGER) AS f,
            CAST("Totale" AS INTEGER) AS tot
        FROM read_csv(
            '{csv_path}',
            delim=';',
            header=true,
            skip=1,
            quote='"',
            ignore_errors=true,
            all_varchar=false
        )
    """)

    n_total = con.execute("SELECT COUNT(*) FROM pop").fetchone()[0]
    n_comuni = con.execute("SELECT COUNT(DISTINCT istat) FROM pop").fetchone()[0]
    log.info("demografia_loaded", rows=n_total, comuni=n_comuni)

    # Per ogni comune, costruisci la matrice eta -> {m, f, tot} per eta 0-100
    # + estrai riga totale (eta=999)
    rows = con.execute("""
        SELECT istat, comune, eta, m, f, tot
        FROM pop
        WHERE istat IS NOT NULL
        ORDER BY istat, eta
    """).fetchall()

    # Raggruppa per istat
    by_istat: dict[str, dict] = {}
    for istat, comune, eta, m, f, tot in rows:
        if istat not in by_istat:
            by_istat[istat] = {
                "istat_code": istat,
                "comune": comune,
                "matrice": {},  # {eta: {m, f, tot}}
                "totale_riga": None,  # riga eta=999
            }
        if eta == 999:
            by_istat[istat]["totale_riga"] = {"m": m or 0, "f": f or 0, "tot": tot or 0}
        else:
            by_istat[istat]["matrice"][eta] = {"m": m or 0, "f": f or 0, "tot": tot or 0}

    # Calcola KPI per ogni comune
    n_written = 0
    total_bytes = 0
    for istat, d in by_istat.items():
        m_tot = d["totale_riga"]["m"] if d["totale_riga"] else 0
        f_tot = d["totale_riga"]["f"] if d["totale_riga"] else 0
        pop_tot = d["totale_riga"]["tot"] if d["totale_riga"] else 0

        if pop_tot == 0:
            log.warning("demografia_skip_empty", istat=istat)
            continue

        # Fasce d'eta
        matrice = d["matrice"]
        pop_0_14 = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(0, 15))
        pop_15_64 = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(15, 65))
        pop_65_piu = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(65, 101))
        pop_85_piu = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(85, 101))

        # Eta media ponderata: somma(eta * pop) / pop_tot
        eta_pop_sum = sum(e * matrice.get(e, {"tot": 0})["tot"] for e in range(0, 101))
        eta_media = round(eta_pop_sum / pop_tot, 1) if pop_tot else 0

        # Indici
        indice_vecchiaia = round(pop_65_piu / pop_0_14 * 100, 1) if pop_0_14 else None
        indice_dipendenza = round(
            (pop_0_14 + pop_65_piu) / pop_15_64 * 100, 1
        ) if pop_15_64 else None

        # Costruisci array piramide: [{eta, m, f, tot}, ...]
        piramide = [
            {
                "eta": e,
                "m": matrice.get(e, {"m": 0})["m"],
                "f": matrice.get(e, {"f": 0})["f"],
                "tot": matrice.get(e, {"tot": 0})["tot"],
            }
            for e in range(0, 101)
        ]

        payload = {
            "_etl_version": "0.2.0",
            "_source": "ISTAT POSAS - Popolazione residente per eta e sesso",
            "_riferimento": "1 gennaio 2026 (stima)",
            "istat_code": istat,
            "comune": d["comune"],
            "popolazione_totale": pop_tot,
            "maschi": m_tot,
            "femmine": f_tot,
            "pct_maschi": round(m_tot / pop_tot * 100, 1),
            "pct_femmine": round(f_tot / pop_tot * 100, 1),
            "fasce_eta": {
                "0_14": {"n": pop_0_14, "pct": round(pop_0_14 / pop_tot * 100, 1)},
                "15_64": {"n": pop_15_64, "pct": round(pop_15_64 / pop_tot * 100, 1)},
                "65_piu": {"n": pop_65_piu, "pct": round(pop_65_piu / pop_tot * 100, 1)},
                "85_piu": {"n": pop_85_piu, "pct": round(pop_85_piu / pop_tot * 100, 1)},
            },
            "eta_media": eta_media,
            "indice_vecchiaia": indice_vecchiaia,
            "indice_dipendenza": indice_dipendenza,
            "piramide": piramide,
        }

        shard_path = shard_dir / f"{istat}.json"
        shard_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n_written += 1
        total_bytes += shard_path.stat().st_size

    log.info("demografia_shards_done",
             n_shards=n_written,
             total_bytes=total_bytes,
             avg_bytes=total_bytes // max(1, n_written))
    return shard_dir


def push_to_r2_parallel(shard_dir: Path) -> int:
    """Upload parallelo in R2 con skip esistenti."""
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )

    existing = set()
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket="cruscotto-italia-data", Prefix="demografia/"
    ):
        for o in page.get("Contents", []):
            existing.add(o["Key"].split("/")[-1])

    shard_files = sorted(shard_dir.glob("*.json"))
    to_upload = [sf for sf in shard_files if sf.name not in existing]
    log.info("demografia_pushing",
             total=len(shard_files),
             to_upload=len(to_upload),
             already_on_r2=len(existing))

    def _upload_one(sf):
        r2.upload_file(sf, f"demografia/{sf.name}", content_type="application/json")
        return sf.name

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 1000 == 0:
                    log.info("demografia_push_progress",
                             uploaded=uploaded,
                             total=len(to_upload))
            except Exception as e:
                log.error("demografia_upload_failed", error=str(e))

    log.info("demografia_push_done", uploaded=uploaded)
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL Demografia ISTAT POSAS")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--csv", type=Path, default=POSAS_CSV)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="cruscotto-demografia-")) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target, output_dir=str(output_dir),
             csv=str(args.csv))

    if not args.csv.exists():
        log.error("csv_not_found", path=str(args.csv))
        return 1

    try:
        shard_dir = build_demografia_shards(args.csv, output_dir)

        if args.target == "r2":
            uploaded = push_to_r2_parallel(shard_dir)
            manifest.update_source(
                "demografia",
                [{"key": "demografia/*", "count": uploaded}],
                status="ok",
            )
            log.info("manifest_updated")

        log.info("etl_done")
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
