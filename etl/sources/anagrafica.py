"""ETL anagrafica unificata.

Compone la spina dorsale del cruscotto:
    ISTAT codici amministrativi  ←─┐
                                   ├─→  anagrafica_unificata.parquet
    IPA enti                     ←─┘

Output:
    lookup/anagrafica_unificata.parquet
    lookup/istat_comuni.parquet (cache intermedia)
    lookup/ipa_enti.parquet (cache intermedia)

Schema anagrafica_unificata:
    codice_istat   STRING (6 char, 0-padded)
    codice_fiscale STRING (11 char, può essere NULL se IPA non lo riporta)
    codice_ipa     STRING (~20 char, può essere NULL)
    denominazione  STRING (nome del comune)
    provincia      STRING (sigla 2 char)
    regione        STRING
    popolazione    INT (NULL se non disponibile dal CSV ISTAT)
    nome_categoria STRING (es. "Comuni e loro Consorzi e Associazioni")

Usage (locale):
    python -m etl.sources.anagrafica --target=local --outdir=/tmp/cruscotto

Usage (R2):
    python -m etl.sources.anagrafica --target=r2
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb
import requests
import structlog

from etl.lib import duck, manifest, r2

log = structlog.get_logger()

# ----------------------------------------------------------------------------
# URLs delle fonti
# ----------------------------------------------------------------------------
ISTAT_COMUNI_CSV = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"

# IPA: dataset "enti" dal portale CKAN ufficiale
IPA_CKAN_BASE = "https://indicepa.gov.it/ipa-dati/api/3/action"
IPA_ENTI_DATASET = "enti"  # CKAN package name

USER_AGENT = "cruscotto-italia-etl/0.1 (+https://github.com/piersoft/cruscotto-italia)"


# ----------------------------------------------------------------------------
# Pull ISTAT codici amministrativi
# ----------------------------------------------------------------------------
def pull_istat_comuni(workdir: Path) -> Path:
    """Scarica il CSV ISTAT con i codici amministrativi dei comuni.

    Note: ISTAT pubblica il CSV in encoding latin-1 con separatore ';'.
    Le colonne possono variare di anno in anno; usiamo il pattern stabile.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "istat-comuni.csv"
    log.info("pulling_istat_comuni", url=ISTAT_COMUNI_CSV)
    headers = {"User-Agent": USER_AGENT, "Accept-Charset": "utf-8;q=0.7,*;q=0.3"}
    r = requests.get(ISTAT_COMUNI_CSV, headers=headers, timeout=120)
    r.raise_for_status()
    # ISTAT pubblica in latin-1; ricodifichiamo in UTF-8 perché DuckDB legge UTF-8
    raw = r.content
    try:
        text = raw.decode("latin-1")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    out.write_text(text, encoding="utf-8")
    log.info("istat_comuni_saved", path=str(out), bytes=out.stat().st_size, encoded="utf-8")
    return out


