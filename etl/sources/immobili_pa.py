"""ETL Immobili PA - MEF Dipartimento Economia (Censimento Beni Immobili Pubblici).

[FASE A - PROTOTIPO] Solo amministrazioni comunali, una regione alla volta,
output locale. R2 push + altre PA (SSN, Universita, ATER) in fasi successive.

Dataset upstream: MEF DE - Dati beni immobili al 31/12/2022.
URL pattern bulk (header User-Agent + Referer obbligatori):
  https://www.de.mef.gov.it/modules/documenti_it/attivo_patrimonio/
    immobili_2022/opendata_imm/Imm_Amministrazioni_Comunali_<REGIONE>_2022.zip

Pubblicazione: 24/03/2025. Riferimento dati: 31/12/2022.
Licenza: CC BY 4.0. Encoding CSV: ISO-8859-1. Separatore: ';'. Decimali: ','.

Strategia Fase A:
  1) Download 1 ZIP regionale (default Valle d'Aosta - regione piccola)
  2) Parse CSV (51 colonne, dtype=str per stabilita)
  3) Lookup Belfiore -> ISTAT da bundle R2 (riusa load_cat_to_istat di scuole.py)
  4) Categorizza Tipologia Bene Immobile -> macro_categoria
  5) Aggrega per ISTAT: KPI + lista punti georeferenziati
  6) Output locale immobili_pa/<istat>.json

Note campi MEF:
  - 'Vinc. culturale/paesaggistico' non e' booleano ma tassonomia di 8 valori
    ('Nessuno', 'Area tutelata per legge - interesse paesaggistico',
     'Dichiarazione di interesse culturale', ...). Vincolo presente se
    valore != 'Nessuno' e non vuoto.
  - 'ui data interamente/parzialmente a terzi' sono booleani 'Si'/'No'.
  - Codice Comune del bene = codice Belfiore (catastale), non ISTAT.

Schema output per shard comune:
{
  "_etl_version": "0.1.0-fase-a",
  "_source": "MEF DE - Beni Immobili Pubblici 2022",
  "_generated_at": "ISO-8601",
  "anno_rilevazione": 2022,
  "kpi": {
    "n_totale": 247,
    "n_fabbricati": 67,
    "n_terreni": 180,
    "pct_geo_referenziati": 98.1,
    "pct_vincolo_qualsiasi": 27.2,
    "pct_vincolo_culturale": 4.1,
    "pct_uso_terzi": 28.7,
    "superficie_totale_mq": 4250000,
    "mix_categoria": {"fabbricati_residenziali": 18, ...},
    "mix_natura": {"FABBRICATO": 67, "TERRENO": 180}
  },
  "punti": [
    {"lat": 45.81, "lon": 7.27, "cat": "fabbricati_residenziali",
     "tipo": "Abitazione", "sup": 85, "vincolo": true, "uso_terzi": false}
  ]
}

Usage Fase A:
  python -m etl.sources.immobili_pa --regione=VALLE-D_AOSTA --outdir=dist/immobili_pa
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import r2

# Riuso lookup canonica e client R2 canonico
from etl.sources.scuole import load_cat_to_istat

log = structlog.get_logger()

ETL_VERSION = "0.1.0-fase-a"
ANNO_RILEVAZIONE = 2022

CACHE_DIR = Path("/tmp/cruscotto-immobili-pa-cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = (
    "https://www.de.mef.gov.it/modules/documenti_it/attivo_patrimonio/"
    "immobili_2022/opendata_imm"
)
REFERER = (
    "https://www.de.mef.gov.it/it/attivita_istituzionali/"
    "patrimonio_pubblico/censimento_immobili_pubblici/"
    "open_data_immobili/dati_immobili_2022.html"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip,*/*",
    "Accept-Encoding": "gzip, deflate",
    "Referer": REFERER,
}

REGIONI_VALIDE = [
    "ABRUZZO", "BASILICATA", "CALABRIA", "CAMPANIA", "EMILIA-ROMAGNA",
    "FRIULI-VENEZIA-GIULIA", "LAZIO", "LIGURIA", "LOMBARDIA", "MARCHE",
    "MOLISE", "PIEMONTE", "PUGLIA", "SARDEGNA", "SICILIA", "TOSCANA",
    "TRENTINO-ALTO-ADIGE", "UMBRIA", "VALLE-D_AOSTA", "VENETO",
]

# === Macro-categorizzazione tipologie ===
# Mappa parole-chiave -> macro categoria. Match case-insensitive su Tipologia Bene Immobile.
# L'ordine conta: pattern piu' specifici vanno prima dei generici.
CATEGORIA_PATTERNS = [
    # SOCIALI
    (r"scolastic|scuola|universit|asilo|nido", "fabbricati_sociali_scolastici"),
    (r"ospedal|ambulatori|sanitari|RSA|poliambulator|consultori", "fabbricati_sociali_sanitari"),
    (r"impianto sportivo|palestra|piscina|stadio|palazzetto", "fabbricati_sociali_sportivi"),
    (r"bibliotec|museo|teatr|pinacotec|gallerie|culturale", "fabbricati_sociali_culturali"),
    # AMMINISTRATIVI
    (r"caserm|carcere|tribunal|prefettura|questura", "fabbricati_amministrativi_sicurezza"),
    (r"ufficio|sede|municipi|palazzo comunale", "fabbricati_amministrativi_uffici"),
    # RESIDENZIALI / PERTINENZE
    (r"abitazione|residenz", "fabbricati_residenziali"),
    (r"cantina|soffitta|rimessa|box|garage|posto auto", "fabbricati_pertinenze"),
    # INFRASTRUTTURE / PRODUTTIVI
    (r"magazzin|deposit", "fabbricati_magazzini"),
    (r"parcheggio", "fabbricati_parcheggi"),
    (r"produttiv|industrial|artigian|fabbric", "fabbricati_produttivi"),
    # TERRENI
    (r"terreno agricolo|terreni agricoli", "terreni_agricoli"),
    (r"boscat|bosco|vegetazione|forest", "terreni_boschivi"),
    (r"pascol", "terreni_pascolo"),
    (r"terreno urbano|area urbana", "terreni_urbani"),
    (r"parco|villa comunale|giardino|riserve naturali", "terreni_parchi_pubblici"),
]


def categorize(tipologia: str) -> str:
    """Mappa tipologia raw MEF -> macro categoria. Fallback 'altro'."""
    if not tipologia:
        return "altro"
    t = tipologia.lower()
    for pattern, cat in CATEGORIA_PATTERNS:
        if re.search(pattern, t):
            return cat
    return "altro"


def normalize_tipologia(s: str) -> str:
    """Comprime spazi e newline interni (i CSV MEF hanno valori multi-riga)."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def parse_num_it(s) -> float | None:
    """Parsa numero italiano (decimale con virgola). None se non valido."""
    if s is None or s == "":
        return None
    try:
        return float(str(s).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None


def download_zip(regione: str, force: bool = False) -> Path:
    """Scarica lo ZIP regionale MEF, cache su disco."""
    fname = f"Imm_Amministrazioni_Comunali_{regione}_2022.zip"
    local = CACHE_DIR / fname
    if local.exists() and not force:
        log.info("immobili_zip_cached", regione=regione, size=local.stat().st_size)
        return local

    url = f"{BASE_URL}/{fname}"
    log.info("immobili_zip_downloading", regione=regione, url=url)
    t0 = time.time()
    r = requests.get(url, headers=HEADERS, timeout=180, stream=True)
    r.raise_for_status()
    with open(local, "wb") as f:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)
    elapsed = time.time() - t0
    log.info("immobili_zip_downloaded", regione=regione,
             size=local.stat().st_size, seconds=round(elapsed, 1))
    return local


