"""ETL OMI — quotazioni immobiliari per zona omogenea, Agenzia delle Entrate.

Sorgente
--------
Agenzia delle Entrate — Osservatorio del Mercato Immobiliare (OMI).
Le quotazioni semestrali individuano, per ogni zona territoriale omogenea
(zona OMI) di ciascun comune, un intervallo minimo/massimo in euro al metro
quadro dei valori di mercato e di locazione, per tipologia immobiliare,
destinazione d'uso e stato di conservazione.

E' l'unica fonte di Cruscotto con granularita' SUB-comunale per il dato
economico: ~27.000 zone su ~7.890 comuni.

Acquisizione
------------
Due flussi distinti, entrambi da endpoint pubblici non autenticati del
servizio di consultazione `geopoi_omi`:

1. QUOTAZIONI — raccolte dal job `omi_build/omi_job.py` (non in questo
   modulo: ~98.000 richieste, ~10 ore, cadenza semestrale). Scrive il
   grezzo in /home/ubuntu/omi_build/shard/<istat>.json.
2. PERIMETRI — `perimetri.php?prov=XX&semestre=YYYYS&formato=kml`, un ZIP
   per provincia con un KML per comune (107 richieste). Convertiti in
   GeoJSON da `omi_build/omi_perimetri.py` in /tmp/omi_geojson/.

Questo modulo NON scarica: legge i due stadi grezzi, normalizza e scrive
gli shard di produzione. La raccolta e' separata perche' dura ore e va
ripetuta solo a ogni nuovo semestre.

Calendario di pubblicazione AE (normato):
  entro il 15 marzo   -> 2o semestre dell'anno precedente
  entro il 15 ottobre -> 1o semestre dell'anno in corso

Licenza
-------
CC-BY 4.0 dichiarata esplicitamente dal titolare sulla pagina "Forniture
dati OMI" dell'Agenzia delle Entrate (verificata il 03/10/2026, snapshot
archiviato in deploysimbacruscotto/docs/omi/snapshot_2026-10-03).
Attribuzione obbligatoria: "Agenzia delle Entrate - OMI".
NB: diversa da agcom_bbmap, che si appoggia al default art. 52 c.2 CAD.

Allineamento anagrafiche
------------------------
Le anagrafiche AE e ISTAT si aggiornano in momenti diversi: alla raccolta
del 03/10/2026 risultavano 1 comune OMI non mappabile (M439 Castegnero
Nanto, fuso il 21/02/2026 e non ancora in anagrafica Cruscotto), 5 comuni
con quotazioni ma senza perimetro e 3 con perimetro ma senza quotazioni.
Sono scostamenti fisiologici: lo shard regge entrambi i casi parziali.

Schema omi/<istat>.json
-----------------------
{
  "_etl_version": "0.1.0",
  "_source": "Agenzia delle Entrate - Osservatorio del Mercato Immobiliare",
  "_source_url": "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/",
  "_license": "CC-BY 4.0",
  "_attribution": "Agenzia delle Entrate - OMI",
  "_data_period": "2025/2",
  "_generated_at": "ISO-8601",
  "istat_code": str, "comune": str, "provincia": str, "regione": str,
  "codcom": str,                      # codice catastale (Belfiore)
  "kpi": {
    "n_zone": int,
    "ha_perimetri": bool,             # esiste omi_full/<istat>.geojson
    "residenziale_min": float|null,   # su tipologie abitative, esclusi box/posti auto
    "residenziale_max": float|null,
    "residenziale_medio": float|null,
    "sup_mista": bool,                # true se convivono superficie L e N:
                                      # i euro/mq NON sono confrontabili fra zone
    "zona_piu_cara": {"zona","dizione","valore"}|null,
    "zona_piu_economica": {...}|null
  },
  "zone": [{
    "zona": str,                      # es. "B3" — chiave di join col GeoJSON
    "fascia": str,                    # B centrale, C semicentrale, D periferica,
                                      # E suburbana, R extraurbana
    "dizione": str,                   # toponimo: "CENTRO STORICO"
    "link_zona": str,                 # id AE, per il deep link
    "url_omi": str,                   # pagina ufficiale della zona su AE
    "destinazioni": {
      "Residenziale"|"Commerciale"|"Produttiva"|"Terziaria": [{
        "tipologia": str, "stato": str,        # chiave di riga = tipologia+stato
        "cv_min": float|null, "cv_max": float|null, "sup_cv": "L"|"N",
        "loc_min": float|null, "loc_max": float|null, "sup_loc": "L"|"N"
      }]
    }
  }]
}

Schema omi_full/<istat>.geojson
-------------------------------
FeatureCollection, WGS84, coordinate a 5 decimali (~1 m).
properties: {codcom, zona, nome}. Join con lo shard su "zona".
Geometrie Polygon o MultiPolygon (con anelli interni dove presenti).

Avvertenza d'uso
----------------
Le quotazioni OMI forniscono indicazioni di valore di larga massima e non
sostituiscono la stima puntuale di un tecnico professionista. Nei comuni
con carente dinamica di mercato derivano da indagine indiretta.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

SOURCE_LABEL = "Agenzia delle Entrate - Osservatorio del Mercato Immobiliare"
SOURCE_URL = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/"
LICENSE = "CC-BY 4.0"
ATTRIBUTION = "Agenzia delle Entrate - OMI"

DEFAULT_RAWDIR = Path("/home/ubuntu/omi_build/shard")
DEFAULT_GEODIR = Path("/tmp/omi_geojson")
DEFAULT_OUTDIR = Path("/var/www/cruscotto-italia/data/omi")
DEFAULT_FULLDIR = Path("/var/www/cruscotto-italia/data/omi_full")

# Sotto questa soglia qualcosa e' andato storto nella raccolta: non si
# sovrascrive la produzione con un set parziale.
MIN_COMUNI = 7000

# Precisione coordinate: 5 decimali ~ 1 m. Riduce il peso di circa un terzo
# senza effetti visibili su mappa. NON si semplificano le geometrie, che
# altererebbero i confini delle zone.
GEO_DECIMALI = 5

# Comuni campione per il log diagnostico (Roma, Matera, Lecce, Morterone).
SAMPLE = ["058091", "077014", "075035", "097055"]


def semestre_leggibile(sem: str) -> str:
    """20252 -> '2025/2'."""
    return f"{sem[:4]}/{sem[4:]}" if len(sem) == 5 else sem


def arrotonda(coords, nd: int = GEO_DECIMALI):
    """Arrotonda ricorsivamente le coordinate di una geometria GeoJSON."""
    if isinstance(coords, (int, float)):
        return round(coords, nd)
    return [arrotonda(c, nd) for c in coords]


def write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_shard(raw: dict, ha_perimetri: bool, now_iso: str) -> dict:
    """Dal grezzo del job allo shard di produzione: aggiunge i metadati
    di convenzione e allinea il flag ha_perimetri allo stato reale."""
    kpi = dict(raw.get("kpi") or {})
    kpi["ha_perimetri"] = ha_perimetri
    return {
        "_etl_version": ETL_VERSION,
        "_source": SOURCE_LABEL,
        "_source_url": SOURCE_URL,
        "_license": LICENSE,
        "_attribution": ATTRIBUTION,
        "_data_period": semestre_leggibile(raw.get("semestre", "")),
        "_generated_at": now_iso,
        "istat_code": raw["istat_code"],
        "comune": raw.get("comune"),
        "provincia": raw.get("provincia"),
        "regione": raw.get("regione"),
        "codcom": raw.get("codcom"),
        "kpi": kpi,
        "zone": raw.get("zone") or [],
    }


def build_full(geo: dict, istat: str, now_iso: str) -> dict:
    """GeoJSON dei perimetri, coordinate ridotte a GEO_DECIMALI."""
    feats = []
    for f in geo.get("features") or []:
        g = f.get("geometry") or {}
        if not g.get("coordinates"):
            continue
        feats.append({
            "type": "Feature",
            "properties": f.get("properties") or {},
            "geometry": {"type": g["type"],
                         "coordinates": arrotonda(g["coordinates"])},
        })
    return {
        "type": "FeatureCollection",
        "_source": SOURCE_LABEL,
        "_license": LICENSE,
        "_attribution": ATTRIBUTION,
        "_istat": istat,
        "_n_zone": len(feats),
        "_generated_at": now_iso,
        "features": feats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL OMI — quotazioni immobiliari per zona omogenea")
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--rawdir", type=Path, default=DEFAULT_RAWDIR,
                        help=f"Grezzo quotazioni (default: {DEFAULT_RAWDIR})")
    parser.add_argument("--geodir", type=Path, default=DEFAULT_GEODIR,
                        help=f"Grezzo perimetri GeoJSON (default: {DEFAULT_GEODIR})")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output shard (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--fulldir", type=Path, default=DEFAULT_FULLDIR,
                        help=f"Output perimetri (default: {DEFAULT_FULLDIR})")
    parser.add_argument("--solo", metavar="ISTAT",
                        help="Processa un solo comune (prototipazione frontend)")
    parser.add_argument("--force", action="store_true",
                        help="Prosegue anche sotto la soglia minima di comuni")
    args = parser.parse_args()

    log.info("etl_omi_start", version=ETL_VERSION)

    if not args.rawdir.is_dir():
        log.error("omi_rawdir_missing", path=str(args.rawdir))
        return 2

    raw_files = sorted(args.rawdir.glob("*.json"))
    if args.solo:
        raw_files = [f for f in raw_files if f.stem == args.solo]
        if not raw_files:
            log.error("omi_solo_not_found", istat=args.solo)
            return 2
    elif len(raw_files) < MIN_COMUNI and not args.force:
        log.error("omi_too_few_comuni", got=len(raw_files), expected_min=MIN_COMUNI,
                  hint="raccolta incompleta o ancora in corso; --force per forzare")
        return 2

    now_iso = datetime.now(timezone.utc).isoformat()
    n_shard = n_full = n_zone = 0
    senza_perimetro: list[str] = []
    periodo = ""

    for f in raw_files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("omi_raw_unreadable", file=f.name, error=str(e))
            continue

        istat = raw.get("istat_code") or f.stem
        geo_path = args.geodir / f"{istat}.geojson"
        ha_perimetri = geo_path.is_file()

        if ha_perimetri:
            try:
                full = build_full(json.loads(geo_path.read_text(encoding="utf-8")),
                                  istat, now_iso)
                write_atomic(args.fulldir / f"{istat}.geojson", full)
                n_full += 1
            except Exception as e:
                log.warning("omi_full_failed", istat=istat, error=str(e))
                ha_perimetri = False
        else:
            senza_perimetro.append(istat)

        shard = build_shard(raw, ha_perimetri, now_iso)
        periodo = periodo or shard["_data_period"]

        if args.outdir == DEFAULT_OUTDIR:
            local_lookup.save_shard("omi", istat, shard)
        else:
            write_atomic(args.outdir / f"{istat}.json", shard)

        n_shard += 1
        n_zone += len(shard["zone"])

        if istat in SAMPLE:
            k = shard["kpi"]
            log.info("sample", istat=istat, comune=shard["comune"],
                     n_zone=k.get("n_zone"),
                     res_min=k.get("residenziale_min"),
                     res_max=k.get("residenziale_max"),
                     sup_mista=k.get("sup_mista"),
                     perimetri=k.get("ha_perimetri"))

    if senza_perimetro:
        log.warning("omi_senza_perimetro", n=len(senza_perimetro),
                    sample=sorted(senza_perimetro)[:5])

    log.info("omi_written", shard=n_shard, full=n_full, zone=n_zone,
             periodo=periodo)

    if not args.solo:
        try:
            manifest.update_source(
                "omi",
                [{"key": "omi/*", "count": n_shard, "n_zone": n_zone,
                  "periodo": periodo},
                 {"key": "omi_full/*", "count": n_full}],
                status="ok",
            )
        except Exception as e:
            log.warning("manifest_update_skipped", error=str(e))

    log.info("etl_omi_done", comuni=n_shard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