# ----------------------------------------------------------------------------
# Pull IPA enti
# ----------------------------------------------------------------------------
def pull_ipa_enti(workdir: Path) -> Path:
    """Scarica il dataset IPA 'enti' (CSV) via CKAN package_show.

    Returns: path al CSV scaricato.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    log.info("pulling_ipa_enti", dataset=IPA_ENTI_DATASET)

    # 1. CKAN: get dataset metadata
    pkg_url = f"{IPA_CKAN_BASE}/package_show?id={IPA_ENTI_DATASET}"
    r = requests.get(pkg_url, headers=headers, timeout=60)
    r.raise_for_status()
    pkg = r.json()
    if not pkg.get("success"):
        raise RuntimeError(f"CKAN package_show failed: {pkg}")

    # 2. Find the resource with the actual dataset (not metadata).
    # Strategy: pick the largest resource, exclude those with "metadata" in the name.
    resources = pkg["result"].get("resources", [])
    log.info("ipa_resources_available",
             count=len(resources),
             items=[(r.get("name"), r.get("format"), r.get("size")) for r in resources])

    data_resources = [
        r for r in resources
        if r.get("id") and "metadata" not in (r.get("name") or "").lower()
    ]
    if not data_resources:
        raise RuntimeError("No data resource (only metadata) in IPA enti dataset.")

    # The biggest resource is the actual data
    chosen = max(data_resources, key=lambda r: int(r.get("size") or 0))
    resource_id = chosen["id"]
    log.info("ipa_resource_chosen",
             resource_id=resource_id,
             name=chosen.get("name"),
             format=chosen.get("format"),
             size=chosen.get("size"))

    # 3. Use CKAN datastore dump endpoint — always returns clean CSV regardless
    # of the underlying resource format (CSV/XLSX/JSON).
    datastore_url = f"https://indicepa.gov.it/ipa-dati/datastore/dump/{resource_id}?bom=True"
    out = workdir / "ipa-enti.csv"
    log.info("downloading_ipa_datastore", url=datastore_url)
    r = requests.get(datastore_url, headers=headers, timeout=300, stream=True)
    r.raise_for_status()
    with out.open("wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    log.info("ipa_enti_saved", path=str(out), bytes=out.stat().st_size)
    return out


# ----------------------------------------------------------------------------
# Trasformazione: compose anagrafica unificata
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# ISTAT POSAS — popolazione residente per comune (1° gennaio)
# Source: https://demo.istat.it/data/posas/POSAS_{YEAR}_it_Comuni.zip
# License: CC-BY 3.0 IT
# ----------------------------------------------------------------------------
def pull_popolazione_istat(workdir: Path, year: int = 2026) -> Path:
    """Download POSAS bulk for all comuni and extract the CSV.

    Returns path to the extracted CSV file.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    url = f"https://demo.istat.it/data/posas/POSAS_{year}_it_Comuni.zip"
    log.info("pulling_posas_zip", url=url, year=year)
    zip_path = workdir / f"POSAS_{year}_it_Comuni.zip"

    response = requests.get(url, timeout=120, stream=True)
    response.raise_for_status()
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            f.write(chunk)
    log.info("posas_zip_saved", bytes=zip_path.stat().st_size, path=str(zip_path))

    extract_dir = workdir / "posas"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    csv_files = list(extract_dir.glob("*.csv"))
    if not csv_files:
        raise RuntimeError(f"No CSV in POSAS zip: {list(extract_dir.iterdir())}")
    csv_path = csv_files[0]
    log.info("posas_csv_extracted", path=str(csv_path), bytes=csv_path.stat().st_size)
    return csv_path


