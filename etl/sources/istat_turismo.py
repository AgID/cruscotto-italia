"""ETL Turismo del comune - Capacita ricettiva ISTAT + Flussi provinciali.

Fonte: ISTAT esploradati.istat.it (SDMX REST API)
Dataflow utilizzati:
- 122_54_DF_DCSC_TUR_1: Capacita esercizi ricettivi per tipo (granularita COMUNALE)
- 122_54_DF_DCSC_TUR_7: Movimento clienti per tipo esercizio - annuale (granularita PROVINCIALE)

Granularita:
- Capacita (TUR_1): comunale - posti letto, camere, n. strutture per categoria
- Flussi (TUR_7): provinciale - arrivi, presenze, permanenza media
  (ISTAT NON pubblica flussi a livello comunale)

Mapping comune -> provincia NUTS3:
- Estratto da CL_ITTER107 (campo <structure:Parent><Ref id="..."/></structure:Parent>)
- Es. 075035 (Lecce) -> ITF45 (Provincia di Lecce)

Output:
- turismo/<istat>.json per ogni comune (~1-2 KB ciascuno)
  Contiene: capacita_comune (alberghi + extra-alberghiero) + flussi_provincia + metadata

Anno di riferimento: 2024 per entrambi i dataflow.

Indice di turisticita: posti letto per 100 abitanti (es. Lecce 13.4).
Calcolato da letti TUR_1 e popolazione dal manifest demografia (gia su R2).

Cache: i CSV bulk ISTAT salvati in cache_dir per ripartibilita.

Usage:
  python -m etl.sources.istat_turismo
  python -m etl.sources.istat_turismo --no-cache  # forza re-download
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

# === Configurazione SDMX ===
SDMX_BASE = "https://esploradati.istat.it/SDMXWS/rest"
SDMX_AGENCY = "IT1"
SDMX_VERSION = "1.0"
UA = "cruscotto-italia/1.0 (+https://cruscotto-italia.piersoftckan.biz)"

# Anno di riferimento
ANNO = 2024

# 2 dataflow turismo
DATAFLOWS = [
    {
        "name": "capacita",
        "id": "122_54_DF_DCSC_TUR_1",
        # 11 dim: FREQ.REF_AREA.DATA_TYPE.ADJUSTMENT.TYPE_ACCOMMODATION.ECON_ACTIVITY_NACE_2007.
        #         COUNTRY_RES_GUESTS.LOCALITY_TYPE.URBANIZ_DEGREE.COASTAL_AREA.SIZE_BY_NUMBER_ROOMS
        # key wildcard = A + 10 punti
        "key": "A..........",
        "year_start": ANNO,
        "year_end": ANNO,
    },
    {
        "name": "flussi",
        "id": "122_54_DF_DCSC_TUR_7",
        # Stesso schema 11 dim
        "key": "A..........",
        "year_start": ANNO,
        "year_end": ANNO,
    },
]

# Categorie di alloggio, gruppate in alberghi vs extra-alberghiero
ALBERGHI_CATS = {
    "5_5P_STARSHOTELS": "stelle_5",
    "4_STARSHOTELS":    "stelle_4",
    "3_STARSHOTELS":    "stelle_3",
    "2_STARSHOTELS":    "stelle_2",
    "1_STARSHOTELS":    "stelle_1",
    "RES":              "residence",
}
EXTRA_CATS = {
    "BNB":         "bnb",
    "DWELLINGS":   "case_in_affitto",
    "CAMP_VILL":   "camping_villaggi",
    "FARMHOUSES":  "agriturismi",
    "HOSTELS":     "ostelli",
    "HOMES":       "case_per_ferie",
    "MNTREF":      "rifugi_montagna",
    "OTHERACCNEC": "altri_extra",
}

# DATA_TYPE codici
DATA_BEDS = "BEDS"        # posti letto
DATA_NUM_EST = "NUM_EST"  # numero esercizi
DATA_BED_RMS = "BED_RMS"  # camere


def sdmx_data_url(df_id: str, key: str, y_start: int, y_end: int) -> str:
    return (f"{SDMX_BASE}/data/{SDMX_AGENCY},{df_id},{SDMX_VERSION}/{key}"
            f"?startPeriod={y_start}&endPeriod={y_end}")


def sdmx_dataflow_url(df_id: str) -> str:
    """URL per scaricare metadati + codelist (CL_ITTER107)."""
    return (f"{SDMX_BASE}/dataflow/{SDMX_AGENCY}/{df_id}/{SDMX_VERSION}"
            f"?references=all")


def download_url(url: str, out: Path, accept: str, force: bool = False) -> Path:
    if out.exists() and out.stat().st_size > 1000 and not force:
        log.info("istat_cache_hit", path=str(out), size=out.stat().st_size)
        return out
    log.info("istat_downloading", url=url)
    req = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        log.error("istat_http_error", status=e.code, reason=e.reason, url=url)
        raise
    except urllib.error.URLError as e:
        log.error("istat_url_error", reason=str(e.reason), url=url)
        raise
    out.write_bytes(data)
    log.info("istat_downloaded", bytes=len(data), path=str(out))
    return out


def download_dataflow_csv(df: dict, cache_dir: Path, force: bool = False) -> Path:
    """Scarica CSV bulk del dataflow."""
    out = cache_dir / f"{df['name']}.csv"
    url = sdmx_data_url(df["id"], df["key"], df["year_start"], df["year_end"])
    return download_url(url, out, "application/vnd.sdmx.data+csv;version=1.0.0",
                        force=force)


def download_codelist_xml(cache_dir: Path, force: bool = False) -> Path:
    """Scarica metadati TUR_1 con codelist CL_ITTER107 (per gerarchia comune->provincia)."""
    out = cache_dir / "itter107.xml"
    # references=all sul dataflow capacita restituisce CL_ITTER107 con gerarchia
    url = sdmx_dataflow_url(DATAFLOWS[0]["id"])
    return download_url(url, out, "application/xml", force=force)


def parse_comune_to_provincia(xml_path: Path) -> dict:
    """Estrae mappa codice_istat_comune -> codice_nuts3_provincia da CL_ITTER107.

    Pattern XML:
      <structure:Code id="075035">
        ...
        <structure:Parent><Ref id="ITF45" /></structure:Parent>
      </structure:Code>

    Restituisce anche nomi provincia: { com_istat: (prov_nuts3, prov_nome) }
    """
    log.info("itter107_parsing", path=str(xml_path))
    text = xml_path.read_text(encoding="utf-8")

    # Estrai prima nome di ogni provincia NUTS3 (IT[A-Z]\d{2})
    prov_pattern = re.compile(
        r'<structure:Code id="(IT[A-Z]\d{2})">(.*?)</structure:Code>',
        re.DOTALL,
    )
    prov_nomi = {}
    for m in prov_pattern.finditer(text):
        cid = m.group(1)
        body = m.group(2)
        nm = re.search(r'<common:Name xml:lang="it">([^<]*)</common:Name>', body)
        prov_nomi[cid] = nm.group(1) if nm else cid
    log.info("itter107_province_loaded", n=len(prov_nomi))

    # Estrai mapping comune -> NUTS3 (parent)
    com_pattern = re.compile(
        r'<structure:Code id="(\d{6})">(.*?)</structure:Code>',
        re.DOTALL,
    )
    com_to_prov = {}
    for m in com_pattern.finditer(text):
        cid = m.group(1)
        body = m.group(2)
        parent = re.search(r'<structure:Parent>\s*<Ref id="(IT[A-Z]\d{2})"', body)
        if parent:
            nuts3 = parent.group(1)
            com_to_prov[cid] = (nuts3, prov_nomi.get(nuts3, nuts3))
    log.info("itter107_comuni_loaded", n=len(com_to_prov))
    return com_to_prov


def build_turismo_shards(cache_dir: Path, output_dir: Path) -> Path:
    """Aggrega 2 CSV (capacita comune + flussi provincia) in 1 JSON per comune."""
    shard_dir = output_dir / "turismo"
    shard_dir.mkdir(parents=True, exist_ok=True)

    # 1) Mapping comune -> provincia da CL_ITTER107
    itter_xml = cache_dir / "itter107.xml"
    com_to_prov = parse_comune_to_provincia(itter_xml)

    con = duckdb.connect()

    # 2) Carica i 2 CSV
    for df in DATAFLOWS:
        csv_path = cache_dir / f"{df['name']}.csv"
        if not csv_path.exists():
            log.warning("istat_csv_missing", source=df["name"], path=str(csv_path))
            continue
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

    # 3) === CAPACITA per comune ===
    # Filtri: ADJUSTMENT=N, COUNTRY_RES_GUESTS=NAP, LOCALITY=ALL, URBANIZ=ALL,
    #         COASTAL=ALL, SIZE=TOT
    # ECON_ACTIVITY: 551 (alberghi) | 552_553 (extra-alberg) | 551_553 (totale)
    # DATA_TYPE: BEDS | NUM_EST | BED_RMS
    # TYPE_ACCOMMODATION: ALL | HOTELLIKE | OTHER | singole categorie
    log.info("istat_aggregating", section="capacita")
    capacita = con.execute("""
        SELECT
            REF_AREA AS istat,
            DATA_TYPE,
            TYPE_ACCOMMODATION AS type_acc,
            ECON_ACTIVITY_NACE_2007 AS ateco,
            CAST(OBS_VALUE AS DOUBLE) AS val
        FROM capacita
        WHERE ADJUSTMENT = 'N'
          AND COUNTRY_RES_GUESTS = 'NAP'
          AND LOCALITY_TYPE = 'ALL'
          AND URBANIZ_DEGREE = 'ALL'
          AND COASTAL_AREA = 'ALL'
          AND SIZE_BY_NUMBER_ROOMS = 'TOT'
          AND OBS_VALUE IS NOT NULL
          AND DATA_TYPE IN ('BEDS', 'NUM_EST', 'BED_RMS')
    """).fetchall()
    log.info("istat_capacita_rows", rows=len(capacita))

    # Raggruppa per comune
    cap_by_istat: dict[str, dict] = {}
    for istat, dt, type_acc, ateco, val in capacita:
        # Solo comuni (codici 6 cifre numeriche)
        if not istat or not istat.isdigit() or len(istat) != 6:
            continue
        d = cap_by_istat.setdefault(istat, {})
        # Chiave composta (data_type, type_acc) per evitare collisioni
        d[(dt, type_acc, ateco)] = val
    log.info("istat_capacita_comuni", comuni=len(cap_by_istat))

    # 4) === FLUSSI per provincia ===
    # Filtri: ADJUSTMENT=N, TYPE_ACCOMMODATION=ALL, ECON_ACTIVITY=551_553,
    #         LOCALITY=ALL, URBANIZ=ALL, COASTAL=ALL, SIZE=TOT
    # DATA_TYPE: AR (arrivi) | NI (presenze - notti)
    # COUNTRY_RES_GUESTS: IT | WORLD | WRL_X_ITA
    log.info("istat_aggregating", section="flussi")
    flussi = con.execute("""
        SELECT
            REF_AREA AS prov,
            DATA_TYPE,
            COUNTRY_RES_GUESTS AS country,
            CAST(OBS_VALUE AS DOUBLE) AS val
        FROM flussi
        WHERE ADJUSTMENT = 'N'
          AND TYPE_ACCOMMODATION = 'ALL'
          AND ECON_ACTIVITY_NACE_2007 = '551_553'
          AND LOCALITY_TYPE = 'ALL'
          AND URBANIZ_DEGREE = 'ALL'
          AND COASTAL_AREA = 'ALL'
          AND SIZE_BY_NUMBER_ROOMS = 'TOT'
          AND DATA_TYPE IN ('AR', 'NI')
          AND COUNTRY_RES_GUESTS IN ('IT', 'WORLD', 'WRL_X_ITA')
          AND OBS_VALUE IS NOT NULL
    """).fetchall()
    log.info("istat_flussi_rows", rows=len(flussi))

    flussi_by_prov: dict[str, dict] = {}
    for prov, dt, country, val in flussi:
        # Solo province NUTS3 (IT[A-Z]\d{2})
        if not re.fullmatch(r'IT[A-Z]\d{2}', prov or ''):
            continue
        d = flussi_by_prov.setdefault(prov, {})
        d[(dt, country)] = val
    log.info("istat_flussi_province", province=len(flussi_by_prov))

    # 5) === Carica popolazione comunale per indice turisticita ===
    # Legge dal bundle anagrafica locale (lookup/comuni-bundle.json).
    pop_by_istat = load_popolazioni()
    log.info("istat_pop_loaded", comuni=len(pop_by_istat))

    # 6) === Costruisci shard JSON per ogni comune ===
    # Set di tutti gli istat che compaiono in capacita o nel mapping
    all_istat = set(cap_by_istat.keys()) | set(com_to_prov.keys())
    written = 0
    for istat in sorted(all_istat):
        prov_info = com_to_prov.get(istat)
        if not prov_info:
            continue  # comune non in CL_ITTER107 (raro)
        prov_nuts3, prov_nome = prov_info

        cap_data = cap_by_istat.get(istat, {})
        fl_data = flussi_by_prov.get(prov_nuts3, {})
        pop = pop_by_istat.get(istat)

        shard = build_shard(istat, prov_nuts3, prov_nome, cap_data, fl_data, pop)

        # Scrivi solo se almeno una sezione ha dati
        has_capacita = any(cap_data.values())
        has_flussi = any(fl_data.values())
        if not has_capacita and not has_flussi:
            continue

        out = shard_dir / f"{istat}.json"
        out.write_text(json.dumps(shard, ensure_ascii=False,
                                  separators=(",", ":")))
        written += 1
        if written % 1000 == 0:
            log.info("istat_shards_progress", written=written)

    log.info("istat_shards_done", written=written, dir=str(shard_dir))
    return shard_dir


def load_popolazioni() -> dict[str, int]:
    """Carica popolazione totale per ogni comune dal bundle anagrafica locale.

    Se il bundle e' assente, ritorna dict vuoto: l'indice di turisticita
    sara' None per i comuni senza popolazione disponibile.
    """
    pop = {}
    comuni = local_lookup.load_comuni_bundle()
    if comuni is None:
        log.warning("popolazioni_bundle_unavailable",
                    path=str(local_lookup.get_lookup_dir() / "comuni-bundle.json"))
        return pop
    for istat, c in comuni.items():
        kpi = c.get("kpi") if isinstance(c, dict) else None
        popval = kpi.get("popolazione") if isinstance(kpi, dict) else None
        if istat and popval:
            pop[istat] = int(popval)
    log.info("popolazioni_from_bundle", n=len(pop))
    return pop


def build_shard(istat, prov_nuts3, prov_nome, cap_data, fl_data, pop):
    """Costruisce il dizionario JSON del singolo comune."""

    # === CAPACITA: aggregazioni per categoria ===
    def get_cat(data_type, type_acc, ateco):
        return cap_data.get((data_type, type_acc, ateco))

    def cat_int(data_type, type_acc, ateco):
        v = get_cat(data_type, type_acc, ateco)
        return round(v) if v is not None else None

    # Totali (TYPE=ALL, ATECO=551_553 = somma alberghi+extra)
    totale_strutture = cat_int(DATA_NUM_EST, "ALL", "551_553")
    totale_letti = cat_int(DATA_BEDS, "ALL", "551_553")
    totale_camere = cat_int(DATA_BED_RMS, "ALL", "551_553")

    # Alberghi (TYPE=HOTELLIKE, ATECO=551)
    alb_strutture = cat_int(DATA_NUM_EST, "HOTELLIKE", "551")
    alb_letti = cat_int(DATA_BEDS, "HOTELLIKE", "551")

    # Extra-alberghiero (TYPE=OTHER, ATECO=552_553)
    extra_strutture = cat_int(DATA_NUM_EST, "OTHER", "552_553")
    extra_letti = cat_int(DATA_BEDS, "OTHER", "552_553")

    # Indice di turisticita (letti per 100 abitanti)
    indice_turisticita = None
    if totale_letti and pop:
        indice_turisticita = round(100.0 * totale_letti / pop, 1)

    # Categorie alberghi (ATECO=551)
    alberghi_cats = {}
    for cat_code, cat_name in ALBERGHI_CATS.items():
        alberghi_cats[cat_name] = {
            "strutture": cat_int(DATA_NUM_EST, cat_code, "551"),
            "letti":     cat_int(DATA_BEDS, cat_code, "551"),
        }

    # Categorie extra-alberghiero (ATECO=552_553)
    extra_cats = {}
    for cat_code, cat_name in EXTRA_CATS.items():
        extra_cats[cat_name] = {
            "strutture": cat_int(DATA_NUM_EST, cat_code, "552_553"),
            "letti":     cat_int(DATA_BEDS, cat_code, "552_553"),
        }

    sez_capacita = {
        "anno": ANNO,
        "totale_strutture":         totale_strutture,
        "totale_letti":             totale_letti,
        "totale_camere":            totale_camere,
        "indice_turisticita_per_100ab": indice_turisticita,
        "popolazione_riferimento":  pop,
        "alberghi": {
            "totale_strutture": alb_strutture,
            "totale_letti":     alb_letti,
            **alberghi_cats,
        },
        "extra_alberghiero": {
            "totale_strutture": extra_strutture,
            "totale_letti":     extra_letti,
            **extra_cats,
        },
    }

    # === FLUSSI: per provincia ===
    def fl(dt, country):
        v = fl_data.get((dt, country))
        return round(v) if v is not None else None

    arrivi_tot = fl("AR", "WORLD")
    arrivi_ita = fl("AR", "IT")
    arrivi_str = fl("AR", "WRL_X_ITA")
    presenze_tot = fl("NI", "WORLD")
    presenze_ita = fl("NI", "IT")
    presenze_str = fl("NI", "WRL_X_ITA")

    permanenza = None
    if presenze_tot and arrivi_tot:
        permanenza = round(presenze_tot / arrivi_tot, 1)

    stranieri_pct = None
    if arrivi_tot and arrivi_str is not None:
        stranieri_pct = round(100.0 * arrivi_str / arrivi_tot, 1)

    sez_flussi = {
        "anno": ANNO,
        "_warning": "Dato a livello provinciale (NUTS3), non comunale: ISTAT non pubblica i flussi turistici per singolo comune.",
        "provincia_nuts3": prov_nuts3,
        "provincia_nome":  prov_nome,
        "arrivi_totali":     arrivi_tot,
        "arrivi_italiani":   arrivi_ita,
        "arrivi_stranieri":  arrivi_str,
        "presenze_totali":   presenze_tot,
        "presenze_italiane": presenze_ita,
        "presenze_straniere": presenze_str,
        "permanenza_media":  permanenza,
        "stranieri_pct":     stranieri_pct,
    }

    return {
        "codice_istat":   istat,
        "capacita_comune": sez_capacita,
        "flussi_provincia": sez_flussi,
        "fonte":     "ISTAT - Capacita ed esercizio degli esercizi ricettivi",
        "fonte_url": "https://esploradati.istat.it/",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Turismo del comune (capacita ricettiva + flussi provinciali ISTAT)"
    )
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("/tmp/cruscotto-istat-turismo-cache"))
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

    log.info("etl_start",
             cache_dir=str(cache_dir),
             output_dir=str(output_dir))

    try:
        # 1) Download dei 2 CSV (capacita + flussi) + 1 XML (CL_ITTER107)
        for df in DATAFLOWS:
            download_dataflow_csv(df, cache_dir, force=args.no_cache)
        download_codelist_xml(cache_dir, force=args.no_cache)

        # 2) Aggrega in shard per comune
        shard_dir = build_turismo_shards(cache_dir, output_dir)

        # Manifest update best-effort
        try:
            shard_count = len(list(shard_dir.glob("*.json")))
            manifest.update_source(
                "istat_turismo",
                [{"key": "turismo/*", "count": shard_count}],
                status="ok",
            )
            log.info("manifest_updated", count=shard_count)
        except Exception as e:
            log.warning("manifest_update_skipped", error=str(e))

        log.info("etl_done")
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
