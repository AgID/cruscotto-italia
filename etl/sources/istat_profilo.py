"""ETL Profilo del comune - Censimento permanente ISTAT.

Fonte: ISTAT esploradati.istat.it (SDMX REST API)
Dataflow utilizzati:
- DF_DCSS_ISTR_LAV_PEN_2_TV_1: istruzione (popolazione per grado di istruzione)
- DF_DCSS_ISTR_LAV_PEN_2_TV_3: lavoro (popolazione per condizione professionale)
- DF_DCSS_ISTR_LAV_PEN_2_TV_5: pendolarismo (dati 2018-2019, ultimi disponibili)
- DF_DCSS_FAM_POP_TV_1:        famiglie e popolazione in famiglia
- DF_DCSS_POP_DEMCITMIG_TV_2:  popolazione per cittadinanza

Output:
- profilo/<istat>.json per ogni comune (~500 byte ciascuno)
  Contiene: istruzione + lavoro + famiglie + mobilita + cittadinanza + metadata

Granularita: comunale (codice ISTAT 6 cifre, REF_AREA in CL_ITTER107)
Anno di riferimento: ultimo disponibile per ciascun dataflow
  - 2024 per istruzione, lavoro, famiglie, cittadinanza
  - 2019 per pendolarismo (ultimo aggiornamento ISTAT)

Cache: i CSV bulk ISTAT sono salvati in cache_dir per ripartibilita.

Usage:
  python -m etl.sources.istat_profilo --target=local
  python -m etl.sources.istat_profilo --target=r2
  python -m etl.sources.istat_profilo --target=local --no-cache  # forza re-download
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# === Configurazione SDMX ===
SDMX_BASE = "https://esploradati.istat.it/SDMXWS/rest/data"
SDMX_AGENCY = "IT1"
SDMX_VERSION = "1.0"
UA = "cruscotto-italia/1.0 (+https://cruscotto-italia.piersoftckan.biz)"

# Per ogni dataflow: (id, key_dimensions, anno_min, anno_max, descrizione)
# La key SDMX e' una sequenza di valori dimensione separati da '.';
# valore vuoto = wildcard (tutti i codici di quella dimensione).
# Lasciamo REF_AREA wildcard per ottenere TUTTI i territori (poi filtriamo i comuni).
DATAFLOWS = [
    {
        "name": "istruzione",
        "id": "DF_DCSS_ISTR_LAV_PEN_2_TV_1",
        # 10 dim: FREQ.REF_AREA.INDICATOR.GENDER.AGE_NOCLASS.CITIZENSHIP.EDU_ATTAIN.CUR_ACT_STAT.LOC_DEST.REAS_COMMUTING
        "key": "A.........",  # FREQ=A + 9 wildcards = 10 dim
        "year_start": 2024,
        "year_end": 2024,
    },
    {
        "name": "lavoro",
        "id": "DF_DCSS_ISTR_LAV_PEN_2_TV_3",
        "key": "A.........",
        "year_start": 2024,
        "year_end": 2024,
    },
    {
        "name": "pendolari",
        "id": "DF_DCSS_ISTR_LAV_PEN_2_TV_5",
        "key": "A.........",
        "year_start": 2019,
        "year_end": 2019,  # ultimo anno disponibile
    },
    {
        "name": "famiglie",
        "id": "DF_DCSS_FAM_POP_TV_1",
        # 3 dim: FREQ.REF_AREA.INDICATOR
        "key": "A..",
        "year_start": 2024,
        "year_end": 2024,
    },
    {
        "name": "cittadinanza",
        "id": "DF_DCSS_POP_DEMCITMIG_TV_2",
        # 9 dim: FREQ.REF_AREA.INDICATOR.GENDER.AGE_CLASS.MARITAL_STATUS.CITIZENSHIP.AREA_CONTRY_CITIZEN.USUAL_RESID_1Y
        "key": "A........",
        "year_start": 2024,
        "year_end": 2024,
    },
]


def sdmx_url(df_id: str, key: str, year_start: int, year_end: int) -> str:
    """Costruisce l'URL SDMX per il pull bulk."""
    return (
        f"{SDMX_BASE}/{SDMX_AGENCY},{df_id},{SDMX_VERSION}/{key}"
        f"?startPeriod={year_start}&endPeriod={year_end}"
    )


