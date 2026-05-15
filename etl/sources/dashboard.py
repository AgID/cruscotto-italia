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
- agcom_bbmap/<istat>.json       (AGCOM - Broadband Map, copertura banda larga)
- carburanti/<istat>.json        (MIMIT - Osservatorio Prezzi Carburanti, anagrafica impianti + prezzi praticati)
- runts/<istat>.json             (Min. Lavoro - Registro Unico Nazionale Terzo Settore, enti iscritti)
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
    ("agcom_bbmap","agcom_bbmap/{istat}.json"),
    ("carburanti", "carburanti/{istat}.json"),
    ("runts",      "runts/{istat}.json"),
    ("asia",       "asia/{istat}.json"),
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


def _safe(d: dict | None, *path, default=None):
    """Accesso sicuro a nested dict: _safe(d, 'a', 'b', 'c', default=0)."""
    cur = d
    for k in path:
        if cur is None or not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _per_1000(n, pop):
    """Calcola N per 1000 abitanti (None se pop<=0 o n None)."""
    if n is None or pop is None or pop <= 0:
        return None
    return round(n * 1000 / pop, 2)


def _per_abitante(eur, pop):
    """Calcola euro per abitante (None se pop<=0 o eur None)."""
    if eur is None or pop is None or pop <= 0:
        return None
    return round(eur / pop, 2)