def parse_csv_from_zip(zip_path: Path):
    """Yield dict per ciascuna riga del CSV dentro lo ZIP."""
    import csv
    import io
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        # Si assume 1 solo CSV per ZIP (confermato in verifica)
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        log.info("immobili_csv_parsing", file=csv_name)
        with zf.open(csv_name) as raw:
            text = io.TextIOWrapper(raw, encoding="iso-8859-1", newline="")
            reader = csv.DictReader(text, delimiter=";")
            yield from reader


def build_shards(zip_path: Path, cat_to_istat: dict[str, str]) -> dict[str, dict]:
    """Costruisce un dict istat -> shard_data dal CSV regionale."""
    by_istat: dict[str, list[dict]] = {}
    unmatched_belfiore: set[str] = set()
    total_rows = 0

    for row in parse_csv_from_zip(zip_path):
        total_rows += 1
        cat_bene = (row.get("Codice Comune del bene") or "").strip().upper()
        if not cat_bene:
            continue
        istat = cat_to_istat.get(cat_bene)
        if not istat:
            unmatched_belfiore.add(cat_bene)
            continue

        tipologia = normalize_tipologia(row.get("Tipologia Bene Immobile") or "")
        categoria = categorize(tipologia)
        natura = (row.get("Natura del bene") or "").strip().upper()
        sup = parse_num_it(row.get("Superficie (mq)"))
        lat = parse_num_it(row.get("Latitudine"))
        lon = parse_num_it(row.get("Longitudine"))
        geo_ref = (row.get("Immobile Geo-Ref.") or "").strip().lower() == "sì"

        # Vincolo: tassonomia di 8 valori, non booleano
        vincolo_raw = (row.get("Vinc. culturale/paesaggistico") or "").strip()
        vincolo = bool(vincolo_raw) and vincolo_raw.lower() != "nessuno"
        vincolo_culturale_stretto = ("interesse culturale" in vincolo_raw.lower()
                                      or "tutela" in vincolo_raw.lower())

        uso_intero = (row.get("ui data interamente a terzi") or "").strip().lower() == "sì"
        uso_parz = (row.get("ui data parzialmente a terzi") or "").strip().lower() == "sì"
        uso_terzi = uso_intero or uso_parz

        entry = {
            "natura": natura,
            "categoria": categoria,
            "tipologia": tipologia,
            "sup_mq": sup,
            "lat": lat,
            "lon": lon,
            "geo_ref": geo_ref,
            "vincolo": vincolo,
            "vincolo_culturale_stretto": vincolo_culturale_stretto,
            "uso_terzi": uso_terzi,
        }
        by_istat.setdefault(istat, []).append(entry)

    if unmatched_belfiore:
        log.warning("immobili_belfiore_unmatched",
                    n=len(unmatched_belfiore),
                    sample=sorted(unmatched_belfiore)[:5])

    log.info("immobili_parsed", total_rows=total_rows,
             comuni_with_data=len(by_istat))

    # Aggrega in shard finali
    shards: dict[str, dict] = {}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for istat, items in by_istat.items():
        n_tot = len(items)
        n_fab = sum(1 for x in items if x["natura"] == "FABBRICATO")
        n_ter = sum(1 for x in items if x["natura"] == "TERRENO")
        n_geo = sum(1 for x in items if x["geo_ref"])
        n_vin = sum(1 for x in items if x["vincolo"])
        n_vin_culturale = sum(1 for x in items if x.get("vincolo_culturale_stretto"))
        n_terzi = sum(1 for x in items if x["uso_terzi"])
        sup_tot = sum(x["sup_mq"] for x in items if x["sup_mq"]) or 0

        mix_categoria: dict[str, int] = {}
        mix_natura: dict[str, int] = {}
        for x in items:
            mix_categoria[x["categoria"]] = mix_categoria.get(x["categoria"], 0) + 1
            mix_natura[x["natura"]] = mix_natura.get(x["natura"], 0) + 1

        # Punti su mappa: solo quelli effettivamente georeferenziati con lat/lon validi
        # Cap a 500 per comune (sample) per evitare shard giganti su comuni capoluogo
        punti_validi = [
            {
                "lat": round(x["lat"], 6),
                "lon": round(x["lon"], 6),
                "cat": x["categoria"],
                "tipo": x["tipologia"],
                "sup": x["sup_mq"],
                "vincolo": x["vincolo"],
                "uso_terzi": x["uso_terzi"],
            }
            for x in items
            if x["geo_ref"] and x["lat"] is not None and x["lon"] is not None
        ]
        if len(punti_validi) > 500:
            # Sampling stratificato per categoria, deterministico
            from collections import defaultdict
            grouped: dict[str, list[dict]] = defaultdict(list)
            for p in punti_validi:
                grouped[p["cat"]].append(p)
            sampled = []
            quota = max(1, 500 // max(1, len(grouped)))
            for _cat, ps in grouped.items():
                sampled.extend(ps[:quota])
            punti_validi = sampled[:500]

        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": "MEF DE - Beni Immobili Pubblici 2022",
            "_generated_at": generated_at,
            "anno_rilevazione": ANNO_RILEVAZIONE,
            "kpi": {
                "n_totale": n_tot,
                "n_fabbricati": n_fab,
                "n_terreni": n_ter,
                "pct_geo_referenziati": round(100 * n_geo / n_tot, 1) if n_tot else 0.0,
                "pct_vincolo_qualsiasi": round(100 * n_vin / n_tot, 1) if n_tot else 0.0,
                "pct_vincolo_culturale": round(100 * n_vin_culturale / n_tot, 1) if n_tot else 0.0,
                "pct_uso_terzi": round(100 * n_terzi / n_tot, 1) if n_tot else 0.0,
                "superficie_totale_mq": round(sup_tot, 1),
                "mix_categoria": dict(sorted(mix_categoria.items(),
                                             key=lambda kv: -kv[1])),
                "mix_natura": mix_natura,
            },
            "punti": punti_validi,
        }

    return shards