def download_dataflow(df: dict, cache_dir: Path, force: bool = False) -> Path:
    """Scarica un dataflow ISTAT in CSV nella cache. Riusa se gia presente."""
    out = cache_dir / f"{df['name']}.csv"
    if out.exists() and out.stat().st_size > 1000 and not force:
        log.info("istat_cache_hit", source=df["name"], path=str(out),
                 size=out.stat().st_size)
        return out

    url = sdmx_url(df["id"], df["key"], df["year_start"], df["year_end"])
    log.info("istat_downloading", source=df["name"], url=url,
             year_start=df["year_start"], year_end=df["year_end"])

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.sdmx.data+csv;version=1.0.0",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        log.error("istat_http_error", source=df["name"], status=e.code,
                  reason=e.reason)
        raise
    except urllib.error.URLError as e:
        log.error("istat_url_error", source=df["name"], reason=str(e.reason))
        raise

    out.write_bytes(data)
    log.info("istat_downloaded", source=df["name"], bytes=len(data))
    return out


def build_profilo_shards(cache_dir: Path, output_dir: Path) -> Path:
    """Aggrega i 5 CSV in 1 JSON per comune (profilo/<istat>.json)."""
    shard_dir = output_dir / "profilo"
    shard_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()

    # === Carica i 5 CSV in altrettante tabelle ===
    for df in DATAFLOWS:
        csv_path = cache_dir / f"{df['name']}.csv"
        if not csv_path.exists():
            log.warning("istat_csv_missing", source=df["name"],
                        path=str(csv_path))
            continue
        # SDMX CSV ha header, separatore virgola, encoding utf-8
        con.execute(f"""
            CREATE TABLE {df['name']} AS
            SELECT * FROM read_csv(
                '{csv_path}',
                delim=',',
                header=true,
                quote='"',
                ignore_errors=true,
                all_varchar=true
            )
        """)
        n = con.execute(f"SELECT COUNT(*) FROM {df['name']}").fetchone()[0]
        log.info("istat_loaded", source=df["name"], rows=n)

    # === ISTRUZIONE ===
    # I codici EDU_ATTAIN nel censimento permanente comunale sono:
    #   ALL    = totale
    #   NED    = nessun titolo di studio
    #   PSE    = licenza scuola elementare
    #   LSE    = licenza scuola media inferiore
    #   USE_IF = diploma maturita 4-5 anni o qualifica professionale 2-3
    #   BL     = laurea triennale o ITS (terziario primo livello)
    #   ML_RDD = laurea magistrale + dottorato (terziario secondo livello)
    # Verifica empirica Lecce 2024: NED+PSE+LSE+USE_IF+BL+ML_RDD = ALL (delta 0)
    # KPI:
    #   terziario_pct       = (BL + ML_RDD) / ALL
    #   diploma_oltre_pct   = (USE_IF + BL + ML_RDD) / ALL
    #   max_media_pct       = (NED + PSE + LSE) / ALL
    # base: 25-49 + 50-64 sommati per fascia "25-64"
    # filtri fissi: GENDER=T, CITIZENSHIP=TOTAL, CUR_ACT_STAT=99, LOC=ALL, REA=ALL
    log.info("istat_aggregating", section="istruzione")
    istruzione = con.execute("""
        WITH base AS (
            SELECT
                REF_AREA AS istat,
                EDU_ATTAIN AS edu,
                CAST(OBS_VALUE AS DOUBLE) AS val
            FROM istruzione
            WHERE GENDER = 'T'
              AND CITIZENSHIP = 'TOTAL'
              AND CUR_ACT_STAT = '99'
              AND LOC_DEST = 'ALL'
              AND REAS_COMMUTING = 'ALL'
              AND AGE_NOCLASS IN ('Y25-49', 'Y50-64')
              AND OBS_VALUE IS NOT NULL
        ),
        agg AS (
            SELECT istat, edu, SUM(val) AS val
            FROM base
            GROUP BY istat, edu
        )
        SELECT
            istat,
            MAX(CASE WHEN edu = 'ALL'    THEN val END) AS pop_25_64,
            MAX(CASE WHEN edu = 'NED'    THEN val END) AS nessun_titolo,
            MAX(CASE WHEN edu = 'PSE'    THEN val END) AS elementare,
            MAX(CASE WHEN edu = 'LSE'    THEN val END) AS media,
            MAX(CASE WHEN edu = 'USE_IF' THEN val END) AS diploma,
            MAX(CASE WHEN edu = 'BL'     THEN val END) AS lau_triennale,
            MAX(CASE WHEN edu = 'ML_RDD' THEN val END) AS lau_magistrale_dott
        FROM agg
        GROUP BY istat
    """).fetchall()
    istruzione_cols = [d[0] for d in con.description]
    istruzione_dict = {row[0]: dict(zip(istruzione_cols, row, strict=False))
                       for row in istruzione}
    log.info("istat_istruzione_done", comuni=len(istruzione_dict))

    # === LAVORO ===
    # CUR_ACT_STAT: 1=occupato, 12=in cerca, 22=forze lavoro, 99=totale
    # AGE: Y25-49 + Y50-64 = 25-64 (analogo a istruzione)
    log.info("istat_aggregating", section="lavoro")
    lavoro = con.execute("""
        WITH base AS (
            SELECT
                REF_AREA AS istat,
                CUR_ACT_STAT AS act,
                AGE_NOCLASS AS age,
                CAST(OBS_VALUE AS DOUBLE) AS val
            FROM lavoro
            WHERE GENDER = 'T'
              AND CITIZENSHIP = 'TOTAL'
              AND EDU_ATTAIN = 'ALL'
              AND LOC_DEST = 'ALL'
              AND REAS_COMMUTING = 'ALL'
              AND AGE_NOCLASS IN ('Y25-49', 'Y50-64')
              AND OBS_VALUE IS NOT NULL
        ),
        agg AS (
            SELECT istat, act, SUM(val) AS val
            FROM base
            GROUP BY istat, act
        )
        SELECT
            istat,
            MAX(CASE WHEN act = '99' THEN val END) AS pop_25_64,
            MAX(CASE WHEN act = '1'  THEN val END) AS occupati,
            MAX(CASE WHEN act = '12' THEN val END) AS in_cerca,
            MAX(CASE WHEN act = '22' THEN val END) AS forze_lavoro,
            MAX(CASE WHEN act = '23' THEN val END) AS non_forze
        FROM agg
        GROUP BY istat
    """).fetchall()
    lavoro_cols = [d[0] for d in con.description]
    lavoro_dict = {row[0]: dict(zip(lavoro_cols, row, strict=False)) for row in lavoro}
    log.info("istat_lavoro_done", comuni=len(lavoro_dict))

    # === FAMIGLIE ===
    # 4 INDICATOR: NPHH_AV (n. famiglie), POPHH_AV (pop in famiglia),
    # RESPOP_AV (residenti), INST_RESPOP_AV (in convivenza)
    log.info("istat_aggregating", section="famiglie")
    famiglie = con.execute("""
        SELECT
            REF_AREA AS istat,
            MAX(CASE WHEN INDICATOR = 'NPHH_AV'        THEN CAST(OBS_VALUE AS DOUBLE) END) AS n_famiglie,
            MAX(CASE WHEN INDICATOR = 'POPHH_AV'       THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_in_famiglia,
            MAX(CASE WHEN INDICATOR = 'RESPOP_AV'      THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_residente,
            MAX(CASE WHEN INDICATOR = 'INST_RESPOP_AV' THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_in_convivenza
        FROM famiglie
        WHERE OBS_VALUE IS NOT NULL
        GROUP BY REF_AREA
    """).fetchall()
    famiglie_cols = [d[0] for d in con.description]
    famiglie_dict = {row[0]: dict(zip(famiglie_cols, row, strict=False)) for row in famiglie}
    log.info("istat_famiglie_done", comuni=len(famiglie_dict))

    # === PENDOLARI ===
    # INDICATOR=RP_COM_DAY (popolazione che si sposta giornalmente)
    # GENDER=T, AGE=TOTAL, CITIZENSHIP=TOTAL, EDU=ALL, ACT=99
    # LOC_DEST: ALL=tutti, OMPUR=FUORI comune (Outside MPUR), SMPUR=STESSO comune (Same MPUR)
    # REAS_COMMUTING: ALL=tutti, STD=studio, WK=lavoro
    # Verifica empirica Lecce 2019: OMPUR + SMPUR = ALL (delta 0)
    log.info("istat_aggregating", section="pendolari")
    pendolari = con.execute("""
        SELECT
            REF_AREA AS istat,
            MAX(CASE WHEN LOC_DEST='ALL'   AND REAS_COMMUTING='ALL' THEN CAST(OBS_VALUE AS DOUBLE) END) AS totale,
            MAX(CASE WHEN LOC_DEST='OMPUR' AND REAS_COMMUTING='ALL' THEN CAST(OBS_VALUE AS DOUBLE) END) AS fuori_comune,
            MAX(CASE WHEN LOC_DEST='SMPUR' AND REAS_COMMUTING='ALL' THEN CAST(OBS_VALUE AS DOUBLE) END) AS dentro_comune,
            MAX(CASE WHEN LOC_DEST='ALL'   AND REAS_COMMUTING='WK'  THEN CAST(OBS_VALUE AS DOUBLE) END) AS per_lavoro,
            MAX(CASE WHEN LOC_DEST='ALL'   AND REAS_COMMUTING='STD' THEN CAST(OBS_VALUE AS DOUBLE) END) AS per_studio
        FROM pendolari
        WHERE GENDER = 'T'
          AND AGE_NOCLASS = 'TOTAL'
          AND CITIZENSHIP = 'TOTAL'
          AND EDU_ATTAIN = 'ALL'
          AND CUR_ACT_STAT = '99'
          AND OBS_VALUE IS NOT NULL
        GROUP BY REF_AREA
    """).fetchall()
    pendolari_cols = [d[0] for d in con.description]
    pendolari_dict = {row[0]: dict(zip(pendolari_cols, row, strict=False))
                      for row in pendolari}
    log.info("istat_pendolari_done", comuni=len(pendolari_dict))

    # === CITTADINANZA ===
    # INDICATOR=RESPOP_AV, GENDER=T, AGE_CLASS=TOTAL, MARITAL_STATUS=ALL,
    # AREA_CONTRY_CITIZEN=ALL, USUAL_RESID_1Y=ALL
    # CITIZENSHIP: TOTAL=tutti, ITL=italiani, FRGAPO=stranieri+apolidi
    # Verifica empirica Lecce 2024: ITL + FRGAPO = TOTAL (delta 0)
    log.info("istat_aggregating", section="cittadinanza")
    cittadinanza = con.execute("""
        SELECT
            REF_AREA AS istat,
            MAX(CASE WHEN CITIZENSHIP = 'TOTAL'  THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_totale,
            MAX(CASE WHEN CITIZENSHIP = 'ITL'    THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_italiana,
            MAX(CASE WHEN CITIZENSHIP = 'FRGAPO' THEN CAST(OBS_VALUE AS DOUBLE) END) AS pop_straniera
        FROM cittadinanza
        WHERE INDICATOR = 'RESPOP_AV'
          AND GENDER = 'T'
          AND AGE_CLASS = 'TOTAL'
          AND MARITAL_STATUS = 'ALL'
          AND AREA_CONTRY_CITIZEN = 'ALL'
          AND USUAL_RESID_1Y = 'ALL'
          AND OBS_VALUE IS NOT NULL
        GROUP BY REF_AREA
    """).fetchall()
    cittadinanza_cols = [d[0] for d in con.description]
    cittadinanza_dict = {row[0]: dict(zip(cittadinanza_cols, row, strict=False))
                         for row in cittadinanza}
    log.info("istat_cittadinanza_done", comuni=len(cittadinanza_dict))

    # === Unione: tutti i comuni che appaiono in almeno una sezione ===
    all_istat = (
        set(istruzione_dict)
        | set(lavoro_dict)
        | set(famiglie_dict)
        | set(pendolari_dict)
        | set(cittadinanza_dict)
    )
    # Mantieni solo codici comune (6 cifre numeriche, non IT/ITF4/ecc)
    all_istat = {i for i in all_istat if i and i.isdigit() and len(i) == 6}
    log.info("istat_merging", comuni_totali=len(all_istat))

    # === Scrivi 1 shard JSON per comune ===
    written = 0
    for istat in sorted(all_istat):
        shard = build_shard(
            istat,
            istruzione_dict.get(istat, {}),
            lavoro_dict.get(istat, {}),
            famiglie_dict.get(istat, {}),
            pendolari_dict.get(istat, {}),
            cittadinanza_dict.get(istat, {}),
        )
        out = shard_dir / f"{istat}.json"
        out.write_text(json.dumps(shard, ensure_ascii=False,
                                  separators=(",", ":")))
        written += 1
        if written % 1000 == 0:
            log.info("istat_shards_progress", written=written)

    log.info("istat_shards_done", written=written, dir=str(shard_dir))
    return shard_dir


