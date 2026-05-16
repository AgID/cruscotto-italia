"""ETL Carburanti — anagrafica impianti + prezzi praticati MIMIT.

Sorgente
--------
Ministero delle Imprese e del Made in Italy (MIMIT) — Osservatorio prezzi
carburanti, dataset "Carburanti - Prezzi praticati e anagrafica degli
impianti" pubblicato in attuazione dell'art. 51 L. 99/2009.

Pagina dataset:
  https://www.mimit.gov.it/it/open-data/elenco-dataset/carburanti-prezzi-praticati-e-anagrafica-degli-impianti

Acquisizione
------------
Due CSV nazionali aggiornati quotidianamente, separator '|' (dal 10/02/2026,
sostituisce la vecgia ',' per evitare collisioni con virgole nelle anagrafiche).

  https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv
  https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv

Il primo rigo di ogni CSV e' l'header "Estrazione del YYYY-MM-DD", il secondo
e' l'header delle colonne. ~23.700 impianti, ~93.000 righe prezzo.

Schema anagrafica:
  idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine

Schema prezzi:
  idImpianto|descCarburante|prezzo|isSelf|dtComu

Coverage: 99.83% degli impianti name-matchabile a ISTAT con strategia 3-step
(esatto -> fuzzy fusioni comuni -> 'Provincia-as-nome' per record dirty con
indirizzo nel campo Comune). ~5.450 comuni con almeno un impianto (69%).

Licenza
-------
IODL 2.0 (Italian Open Data Licence) — attribution-only, compatibile con
CC BY 4.0 dei lavori derivati. Attribuzione: "Ministero delle Imprese e del
Made in Italy — Osservatorio prezzi carburanti".
Riferimento: https://www.dati.gov.it/content/italian-open-data-license-v20

Schema carburanti/<istat>.json
------------------------------
{
  "_etl_version": "0.1.0",
  "_source": "MIMIT - Osservatorio Prezzi Carburanti",
  "_source_url": "https://www.mimit.gov.it/it/open-data/elenco-dataset/carburanti-prezzi-praticati-e-anagrafica-degli-impianti",
  "_license": "IODL 2.0",
  "_data_last_modified": "ISO-8601",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_impianti": int,
    "n_stradali": int, "n_autostradali": int,
    "n_pompe_bianche": int,
    "pct_pompe_bianche": float,
    "n_bandiere_distinte": int,
    "mix_bandiere": {<top5+Altre>: int},
    "prezzo_medio": {                  # null se nessun impianto offre quel carburante
      "benzina_self": float, "benzina_serv": float,
      "gasolio_self": float, "gasolio_serv": float,
      "gpl": float, "metano": float, "hvo": float
    },
    "prezzo_min": {                    # prezzo piu' basso nel comune
      "benzina_self": float, "gasolio_self": float
    },
    "freshness_pct": float             # % impianti con prezzo <=7gg
  },
  "punti": [
    {
      "id": int,
      "name": str,
      "brand": str,
      "tipo": "Stradale"|"Autostradale",
      "lat": float, "lon": float,
      "indirizzo": str|None,
      "prezzi": {<carb>_self|serv: float},
      "prezzi_extra": {<premium>: float},  # V-Power, Hi-Q, Supreme, Blue Diesel
      "ultimo_aggiornamento": "YYYY-MM-DD"|None
    }
  ]
}

A livello globale viene anche scritto carburanti/_nazionale.json con le
medie nazionali e regionali pre-calcolate (per la KPI delta_vs_nazionale
sul frontend, fetch lazy quando la tab si apre).

Uso
---
  python -m etl.sources.carburanti --target=local
  python -m etl.sources.carburanti --target=r2
  python -m etl.sources.carburanti --target=r2 --force

Cadenza: giornaliera (CSV rigenerati lato MIMIT al mattino, "Prezzo alle 8 di
mattina"). Skip automatico se hash dei due CSV e' invariato rispetto
all'ultima run (persistito in carburanti/_meta.json su R2).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import manifest, r2

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "MIMIT - Osservatorio Prezzi Carburanti"
SOURCE_URL = ("https://www.mimit.gov.it/it/open-data/elenco-dataset/"
              "carburanti-prezzi-praticati-e-anagrafica-degli-impianti")
LICENSE_STR = "IODL 2.0"

ANAG_URL = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
PREZZI_URL = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"

# R2 destinazione
R2_PREFIX = "carburanti"               # → R2: carburanti/<istat>.json
R2_META_KEY = "carburanti/_meta.json"
R2_NAZIONALE_KEY = "carburanti/_nazionale.json"

# Output locale (default produzione VM AgID; override via --outdir)
DEFAULT_OUTDIR = Path("/var/www/cruscotto-italia/data/carburanti")
CACHE_DIR = Path(".cache/carburanti")

# Elenco comuni ISTAT (riusa pattern di anagrafica/pun)
ISTAT_COMUNI_URL = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
USER_AGENT = "cruscotto-italia-etl/0.1 (+https://github.com/piersoft/cruscotto-italia)"

# Bounding box Italia
BBOX_LAT_MIN, BBOX_LAT_MAX = 35.0, 47.5
BBOX_LON_MIN, BBOX_LON_MAX = 6.0, 19.0

# Soglia freshness prezzo (giorni)
FRESHNESS_DAYS = 7

# Carburanti core (in KPI prezzo_medio)
CARB_CORE = {
    # descCarburante MIMIT  ->  (slug, self/serv si applica)
    "Benzina":          "benzina",
    "Gasolio":          "gasolio",
    "GPL":              "gpl",
    "Metano":           "metano",
    "HVOlution":        "hvo",          # ENI HVO 100% rinnovabile
    "HVO":              "hvo",
}
# Premium / proprietari (in prezzi_extra)
CARB_PREMIUM = {
    "Benzina Shell V Power", "Diesel Shell V Power",
    "Hi-Q Diesel", "HiQ Perform+",
    "Supreme Diesel", "Blue Diesel", "Blue Super",
    "Gasolio speciale", "Gasolio Premium",
    "Automotive gas oil",
}


# ──────────────────────── NORMALIZE / RESOLVE ──────────────────────

def normalize(s: str) -> str:
    """Normalizzazione comuni: lowercase, no accenti/apostrofi/trattini,
    slash → spazio. Allineata a pun.normalize().
    """
    if not s:
        return ""
    s = s.strip().lower().replace("\u2019", "'").replace("`", "'")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"['\-]", " ", s)
    s = re.sub(r"/", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def in_italy(lat: float, lon: float) -> bool:
    return BBOX_LAT_MIN <= lat <= BBOX_LAT_MAX and BBOX_LON_MIN <= lon <= BBOX_LON_MAX


def pull_istat_comuni(workdir: Path) -> Path:
    """Scarica CSV ISTAT comuni (pattern preso da pun.pull_istat_comuni)."""
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "istat-comuni.csv"
    if out.exists() and out.stat().st_size > 500_000:
        log.info("istat_comuni_cache_hit", path=str(out))
        return out
    log.info("pulling_istat_comuni", url=ISTAT_COMUNI_URL)
    r = requests.get(ISTAT_COMUNI_URL,
                     headers={"User-Agent": USER_AGENT}, timeout=120)
    r.raise_for_status()
    try:
        text = r.content.decode("latin-1")
    except UnicodeDecodeError:
        text = r.content.decode("utf-8", errors="replace")
    out.write_text(text, encoding="utf-8")
    log.info("istat_comuni_saved", path=str(out), bytes=out.stat().st_size)
    return out


def build_istat_index(csv_path: Path) -> tuple[dict, dict, dict, dict]:
    """Costruisce quattro indici:
      - by_ns: (nome_norm, sigla_upper) -> ISTAT6     (match preferito: nome+sigla auto)
      - by_name: nome_norm -> set(ISTAT6)             (fallback)
      - by_tokens: dict[ISTAT6 -> set(token)]         (per fuzzy fusioni)
      - region_by_istat: ISTAT6 -> nome_regione       (per medie regionali)
    """
    by_ns: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[str]] = defaultdict(set)
    by_tokens: dict[str, set[str]] = {}
    region_by_istat: dict[str, str] = {}

    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            if len(row) < 16:
                continue
            istat6 = row[4].strip()
            if not istat6.isdigit() or len(istat6) != 6:
                continue
            nome_full = row[5].strip()
            nome_it = row[6].strip()
            nome_alt = row[7].strip()
            regione = row[10].strip()
            sigla = row[14].strip().upper()

            cands = {nome_full, nome_it}
            if nome_alt:
                cands.add(nome_alt)
            if "/" in nome_full:
                for p in nome_full.split("/"):
                    cands.add(p.strip())

            tokens: set[str] = set()
            for nm in cands:
                if not nm:
                    continue
                n_norm = normalize(nm)
                if sigla:
                    by_ns[(n_norm, sigla)] = istat6
                by_name[n_norm].add(istat6)
                for t in n_norm.split():
                    if len(t) >= 4 and t not in {
                        "san", "santa", "sant", "monte", "pieve",
                        "val", "valle", "del", "della", "delle", "dei",
                        "alto", "alta", "basso", "bassa",
                    }:
                        tokens.add(t)
            by_tokens[istat6] = tokens
            region_by_istat[istat6] = regione

    log.info("istat_index_built",
             n_ns=len(by_ns), n_names=len(by_name),
             n_tokens_comuni=len(by_tokens))
    return by_ns, by_name, by_tokens, region_by_istat


def resolve_istat(comune: str, prov: str,
                  by_ns: dict, by_name: dict,
                  by_tokens: dict, prov_full_map: dict) -> tuple[str | None, str]:
    """Risolve (Comune, Provincia) → (ISTAT6, strategy) | (None, reason).

    Strategia a 3 livelli (testata: 99.83% match al primo giro):
      1. Match esatto (nome_norm, sigla_provincia)
      2. Match name-only se univoco
      3. Fuzzy fusioni: cerco token significativo del nome impianto
         nei token ISTAT, filtrato per provincia
      4. 'Provincia-as-nome': record dirty dove Comune contiene
         l'indirizzo e Provincia contiene il nome del comune
    """
    c = normalize(comune)
    p = (prov or "").strip().upper()

    if not c:
        return None, "empty_name"

    # 1) match esatto nome+sigla
    if (c, p) in by_ns:
        return by_ns[(c, p)], "exact_nome_sigla"

    # 2) match solo per nome (univoco)
    cands = by_name.get(c, set())
    if len(cands) == 1:
        return next(iter(cands)), "unique_nome"

    # 3) fuzzy fusioni nella provincia
    if p:
        tokens_imp = {t for t in c.split() if len(t) >= 5}
        for istat6, tokens_istat in by_tokens.items():
            if istat6[:3] != prov_full_map.get(p, ""):
                # skip: provincia diversa
                continue
            common = tokens_imp & tokens_istat
            if common:
                return istat6, "fuzzy_fusione"

    # 4) Provincia-as-nome (record dirty con indirizzo nel campo Comune)
    p_norm = normalize(prov)
    cands_p = by_name.get(p_norm, set())
    if len(cands_p) == 1:
        return next(iter(cands_p)), "dirty_provincia_as_nome"
    if len(cands_p) > 1:
        # se tutti candidati hanno lo stesso codice (bilingui), ok
        if len(set(cands_p)) == 1:
            return cands_p.pop() if cands_p else None, "dirty_provincia_as_nome"

    return None, "unmatched"


def build_prov_full_map(csv_path: Path) -> dict[str, str]:
    """Mappa SIGLA → prefisso ISTAT comune (3 cifre, codice provincia).

    Necessaria per il fuzzy step 3: il filtro provincia evita falsi positivi
    cross-region.
    """
    out: dict[str, str] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            if len(row) < 16:
                continue
            istat6 = row[4].strip()
            sigla = row[14].strip().upper()
            if sigla and istat6.isdigit() and len(istat6) == 6:
                out.setdefault(sigla, istat6[:3])
    return out


# ──────────────────────── FETCH CSV ────────────────────────────────

def fetch_csv(url: str) -> bytes:
    """Scarica un CSV MIMIT. Header standard 'Estrazione del YYYY-MM-DD'
    sulla prima riga + colonne sulla seconda.
    """
    log.info("fetch_csv_start", url=url)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300)
    r.raise_for_status()
    log.info("fetch_csv_ok", url=url, bytes=len(r.content))
    return r.content


def parse_anagrafica(body: bytes) -> tuple[list[dict], str | None]:
    """UTF-8, separatore '|', skip prima riga 'Estrazione del'. Ritorna
    (rows, snapshot_date)."""
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    snapshot = None
    if lines and lines[0].lower().startswith("estrazione del"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", lines[0])
        if m:
            snapshot = m.group(1)
        lines = lines[1:]
    rdr = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|")
    return list(rdr), snapshot


def parse_prezzi(body: bytes) -> tuple[list[dict], str | None]:
    """UTF-8, separatore '|', skip prima riga 'Estrazione del'."""
    text = body.decode("utf-8", errors="replace")
    lines = text.splitlines()
    snapshot = None
    if lines and lines[0].lower().startswith("estrazione del"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", lines[0])
        if m:
            snapshot = m.group(1)
        lines = lines[1:]
    rdr = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|")
    return list(rdr), snapshot


def _parse_price(s: str) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
        return v if 0 < v < 100 else None  # filtro outlier (>100 €/litro è dirty)
    except ValueError:
        return None


def _parse_dtcomu(s: str) -> str | None:
    """'08/05/2026 13:30:09' -> '2026-05-08'."""
    if not s:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s.strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


# ──────────────────────── BUILD SHARDS ─────────────────────────────

def build_shards(anag_rows: list[dict],
                 prezzi_rows: list[dict],
                 by_ns: dict, by_name: dict, by_tokens: dict,
                 prov_full_map: dict,
                 region_by_istat: dict,
                 snapshot_date: str | None) -> tuple[dict[str, dict], dict]:
    """Aggrega impianti e prezzi per ISTAT con KPI per comune.

    Ritorna (shards, aggregati_nazionali_regionali).
    """
    # Indice prezzi per impianto: id -> list[(descCarb, prezzo, isSelf, dt)]
    prezzi_by_imp: dict[str, list[tuple[str, float, bool, str | None]]] = defaultdict(list)
    for r in prezzi_rows:
        pid = (r.get("idImpianto") or "").strip()
        price = _parse_price(r.get("prezzo"))
        if not pid or price is None:
            continue
        desc = (r.get("descCarburante") or "").strip()
        is_self = (r.get("isSelf") or "").strip() in ("1", "true", "True", "Si", "Sì")
        dt = _parse_dtcomu(r.get("dtComu") or "")
        prezzi_by_imp[pid].append((desc, price, is_self, dt))

    log.info("prezzi_indexed", impianti_con_prezzi=len(prezzi_by_imp))

    # Aggrega impianti per comune
    now_iso = datetime.now(timezone.utc).isoformat()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    shards: dict[str, dict] = {}
    stats = Counter()
    sample_unmatched: list[tuple[str, str]] = []

    # Per medie nazionali/regionali raccolgo tutti i prezzi (geo'd in Italia)
    naz_prices: dict[str, list[float]] = defaultdict(list)
    reg_prices: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for r in anag_rows:
        try:
            lat = float((r.get("Latitudine") or "").replace(",", "."))
            lon = float((r.get("Longitudine") or "").replace(",", "."))
        except (ValueError, TypeError):
            stats["bad_coords"] += 1
            continue
        if not in_italy(lat, lon):
            stats["outside_italy"] += 1
            continue

        comune = (r.get("Comune") or "").strip()
        prov = (r.get("Provincia") or "").strip()
        istat, strategy = resolve_istat(
            comune, prov, by_ns, by_name, by_tokens, prov_full_map
        )
        stats[f"match_{strategy}"] += 1
        if not istat:
            if len(sample_unmatched) < 10:
                sample_unmatched.append((comune, prov))
            continue

        # Estrai prezzi per questo impianto
        pid = (r.get("idImpianto") or "").strip()
        rows_p = prezzi_by_imp.get(pid, [])

        prezzi_core: dict[str, float] = {}
        prezzi_extra: dict[str, float] = {}
        last_dt: str | None = None

        for desc, price, is_self, dt in rows_p:
            if dt and (last_dt is None or dt > last_dt):
                last_dt = dt
            if desc in CARB_CORE:
                slug = CARB_CORE[desc]
                if slug in ("benzina", "gasolio"):
                    key = f"{slug}_{'self' if is_self else 'serv'}"
                else:
                    key = slug
                # Se piu' di un valore per stessa chiave, tieni il piu' recente
                # (per semplicita': i CSV MIMIT hanno comunque un solo prezzo
                # corrente per impianto/carburante)
                prezzi_core[key] = price
                # accumula per medie naz./reg.
                naz_prices[key].append(price)
                reg = region_by_istat.get(istat, "")
                if reg:
                    reg_prices[reg][key].append(price)
            elif desc in CARB_PREMIUM:
                # premium proprietari: nello stesso key (no _self/_serv)
                prezzi_extra[desc] = price

        tipo = (r.get("Tipo Impianto") or "Stradale").strip()

        punto = {
            "id": int(pid) if pid.isdigit() else None,
            "name": (r.get("Nome Impianto") or "").strip() or None,
            "brand": (r.get("Bandiera") or "").strip() or None,
            "tipo": tipo,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "indirizzo": (r.get("Indirizzo") or "").strip() or None,
            "prezzi": prezzi_core,
            "ultimo_aggiornamento": last_dt,
        }
        if prezzi_extra:
            punto["prezzi_extra"] = prezzi_extra

        if istat not in shards:
            shards[istat] = {
                "_etl_version": ETL_VERSION,
                "_source": SOURCE_LABEL,
                "_source_url": SOURCE_URL,
                "_license": LICENSE_STR,
                "_data_last_modified": snapshot_date or today_str,
                "_generated_at": now_iso,
                "punti": [],
            }
        shards[istat]["punti"].append(punto)

    # KPI per shard
    today_dt = datetime.now(timezone.utc).date()
    for _istat, sh in shards.items():
        punti = sh["punti"]
        n_tot = len(punti)
        n_stradali = sum(1 for p in punti if p["tipo"] == "Stradale")
        n_auto = n_tot - n_stradali

        # Pompe bianche (no brand affiliato a major)
        n_pb = sum(1 for p in punti
                   if (p["brand"] or "").lower() in
                   ("pompe bianche", "pompebianche", ""))
        # Bandiere
        bandiere = Counter(p["brand"] for p in punti if p["brand"])
        n_bandiere = len(bandiere)
        top5 = dict(bandiere.most_common(5))
        if n_bandiere > 5:
            altre = sum(v for k, v in bandiere.items() if k not in top5)
            top5["Altre"] = altre

        # Prezzo medio e minimo per carburante
        prezzo_medio: dict[str, float | None] = {}
        prezzo_min: dict[str, float] = {}
        for key in ["benzina_self", "benzina_serv", "gasolio_self",
                    "gasolio_serv", "gpl", "metano", "hvo"]:
            vals = [p["prezzi"][key] for p in punti if key in p["prezzi"]]
            prezzo_medio[key] = round(sum(vals) / len(vals), 3) if vals else None
            if key in ("benzina_self", "gasolio_self") and vals:
                prezzo_min[key] = min(vals)

        # Freshness
        n_fresh = 0
        for p in punti:
            dt = p.get("ultimo_aggiornamento")
            if not dt:
                continue
            try:
                d = datetime.strptime(dt, "%Y-%m-%d").date()
                if (today_dt - d).days <= FRESHNESS_DAYS:
                    n_fresh += 1
            except ValueError:
                pass
        fresh_pct = round(100 * n_fresh / n_tot, 1) if n_tot else 0.0

        sh["kpi"] = {
            "n_impianti": n_tot,
            "n_stradali": n_stradali,
            "n_autostradali": n_auto,
            "n_pompe_bianche": n_pb,
            "pct_pompe_bianche": round(100 * n_pb / n_tot, 1) if n_tot else 0.0,
            "n_bandiere_distinte": n_bandiere,
            "mix_bandiere": top5,
            "prezzo_medio": prezzo_medio,
            "prezzo_min": prezzo_min,
            "freshness_pct": fresh_pct,
        }

    # Aggregato nazionale/regionale
    aggregati = {
        "_etl_version": ETL_VERSION,
        "_source": SOURCE_LABEL,
        "_license": LICENSE_STR,
        "_generated_at": now_iso,
        "_snapshot_date": snapshot_date,
        "nazionale": {
            k: round(sum(v) / len(v), 3) for k, v in naz_prices.items() if v
        },
        "regionale": {
            reg: {k: round(sum(vals) / len(vals), 3)
                  for k, vals in regd.items() if vals}
            for reg, regd in reg_prices.items()
        },
    }

    log.info("carburanti_aggregate_done",
             comuni_shard=len(shards),
             match_stats={k: v for k, v in stats.items() if k.startswith("match_")},
             bad_coords=stats["bad_coords"],
             outside_italy=stats["outside_italy"],
             sample_unmatched=sample_unmatched[:5])
    return shards, aggregati


# ──────────────────────── SKIP LOGIC (R2 meta) ─────────────────────

def read_last_known_hash() -> str | None:
    try:
        client = r2.get_r2_client()
        obj = client.get_object(Bucket=r2.get_bucket(), Key=R2_META_KEY)
        return json.loads(obj["Body"].read()).get("hash")
    except Exception:
        return None


def write_last_known_hash(combined_hash: str, snapshot_date: str | None) -> None:
    body = json.dumps(
        {"hash": combined_hash,
         "snapshot_date": snapshot_date,
         "checked_at": datetime.now(timezone.utc).isoformat()},
        indent=2,
    ).encode("utf-8")
    r2.upload_bytes(body, R2_META_KEY, content_type="application/json")


# ──────────────────────── PUSH R2 / WRITE LOCAL ────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def push_shards_r2(shards: dict[str, dict],
                   aggregati: dict,
                   force: bool = False) -> tuple[int, int]:
    """Push shard su R2 con skip-by-md5 (pattern pun/aria/veicoli).

    1) UNA list_objects_v2 paginata per leggere TUTTI gli ETag remoti
    2) calcolo md5 locale e diff
    3) upload paralleli con ThreadPoolExecutor(24)
    4) progress log ogni 200 upload
    """
    total = len(shards)
    log.info("shards_r2_start", total=total, force=force, prefix=R2_PREFIX)
    t0 = time.time()

    # 1) Lista oggetti remoti
    remote_etag: dict[str, str] = {}
    if not force:
        try:
            client = r2.get_r2_client()
            pag = client.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=r2.get_bucket(),
                                     Prefix=f"{R2_PREFIX}/"):
                for o in page.get("Contents", []):
                    name = o["Key"].split("/")[-1]
                    if name.startswith("_"):
                        continue
                    etag = (o.get("ETag") or "").strip('"').lower()
                    remote_etag[name] = etag
            log.info("shards_r2_remote_listed", count=len(remote_etag))
        except Exception as e:
            log.warning("shards_r2_list_fail", err=str(e))

    # 2) Diff
    bodies: dict[str, bytes] = {}
    md5s: dict[str, str] = {}
    for istat, shard in shards.items():
        body = json.dumps(shard, ensure_ascii=False).encode("utf-8")
        bodies[istat] = body
        md5s[istat] = _md5_of_bytes(body)

    to_upload: list[str] = []
    n_same = 0
    if force:
        to_upload = list(shards.keys())
    else:
        for istat, md5 in md5s.items():
            rmd5 = remote_etag.get(f"{istat}.json")
            if rmd5 is None or rmd5 != md5:
                to_upload.append(istat)
            else:
                n_same += 1
        log.info("shards_r2_md5_compared", total=total,
                 unchanged=n_same, to_upload=len(to_upload))

    # 3) Upload paralleli
    files_for_manifest: list[dict] = []

    def _upload_one(istat: str) -> tuple[str, int, str]:
        key = f"{R2_PREFIX}/{istat}.json"
        body = bodies[istat]
        r2.upload_bytes(body, key, content_type="application/json")
        return (key, len(body), md5s[istat])

    uploaded = 0
    if to_upload:
        with ThreadPoolExecutor(max_workers=24) as ex:
            futs = {ex.submit(_upload_one, istat): istat for istat in to_upload}
            for f in as_completed(futs):
                try:
                    key, size, md5 = f.result()
                    files_for_manifest.append(
                        {"key": key, "size": size, "md5": md5}
                    )
                    uploaded += 1
                    if uploaded % 200 == 0:
                        elapsed = time.time() - t0
                        rate = uploaded / elapsed if elapsed > 0 else 0
                        eta = (len(to_upload) - uploaded) / rate if rate > 0 else 0
                        log.info("shards_r2_progress",
                                 uploaded=uploaded, to_upload=len(to_upload),
                                 elapsed_s=round(elapsed, 1),
                                 eta_s=round(eta, 1),
                                 rate=round(rate, 1))
                except Exception as e:
                    log.error("shards_r2_upload_fail", err=str(e))

    # Aggregati nazionali (sempre)
    nz_body = json.dumps(aggregati, ensure_ascii=False, indent=2).encode("utf-8")
    r2.upload_bytes(nz_body, R2_NAZIONALE_KEY, content_type="application/json")
    log.info("aggregati_nazionali_uploaded", bytes=len(nz_body))

    elapsed = round(time.time() - t0, 1)
    log.info("shards_r2_done", uploaded=uploaded, skipped=n_same,
             total=total, elapsed_s=elapsed)
    if files_for_manifest:
        manifest.update_source("carburanti", files_for_manifest, status="ok")
    return uploaded, n_same


def write_shards_local(shards: dict[str, dict], aggregati: dict, outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, payload in shards.items():
        (outdir / f"{istat}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        n += 1
    (outdir / "_nazionale.json").write_text(
        json.dumps(aggregati, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("carburanti_local_done", written=n, dir=str(outdir))
    return n


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Carburanti — anagrafica impianti + prezzi MIMIT"
    )
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output dir per --target=local (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--force", action="store_true",
                        help="Forza esecuzione anche se hash CSV invariato")
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza ridownload del CSV ISTAT comuni")
    args = parser.parse_args()

    log.info("etl_carburanti_start", target=args.target, version=ETL_VERSION)

    # Fetch CSV MIMIT
    body_anag = fetch_csv(ANAG_URL)
    body_prezzi = fetch_csv(PREZZI_URL)

    # Skip check (solo target=r2)
    combined_hash = hashlib.sha256(body_anag + body_prezzi).hexdigest()
    if args.target == "r2" and not args.force:
        last_known = read_last_known_hash()
        if last_known == combined_hash:
            log.info("carburanti_skip", reason="hash_unchanged",
                     hash=combined_hash[:16])
            return 0
        log.info("carburanti_run",
                 last_known=(last_known or "")[:16],
                 current=combined_hash[:16])

    # Parse
    anag_rows, snapshot_anag = parse_anagrafica(body_anag)
    prezzi_rows, snapshot_prezzi = parse_prezzi(body_prezzi)
    snapshot = snapshot_prezzi or snapshot_anag
    log.info("csv_parsed", impianti=len(anag_rows),
             righe_prezzi=len(prezzi_rows), snapshot=snapshot)

    # Index ISTAT
    if args.no_cache and CACHE_DIR.exists():
        for p in CACHE_DIR.glob("istat-comuni.csv"):
            p.unlink()
    istat_csv = pull_istat_comuni(CACHE_DIR)
    by_ns, by_name, by_tokens, region_by_istat = build_istat_index(istat_csv)
    prov_full_map = build_prov_full_map(istat_csv)

    # Build shards
    shards, aggregati = build_shards(
        anag_rows, prezzi_rows,
        by_ns, by_name, by_tokens, prov_full_map,
        region_by_istat, snapshot,
    )

    # Sample log
    for sample in ["058091", "015146", "077014", "075035", "097055", "007021"]:
        sh = shards.get(sample)
        if sh:
            log.info("sample", istat=sample, **sh["kpi"])
        else:
            log.info("sample_no_data", istat=sample)

    # Write
    if args.target == "local":
        write_shards_local(shards, aggregati, args.outdir)
    else:
        push_shards_r2(shards, aggregati, force=args.force)
        write_last_known_hash(combined_hash, snapshot)

    log.info("etl_carburanti_done", target=args.target, comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