def load_popolazione_map(csv_path: Path) -> dict[str, int]:
    """Parse POSAS CSV and return {istat_code: popolazione_totale}.

    POSAS publishes one row per (comune, eta, sesso). Età=999 is the
    pre-aggregated total row, which we use directly to avoid double-counting.
    """
    log.info("loading_popolazione_csv", path=str(csv_path))
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT
            "Codice comune" AS istat_code,
            "Totale" AS popolazione
        FROM read_csv(
            '{csv_path}',
            delim=';',
            header=true,
            skip=1,
            encoding='utf-8',
            ignore_errors=true
        )
        WHERE "Età" = 999
          AND "Codice comune" IS NOT NULL
    """).fetchall()
    pop_map = {r[0]: int(r[1]) for r in rows if r[1] is not None}
    if pop_map:
        log.info("popolazione_map_built",
                 count=len(pop_map),
                 total_italia=sum(pop_map.values()),
                 max_comune=max(pop_map.values()),
                 min_comune=min(pop_map.values()))
    else:
        log.warning("popolazione_map_empty")
    return pop_map



def build_anagrafica(istat_csv: Path, ipa_csv: Path, output_dir: Path, pop_map: dict[str, int] | None = None) -> dict:
    """Componi anagrafica unificata via DuckDB.

    Approccio:
        1. Carica ISTAT (encoding latin-1, separatore ';')
        2. Carica IPA (encoding utf-8, separatore ',')
        3. Normalizza il codice ISTAT a 6 cifre 0-padded
        4. Join LEFT su codice_istat (un comune può non avere riga IPA)
        5. Scrivi output Parquet

    Returns: dict con statistiche e paths dei file generati.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []

    with duck.duck_session() as con:
        # --- ISTAT comuni ---
        # Le colonne ISTAT cambiano nei vari anni. Usiamo discovery dinamica.
        log.info("loading_istat_csv", path=str(istat_csv))
        con.execute(f"""
            CREATE TABLE istat_raw AS
            SELECT * FROM read_csv(
                '{istat_csv}',
                delim=';',
                header=true,
                ignore_errors=true,
                null_padding=true,
                strict_mode=false,
                quote='"',
                escape='"',
                all_varchar=true
            )
        """)
        cols_istat = [c[0] for c in con.execute("DESCRIBE istat_raw").fetchall()]
        log.info("istat_columns", count=len(cols_istat), preview=cols_istat[:8])

        # Detect the codice istat column heuristically
        codice_istat_col = next(
            (c for c in cols_istat if "codice comune" in c.lower() or "codice istat" in c.lower() or "comune (numerico)" in c.lower()),
            None,
        )
        denom_col = next(
            (c for c in cols_istat if "denominazione" in c.lower() and "italiana" in c.lower()),
            None,
        ) or next(
            (c for c in cols_istat if "denominazione in italiano" in c.lower()),
            None,
        ) or next(
            (c for c in cols_istat if c.lower().startswith("denominazione")),
            None,
        )
        sigla_prov_col = next(
            (c for c in cols_istat if "sigla automobilistica" in c.lower() or "sigla provincia" in c.lower()),
            None,
        )
        regione_col = next(
            (c for c in cols_istat if c.lower() == "denominazione regione" or ("regione" in c.lower() and "denominazione" in c.lower())),
            None,
        )
        codice_catastale_col = next(
            (c for c in cols_istat if "codice catastale" in c.lower() or "belfiore" in c.lower()),
            None,
        )

        if not codice_istat_col or not denom_col:
            raise RuntimeError(
                f"Cannot detect ISTAT key columns. Found: {cols_istat}. "
                f"codice_istat_col={codice_istat_col}, denom_col={denom_col}"
            )

        log.info("istat_columns_detected",
                 codice=codice_istat_col, denom=denom_col,
                 provincia=sigla_prov_col, regione=regione_col, catastale=codice_catastale_col)

        # Normalize ISTAT
        con.execute(f"""
            CREATE TABLE istat_comuni AS
            SELECT
                lpad(CAST("{codice_istat_col}" AS VARCHAR), 6, '0') AS codice_istat,
                "{denom_col}" AS denominazione,
                {f'"{sigla_prov_col}"' if sigla_prov_col else "NULL"} AS provincia,
                {f'"{regione_col}"' if regione_col else "NULL"} AS regione,
                {f'"{codice_catastale_col}"' if codice_catastale_col else "NULL"} AS codice_catastale
            FROM istat_raw
            WHERE "{codice_istat_col}" IS NOT NULL
        """)
        istat_count = con.execute("SELECT COUNT(*) FROM istat_comuni").fetchone()[0]
        log.info("istat_normalized", row_count=istat_count)

        # Cache ISTAT in Parquet
        istat_pq = output_dir / "istat_comuni.parquet"
        con.execute(f"COPY istat_comuni TO '{istat_pq}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        files.append({"key": "lookup/istat_comuni.parquet", "size": istat_pq.stat().st_size, "row_count": istat_count})

        # --- IPA enti ---
        ipa_ext = str(ipa_csv).rsplit(".", 1)[-1].lower()
        log.info("loading_ipa_data", path=str(ipa_csv), format=ipa_ext)
        if ipa_ext == "json":
            con.execute(f"""
                CREATE TABLE ipa_raw AS
                SELECT * FROM read_json_auto('{ipa_csv}', maximum_object_size=104857600)
            """)
        elif ipa_ext in ("csv", "txt"):
            con.execute(f"""
                CREATE TABLE ipa_raw AS
                SELECT * FROM read_csv_auto(
                    '{ipa_csv}',
                    header=true,
                    ignore_errors=true,
                    null_padding=true,
                    strict_mode=false
                )
            """)
        elif ipa_ext == "xlsx":
            raise RuntimeError("XLSX not yet supported. Install duckdb spatial+excel or use JSON resource.")
        else:
            raise RuntimeError(f"Unsupported IPA file extension: {ipa_ext}")
        cols_ipa = [c[0] for c in con.execute("DESCRIBE ipa_raw").fetchall()]
        log.info("ipa_columns", count=len(cols_ipa), preview=cols_ipa[:10])

        # Filter only Comuni
        # IPA "Nome_Categoria" contains values like "Comuni e loro Consorzi e Associazioni"
        nome_cat_col = "Nome_Categoria" if "Nome_Categoria" in cols_ipa else next(
            (c for c in cols_ipa if c.lower() in ("categoria", "tipologia")), None
        )
        codice_categoria_col = "Codice_Categoria" if "Codice_Categoria" in cols_ipa else None
        codice_ipa_col = "Codice_IPA" if "Codice_IPA" in cols_ipa else next(
            (c for c in cols_ipa if "codice_ipa" in c.lower().replace("_", "_")), None
        )
        cf_ente_col = "Codice_fiscale_ente" if "Codice_fiscale_ente" in cols_ipa else next(
            (c for c in cols_ipa if "codice_fiscale" in c.lower()), None
        )
        denom_ente_col = "Denominazione_ente" if "Denominazione_ente" in cols_ipa else next(
            (c for c in cols_ipa if "denominazione" in c.lower()), None
        )
        codice_comune_istat_col = "Codice_comune_ISTAT" if "Codice_comune_ISTAT" in cols_ipa else next(
            (c for c in cols_ipa if "codice_comune_istat" in c.lower() or "comune_istat" in c.lower()), None
        )

        log.info("ipa_columns_detected",
                 codice_ipa=codice_ipa_col, cf=cf_ente_col, denom=denom_ente_col,
                 codice_comune=codice_comune_istat_col, categoria=nome_cat_col,
                 codice_categoria=codice_categoria_col)

        if not codice_ipa_col or not cf_ente_col:
            raise RuntimeError(f"Cannot detect IPA key columns. Found: {cols_ipa}")

        # Build clean IPA enti table — keep only one row per ente (some have multiple AOO)
        # We deduplicate on Codice_IPA keeping first occurrence
        if codice_categoria_col:
            # AgID code 'L6' = Comuni e loro Consorzi e Associazioni (stable code)
            comune_filter = f'WHERE "{codice_categoria_col}" = \'L6\''
        elif nome_cat_col:
            comune_filter = f'WHERE LOWER("{nome_cat_col}") LIKE \'%comun%\''
        else:
            comune_filter = ''
        codice_comune_select = (
            f'lpad(CAST("{codice_comune_istat_col}" AS VARCHAR), 6, \'0\')'
            if codice_comune_istat_col else "NULL"
        )

        con.execute(f"""
            CREATE TABLE ipa_enti AS
            SELECT DISTINCT ON (codice_ipa)
                "{codice_ipa_col}" AS codice_ipa,
                "{cf_ente_col}" AS codice_fiscale_ente,
                "{denom_ente_col}" AS denominazione_ipa,
                {codice_comune_select} AS codice_comune_istat,
                {f'"{nome_cat_col}"' if nome_cat_col else "NULL"} AS nome_categoria
            FROM ipa_raw
            {comune_filter}
        """)
        ipa_count = con.execute("SELECT COUNT(*) FROM ipa_enti").fetchone()[0]
        log.info("ipa_enti_filtered", row_count=ipa_count)

        ipa_pq = output_dir / "ipa_enti.parquet"
        con.execute(f"COPY ipa_enti TO '{ipa_pq}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        files.append({"key": "lookup/ipa_enti.parquet", "size": ipa_pq.stat().st_size, "row_count": ipa_count})

        # --- JOIN: anagrafica unificata ---
        log.info("building_anagrafica_unificata")
        con.execute("""
            CREATE TABLE anagrafica_unificata AS
            SELECT
                i.codice_istat,
                ipa.codice_fiscale_ente AS codice_fiscale,
                ipa.codice_ipa,
                i.denominazione,
                i.provincia,
                i.regione,
                i.codice_catastale,
                ipa.nome_categoria,
                CAST(NULL AS INTEGER) AS popolazione  -- will be enriched in v0.2 from ISTAT API
            FROM istat_comuni i
            LEFT JOIN ipa_enti ipa
                ON i.codice_istat = ipa.codice_comune_istat
        """)
        unif_count = con.execute("SELECT COUNT(*) FROM anagrafica_unificata").fetchone()[0]
        joined_count = con.execute(
            "SELECT COUNT(*) FROM anagrafica_unificata WHERE codice_ipa IS NOT NULL"
        ).fetchone()[0]
        log.info("anagrafica_unificata_built",
                 total=unif_count, with_ipa_match=joined_count,
                 join_coverage_pct=round(joined_count / unif_count * 100, 1) if unif_count else 0)

        unif_pq = output_dir / "anagrafica_unificata.parquet"
        con.execute(f"COPY anagrafica_unificata TO '{unif_pq}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        files.append({
            "key": "lookup/anagrafica_unificata.parquet",
            "size": unif_pq.stat().st_size,
            "row_count": unif_count,
        })

        # Top 5 comuni for sanity check
        sample = con.execute("""
            SELECT codice_istat, denominazione, provincia, regione, codice_ipa
            FROM anagrafica_unificata
            ORDER BY denominazione
            LIMIT 5
        """).fetchall()
        log.info("anagrafica_sample", rows=[dict(zip(['istat','nome','prov','reg','ipa'], r, strict=False)) for r in sample])

        # ----------------------------------------------------------------
        # Build JSON lookups for the worker (no Parquet parsing in JS).
        # We produce ONE unique row per codice_istat: prefer the IPA entity
        # whose codice_ipa starts with 'c_' (the canonical Comune entity).
        # ----------------------------------------------------------------
        log.info("building_json_lookups")
        con.execute("""
            CREATE TABLE anagrafica_dedup AS
            SELECT * FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY codice_istat
                        ORDER BY
                            CASE WHEN codice_ipa LIKE 'c\\_%' ESCAPE '\\' THEN 0
                                 WHEN codice_ipa IS NOT NULL THEN 1
                                 ELSE 2 END,
                            codice_ipa
                    ) AS rn
                FROM anagrafica_unificata
            )
            WHERE rn = 1
        """)
        dedup_count = con.execute("SELECT COUNT(*) FROM anagrafica_dedup").fetchone()[0]
        log.info("anagrafica_dedup_built", row_count=dedup_count)

        # 1. Compact index (one entry per comune): used by search_comune autocomplete
        index_rows = con.execute("""
            SELECT codice_istat, denominazione, provincia, regione, codice_ipa, codice_fiscale
            FROM anagrafica_dedup
            WHERE codice_istat IS NOT NULL
            ORDER BY denominazione
        """).fetchall()
        index_path = output_dir / "comuni-index.json"
        index_path.write_text(json.dumps([
            {
                "i": r[0],          # istat (compact key)
                "n": r[1],          # nome
                "p": r[2],          # provincia
                "r": r[3],          # regione
                "ipa": r[4],
                "cf": r[5],
            } for r in index_rows
        ], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        files.append({
            "key": "lookup/comuni-index.json",
            "size": index_path.stat().st_size,
            "row_count": len(index_rows),
        })
        log.info("comuni_index_built", path=str(index_path), rows=len(index_rows), bytes=index_path.stat().st_size)

        # 2. SINGLE bundle file with all comuni details — much faster R2 upload
        # than 7896 small files. Worker downloads once and serves from edge cache.
        # When we add ANAC/BDAP KPIs in v0.2, we may shard this by region.
        detail_rows = con.execute("""
            SELECT codice_istat, denominazione, provincia, regione,
                   codice_ipa, codice_fiscale, nome_categoria, codice_catastale
            FROM anagrafica_dedup
            WHERE codice_istat IS NOT NULL
        """).fetchall()
        bundle = {}
        for r in detail_rows:
            istat = r[0]
            bundle[istat] = {
                "istat_code": istat,
                "denominazione": r[1],
                "provincia": r[2],
                "regione": r[3],
                "codice_ipa": r[4],
                "codice_fiscale": r[5],
                "nome_categoria": r[6],
                "codice_catastale": r[7],
                "kpi": {
                    "contratti": None,
                    "opere": None,
                    "spese_siope": None,
                    "coesione": None,
                    "popolazione": pop_map.get(istat),
                },
            }
        bundle_path = output_dir / "comuni-bundle.json"
        bundle_path.write_text(
            json.dumps({"_etl_version": "0.1.0", "comuni": bundle},
                       ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8"
        )
        files.append({
            "key": "lookup/comuni-bundle.json",
            "size": bundle_path.stat().st_size,
            "row_count": len(bundle),
        })
        log.info("comuni_bundle_built", count=len(bundle), path=str(bundle_path), bytes=bundle_path.stat().st_size)

    return {
        "files": files,
        "stats": {
            "istat_count": istat_count,
            "ipa_count": ipa_count,
            "unified_count": unif_count,
            "join_coverage_pct": round(joined_count / unif_count * 100, 1) if unif_count else 0,
        },
    }


# ----------------------------------------------------------------------------
# Push to R2
# ----------------------------------------------------------------------------
def push_to_r2(local_dir: Path, files: list[dict]) -> None:
    """Upload all generated files to R2 (Parquet, JSON index, JSON details)."""
    for f in files:
        key = f["key"]
        # Special: glob-style key 'lookup/comuni/*.json' means upload an entire dir
        if key.endswith("/*.json"):
            prefix = key.rsplit("/*.json", 1)[0]
            local_subdir = local_dir / Path(prefix).name
            json_files = sorted(local_subdir.glob("*.json"))
            log.info("uploading_directory", count=len(json_files), prefix=prefix)
            for jf in json_files:
                r2.upload_file(jf, f"{prefix}/{jf.name}", content_type="application/json")
            continue
        # Standard single-file upload
        local = local_dir / Path(key).name
        if key.endswith(".parquet"):
            ct = "application/vnd.apache.parquet"
        elif key.endswith(".json"):
            ct = "application/json"
        else:
            ct = "application/octet-stream"
        r2.upload_file(local, key, content_type=ct)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="ETL anagrafica unificata (ISTAT + IPA)")
    parser.add_argument("--target", choices=["local", "r2"], default="local",
                        help="Where to write output (local=disk only, r2=upload to Cloudflare R2)")
    parser.add_argument("--outdir", type=Path,
                        default=Path("/var/www/cruscotto-italia/data"),
                        help="Local output root (default: /var/www/cruscotto-italia/data)")
    parser.add_argument("--keep-workdir", action="store_true",
                        help="Don't delete temp workdir after run (debugging)")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ]
    )

    output_dir = args.outdir
    workdir = output_dir / "_work"

    log.info("etl_start", target=args.target, output_dir=str(output_dir))

    try:
        # 1. Pull
        istat_csv = pull_istat_comuni(workdir)
        ipa_csv = pull_ipa_enti(workdir)
        posas_csv = pull_popolazione_istat(workdir)
        pop_map = load_popolazione_map(posas_csv)

        # 2. Build
        result = build_anagrafica(istat_csv, ipa_csv, output_dir, pop_map=pop_map)

        # 3. Push (optional)
        if args.target == "r2":
            push_to_r2(output_dir, result["files"])
            manifest.update_source("anagrafica", result["files"], status="ok")
            log.info("manifest_updated")

        log.info("etl_done", **result["stats"])
        return 0

    except Exception as e:
        log.exception("etl_failed", error=str(e))
        if args.target == "r2":
            try:
                manifest.update_source("anagrafica", [], status=f"failed: {e}")
            except Exception:
                log.exception("manifest_update_also_failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