def safe_pct(num, den):
    """Percentuale arrotondata a 1 decimale, None se den nullo o mancante."""
    if num is None or den is None or den == 0:
        return None
    return round(100.0 * num / den, 1)


def safe_int(v):
    if v is None:
        return None
    return round(v)


def safe_round(v, n=2):
    if v is None:
        return None
    return round(v, n)


def build_shard(istat, istr, lav, fam, pend, citt):
    """Costruisce il dizionario JSON del singolo comune."""
    # === Istruzione: percentuali ===
    pop_2564 = istr.get("pop_25_64")
    nessun = istr.get("nessun_titolo") or 0
    element = istr.get("elementare") or 0
    media = istr.get("media") or 0
    diploma = istr.get("diploma") or 0
    lau_tri = istr.get("lau_triennale") or 0
    lau_mag = istr.get("lau_magistrale_dott") or 0

    terziario = lau_tri + lau_mag if (lau_tri or lau_mag) else None
    diploma_oltre = (
        (diploma or 0) + (lau_tri or 0) + (lau_mag or 0)
        if (diploma is not None or lau_tri or lau_mag) else None
    )
    max_media = (nessun + element + media) if any([nessun, element, media]) else None

    sez_istruzione = {
        "anno": 2024,
        "pop_riferimento_25_64": safe_int(pop_2564),
        "terziario_n":      safe_int(terziario),
        "terziario_pct":    safe_pct(terziario, pop_2564),
        "diploma_oltre_n":  safe_int(diploma_oltre),
        "diploma_oltre_pct": safe_pct(diploma_oltre, pop_2564),
        "max_media_n":      safe_int(max_media),
        "max_media_pct":    safe_pct(max_media, pop_2564),
        # Dettaglio per UI grafico a barre
        "dettaglio": {
            "nessun_titolo":  safe_int(istr.get("nessun_titolo")),
            "elementare":     safe_int(istr.get("elementare")),
            "media":          safe_int(istr.get("media")),
            "diploma":        safe_int(istr.get("diploma")),
            "laurea_triennale": safe_int(istr.get("lau_triennale")),
            "laurea_magistrale_dottorato": safe_int(istr.get("lau_magistrale_dott")),
        },
    }

    # === Lavoro: tassi ===
    pop_lav_2564 = lav.get("pop_25_64")
    forze = lav.get("forze_lavoro")
    sez_lavoro = {
        "anno": 2024,
        "pop_riferimento_25_64": safe_int(pop_lav_2564),
        "occupati_n":          safe_int(lav.get("occupati")),
        "in_cerca_n":          safe_int(lav.get("in_cerca")),
        "forze_lavoro_n":      safe_int(forze),
        "tasso_occupazione":   safe_pct(lav.get("occupati"), pop_lav_2564),
        "tasso_disoccupazione": safe_pct(lav.get("in_cerca"), forze),
        "tasso_attivita":      safe_pct(forze, pop_lav_2564),
    }

    # === Famiglie ===
    n_fam = fam.get("n_famiglie")
    pop_fam = fam.get("pop_in_famiglia")
    sez_famiglie = {
        "anno": 2024,
        "n_famiglie":         safe_int(n_fam),
        "pop_in_famiglia":    safe_int(pop_fam),
        "pop_in_convivenza":  safe_int(fam.get("pop_in_convivenza")),
        "dim_media_famiglia": safe_round(pop_fam / n_fam if n_fam else None, 2),
    }

    # === Pendolari (anno 2019, badge da mostrare in UI) ===
    tot_pend = pend.get("totale")
    sez_mobilita = {
        "anno": 2019,
        "_warning": "Dato aggiornato all'ultimo censimento permanente disponibile per pendolarismo (2019)",
        "pendolari_totale_n": safe_int(tot_pend),
        "fuori_comune_n":     safe_int(pend.get("fuori_comune")),
        "fuori_comune_pct":   safe_pct(pend.get("fuori_comune"), tot_pend),
        "per_lavoro_n":       safe_int(pend.get("per_lavoro")),
        "per_lavoro_pct":     safe_pct(pend.get("per_lavoro"), tot_pend),
        "per_studio_n":       safe_int(pend.get("per_studio")),
        "per_studio_pct":     safe_pct(pend.get("per_studio"), tot_pend),
    }

    # === Cittadinanza ===
    tot_citt = citt.get("pop_totale")
    sez_cittadinanza = {
        "anno": 2024,
        "pop_totale_n":   safe_int(tot_citt),
        "italiani_n":     safe_int(citt.get("pop_italiana")),
        "stranieri_n":    safe_int(citt.get("pop_straniera")),
        "stranieri_pct":  safe_pct(citt.get("pop_straniera"), tot_citt),
    }

    return {
        "codice_istat": istat,
        "istruzione":   sez_istruzione,
        "lavoro":       sez_lavoro,
        "famiglie":     sez_famiglie,
        "mobilita":     sez_mobilita,
        "cittadinanza": sez_cittadinanza,
        "fonte":        "ISTAT - Censimento permanente",
        "fonte_url":    "https://esploradati.istat.it/",
    }


