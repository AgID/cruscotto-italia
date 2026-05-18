"""ETL ISPRA SNPA - Qualita' dell'aria per comune.

Fonte: ISPRA - Statistiche di qualita' dell'aria nazionale, pubblicate ogni
anno secondo la Decisione UE 2011/850/EU. Le serie storiche aggregano i
rilevamenti delle stazioni di monitoraggio gestite da Regioni e Province
Autonome (rete SNPA: ISPRA + ARPA/APPA regionali).

Inquinanti coperti:
  - PM10  (2002-2022)  - particolato grossolano,  limite legge 40 ug/m3 annua
  - PM2.5 (2004-2022)  - particolato fine,        limite legge 25 ug/m3 annua
  - NO2   (2001-2022)  - biossido di azoto,       limite legge 40 ug/m3 annua
  - O3    (2002-2022)  - ozono,                   obiettivo lungo termine

Granularita': stazione di monitoraggio, aggregata per comune via id_comune
ISTAT estratto dal codice ISPRA (vedi note mapping).

Output:
  - aria/<istat>.json per ogni comune con almeno 1 stazione (~397 comuni 2022)
  - lookup/aria-aggregato.json (overview nazionale: top peggiori, distribuzione)

Uso:
  python -m etl.sources.aria
  python -m etl.sources.aria --no-cache              # forza re-download CSV

Cadenza: annuale - ISPRA aggiorna a gennaio dell'anno N+2 (es. dati 2024
pubblicati gennaio 2026). Pianificare cron in etl-annual.yml.

Note mapping ISPRA -> ISTAT:
  Il campo id_comune ISPRA e' un float a 7 cifre del tipo
  <id_regione><id_provincia_3_cifre><id_comune_3_cifre>, per esempio
  1001028.0 = Borgaro Torinese (Piemonte=1, Torino=001, comune=028).
  Il codice ISTAT canonico a 6 cifre coincide con le ULTIME 6 cifre
  dello stringa zfill(7), oppure equivalentemente con
  id_provincia.zfill(3) + ultime_3(id_comune).zfill(3).
  Test 2026-05-11 su PM10/2022: 595/595 match (100%).

Encoding:
  Il CSV ISPRA e' separato da ';' con encoding latin1 (cp1252). Alcuni nomi
  comuni con accenti vengono letti corrotti (es. 'MondovÃ¬' invece di
  'Mondovi"'). Il codice fa fix decodificando la stringa come bytes latin1
  e ri-decodificandola come utf-8 dove necessario.

Schema shard aria/<istat>.json (v0.1.0):
{
  "_etl_version": "0.1.0",
  "_source": "ISPRA SNPA - Qualita' dell'aria",
  "_generated_at": "ISO-8601",
  "_anno_dati": 2022,
  "_aggiornamento_atteso": "annuale (gennaio dell'anno N+2)",
  "istat_code": "001027",
  "n_stazioni": 1,
  "stazioni": [
    {
      "station_eu_code": "IT1128A",
      "nome": "Borgaro T. - Caduti",
      "lat": 45.1546, "lon": 7.658,
      "tipo_zona": "SUBURBANA",
      "tipo_stazione": "FONDO",
      "tipo_combinato": "SF"
    }
  ],
  "ultimo_anno": {
    "anno": 2022,
    "pm10":  {"media": 23.0, "n_stazioni_con_dato": 1, "n_sup50_giorni_max": 12,
              "n_stazioni_oltre_limite_legge": 0, "n_stazioni_oltre_limite_oms": 1,
              "fascia": "(20;30]"},
    "pm25":  {...},
    "no2":   {...},
    "o3":    {...}
  },
  "trend_decennale": {
    "anni":       [2013, 2014, ..., 2022],
    "pm10_media": [35.0, 32.0, ..., 23.0],
    "pm25_media": [...],
    "no2_media":  [...],
    "o3_media":   [...]
  },
  "stazioni_dettaglio": [
    {
      "station_eu_code": "IT1128A",
      "nome": "Borgaro T. - Caduti",
      "anni": [
        {
          "anno": 2022,
          "pm10": {"media": 23.0, "sup50": 12, "n_giorni_validi": 326, "fascia": "(20;30]"},
          "pm25": {...}, "no2": {...}, "o3": {...}
        }
      ]
    }
  ]
}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

# URL CSV ufficiali ISPRA (serie storiche per inquinante).
# Aggiornati al 2024-01-09 (PM10, PM2.5, NO2) e 2024-02-26 (O3) per dati 2022.
# Quando ISPRA pubblichera' i dati 2023/2024 cambieranno gli URL e i nomi
# file. Verificare periodicamente:
#   https://www.isprambiente.gov.it/it/banche-dati/banche-dati-folder/aria/qualita-dellaria
INQUINANTI_URLS: dict[str, str] = {
    "pm10": "https://www.isprambiente.gov.it/files2024/attivita/aria/2024-01-09_2002_2022_pm10__statistiche.csv",
    "pm25": "https://www.isprambiente.gov.it/files2024/attivita/aria/2024-01-09_2004_2022_pm25__statistiche.csv",
    "no2":  "https://www.isprambiente.gov.it/files2024/attivita/aria/2024-01-09-2001_2022-no2-_statistiche.csv",
}

# Limiti normativi (ug/m3, media annuale) - D.Lgs. 155/2010 e WHO 2021 AQG.
# Il "limite legge" e' quello vincolante UE; "limite OMS" e' indicativo
# (raccomandazione sanitaria, piu' stringente).
LIMITI_ANNUALI: dict[str, dict[str, float]] = {
    "pm10": {"legge": 40.0, "oms": 15.0},
    "pm25": {"legge": 25.0, "oms": 5.0},
    "no2":  {"legge": 40.0, "oms": 10.0},
}

# Cache locale CSV scaricati (per evitare re-download durante development).
CACHE_DIR = Path("/tmp/cruscotto-aria-cache")

# Anno di riferimento "ultimo_anno" del shard. Aggiornare quando ISPRA
# pubblichera' i dati 2023/2024.
ANNO_RIFERIMENTO = 2022

# Numero anni nel trend_decennale (2013-2022 = 10 anni se ANNO_RIFERIMENTO=2022).
TREND_ANNI = 10


# ============================================================================
# Download e parsing CSV
# ============================================================================

def fetch_csv(inquinante: str, url: str, no_cache: bool = False) -> Path:
    """Scarica un CSV ISPRA, con cache locale.

    Ritorna Path al file CSV su disco.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{inquinante}.csv"
    if cached.exists() and not no_cache:
        log.info("aria_cache_hit", inquinante=inquinante,
                 path=str(cached), bytes=cached.stat().st_size)
        return cached

    log.info("aria_downloading", inquinante=inquinante, url=url)
    headers = {
        "User-Agent": "Mozilla/5.0 Cruscotto-Italia/1.0 (+https://cruscotto-italia.dati.gov.it)",
        "Accept": "text/csv,application/csv,*/*",
    }
    resp = requests.get(url, headers=headers, timeout=120)
    resp.raise_for_status()
    cached.write_bytes(resp.content)
    log.info("aria_downloaded", inquinante=inquinante,
             bytes=len(resp.content), path=str(cached))
    return cached


