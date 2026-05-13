"""ETL BDAP MOP — opere pubbliche aggregate per comune.

Fonte: bdap-opendata.rgs.mef.gov.it, dataset CKAN-style:
  - spd_mop_prg_mon_opere_01_9999  (Progetti Opere Pubbliche - Totale, ~400 MB)
  - spd_mop_loc_mon_local_01_9999  (Localizzazione Geografica, ~65 MB)
Licenza: CC-BY 4.0 (IODL 2.0 compatibile)

Pipeline:
  1. Pull CSV (entrambi sono in latin-1, vengono convertiti UTF-8 streaming)
  2. DuckDB query su Progetti Totale: aggregato per buyer_cf con stato/finanziamenti
  3. Output 'lookup/bdap-aggregato.json' (mappa CF → KPI opere)
  4. Push su R2

Schema KPI per comune:
  {
    "totale": {"count": N, "costo_lavori": N},
    "attivi": {...}, "chiusi": {...}, "cancellati": {...},
    "finanziamenti": {"statali": N, "europei": N, "enti_terr": N, "privati": N, "altri": N},
    "top_settori": [{"settore": "Strade", "count": N, "costo": N}, ...]
  }

Usage:
  python -m etl.sources.bdap --target=local
  python -m etl.sources.bdap --target=r2
"""
from __future__ import annotations

import argparse
import json
import os
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

# CKAN dataset UUIDs
DATASET_PROGETTI_TOTALE = "c76e90f7-eea5-4f32-8767-6b60e3505a1d"
DATASET_LOCALIZZAZIONE = "31b40a28-9d84-49f4-905e-f4bf471aae8b"
BDAP_DUMP = "https://bdap-opendata.rgs.mef.gov.it/SpodCkanApi/api/3/datastore/dump"