def push_to_r2_parallel(shard_dir: Path) -> int:
    """Upload parallelo degli shard su R2 sotto prefix 'profilo/'."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()

    # Lista file gia su R2 per skip
    existing = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="profilo/"):
        for o in page.get("Contents", []):
            existing.add(o["Key"].split("/")[-1])

    shard_files = sorted(shard_dir.glob("*.json"))
    to_upload = [sf for sf in shard_files if sf.name not in existing]
    log.info("istat_pushing",
             total=len(shard_files),
             to_upload=len(to_upload),
             already_on_r2=len(existing))

    def _upload_one(sf):
        r2.upload_file(sf, f"profilo/{sf.name}",
                       content_type="application/json")
        return sf.name

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(_upload_one, sf): sf for sf in to_upload}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 1000 == 0:
                    log.info("istat_push_progress",
                             uploaded=uploaded,
                             total=len(to_upload))
            except Exception as e:
                log.error("istat_upload_failed", error=str(e))

    log.info("istat_push_done", uploaded=uploaded)
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Profilo del comune (Censimento permanente ISTAT)"
    )
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("/tmp/cruscotto-istat-profilo-cache"))
    parser.add_argument("--outdir", type=Path, default=Path("/var/www/cruscotto-italia/data"))
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza re-download dei CSV ISTAT anche se gia in cache")
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    output_dir = args.outdir
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target,
             cache_dir=str(cache_dir),
             output_dir=str(output_dir))

    try:
        # 1) Download dei 5 CSV ISTAT (con cache)
        for df in DATAFLOWS:
            download_dataflow(df, cache_dir, force=args.no_cache)

        # 2) Aggrega in shard per comune
        shard_dir = build_profilo_shards(cache_dir, output_dir)

        # 3) Upload su R2 (solo se target=r2)
        if args.target == "r2":
            uploaded = push_to_r2_parallel(shard_dir)
            manifest.update_source(
                "istat_profilo",
                [{"key": "profilo/*", "count": uploaded}],
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
