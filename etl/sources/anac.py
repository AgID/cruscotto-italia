"""ETL ANAC OCDS — contratti pubblici aggregati per comune.

Fonte: dati.anticorruzione.it/opendata, dataset 'ocds-appalti-ordinari-{anno}'
URL pattern: https://dati.anticorruzione.it/opendata/download/dataset/ocds/filesystem/bulk/{YYYY}/{MM}.json
Licenza: CC-BY-SA 4.0

Pipeline:
  1. Per ogni (anno, mese) richiesto:
     - Download {YYYY}/{MM}.json se non già presente (idempotente)
     - DuckDB query → Parquet '{anno}-{mese}-awards.parquet' con un row per award
  2. Aggregazione cross-mensile via DuckDB:
     - Per ogni buyer_cf (= CF stazione appaltante): count, importo totale, top CPV, top fornitori
     - Output: 'lookup/anac-aggregato.json' (mappa CF → KPI)
  3. Push su R2 + arricchimento bundle comuni con KPI contratti

Usage:
  python -m etl.sources.anac --target=local --years=2026 --months=1,2,3
  python -m etl.sources.anac --target=r2 --years=2025,2026
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import duckdb
import requests
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ANAC_BASE = "https://dati.anticorruzione.it/opendata/download/dataset/ocds/filesystem/bulk"


# ----------------------------------------------------------------------------
# Pull (download monthly files)
# ----------------------------------------------------------------------------
def pull_anac_month(workdir: Path, year: int, month: int) -> Path:
    """Download one OCDS monthly bulk file. Idempotent: skip if file exists."""
    workdir.mkdir(parents=True, exist_ok=True)
    fname = f"{year}-{month:02d}.json"
    fpath = workdir / fname

    if fpath.exists() and fpath.stat().st_size > 100_000:  # >100KB sanity
        log.info("anac_month_already_downloaded", path=str(fpath), bytes=fpath.stat().st_size)
        return fpath

    url = f"{ANAC_BASE}/{year}/{month:02d}.json"
    log.info("anac_month_pulling", url=url, dest=str(fpath))

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with requests.get(url, headers=headers, stream=True, timeout=900) as resp:
        resp.raise_for_status()
        size = 0
        with open(fpath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
                f.write(chunk)
                size += len(chunk)

    log.info("anac_month_saved", path=str(fpath), bytes=size, mb=round(size / 1024 / 1024, 1))
    return fpath


# ----------------------------------------------------------------------------
# Transform (1 month JSON → 1 Parquet of awards)
# ----------------------------------------------------------------------------
def transform_anac_month(json_path: Path, output_dir: Path) -> Path:
    """Parse OCDS JSON and produce Parquet with one row per award."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pq_path = output_dir / f"{json_path.stem}-awards.parquet"

    if pq_path.exists():
        log.info("anac_parquet_already_built", path=str(pq_path), bytes=pq_path.stat().st_size)
        return pq_path

    log.info("anac_transforming", json=str(json_path))
    con = duckdb.connect()

    # Estrazione: una riga per award. Conserva CF, nome, importo, data, CPV.
    # CPV va estratto dal primo item dell'award (di solito ce n'è uno solo).
    con.execute(f"""
        COPY (
            SELECT
                r.ocid AS ocid,
                r.id AS release_id,
                r.buyer.id AS buyer_cf,
                r.buyer.name AS buyer_name,
                r.tender.mainProcurementCategory AS category,
                r.tender.procurementMethodDetails AS procurement_method,
                a.id AS award_id,
                a.status AS award_status,
                CAST(a.date AS VARCHAR) AS award_date,
                CAST(a.value.amount AS DOUBLE) AS award_amount,
                a.value.currency AS award_currency,
                -- CPV dal primo item
                (a.items[1]).classification.id AS cpv_code,
                (a.items[1]).classification.description AS cpv_desc,
                (a.items[1]).description AS item_description
            FROM (
                SELECT unnest(releases) AS r
                FROM read_json('{json_path}', maximum_object_size=2147483647)
            ),
            unnest(r.awards) AS award_t(a)
            WHERE r.buyer.id IS NOT NULL
        )
        TO '{pq_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n_rows = con.execute(f"SELECT COUNT(*) FROM '{pq_path}'").fetchone()[0]
    n_buyers = con.execute(
        f"SELECT COUNT(DISTINCT buyer_cf) FROM '{pq_path}'"
    ).fetchone()[0]
    log.info(
        "anac_parquet_built",
        path=str(pq_path),
        bytes=pq_path.stat().st_size,
        rows=n_rows,
        unique_buyers=n_buyers,
    )
    return pq_path


# ----------------------------------------------------------------------------
# Aggregate (N Parquet → 1 JSON map: buyer_cf → KPI)
# ----------------------------------------------------------------------------
def aggregate_anac(parquet_paths: list[Path], output_dir: Path) -> Path:
    """Aggregate all monthly awards parquets into a per-buyer KPI JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "anac-aggregato.json"

    log.info("anac_aggregating", n_parquets=len(parquet_paths))
    con = duckdb.connect()

    # UNION di tutti i parquet
    union_sql = " UNION ALL ".join(f"SELECT * FROM '{p}'" for p in parquet_paths)
    con.execute(f"CREATE TEMP TABLE all_awards AS {union_sql}")

    n_total = con.execute("SELECT COUNT(*) FROM all_awards").fetchone()[0]
    n_buyers = con.execute("SELECT COUNT(DISTINCT buyer_cf) FROM all_awards").fetchone()[0]
    log.info("anac_total_awards_loaded", awards=n_total, unique_buyers=n_buyers)

    # Aggregazione principale per buyer_cf
    rows = con.execute("""
        SELECT
            buyer_cf,
            ANY_VALUE(buyer_name) AS buyer_name,
            COUNT(*) AS count_total,
            SUM(award_amount) AS importo_totale,
            MIN(award_date) AS first_award_date,
            MAX(award_date) AS last_award_date,
            COUNT(DISTINCT cpv_code) AS distinct_cpv
        FROM all_awards
        WHERE buyer_cf IS NOT NULL
        GROUP BY buyer_cf
    """).fetchall()

    # Top 5 CPV per buyer (separato)
    cpv_rows = con.execute("""
        SELECT buyer_cf, cpv_code, cpv_desc, COUNT(*) AS n, SUM(award_amount) AS importo
        FROM all_awards
        WHERE cpv_code IS NOT NULL AND buyer_cf IS NOT NULL
        GROUP BY buyer_cf, cpv_code, cpv_desc
        QUALIFY ROW_NUMBER() OVER (PARTITION BY buyer_cf ORDER BY importo DESC) <= 5
    """).fetchall()
    top_cpv: dict[str, list] = {}
    for r in cpv_rows:
        top_cpv.setdefault(r[0], []).append({
            "code": r[1],
            "desc": r[2][:80] if r[2] else None,
            "count": r[3],
            "importo": float(r[4]) if r[4] else 0.0,
        })

    # Costruzione map finale
    aggregato: dict[str, dict] = {}
    for r in rows:
        cf = r[0]
        aggregato[cf] = {
            "buyer_name": r[1],
            "count": r[2],
            "importo_totale": float(r[3]) if r[3] else 0.0,
            "first_award_date": r[4],
            "last_award_date": r[5],
            "distinct_cpv": r[6],
            "top_cpv": top_cpv.get(cf, []),
        }

    out_path.write_text(
        json.dumps(
            {
                "_etl_version": "0.1.0",
                "_source": "ANAC OCDS",
                "_period_files": [p.stem for p in parquet_paths],
                "data": aggregato,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    log.info(
        "anac_aggregato_built",
        path=str(out_path),
        bytes=out_path.stat().st_size,
        n_buyers=len(aggregato),
    )
    return out_path


# ----------------------------------------------------------------------------
# Push to R2
# ----------------------------------------------------------------------------
def push_to_r2(local_path: Path, key: str, content_type: str = "application/json") -> dict:
    r2.upload_file(local_path, key, content_type=content_type)
    return {"key": key, "size": local_path.stat().st_size}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def parse_months(s: str) -> list[int]:
    return [int(m.strip()) for m in s.split(",") if m.strip()]


def parse_years(s: str) -> list[int]:
    return [int(y.strip()) for y in s.split(",") if y.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL ANAC OCDS — contratti aggregati per comune")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--years", type=str, default="2026", help="Comma-separated, e.g. 2025,2026")
    parser.add_argument("--months", type=str, default="1,2,3,4,5,6,7,8,9,10,11,12",
                        help="Comma-separated month list, default all 12")
    parser.add_argument("--outdir", type=Path, default=Path("/var/www/cruscotto-italia/data"))
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/cruscotto-anac-cache"),
                        help="Persistent download cache (won't re-download)")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ]
    )

    years = parse_years(args.years)
    months = parse_months(args.months)

    output_dir = args.outdir
    output_dir.mkdir(parents=True, exist_ok=True)
    args.workdir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target, output_dir=str(output_dir),
             workdir=str(args.workdir), years=years, months=months)

    try:
        # 1. Pull + transform
        parquet_paths: list[Path] = []
        for year in years:
            for month in months:
                # Skip future months for ongoing year
                # (ANAC pubblica con 2 mesi di ritardo circa)
                try:
                    json_path = pull_anac_month(args.workdir, year, month)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        log.warning("anac_month_not_available", year=year, month=month)
                        continue
                    raise

                # Sanity: verifica che il file scaricato sia davvero JSON (non HTML del WAF)
                if json_path.stat().st_size < 1_000_000:  # < 1MB = sospetto
                    head = json_path.read_bytes()[:200].decode("utf-8", errors="replace")
                    if "<html" in head.lower():
                        log.error("anac_month_blocked_by_waf", year=year, month=month, head=head[:200])
                        json_path.unlink(missing_ok=True)
                        continue

                pq = transform_anac_month(json_path, output_dir / "monthly")
                parquet_paths.append(pq)

        if not parquet_paths:
            log.error("no_parquets_built")
            return 1

        # 2. Aggregate
        aggr_path = aggregate_anac(parquet_paths, output_dir)

        # 3. Push (optional)
        files = []
        if args.target == "r2":
            f = push_to_r2(aggr_path, "lookup/anac-aggregato.json")
            files.append(f)
            # Push anche i parquet mensili (utile per audit)
            for pq in parquet_paths:
                key = f"anac/monthly/{pq.name}"
                files.append(push_to_r2(pq, key, content_type="application/vnd.apache.parquet"))
            manifest.update_source("anac", files, status="ok")
            log.info("manifest_updated")

        log.info("etl_done", parquets_built=len(parquet_paths), aggregato_bytes=aggr_path.stat().st_size)
        return 0

    except Exception as e:
        log.exception("etl_failed", error=str(e))
        if args.target == "r2":
            try:
                manifest.update_source("anac", [], status=f"failed: {e}")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