def pull_csv(workdir: Path, uuid: str, name: str) -> Path:
    """Download a BDAP CKAN datastore CSV. Idempotent."""
    workdir.mkdir(parents=True, exist_ok=True)
    raw_path = workdir / f"{name}.csv"
    utf8_path = workdir / f"{name}-utf8.csv"

    if utf8_path.exists() and utf8_path.stat().st_size > 1_000_000:
        log.info("bdap_csv_already_converted", name=name, path=str(utf8_path))
        return utf8_path

    if not raw_path.exists() or raw_path.stat().st_size < 1_000_000:
        url = f"{BDAP_DUMP}/{uuid}.csv"
        log.info("bdap_pulling", name=name, url=url)
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
            stream=True,
            timeout=900,
        ) as resp:
            resp.raise_for_status()
            with open(raw_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        log.info("bdap_csv_saved", name=name, bytes=raw_path.stat().st_size)
    else:
        log.info("bdap_csv_already_downloaded", name=name)

    # Convert latin-1 → UTF-8
    log.info("bdap_converting_to_utf8", src=str(raw_path), dst=str(utf8_path))
    chunk_size = 64 * 1024 * 1024
    total = 0
    with open(raw_path, "rb") as fin, open(utf8_path, "wb") as fout:
        while True:
            data = fin.read(chunk_size)
            if not data:
                break
            fout.write(data.decode("latin-1").encode("utf-8"))
            total += len(data)
    log.info("bdap_csv_utf8_ready", name=name, bytes=total, path=str(utf8_path))
    return utf8_path


def aggregate_progetti(progetti_csv: Path, output_dir: Path) -> Path:
    """DuckDB aggregation: progetti per buyer_cf with all the KPIs we need."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "bdap-aggregato.json"

    log.info("bdap_aggregating", csv=str(progetti_csv))
    con = duckdb.connect()

    # Carica una volta in memoria
    con.execute(f"""
        CREATE TABLE prg AS
        SELECT
            "Codice Fiscale Titolare" AS cf,
            "Descrizione Titolare" AS nome,
            "Codice CUP" AS cup,
            "Descrizione Stato CUP" AS stato,
            "Settore Interv Inv" AS settore,
            "Sottosettore Interv Inv" AS sottosettore,
            "Natura Intervento" AS natura,
            CAST(REPLACE("Costo Lavori Effettivo", ',', '.') AS DOUBLE) AS costo_eff,
            CAST(REPLACE("Costo Lavori Previsto", ',', '.') AS DOUBLE) AS costo_prev,
            CAST(REPLACE("Finanziamenti Statali", ',', '.') AS DOUBLE) AS fin_stat,
            CAST(REPLACE("Finanziamenti Europei", ',', '.') AS DOUBLE) AS fin_eu,
            CAST(REPLACE("Finanziamenti Enti Territorial", ',', '.') AS DOUBLE) AS fin_terr,
            CAST(REPLACE("Finanziamenti Privati", ',', '.') AS DOUBLE) AS fin_priv,
            CAST(REPLACE("Altre fonti di finanziamento", ',', '.') AS DOUBLE) AS fin_altri
        FROM read_csv(
            '{progetti_csv}',
            delim=';', header=true, ignore_errors=true, all_varchar=true
        )
        WHERE "Codice Fiscale Titolare" IS NOT NULL
    """)

    n_total = con.execute("SELECT COUNT(*) FROM prg").fetchone()[0]
    n_buyers = con.execute("SELECT COUNT(DISTINCT cf) FROM prg").fetchone()[0]
    log.info("bdap_loaded", projects=n_total, unique_buyers=n_buyers)

    # 1) Aggregato principale per CF + stato
    rows = con.execute("""
        SELECT
            cf,
            ANY_VALUE(nome) AS nome,
            stato,
            COUNT(*) AS n,
            SUM(costo_eff) AS costo_eff,
            SUM(costo_prev) AS costo_prev,
            SUM(fin_stat) AS fin_stat,
            SUM(fin_eu) AS fin_eu,
            SUM(fin_terr) AS fin_terr,
            SUM(fin_priv) AS fin_priv,
            SUM(fin_altri) AS fin_altri
        FROM prg
        GROUP BY cf, stato
    """).fetchall()

    # Costruisci mappa CF → {stato: kpi}
    by_cf: dict[str, dict] = {}
    for r in rows:
        cf, nome, stato, n, ce, cp, fs, fe, ft, fp, fa = r
        if cf not in by_cf:
            by_cf[cf] = {"nome": nome, "per_stato": {}}
        by_cf[cf]["per_stato"][stato or "?"] = {
            "count": int(n),
            "costo_lavori_eff": float(ce or 0),
            "costo_lavori_prev": float(cp or 0),
            "finanz_statali": float(fs or 0),
            "finanz_europei": float(fe or 0),
            "finanz_enti_terr": float(ft or 0),
            "finanz_privati": float(fp or 0),
            "finanz_altri": float(fa or 0),
        }

    # 2) Top 5 settori per CF
    settori_rows = con.execute("""
        SELECT cf, settore, COUNT(*) AS n, SUM(costo_eff) AS costo
        FROM prg
        WHERE settore IS NOT NULL
        GROUP BY cf, settore
        QUALIFY ROW_NUMBER() OVER (PARTITION BY cf ORDER BY costo DESC) <= 5
    """).fetchall()
    settori_by_cf: dict[str, list] = {}
    for cf, set_, n, c in settori_rows:
        settori_by_cf.setdefault(cf, []).append({
            "settore": set_,
            "count": int(n),
            "costo": float(c or 0),
        })

    # 3) Componi output finale: aggrega tutti gli stati in totali per ogni CF
    output: dict[str, dict] = {}
    for cf, d in by_cf.items():
        per_stato = d["per_stato"]
        # Totale = somma di tutti gli stati
        totale = {
            "count": sum(s["count"] for s in per_stato.values()),
            "costo_lavori_eff": sum(s["costo_lavori_eff"] for s in per_stato.values()),
            "finanz_statali": sum(s["finanz_statali"] for s in per_stato.values()),
            "finanz_europei": sum(s["finanz_europei"] for s in per_stato.values()),
            "finanz_enti_terr": sum(s["finanz_enti_terr"] for s in per_stato.values()),
            "finanz_privati": sum(s["finanz_privati"] for s in per_stato.values()),
            "finanz_altri": sum(s["finanz_altri"] for s in per_stato.values()),
        }
        output[cf] = {
            "nome_titolare": d["nome"],
            "totale": totale,
            "per_stato": per_stato,
            "top_settori": settori_by_cf.get(cf, []),
        }

    out_path.write_text(
        json.dumps(
            {
                "_etl_version": "0.1.0",
                "_source": "BDAP MOP - Progetti Opere Pubbliche - Totale",
                "_dataset_uuid": DATASET_PROGETTI_TOTALE,
                "data": output,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    log.info("bdap_aggregato_built",
             path=str(out_path),
             bytes=out_path.stat().st_size,
             n_buyers=len(output))
    return out_path


def build_dettagli_shard(
    progetti_csv: Path,
    comuni_bundle_path: Path,
    output_dir: Path,
    only_2025: bool = True,
) -> Path:
    """Genera 1 file JSON shard per comune in `bdap/dettaglio/<istat>.json`.

    Ogni shard contiene la lista completa di progetti CUP del comune con
    tutti i campi finanziari + descrizione + date. Permette filtri lato UI.

    Args:
        progetti_csv: CSV BDAP Progetti già scaricato e UTF-8.
        comuni_bundle_path: Path al comuni-bundle.json (per join CF→ISTAT).
        output_dir: dove scrivere `bdap/dettaglio/<istat>.json`.
        only_2025: se True (default), filtra ai progetti CHIUSI con
                   `Data Inizio >= 2023-01-01` (ultimi 3 anni rilevanti)
                   OR stato = 'ATTIVO' (qualsiasi anno).
                   Il nome del flag e' storico (era stretto a 2025); ora
                   include anche i CHIUSI 2023-2024 per dare un orizzonte
                   piu' significativo all'utente nel tab Opere.

    Returns:
        Path della directory `bdap/dettaglio/` contenente gli shard.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = output_dir / "bdap" / "dettaglio"
    shard_dir.mkdir(parents=True, exist_ok=True)

    # Carica anagrafica comuni per join
    bundle = json.loads(comuni_bundle_path.read_text(encoding="utf-8"))
    cf_to_istat = {
        c["codice_fiscale"]: istat
        for istat, c in bundle["comuni"].items()
        if c.get("codice_fiscale")
    }
    log.info("bdap_shard_start",
             n_comuni_with_cf=len(cf_to_istat),
             only_2025=only_2025)

    con = duckdb.connect()
    where_year = ""
    if only_2025:
        # Soglia 2023-01-01: include ATTIVI di qualsiasi anno + CHIUSI 2023+.
        # Volume aggiunto vs filtro 2025: ~6700 CHIUSI (560 da 2025 + 1953 da
        # 2024 + 4218 da 2023) distribuiti su 7895 comuni.
        where_year = """ AND (
            try_cast("Data Inizio Validità CUP" AS DATE) >= DATE '2023-01-01'
            OR "Descrizione Stato CUP" = 'ATTIVO'
        )"""

    # Estrai dettaglio progetti con tutti i campi necessari per lo shard
    rows = con.execute(f"""
        SELECT
            "Codice Fiscale Titolare" AS cf,
            "Codice CUP" AS cup,
            "Descrizione CUP Integrale" AS descrizione,
            "Descrizione Stato CUP" AS stato,
            "Settore Interv Inv" AS settore,
            "Sottosettore Interv Inv" AS sottosettore,
            "Natura Intervento" AS natura,
            "Data Inizio Validità CUP" AS data_inizio,
            "Data Fine Validità CUP" AS data_fine,
            CAST(REPLACE("Costo Lavori Effettivo", ',', '.') AS DOUBLE) AS costo_eff,
            CAST(REPLACE("Costo Lavori Previsto", ',', '.') AS DOUBLE) AS costo_prev,
            CAST(REPLACE("Finanziamenti Statali", ',', '.') AS DOUBLE) AS fin_stat,
            CAST(REPLACE("Finanziamenti Europei", ',', '.') AS DOUBLE) AS fin_eu,
            CAST(REPLACE("Finanziamenti Enti Territorial", ',', '.') AS DOUBLE) AS fin_terr,
            CAST(REPLACE("Finanziamenti Privati", ',', '.') AS DOUBLE) AS fin_priv,
            CAST(REPLACE("Altre fonti di finanziamento", ',', '.') AS DOUBLE) AS fin_altri
        FROM read_csv(
            '{progetti_csv}',
            delim=';', header=true, ignore_errors=true, all_varchar=true
        )
        WHERE "Codice Fiscale Titolare" IN ({", ".join(repr(c) for c in cf_to_istat)})
        {where_year}
    """).fetchall()

    log.info("bdap_shard_query_done", n_rows=len(rows))

    # Raggruppa per CF (in memoria, dataset filtrato è OK)
    by_cf: dict[str, list] = {}
    for r in rows:
        cf = r[0]
        progetto = {
            "cup": r[1],
            "descrizione": r[2],
            "stato": r[3],
            "settore": r[4],
            "sottosettore": r[5],
            "natura": r[6],
            "data_inizio": r[7],
            "data_fine": r[8],
            "costo_eff": float(r[9] or 0),
            "costo_prev": float(r[10] or 0),
            "fin_statali": float(r[11] or 0),
            "fin_europei": float(r[12] or 0),
            "fin_enti_terr": float(r[13] or 0),
            "fin_privati": float(r[14] or 0),
            "fin_altri": float(r[15] or 0),
        }
        by_cf.setdefault(cf, []).append(progetto)

    # Scrivi 1 file per ogni CF mappato
    n_written = 0
    total_bytes = 0
    for cf, projects in by_cf.items():
        istat = cf_to_istat.get(cf)
        if not istat:
            continue
        shard_path = shard_dir / f"{istat}.json"
        # Ordina per costo discendente per UI default
        projects.sort(key=lambda p: -(p["costo_eff"] or p["costo_prev"] or 0))
        payload = {
            "_etl_version": "0.2.0",
            "_source": "BDAP MOP - Progetti Opere Pubbliche - Totale (dettaglio)",
            "_filter": "only_2025" if only_2025 else "all",
            "istat_code": istat,
            "codice_fiscale": cf,
            "n_progetti": len(projects),
            "progetti": projects,
        }
        shard_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n_written += 1
        total_bytes += shard_path.stat().st_size

    log.info("bdap_shard_done",
             n_shards=n_written,
             total_bytes=total_bytes,
             avg_bytes=total_bytes // max(1, n_written))
    return shard_dir


def push_to_r2(local_path: Path, key: str) -> dict:
    r2.upload_file(local_path, key, content_type="application/json")
    return {"key": key, "size": local_path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL BDAP MOP — opere pubbliche per comune")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/cruscotto-bdap-cache"))
    parser.add_argument("--skip-shard", action="store_true",
                        help="Skip generation of per-comune detail shards")
    parser.add_argument("--shard-all-years", action="store_true",
                        help="Include all historical projects (default: only 2025)")
    parser.add_argument("--force-shard-upload", action="store_true",
                        help="Re-upload all shards even if already present on R2 (use after schema/filter changes)")
    parser.add_argument("--comuni-bundle", type=Path, default=None,
                        help="Path to comuni-bundle.json (auto-detected if omitted)")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ]
    )

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="cruscotto-bdap-")) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    args.workdir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target, output_dir=str(output_dir), workdir=str(args.workdir))

    try:
        # 1. Pull (Progetti only per il MVP, Localizzazione la usiamo dopo per coverage geografica)
        progetti_csv = pull_csv(args.workdir, DATASET_PROGETTI_TOTALE, "progetti-totale")

        # 2. Aggregate
        aggr_path = aggregate_progetti(progetti_csv, output_dir)

        # 2b. Shard di dettaglio per comune (tab Opere filtrabile)
        shard_dir = None
        if not args.skip_shard:
            bundle_path = args.comuni_bundle
            if not bundle_path:
                import glob as _glob
                candidates = sorted(_glob.glob("/tmp/cruscotto-anag-*/output/comuni-bundle.json"))
                if candidates:
                    bundle_path = Path(candidates[-1])
                    log.info("bdap_bundle_autodetected", path=str(bundle_path))
                else:
                    log.warning("bdap_no_bundle_skip_shard")
                    bundle_path = None
            if bundle_path and bundle_path.exists():
                shard_dir = build_dettagli_shard(
                    progetti_csv,
                    bundle_path,
                    output_dir,
                    only_2025=not args.shard_all_years,
                )

        # 3. Push (optional)
        files = []
        if args.target == "r2":
            files.append(push_to_r2(aggr_path, "lookup/bdap-aggregato.json"))
            if shard_dir and shard_dir.exists():
                import hashlib
                from concurrent.futures import ThreadPoolExecutor, as_completed
                shard_files = sorted(shard_dir.glob("*.json"))

                # Strategia upload: confronta md5 locale vs ETag remoto.
                # R2 espone ETag = md5 hex per oggetti single-part (i nostri shard
                # sono ~16KB, sempre single-part). Skip upload solo se md5 identico.
                # --force-shard-upload bypassa il confronto (utile in caso di dubbi
                # o cambi di schema massivi).
                #
                # NB: questo sostituisce il vecchio skip-by-existence che era buggy:
                # un cambio di filtro o schema produceva file con stesso nome ma
                # contenuto diverso, e venivano skippati erroneamente.
                import boto3 as _b3
                _client = _b3.client(
                    "s3",
                    endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                )

                # Scarica metadata remota in parallelo: { name -> etag } (None = assente).
                # Usiamo list_objects_v2 perché 1 LIST è enormemente più veloce
                # di 7826 HEAD_OBJECT, e per single-part ETag = md5 affidabile.
                remote_etag: dict[str, str] = {}
                try:
                    _pag = _client.get_paginator("list_objects_v2")
                    for _page in _pag.paginate(Bucket="cruscotto-italia-data", Prefix="bdap/dettaglio/"):
                        for _o in _page.get("Contents", []):
                            name = _o["Key"].split("/")[-1]
                            # ETag arriva tra doppi apici, va strippato
                            etag = (_o.get("ETag") or "").strip('"').lower()
                            remote_etag[name] = etag
                    log.info("bdap_shard_remote_listed", count=len(remote_etag))
                except Exception as e:
                    log.warning("bdap_shard_list_failed", error=str(e))

                # Calcola md5 locali e confronta
                def _local_md5(p: Path) -> str:
                    h = hashlib.md5()
                    with p.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(65536), b""):
                            h.update(chunk)
                    return h.hexdigest()

                to_upload: list[Path] = []
                if args.force_shard_upload:
                    to_upload = list(shard_files)
                    log.info("bdap_force_shard_upload",
                             note="md5 check disabled, uploading all",
                             count=len(to_upload))
                else:
                    n_same = 0
                    for sf in shard_files:
                        rmd5 = remote_etag.get(sf.name)
                        if rmd5 is None:
                            to_upload.append(sf)  # non esiste su R2
                            continue
                        if _local_md5(sf) != rmd5:
                            to_upload.append(sf)  # contenuto diverso
                        else:
                            n_same += 1
                    log.info("bdap_shard_md5_compared",
                             total=len(shard_files),
                             unchanged=n_same,
                             to_upload=len(to_upload))

                log.info("bdap_pushing_shards",
                         total=len(shard_files),
                         to_upload=len(to_upload),
                         already_on_r2_unchanged=len(shard_files) - len(to_upload))

                def _upload_one(sf):
                    r2.upload_file(sf, f"bdap/dettaglio/{sf.name}",
                                   content_type="application/json")
                    return sf.name

                uploaded = 0
                with ThreadPoolExecutor(max_workers=24) as ex:
                    futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
                    for f in as_completed(futures):
                        try:
                            f.result()
                            uploaded += 1
                            if uploaded % 500 == 0:
                                log.info("bdap_shard_push_progress",
                                         uploaded=uploaded,
                                         total=len(to_upload))
                        except Exception as e:
                            log.error("bdap_shard_upload_failed",
                                      file=str(futures[f]),
                                      error=str(e))
                log.info("bdap_shard_push_done", uploaded=uploaded)
                files.append({"key": "bdap/dettaglio/*", "count": len(shard_files)})
            manifest.update_source("bdap", files, status="ok")
            log.info("manifest_updated")

        log.info("etl_done", aggregato_bytes=aggr_path.stat().st_size)
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        if args.target == "r2":
            try:
                manifest.update_source("bdap", [], status=f"failed: {e}")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