def fix_encoding(s: str) -> str:
    """Fix per nomi di comune letti come latin1 ma in realta' utf-8.

    Esempio: 'MondovÃ¬' -> 'Mondovi"' (il glifo i con accento grave).
    Strategia: ri-encode come latin1 bytes e decode come utf-8;
    se fallisce ritorna originale.
    """
    if not s:
        return s
    try:
        return s.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def parse_id_comune_to_istat(id_comune_raw: float | None) -> str | None:
    """Estrae codice ISTAT 6 cifre da id_comune ISPRA.

    id_comune ISPRA e' un float a 7 cifre del tipo XYYYZZZ con
    X = id_regione (1-2 cifre), YYY = id_provincia_3, ZZZ = id_comune_3.
    Il codice ISTAT canonico a 6 cifre e' YYYZZZ (ultime 6 cifre).

    Test 2026-05-11: 595/595 stazioni 2022 PM10 mappate correttamente.
    """
    if id_comune_raw is None:
        return None
    try:
        # Float -> int -> str (numero senza decimali) -> zfill 7 -> ultime 6
        as_int = int(id_comune_raw)
        if as_int <= 0:
            return None
        as_str = str(as_int).zfill(7)
        return as_str[-6:]
    except (ValueError, TypeError):
        return None


def parse_csv(csv_path: Path) -> list[dict]:
    """Parsa un CSV ISPRA in lista di dict normalizzati.

    Restituisce lista di record con chiavi:
      - station_eu_code, nome_stazione
      - istat_code (6 cifre, None se non mappabile)
      - regione, provincia, comune (con encoding fixato)
      - lat, lon
      - tipo_zona, tipo_stazione, tipo_combinato
      - anno (yy)
      - media_yy (media annuale, None se mancante)
      - sup50 (giorni > 50 ug/m3, solo PM10)
      - n_giorni_validi (n)
      - fascia (range_y categoriale, es. "(30;40]")
      - copertura (1 = anno completo, None altrimenti)
    """
    rows: list[dict] = []
    skipped_no_istat = 0
    skipped_no_lat = 0

    with csv_path.open("r", encoding="latin1", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for r in reader:
            # Mapping ISTAT - REQUIRED, skip se mancante
            try:
                id_comune_raw = float(r.get("id_comune") or 0)
            except (ValueError, TypeError):
                id_comune_raw = 0
            istat = parse_id_comune_to_istat(id_comune_raw if id_comune_raw > 0 else None)
            if istat is None:
                skipped_no_istat += 1
                continue

            # Lat/lon - REQUIRED per visualizzazione mappa.
            # NOTE: i CSV ISPRA hanno separatore decimale incoerente: PM10/PM2.5/NO2
            # usano il punto, O3 usa la virgola. Il replace(",", ".") gestisce entrambi.
            def _parse_coord(v: str | None) -> float:
                if not v or not v.strip():
                    return 0.0
                try:
                    return float(v.replace(",", "."))
                except (ValueError, TypeError):
                    return 0.0
            lat = _parse_coord(r.get("Lat"))
            lon = _parse_coord(r.get("Lon"))
            if lat == 0.0 or lon == 0.0:
                skipped_no_lat += 1
                continue

            # Anno
            try:
                anno = int(float(r.get("yy") or 0))
            except (ValueError, TypeError):
                continue
            if anno < 2000 or anno > 2100:
                continue

            # Float helpers: stringa potenzialmente vuota o "NA"
            def to_float(key: str, r=r) -> float | None:
                v = r.get(key)
                if v is None or v == "" or v.strip() == "":
                    return None
                try:
                    return float(v.replace(",", "."))
                except (ValueError, TypeError):
                    return None

            def to_int(key: str, r=r) -> int | None:
                v = r.get(key)
                if v is None or v == "" or v.strip() == "":
                    return None
                try:
                    return int(float(v))
                except (ValueError, TypeError):
                    return None

            rows.append({
                "station_eu_code": (r.get("station_eu_code") or "").strip(),
                "nome_stazione":   fix_encoding((r.get("nome_stazione") or "").strip()),
                "istat_code":      istat,
                "regione":         fix_encoding((r.get("Regione") or "").strip()),
                "provincia":       fix_encoding((r.get("Provincia") or "").strip()),
                "comune":          fix_encoding((r.get("Comune") or "").strip()),
                "lat":             round(lat, 4),
                "lon":             round(lon, 4),
                "tipo_zona":       (r.get("tipo_zona") or "").strip().upper(),
                "tipo_stazione":   (r.get("tipo_stazione") or "").strip().upper(),
                "tipo_combinato": (r.get("TIPO") or "").strip().upper(),
                "anno":            anno,
                "media_yy":        to_float("media_yy"),
                "sup50":           to_int("sup50"),       # solo PM10
                "n_giorni_validi": to_int("n"),
                "fascia":          (r.get("range_y") or "").strip(),
                "copertura":       to_int("copertura"),
            })

    log.info("aria_csv_parsed", path=str(csv_path),
             rows=len(rows), skipped_no_istat=skipped_no_istat,
             skipped_no_lat=skipped_no_lat)
    return rows


# ============================================================================
# Aggregazione per comune
# ============================================================================

def build_shards(
    data_per_inquinante: dict[str, list[dict]],
    output_dir: Path,
) -> tuple[int, dict]:
    """Costruisce gli shard aria/<istat>.json per ogni comune con stazione.

    Args:
        data_per_inquinante: dict {inquinante: [record, ...]} risultato di
                             parse_csv per i 4 inquinanti.
        output_dir: directory dove scrivere gli shard.

    Returns:
        (n_shards_scritti, summary_per_inquinante)
        summary contiene statistiche per il lookup aggregato.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Indice: per ogni comune, raccogliamo tutte le stazioni e tutti i dati.
    # comune_data[istat] = {
    #   "anagrafica": {regione, provincia, comune},
    #   "stazioni": {station_eu_code: {nome, lat, lon, tipo_zona, ...}},
    #   "dati": {(station_eu_code, anno): {pm10: rec, pm25: rec, no2: rec, o3: rec}}
    # }
    comune_data: dict[str, dict] = defaultdict(
        lambda: {"anagrafica": {}, "stazioni": {}, "dati": defaultdict(dict)}
    )

    for inquinante, rows in data_per_inquinante.items():
        for rec in rows:
            istat = rec["istat_code"]
            seu = rec["station_eu_code"]
            anno = rec["anno"]

            cd = comune_data[istat]
            if not cd["anagrafica"]:
                cd["anagrafica"] = {
                    "regione":   rec["regione"],
                    "provincia": rec["provincia"],
                    "comune":    rec["comune"],
                }
            if seu not in cd["stazioni"]:
                cd["stazioni"][seu] = {
                    "station_eu_code": seu,
                    "nome":            rec["nome_stazione"],
                    "lat":             rec["lat"],
                    "lon":             rec["lon"],
                    "tipo_zona":       rec["tipo_zona"],
                    "tipo_stazione":   rec["tipo_stazione"],
                    "tipo_combinato": rec["tipo_combinato"],
                }
            cd["dati"][(seu, anno)][inquinante] = rec

    # 2. Per ogni comune, costruisce lo shard.
    n_written = 0
    inquinanti_summary: dict[str, dict] = {
        ink: {"n_stazioni": 0, "n_comuni": 0, "n_oltre_legge": 0,
              "n_oltre_oms": 0, "media_nazionale": None, "valori": []}
        for ink in INQUINANTI_URLS.keys()
    }

    for istat, cd in comune_data.items():
        anag = cd["anagrafica"]
        stazioni_list = sorted(cd["stazioni"].values(),
                               key=lambda s: s["nome"])

        # --- Blocco "ultimo_anno" (= ANNO_RIFERIMENTO) ---
        ultimo_block: dict = {"anno": ANNO_RIFERIMENTO}
        for ink in INQUINANTI_URLS.keys():
            ultimo_block[ink] = _aggregate_inquinante(
                cd["dati"], ink, ANNO_RIFERIMENTO, inquinanti_summary
            )

        # --- Blocco trend_decennale (medie comune x inquinante x anno) ---
        anni_trend = list(range(ANNO_RIFERIMENTO - TREND_ANNI + 1, ANNO_RIFERIMENTO + 1))
        trend_block: dict = {"anni": anni_trend}
        for ink in INQUINANTI_URLS.keys():
            trend_block[f"{ink}_media"] = []
            for a in anni_trend:
                medie_anno = []
                for (_seu, ay), inks in cd["dati"].items():
                    if ay != a:
                        continue
                    rec = inks.get(ink)
                    if rec and rec["media_yy"] is not None:
                        medie_anno.append(rec["media_yy"])
                if medie_anno:
                    trend_block[f"{ink}_media"].append(round(sum(medie_anno) / len(medie_anno), 1))
                else:
                    trend_block[f"{ink}_media"].append(None)

        # --- Blocco stazioni_dettaglio (per stazione, serie completa) ---
        stazioni_dettaglio = []
        for stz in stazioni_list:
            seu = stz["station_eu_code"]
            anni_stz = sorted({ay for (s, ay) in cd["dati"].keys() if s == seu})
            anni_dati = []
            for a in anni_stz:
                inks_a = cd["dati"].get((seu, a), {})
                row: dict = {"anno": a}
                for ink in INQUINANTI_URLS.keys():
                    rec = inks_a.get(ink)
                    if rec is None:
                        row[ink] = None
                        continue
                    row[ink] = {
                        "media": _round_or_none(rec["media_yy"]),
                        "sup50": rec["sup50"],
                        "n_giorni_validi": rec["n_giorni_validi"],
                        "fascia": rec["fascia"] or None,
                    }
                anni_dati.append(row)
            stazioni_dettaglio.append({
                "station_eu_code": seu,
                "nome": stz["nome"],
                "anni": anni_dati,
            })

        # --- Composizione finale shard ---
        shard = {
            "_etl_version": ETL_VERSION,
            "_source": "ISPRA SNPA - Qualita' dell'aria (Decisione UE 2011/850/EU)",
            "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "_anno_dati": ANNO_RIFERIMENTO,
            "_aggiornamento_atteso": "annuale (gennaio dell'anno N+2)",
            "istat_code": istat,
            "regione": anag.get("regione"),
            "provincia": anag.get("provincia"),
            "comune": anag.get("comune"),
            "n_stazioni": len(stazioni_list),
            "stazioni": stazioni_list,
            "ultimo_anno": ultimo_block,
            "trend_decennale": trend_block,
            "stazioni_dettaglio": stazioni_dettaglio,
        }

        # Scrivo shard
        out_path = output_dir / f"{istat}.json"
        out_path.write_text(json.dumps(shard, ensure_ascii=False, indent=2))
        n_written += 1

    # Calcolo medie nazionali finali
    for _ink, s in inquinanti_summary.items():
        if s["valori"]:
            s["media_nazionale"] = round(sum(s["valori"]) / len(s["valori"]), 1)
        del s["valori"]  # non serve nell'output

    return n_written, inquinanti_summary


def _aggregate_inquinante(
    dati: dict, inquinante: str, anno: int,
    summary: dict,
) -> dict:
    """Aggrega tutte le stazioni di un comune per un singolo inquinante/anno.

    Aggiorna anche summary nazionale con i valori trovati.
    """
    medie = []
    sup50_list = []
    fasce = []
    n_stazioni_dato = 0
    limite = LIMITI_ANNUALI[inquinante]
    n_oltre_legge = 0
    n_oltre_oms = 0

    for (_seu, ay), inks in dati.items():
        if ay != anno:
            continue
        rec = inks.get(inquinante)
        if rec is None:
            continue
        if rec["media_yy"] is not None:
            medie.append(rec["media_yy"])
            n_stazioni_dato += 1
            # Update summary nazionale
            summary[inquinante]["valori"].append(rec["media_yy"])
            summary[inquinante]["n_stazioni"] += 1
            # Sforamenti limiti
            if limite["legge"] is not None and rec["media_yy"] > limite["legge"]:
                n_oltre_legge += 1
                summary[inquinante]["n_oltre_legge"] += 1
            if limite["oms"] is not None and rec["media_yy"] > limite["oms"]:
                n_oltre_oms += 1
                summary[inquinante]["n_oltre_oms"] += 1
        if rec["sup50"] is not None:
            sup50_list.append(rec["sup50"])
        if rec["fascia"]:
            fasce.append(rec["fascia"])

    if n_stazioni_dato == 0:
        return None  # type: ignore[return-value]

    # Note: summary["n_comuni"] ricalcolato a posteriori (non qui per evitare
    # di contare lo stesso comune x N inquinanti)
    return {
        "media": round(sum(medie) / len(medie), 1) if medie else None,
        "n_stazioni_con_dato": n_stazioni_dato,
        "n_sup50_giorni_max": max(sup50_list) if sup50_list else None,
        "n_sup50_giorni_media": round(sum(sup50_list) / len(sup50_list), 1) if sup50_list else None,
        "n_stazioni_oltre_limite_legge": n_oltre_legge,
        "n_stazioni_oltre_limite_oms": n_oltre_oms,
        "fascia_prevalente": _moda(fasce) if fasce else None,
    }


def _moda(items: list[str]) -> str | None:
    """Ritorna l'elemento piu' frequente (None se lista vuota)."""
    if not items:
        return None
    counts: dict[str, int] = defaultdict(int)
    for x in items:
        counts[x] += 1
    return max(counts, key=lambda k: counts[k])


def _round_or_none(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


# ============================================================================
# Lookup aggregato nazionale
# ============================================================================

def build_aggregato(
    inquinanti_summary: dict,
    n_comuni: int,
    output_path: Path,
    shard_dir: Path,
) -> dict:
    """Costruisce lookup/aria-aggregato.json con stats nazionali e top comuni.

    Args:
        inquinanti_summary: dict per inquinante con n_stazioni, n_oltre_legge,
                            n_oltre_oms, media_nazionale.
        n_comuni: numero totale di comuni con almeno una stazione.
        output_path: dove scrivere lookup/aria-aggregato.json
        shard_dir: directory shard aria/<istat>.json (per estrarre top peggiori)
    """
    # Calcolo n_comuni effettivo per ogni inquinante (i.e. comuni con
    # almeno una stazione che misura quell'inquinante per ANNO_RIFERIMENTO).
    n_comuni_per_inquinante: dict[str, int] = {ink: 0 for ink in INQUINANTI_URLS.keys()}
    top_peggiori: dict[str, list[dict]] = {ink: [] for ink in INQUINANTI_URLS.keys()}

    for f in sorted(shard_dir.glob("*.json")):
        try:
            shard = json.loads(f.read_text())
        except Exception:
            continue
        ultimo = shard.get("ultimo_anno") or {}
        for ink in INQUINANTI_URLS.keys():
            blk = ultimo.get(ink)
            if blk and blk.get("media") is not None:
                n_comuni_per_inquinante[ink] += 1
                top_peggiori[ink].append({
                    "istat": shard["istat_code"],
                    "comune": shard.get("comune"),
                    "provincia": shard.get("provincia"),
                    "regione": shard.get("regione"),
                    "media": blk["media"],
                    "n_stazioni": blk["n_stazioni_con_dato"],
                })

    # Top 10 peggiori per ciascun inquinante
    for ink in top_peggiori:
        top_peggiori[ink] = sorted(top_peggiori[ink],
                                    key=lambda x: -x["media"])[:10]

    aggr = {
        "_etl_version": ETL_VERSION,
        "_source": "ISPRA SNPA - Qualita' dell'aria",
        "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_anno_dati": ANNO_RIFERIMENTO,
        "n_comuni_totali_coperti": n_comuni,
        "limiti_annuali": LIMITI_ANNUALI,
        "inquinanti": {
            ink: {
                "media_nazionale": inquinanti_summary[ink]["media_nazionale"],
                "n_stazioni": inquinanti_summary[ink]["n_stazioni"],
                "n_comuni": n_comuni_per_inquinante[ink],
                "n_stazioni_oltre_limite_legge": inquinanti_summary[ink]["n_oltre_legge"],
                "n_stazioni_oltre_limite_oms": inquinanti_summary[ink]["n_oltre_oms"],
                "limite_legge_ug_m3": LIMITI_ANNUALI[ink]["legge"],
                "limite_oms_ug_m3":   LIMITI_ANNUALI[ink]["oms"],
                "top_peggiori": top_peggiori[ink],
            }
            for ink in INQUINANTI_URLS.keys()
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(aggr, ensure_ascii=False, indent=2))
    log.info("aria_aggregato_written", path=str(output_path),
             bytes=output_path.stat().st_size)
    return aggr


# ============================================================================
# Push R2 (skip-by-md5)
# ============================================================================

def save_aggregato_local(aggr_path: Path) -> None:
    """Copia aria-aggregato.json in DATA_DIR/lookup/ (per Worker A1).

    Il file e' gia' stato scritto in output_dir/aria-aggregato.json da
    build_aggregato(). Qui lo copio anche in lookup_dir cosi' il Worker
    lo trova al path canonico data/lookup/aria-aggregato.json.
    """
    if not aggr_path.exists():
        log.warning("aria_aggregato_missing", path=str(aggr_path))
        return
    payload = json.loads(aggr_path.read_text(encoding="utf-8"))
    local_lookup.save_lookup("aria-aggregato.json", payload)
    log.info("aria_aggregato_saved_to_lookup",
             path=str(local_lookup.get_lookup_dir() / "aria-aggregato.json"))


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL ISPRA SNPA - Qualita' dell'aria per comune"
    )
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--outdir", default="/var/www/cruscotto-italia/data/aria",
                        help="Local output directory for shards (default: /var/www/cruscotto-italia/data/aria)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Re-download CSV ISPRA ignorando la cache locale")
    parser.add_argument("--limit", type=int, default=None,
                        help="(debug) processa solo N comuni")
    args = parser.parse_args()

    output_dir = Path(args.outdir)
    shard_dir = output_dir / "shards"
    aggr_path = output_dir / "aria-aggregato.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download dei 4 CSV
        log.info("aria_etl_start", inquinanti=list(INQUINANTI_URLS.keys()))
        csv_paths: dict[str, Path] = {}
        for ink, url in INQUINANTI_URLS.items():
            csv_paths[ink] = fetch_csv(ink, url, no_cache=args.no_cache)

        # 2. Parse di tutti
        data: dict[str, list[dict]] = {}
        for ink, p in csv_paths.items():
            data[ink] = parse_csv(p)

        # 3. Build shards
        log.info("aria_building_shards", output_dir=str(shard_dir))
        n_comuni, inquinanti_summary = build_shards(data, shard_dir)
        log.info("aria_shards_built", n_comuni=n_comuni)

        # 4. Build aggregato
        build_aggregato(inquinanti_summary, n_comuni, aggr_path, shard_dir)

        # 5. Copy aggregato a data/lookup/ per Worker A1
        save_aggregato_local(aggr_path)

        # Manifest update best-effort
        try:
            manifest.update_source(
                "aria",
                [
                    {"key": "lookup/aria-aggregato.json",
                     "size": aggr_path.stat().st_size},
                    {"key": "aria/*", "count": n_comuni},
                ],
                status="ok",
            )
            log.info("aria_manifest_updated")
        except Exception as e:
            log.warning("aria_manifest_update_skipped", error=str(e))

        log.info("aria_etl_done",
                 n_comuni=n_comuni,
                 aggregato_bytes=aggr_path.stat().st_size,
                 inquinanti_summary={
                     k: {"n_stazioni": v["n_stazioni"],
                         "media_nazionale": v["media_nazionale"]}
                     for k, v in inquinanti_summary.items()
                 })
        return 0

    except Exception as e:
        log.error("aria_etl_failed", error=str(e), error_type=type(e).__name__)
        raise


if __name__ == "__main__":
    sys.exit(main())
