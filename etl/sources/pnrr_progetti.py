"""ETL Progetti PNRR del comune - Italia Domani / ReGiS.

Fonte: https://www.italiadomani.gov.it (Presidenza del Consiglio - Sistema ReGiS)
Dataset: PNRR_Progetti.csv (~294 MB, 280k progetti totali Italia)
Licenza: CC-BY 4.0

Strategia di matching comune <-> progetto:
- Solo progetti dove "Soggetto Attuatore" e' un comune italiano
- Match via denominazione: "COMUNE DI <NOME>" vs anagrafica bundle
- Casi speciali: "ROMA CAPITALE" -> 058091
- Esito: ~85.000 progetti su 280.000 (30%) sono comunali
- Copertura ~7.600 comuni su 7.896 (96.5%) hanno almeno 1 progetto

NB: il restante 70% di progetti PNRR e' attuato da Regioni, Ministeri, GSE, ASL,
Universita', etc. Non sono inclusi in questa vista "del comune" perche' la
relazione progetto<->territorio comunale non e' espressa nel dataset (manca
una colonna 'comune destinatario' o 'localizzazione ISTAT').

Output:
  pnrr/<istat>.json per ogni comune con almeno 1 progetto.
  Schema: kpi (totali, distrib stato), per_missione (aggregato), progetti (lista).

Cache: /tmp/cruscotto-pnrr-cache/ (CSV in cache per ripartibilita)

Usage:
  python -m etl.sources.pnrr_progetti --target=local
  python -m etl.sources.pnrr_progetti --target=r2
  python -m etl.sources.pnrr_progetti --target=local --no-cache
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import unicodedata
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

# CSV principale: il file "current" non versionato (sempre piu' recente).
PNRR_PROGETTI_URL = (
    "https://www.italiadomani.gov.it/content/dam/sogei-ng/opendata/PNRR_Progetti.csv"
)
UA = "cruscotto-italia/1.0 (+https://cruscotto-italia.piersoftckan.biz)"

# Aumenta il limite csv di Python (alcune righe sono molto lunghe)
csv.field_size_limit(min(2**31 - 1, sys.maxsize))

# Casi speciali per il match (denominazione PNRR -> codice istat)
SPECIAL_NAMES = {
    # Roma
    "ROMA CAPITALE":   "058091",
    "COMUNE DI ROMA":  "058091",
    "CITTA DI ROMA":   "058091",

    # Casi bilingui non risolti dal split('/') perche\' il CSV PNRR usa
    # un\'unica forma con descrizione tedesca/slovena annessa al nome
    # italiano (es. "CHIENES GEMEINDE KIENS" invece di "Chienes/Kiens").
    "CHIENES GEMEINDE KIENS":              "021021",  # Chienes/Kiens
    "NOVA PONENTE GEMEINDE DEUTSCHNOFEN":  "021060",  # Nova Ponente/Deutschnofen
    "DOBERDO DEL LAGO":                    "031003",  # Doberdo del Lago-Doberdob
    "SAN DORLIGO DELLA VALLE":             "032004",  # San Dorligo della Valle-Dolina
    "SAN FLORIANO DEL COLLIO":             "031019",  # San Floriano del Collio-Steverjan
    "MONRUPINO":                           "032002",  # Monrupino-Repentabor
    "SGONICO":                             "032005",  # Sgonico-Zgonik
    "SAVOGNA DISONZO":                     "031022",  # Savogna d'Isonzo-Sovodnje ob Soci

    # Variante "all'X" elisa nel CSV come "ALLO X" (caso peculiare di Cassano)
    "CASSANO ALLO IONIO":                  "078029",  # Cassano all'Ionio

    # Comuni soppressi per fusione 2018-2024: rimappiamo i progetti del
    # vecchio comune al nuovo comune risultante. NB: il dataset PNRR usa
    # ancora i nomi vecchi per progetti pre-fusione.
    "QUERO VAS":                           "025075",  # → Setteville (BL, 2024)
    "ALANO DI PIAVE":                      "025075",  # → Setteville (BL, 2024)
    "CARCERI":                             "028107",  # → Borgo Veneto (PD, 2018)
    "UGGIATE TREVANO":                     "013256",  # → Uggiate con Ronago (CO, 2024)
    "BREGANO":                             "012144",  # → Bardello con Malgesso e Bregano (VA, 2024)
    "ALBAREDO ARNABOLDI":                  "018026",  # → Campospinoso Albaredo (PV, 2024)
    "GAMBUGLIANO":                         "024128",  # → Sovizzo (VI, 2024)
}

# Override CF: alcuni comuni omonimi nel CSV PNRR usano un CF alternativo
# (storico o secondario) diverso da quello in lookup/comuni-bundle.json.
# Per questi, mappa diretta CF_CSV -> ISTAT che bypassa il match per nome
# (che andrebbe ambiguo dato che ci sono omonimi).
SPECIAL_CF = {
    # CF presenti nel CSV PNRR ma NON in lookup/comuni-bundle.json: sono CF
    # alternativi/storici/secondari. Li mappiamo manualmente al comune corretto.
    "00501900229": "022106",  # Livo (TN) - CF alternativo (non ufficiale)
    "01974610832": "083090",  # San Teodoro (ME) - CF alternativo
}


def normalize(s: str) -> str:
    """Normalizza una stringa per il match (uppercase ASCII, no apostrofi/trattini).

    Casistica gestita (post-A1 fix 2026-05-09):
      1. Bilingui IT/DE/FR con '/' nel bundle (es. "Bolzano/Bozen") -> prendi
         solo la prima parte (italiano).
      2. Apostrofi dritti (') e tipografici (\u2019) rimossi.
      3. Trattini sostituiti con spazio (gestisce "X-Y" vs "X - Y").
      4. NFD-strip degli accenti.
      5. Uppercase + collapse di spazi multipli.

    NB: per i ~6 comuni omonimi (Samone, Castro, Paterno, Livo, San Teodoro,
    Peglio) il match per solo nome e\' ambiguo. Il match deve preferire il CF
    (vedi build_cf_to_istat).
    """
    s = s.split("/")[0]
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("\u2019", "")
    s = s.replace("-", " ")
    return " ".join(s.upper().split())


def load_lookups() -> tuple[dict[str, str], dict[str, str]]:
    """Carica due mappe dal bundle anagrafica:
      - cf_to_istat:   codice_fiscale -> istat_code (matcher PRIMARIO, univoco)
      - nome_to_istat: denominazione_normalizzata -> istat_code (matcher FALLBACK)

    Il match per CF e\' preferito perche\' risolve i 6 omonimi (Samone,
    Castro, Paterno, Livo, San Teodoro, Peglio) che con solo nome
    collidevano post-normalize. Il match per nome serve per i ~10 comuni
    senza CF nel bundle (Olbia, Telti, Lirio, ecc.) o per CF errati nel CSV.
    """
    log.info("anagrafica_loading")
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    obj = client.get_object(Bucket=bucket, Key="lookup/comuni-bundle.json")
    bundle = json.loads(obj["Body"].read())
    comuni = bundle.get("comuni", {})

    cf_to_istat: dict[str, str] = {}
    nome_to_istat: dict[str, str] = {}
    for istat, c in comuni.items():
        cf = (c.get("codice_fiscale") or "").strip()
        if cf:
            cf_to_istat[cf] = istat
        denom = c.get("denominazione", "")
        if denom:
            nome_to_istat[normalize(denom)] = istat
    # Casi speciali (ROMA CAPITALE etc.) solo nella mappa nome
    for n, istat in SPECIAL_NAMES.items():
        nome_to_istat[normalize(n)] = istat

    log.info("anagrafica_loaded",
             n_comuni=len(comuni),
             cf_mapped=len(cf_to_istat),
             nomi_normalizzati=len(nome_to_istat))
    return cf_to_istat, nome_to_istat


# Wrapper di retro-compatibilita\' (mai usato ma evita import error se invocato)
def load_nome_to_istat() -> dict[str, str]:
    _, nome = load_lookups()
    return nome


def download_csv(cache_dir: Path, force: bool = False) -> Path:
    """Scarica PNRR_Progetti.csv (294 MB) con caching su disco."""
    out = cache_dir / "PNRR_Progetti.csv"
    if out.exists() and out.stat().st_size > 100_000_000 and not force:
        log.info("pnrr_cache_hit", path=str(out), size=out.stat().st_size)
        return out
    log.info("pnrr_downloading", url=PNRR_PROGETTI_URL)
    req = urllib.request.Request(PNRR_PROGETTI_URL,
                                 headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            with open(out, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)  # 1 MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        log.error("pnrr_http_error", status=e.code, reason=e.reason)
        raise
    log.info("pnrr_downloaded", bytes=out.stat().st_size, path=str(out))
    return out


def build_cf_to_istat(csv_path: Path,
                     cf_bundle: dict[str, str],
                     nome_to_istat: dict[str, str]) -> dict[str, str]:
    """Costruisce mappa CF Soggetto Attuatore (CSV) -> codice ISTAT.

    Strategia di match (in ordine):
      1. CF DIRETTO: il CF nel CSV e' presente nel bundle -> match univoco.
         Risolve i 6 comuni omonimi (Samone, Castro, Paterno, Livo,
         San Teodoro, Peglio) e i ~10 comuni con stesso nome ma diverso CF.
      2. NOME (fallback): per CF non presenti nel bundle (~10 comuni senza
         CF nel bundle, oppure CF errato/datato nel CSV), prova a matchare
         "COMUNE DI <X>" con la denominazione piu' frequente.

    Raccoglie statistiche di provenienza match (CF vs nome) per debug.
    """
    log.info("cf_mapping_building")
    cf_to_candidates: dict[str, Counter] = defaultdict(Counter)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            cf = (r.get("Codice Fiscale Soggetto Attuatore") or "").strip()
            sogg = (r.get("Soggetto Attuatore") or "").strip()
            if cf and sogg:
                cf_to_candidates[cf][sogg] += 1

    log.info("cf_distinct", n=len(cf_to_candidates))

    cf_to_istat: dict[str, str] = {}
    matched_by_cf = 0
    matched_by_name = 0
    unmatched_examples = []

    for cf, denoms in cf_to_candidates.items():
        # 1a. Match diretto via CF (preferito, univoco)
        if cf in cf_bundle:
            cf_to_istat[cf] = cf_bundle[cf]
            matched_by_cf += 1
            continue

        # 1b. Override CF: comuni con CF alternativo nel CSV
        if cf in SPECIAL_CF:
            cf_to_istat[cf] = SPECIAL_CF[cf]
            matched_by_cf += 1
            continue

        # 2. Fallback: match via nome
        main_name = denoms.most_common(1)[0][0]
        norm = normalize(main_name)
        if norm.startswith("COMUNE DI "):
            target = norm[10:]
            if target in nome_to_istat:
                cf_to_istat[cf] = nome_to_istat[target]
                matched_by_name += 1
                continue
        if norm in nome_to_istat:
            cf_to_istat[cf] = nome_to_istat[norm]
            matched_by_name += 1
            continue

        if len(unmatched_examples) < 10 and norm.startswith("COMUNE DI "):
            unmatched_examples.append((cf, main_name))

    istat_distinct = set(cf_to_istat.values())
    log.info("cf_matched",
             cf_matched=len(cf_to_istat),
             matched_by_cf=matched_by_cf,
             matched_by_name=matched_by_name,
             comuni_distinct=len(istat_distinct))
    if unmatched_examples:
        log.info("cf_unmatched_examples", samples=unmatched_examples[:5])
    return cf_to_istat


def to_float(v) -> float:
    """Converte importo PNRR (it_IT format con virgola) in float."""
    if v is None or v == "":
        return 0.0
    s = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date_it(v: str) -> str | None:
    """Converte 'DD/MM/YYYY' in 'YYYY-MM-DD' (ISO)."""
    if not v:
        return None
    parts = v.strip().split("/")
    if len(parts) != 3:
        return None
    d, m, y = parts
    if len(y) != 4 or not y.isdigit():
        return None
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def build_pnrr_shards(csv_path: Path,
                      cf_to_istat: dict[str, str],
                      output_dir: Path) -> tuple[Path, dict]:
    """Aggrega CSV in shard JSON per comune.

    Restituisce (shard_dir, stats_dict).
    """
    shard_dir = output_dir / "pnrr"
    shard_dir.mkdir(parents=True, exist_ok=True)

    # Indicizzo: istat -> lista progetti
    by_istat: dict[str, list] = defaultdict(list)
    data_estrazione = None
    n_total = 0
    n_matched = 0

    log.info("pnrr_aggregating")
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            n_total += 1
            cf = (r.get("Codice Fiscale Soggetto Attuatore") or "").strip()
            istat = cf_to_istat.get(cf)
            if not istat:
                continue
            n_matched += 1

            # Estraggo data estrazione (uguale per tutto il CSV, prendo la prima)
            if data_estrazione is None:
                d_est = parse_date_it(r.get("Data di Estrazione", ""))
                if d_est:
                    data_estrazione = d_est

            # Costruisco progetto compatto
            fin_pnrr = to_float(r.get("Finanziamento PNRR"))
            fin_tot = to_float(r.get("Finanziamento Totale"))

            progetto = {
                "cup": (r.get("CUP") or "").strip(),
                "titolo": (r.get("Titolo Progetto") or "").strip()[:300],
                "missione": (r.get("Missione") or "").strip(),
                "missione_descrizione": (r.get("Descrizione Missione") or "").strip(),
                "componente": (r.get("Componente") or "").strip(),
                "componente_descrizione": (r.get("Descrizione Componente") or "").strip(),
                "submisura": (r.get("Codice Univoco Submisura") or "").strip(),
                "submisura_descrizione": (r.get("Descrizione Submisura") or "").strip()[:200],
                "finanziamento_pnrr": int(round(fin_pnrr)) if fin_pnrr else 0,
                "finanziamento_totale": int(round(fin_tot)) if fin_tot else 0,
                "stato_avanzamento": (r.get("Stato Avanzamento Progetto") or "").strip(),
                "fase_iter": (r.get("Descrizione Fase Iter di Progetto") or "").strip()[:120],
                "stato_fase_iter": (r.get("Stato Fase Iter di Progetto") or "").strip(),
                "data_inizio_prevista": parse_date_it(r.get("Data Inizio Progetto Prevista", "")),
                "data_inizio_effettiva": parse_date_it(r.get("Data Inizio Progetto Effettiva", "")),
                "data_fine_prevista": parse_date_it(r.get("Data Fine Progetto Prevista", "")),
                "data_fine_effettiva": parse_date_it(r.get("Data Fine Progetto Effettiva", "")),
                "soggetto_attuatore": (r.get("Soggetto Attuatore") or "").strip()[:120],
                "settore": (r.get("CUP Descrizione Settore") or "").strip(),
                "natura": (r.get("CUP Descrizione Natura") or "").strip(),
            }
            by_istat[istat].append(progetto)

    log.info("pnrr_aggregated", total=n_total, matched=n_matched,
             comuni_with_data=len(by_istat))

    # Costruisco shard per ogni comune
    written = 0
    for istat, progetti in by_istat.items():
        # Ordino per finanziamento PNRR decrescente
        progetti.sort(key=lambda p: p["finanziamento_pnrr"], reverse=True)

        # KPI
        n_prog = len(progetti)
        tot_pnrr = sum(p["finanziamento_pnrr"] for p in progetti)
        tot_glob = sum(p["finanziamento_totale"] for p in progetti)
        stati = Counter(p["stato_avanzamento"] for p in progetti)
        missioni = Counter(p["missione"] for p in progetti)

        # Aggregazione per missione
        per_missione_dict: dict[str, dict] = {}
        for p in progetti:
            m = p["missione"]
            if not m:
                continue
            agg = per_missione_dict.setdefault(m, {
                "missione": m,
                "descrizione": p["missione_descrizione"],
                "n_progetti": 0,
                "tot_pnrr": 0,
                "tot_globale": 0,
            })
            agg["n_progetti"] += 1
            agg["tot_pnrr"] += p["finanziamento_pnrr"]
            agg["tot_globale"] += p["finanziamento_totale"]
        per_missione = sorted(per_missione_dict.values(),
                              key=lambda x: x["tot_pnrr"], reverse=True)

        shard = {
            "codice_istat": istat,
            "kpi": {
                "n_progetti": n_prog,
                "totale_finanziamento_pnrr": tot_pnrr,
                "totale_finanziamento_globale": tot_glob,
                "n_concluso": stati.get("Concluso", 0),
                "n_in_corso": stati.get("In Corso", 0),
                "n_altro": n_prog - stati.get("Concluso", 0) - stati.get("In Corso", 0),
                "n_missioni_distinte": len(missioni),
                "missioni_principali": [m for m, _ in missioni.most_common(5)],
            },
            "per_missione": per_missione,
            "progetti": progetti,
            "fonte": "Italia Domani - Sistema ReGiS",
            "fonte_url": "https://www.italiadomani.gov.it/",
            "data_estrazione": data_estrazione,
        }
        out = shard_dir / f"{istat}.json"
        out.write_text(json.dumps(shard, ensure_ascii=False,
                                  separators=(",", ":")))
        written += 1
        if written % 1000 == 0:
            log.info("pnrr_shards_progress", written=written)

    log.info("pnrr_shards_done", written=written, dir=str(shard_dir))
    stats = {
        "total_records": n_total,
        "matched_records": n_matched,
        "comuni_with_data": len(by_istat),
        "data_estrazione": data_estrazione,
    }
    return shard_dir, stats


def push_to_r2_parallel(shard_dir: Path) -> int:
    """Upload parallelo degli shard su R2 sotto prefix 'pnrr/'."""
    client = r2.get_r2_client()
    bucket = r2.get_bucket()

    existing = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="pnrr/"):
        for o in page.get("Contents", []):
            existing.add(o["Key"].split("/")[-1])

    shard_files = sorted(shard_dir.glob("*.json"))
    log.info("pnrr_pushing", total=len(shard_files), already_on_r2=len(existing))

    def _upload_one(sf):
        r2.upload_file(sf, f"pnrr/{sf.name}", content_type="application/json")
        return sf.name

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        # NB: rifacciamo upload anche degli esistenti (i progetti cambiano stato
        # ogni mese, gli shard vanno aggiornati).
        futures = {ex.submit(_upload_one, sf): sf for sf in shard_files}
        for fut in as_completed(futures):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 1000 == 0:
                    log.info("pnrr_push_progress", uploaded=uploaded,
                             total=len(shard_files))
            except Exception as e:
                log.error("pnrr_upload_failed", error=str(e))
    log.info("pnrr_push_done", uploaded=uploaded)
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Progetti PNRR del comune (Italia Domani / ReGiS)",
    )
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("/tmp/cruscotto-pnrr-cache"))
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza re-download del CSV (~294 MB)")
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path(tempfile.mkdtemp(
            prefix="cruscotto-pnrr-")) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", target=args.target,
             cache_dir=str(cache_dir), output_dir=str(output_dir))

    try:
        # 1) Download del CSV
        csv_path = download_csv(cache_dir, force=args.no_cache)

        # 2) Carico anagrafica e costruisco mappa CF -> istat
        cf_bundle, nome_to_istat = load_lookups()
        cf_to_istat = build_cf_to_istat(csv_path, cf_bundle, nome_to_istat)

        # 3) Aggrego in shard
        shard_dir, stats = build_pnrr_shards(csv_path, cf_to_istat, output_dir)

        # 4) Upload su R2 (solo se target=r2)
        if args.target == "r2":
            uploaded = push_to_r2_parallel(shard_dir)
            manifest.update_source(
                "pnrr_progetti",
                [{"key": "pnrr/*", "count": uploaded}],
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