def compute_kpi_summary(out: dict) -> dict:
    """Estrae ~55 KPI sintetici dalle sezioni del shard A1.

    Nessun array, nessun time series, nessun top_N. Solo numeri.
    ~2.5KB minificato. Schema stabile con null espliciti.
    """
    ana = out.get("anagrafica") or {}
    demo = out.get("demografia") or {}
    redditi = out.get("redditi") or {}
    profilo = out.get("profilo") or {}
    scuole = out.get("scuole") or {}
    anac = out.get("anac") or {}
    bdap_kpi = out.get("bdap_kpi") or {}
    pnrr = out.get("pnrr") or {}
    siope = out.get("siope") or {}
    immobili = out.get("immobili_pa") or {}
    territorio = out.get("territorio") or {}
    aria = out.get("aria") or {}
    turismo = out.get("turismo") or {}
    veicoli = out.get("veicoli") or {}
    agcom = out.get("agcom_bbmap") or {}
    pun = out.get("pun") or {}
    carburanti = out.get("carburanti") or {}
    anncsu = out.get("anncsu") or {}
    runts = out.get("runts") or {}
    sanita = out.get("sanita_mds") or {}
    asia = out.get("asia") or {}

    # Popolazione (riferimento per molti pro-capite/per-1000)
    pop = _safe(demo, "popolazione_totale")

    # Reddito: estrai ultimo anno disponibile
    anni_red = redditi.get("anni_disponibili") or []
    ultimo_anno_red = max(anni_red) if anni_red else None
    dati_red = _safe(redditi, "anni", str(ultimo_anno_red)) if ultimo_anno_red else None

    # SIOPE: anno default
    anno_siope = siope.get("anno_default")
    siope_anno = _safe(siope, "per_anno", str(anno_siope)) if anno_siope else None

    # ANAC: importo totale (può essere float DuckDB)
    anac_importo = anac.get("importo_totale")
    anac_importo_int = int(anac_importo) if anac_importo is not None else None

    # BDAP: totale aggregato (somma dei finanziamenti = importo opera)
    bdap_totale = bdap_kpi.get("totale") or {}
    bdap_importo = (
        (bdap_totale.get("finanz_statali") or 0)
        + (bdap_totale.get("finanz_europei") or 0)
        + (bdap_totale.get("finanz_enti_terr") or 0)
        + (bdap_totale.get("finanz_privati") or 0)
        + (bdap_totale.get("finanz_altri") or 0)
    ) if bdap_kpi else None
    bdap_importo_int = int(bdap_importo) if bdap_importo else None

    # Sanità: n_farmacie, n_parafarmacie, n_ospedali, posti_letto
    n_farmacie = _safe(sanita, "farmacie", "kpi", "n_totale")
    n_parafarmacie = _safe(sanita, "parafarmacie", "kpi", "n_totale")
    n_ospedali = _safe(sanita, "ospedali", "kpi", "n_stabilimenti")
    posti_letto = _safe(sanita, "ospedali", "kpi", "posti_letto_totali")

    summary = {
        "anagrafica": {
            "istat": ana.get("istat_code"),
            "nome": ana.get("denominazione"),
            "provincia_sigla": ana.get("provincia"),
            "regione": ana.get("regione"),
            "codice_fiscale": ana.get("codice_fiscale"),
            "codice_catastale": ana.get("codice_catastale"),
        },
        "demografia": {
            "popolazione": pop,
            "maschi": demo.get("maschi"),
            "femmine": demo.get("femmine"),
            "eta_media": demo.get("eta_media"),
            "indice_vecchiaia": demo.get("indice_vecchiaia"),
            "indice_dipendenza": demo.get("indice_dipendenza"),
            "riferimento": demo.get("_riferimento"),
        },
        "istruzione_profilo": {
            "anno": _safe(profilo, "istruzione", "anno"),
            "pct_terziario": _safe(profilo, "istruzione", "terziario_pct"),
            "pct_diploma_oltre": _safe(profilo, "istruzione", "diploma_oltre_pct"),
        },
        "lavoro_profilo": {
            "anno": _safe(profilo, "lavoro", "anno"),
            "tasso_occupazione": _safe(profilo, "lavoro", "tasso_occupazione"),
            "tasso_disoccupazione": _safe(profilo, "lavoro", "tasso_disoccupazione"),
            "tasso_attivita": _safe(profilo, "lavoro", "tasso_attivita"),
        },
        "redditi_mef": {
            "anno_fiscale": ultimo_anno_red,
            "n_contribuenti": _safe(dati_red, "contribuenti"),
            "reddito_medio_eur": _safe(dati_red, "reddito_complessivo", "medio"),
            "imposta_netta_media_eur": _safe(dati_red, "imposta_netta", "medio"),
        },
        "scuole_miur": {
            "n_scuole": _safe(scuole, "kpi", "n_scuole"),
            "anno_scolastico": scuole.get("anno_scolastico"),
            "scuole_per_1000_ab": _per_1000(_safe(scuole, "kpi", "n_scuole"), pop),
        },
        "contratti_anac": {
            "n_aggiudicazioni": anac.get("count"),
            "importo_totale_eur": anac_importo_int,
            "importo_per_abitante_eur": _per_abitante(anac_importo_int, pop),
            "ultima_aggiudicazione": (anac.get("last_award_date") or "").split(" ")[0] if anac.get("last_award_date") else None,
        },
        "opere_bdap": {
            "n_progetti": bdap_totale.get("count") if bdap_kpi else None,
            "importo_totale_eur": bdap_importo_int,
            "importo_per_abitante_eur": _per_abitante(bdap_importo_int, pop),
        },
        "pnrr": {
            "n_progetti": _safe(pnrr, "kpi", "n_progetti"),
            "n_concluso": _safe(pnrr, "kpi", "n_concluso"),
            "n_in_corso": _safe(pnrr, "kpi", "n_in_corso"),
            "importo_assegnato_eur": _safe(pnrr, "kpi", "totale_finanziamento_pnrr"),
            "importo_per_abitante_eur": _per_abitante(_safe(pnrr, "kpi", "totale_finanziamento_pnrr"), pop),
        },
        "siope": {
            "anno": anno_siope,
            "totale_uscite_eur": _safe(siope_anno, "totale_anno"),
            "uscite_per_abitante_eur": _per_abitante(_safe(siope_anno, "totale_anno"), pop),
        },
        "patrimonio_pa": {
            "n_immobili": _safe(immobili, "kpi", "n_totale"),
            "n_fabbricati": _safe(immobili, "kpi", "n_fabbricati"),
            "n_terreni": _safe(immobili, "kpi", "n_terreni"),
            "superficie_totale_mq": _safe(immobili, "kpi", "superficie_totale_mq"),
        },
        "ambiente": {
            "superficie_kmq": _safe(territorio, "kpi", "ar_kmq"),
            "consumo_suolo_pct": _safe(territorio, "kpi", "suolo_consumato_2024_pct"),
            "raccolta_differenziata_pct": _safe(territorio, "kpi", "rd_pct_ultimo_anno"),
            "rifiuti_kg_per_abitante": _safe(territorio, "kpi", "kg_per_abitante_ultimo_anno"),
        },
        "aria_ispra": {
            "ha_stazione": (aria.get("n_stazioni") or 0) > 0,
            "anno": _safe(aria, "ultimo_anno", "anno"),
            "pm10_media": _safe(aria, "ultimo_anno", "pm10", "media"),
            "pm25_media": _safe(aria, "ultimo_anno", "pm25", "media"),
            "no2_media": _safe(aria, "ultimo_anno", "no2", "media"),
        },
        "turismo": {
            "anno": _safe(turismo, "capacita_comune", "anno"),
            "totale_strutture": _safe(turismo, "capacita_comune", "totale_strutture"),
            "totale_letti": _safe(turismo, "capacita_comune", "totale_letti"),
            "indice_turisticita_per_100ab": _safe(turismo, "capacita_comune", "indice_turisticita_per_100ab"),
        },
        "veicoli_aci": {
            "anno": _safe(veicoli, "parco_veicoli", "anno"),
            "totale_veicoli": _safe(veicoli, "parco_veicoli", "totale"),
            "autovetture": _safe(veicoli, "parco_veicoli", "autovetture"),
            "tasso_motorizzazione_per_1000_ab": _safe(veicoli, "parco_veicoli", "tasso_motorizzazione_per_1000_ab"),
            "pct_inquinanti": _safe(veicoli, "parco_veicoli", "euro", "pct_inquinanti"),
        },
        "banda_larga_agcom": {
            "famiglie_residenti": _safe(agcom, "kpi", "famiglie_residenti"),
            "copertura_ftth_pct": _safe(agcom, "kpi", "copertura_ftth_desi_pct"),
            "copertura_ftth_20m_pct": _safe(agcom, "kpi", "copertura_ftth_20m_pct"),
            "data_rilevazione": agcom.get("_data_period"),
        },
        "ricarica_ev_pun": {
            "n_totale": _safe(pun, "kpi", "n_totale"),
            "n_attivi": _safe(pun, "kpi", "n_attivi"),
            "pct_attivi": _safe(pun, "kpi", "pct_attivi"),
            "potenza_totale_kw": _safe(pun, "kpi", "potenza_tot_kw"),
            "punti_per_1000_ab": _per_1000(_safe(pun, "kpi", "n_attivi"), pop),
        },
        "carburanti_mimit": {
            "n_impianti": _safe(carburanti, "kpi", "n_impianti"),
            "n_pompe_bianche": _safe(carburanti, "kpi", "n_pompe_bianche"),
            "prezzo_medio_benzina_self": _safe(carburanti, "kpi", "prezzo_medio", "benzina_self"),
            "prezzo_medio_gasolio_self": _safe(carburanti, "kpi", "prezzo_medio", "gasolio_self"),
            "impianti_per_1000_ab": _per_1000(_safe(carburanti, "kpi", "n_impianti"), pop),
        },
        "civici_anncsu": {
            "n_strade": _safe(anncsu, "kpi", "n_strade"),
            "n_civici": _safe(anncsu, "kpi", "n_civici"),
            "pct_geo_ref": _safe(anncsu, "kpi", "pct_geo_ref"),
            "snapshot_date": anncsu.get("_snapshot_date"),
        },
        "terzo_settore_runts": {
            "n_enti_totali": _safe(runts, "kpi", "n_totale"),
            "n_5x1000": _safe(runts, "kpi", "n_5x1000"),
            "pct_5x1000": _safe(runts, "kpi", "pct_5x1000"),
            "enti_per_1000_ab": _per_1000(_safe(runts, "kpi", "n_totale"), pop),
            "snapshot_date": runts.get("_snapshot_date"),
        },
        "imprese_asia": {
            "anno": asia.get("_latest_year"),
            "ul_totali": _safe(asia, "kpi", "ul_totali"),
            "addetti_totali": _safe(asia, "kpi", "addetti_totali"),
            "addetti_per_ul": _safe(asia, "kpi", "addetti_per_ul"),
            "ul_yoy_pct": _safe(asia, "kpi", "ul_yoy_pct"),
            "ul_per_1000_ab": _per_1000(_safe(asia, "kpi", "ul_totali"), pop),
        },
        "sanita_mds": {
            "n_farmacie": n_farmacie,
            "n_parafarmacie": n_parafarmacie,
            "n_ospedali": n_ospedali,
            "posti_letto_ospedalieri": posti_letto,
            "farmacie_per_1000_ab": _per_1000(n_farmacie, pop),
        },
    }
    return summary


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

    # KPI summary leggero (~2.5KB) per tool comune_kpi MCP.
    # Computato DOPO che tutte le sezioni sono state popolate.
    out["kpi_summary"] = compute_kpi_summary(out)

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
                _istat, missing = fut.result()
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