def write_local(shards: dict[str, dict], outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, data in shards.items():
        with open(outdir / f"{istat}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        n += 1
    log.info("immobili_local_done", n=n, outdir=str(outdir))
    return n


def push_to_r2_parallel(shards: dict[str, dict]) -> int:
    """Upload shard immobili_pa/<istat>.json su R2 in parallelo."""
    log.info("immobili_pushing", n=len(shards))
    client = r2.get_r2_client()
    bucket = r2.get_bucket()

    def upload(istat: str, data: dict) -> bool:
        try:
            client.put_object(
                Bucket=bucket,
                Key=f"immobili_pa/{istat}.json",
                Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json; charset=utf-8",
            )
            return True
        except Exception as e:
            log.error("upload_failed", istat=istat, error=str(e))
            return False

    uploaded = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(upload, istat, data) for istat, data in shards.items()]
        for f in as_completed(futures):
            if f.result():
                uploaded += 1
    log.info("immobili_push_done", uploaded=uploaded)
    return uploaded


def main() -> int:
    p = argparse.ArgumentParser(description="ETL Immobili PA (MEF) - Fase B")
    p.add_argument("--regione", default="VALLE-D_AOSTA",
                   choices=[*REGIONI_VALIDE, "ALL"],
                   help="Regione singola, oppure ALL per tutte le 20 regioni")
    p.add_argument("--target", choices=["local", "r2"], default="local",
                   help="Destinazione output: local (file system) o r2 (Cloudflare)")
    p.add_argument("--no-cache", action="store_true",
                   help="Forza re-download dello ZIP")
    p.add_argument("--outdir", default="dist/immobili_pa",
                   help="Directory output shard locali (solo --target=local)")
    args = p.parse_args()

    regioni = REGIONI_VALIDE if args.regione == "ALL" else [args.regione]
    log.info("etl_start", regioni=regioni, target=args.target,
             n_regioni=len(regioni))

    # Carica lookup una sola volta (non per regione)
    cat_to_istat = load_cat_to_istat()

    # Merge cumulativo dei shard di tutte le regioni processate
    # Strategia leak management: se ISTAT compare in due regioni
    # (es. 015040 leak in VdA, vero match in Lombardia), la regione vera
    # arriva dopo e sovrascrive. Quindi processiamo nell'ordine REGIONI_VALIDE.
    all_shards: dict[str, dict] = {}

    for regione in regioni:
        try:
            zip_path = download_zip(regione, force=args.no_cache)
            shards = build_shards(zip_path, cat_to_istat)
            # Merge: shard "veri" sovrascrivono leak da regioni processate prima
            for istat, data in shards.items():
                if istat in all_shards:
                    # Se gia' presente, manteniamo quello con piu' immobili
                    # (proxy: e' il comune effettivo, non un leak da 1-2 record)
                    existing = all_shards[istat]
                    if data["kpi"]["n_totale"] >= existing["kpi"]["n_totale"]:
                        all_shards[istat] = data
                else:
                    all_shards[istat] = data
            log.info("etl_regione_done", regione=regione,
                     comuni_new=len(shards),
                     cumulative=len(all_shards))
        except Exception as e:
            log.error("etl_regione_failed", regione=regione, error=str(e))

    log.info("etl_aggregated", comuni_totali=len(all_shards))

    if args.target == "r2":
        push_to_r2_parallel(all_shards)
    else:
        write_local(all_shards, Path(args.outdir))

    log.info("etl_done", comuni_with_data=len(all_shards), target=args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
