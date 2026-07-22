"""ETL Carburanti — anagrafica impianti + prezzi praticati MIMIT.

Sorgente
--------
Ministero delle Imprese e del Made in Italy (MIMIT) — Osservatorio prezzi
carburanti, dataset "Carburanti - Prezzi praticati e anagrafica degli
impianti" pubblicato in attuazione dell'art. 51 L. 99/2009.

Pagina dataset:
  https://www.mimit.gov.it/it/open-data/elenco-dataset/carburanti-prezzi-praticati-e-anagrafica-degli-impianti

Acquisizione (ibrida)
---------------------
- Anagrafica: CSV nazionale MIMIT (separator '|', dato statico):
    https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv
- Prezzi: LIVE dal backend del portale OsservaPrezzi (ospzApi/search/area),
  aggiornati in giornata (il CSV prezzo_alle_8.csv ha ~1 giorno di ritardo).
  Fallback automatico al CSV dopo >=2 giorni senza fetch API riuscito
  (o con --prezzi csv):
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
  python -m etl.sources.carburanti
  python -m etl.sources.carburanti --force         # ignora skip su hash invariato
  python -m etl.sources.carburanti --no-cache      # forza redownload ISTAT comuni

Cadenza: giornaliera (CSV rigenerati lato MIMIT al mattino, "Prezzo alle 8 di
mattina"). Skip automatico se hash dei due CSV e' invariato rispetto
all'ultima run (persistito in DATA_DIR/carburanti/_meta.json local).
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

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.1.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "MIMIT - Osservatorio Prezzi Carburanti"
SOURCE_URL = ("https://www.mimit.gov.it/it/open-data/elenco-dataset/"
              "carburanti-prezzi-praticati-e-anagrafica-degli-impianti")
LICENSE_STR = "IODL 2.0"

ANAG_URL = "https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv"
PREZZI_URL = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"

# Output prefix (locale, sotto DATA_DIR/carburanti/)
SHARD_PREFIX = "carburanti"

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

# -- Prezzi live via portale MIMIT (ospzApi) -- sorgente primaria dei prezzi.
# L'anagrafica resta dai CSV; i prezzi praticati sono letti in tempo reale dal
# backend del portale OsservaPrezzi (stesso dato IODL 2.0, aggiornato in giornata
# invece che con ~1gg di ritardo del CSV). Il portale e' dietro Akamai: serve
# User-Agent browser-like (UA "bot" -> 403).
API_BASE = "https://carburanti.mise.gov.it/ospzApi"
API_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://carburanti.mise.gov.it/ospzSearch/risultati",
    "Content-Type": "application/json",
}
# fuelType filtra gli IMPIANTI (non i carburanti) -> unione famiglie per coprire
# anche gli impianti che non vendono benzina.
API_FUEL_FAMILIES = ["1-x", "2-x", "3-x", "4-x"]  # benzina, gasolio, metano, GPL
API_TIMEOUT = 120
API_MIN_IMPIANTI = 15000  # sotto questa soglia un run nazionale e' incompleto

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


# ---------------------- PREZZI API (ospzApi) -----------------------


def _api_post(path: str, payload: dict, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        try:
            r = requests.post(API_BASE + path, headers=API_HEADERS,
                              json=payload, timeout=API_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _api_get(path: str, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        try:
            r = requests.get(API_BASE + path, headers=API_HEADERS, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _api_fmt_dtcomu(iso: str | None) -> str:
    """insertDate ISO -> 'DD/MM/YYYY HH:MM:SS' (formato dtComu CSV)."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return ""


def fetch_api_prezzi(region_ids: list | None = None) -> tuple[list[dict], str, dict]:
    """Prezzi praticati LIVE dal portale MIMIT (ospzApi/search/area).

    Produce SOLO prezzi_rows nella forma di parse_prezzi; l'anagrafica resta dal
    CSV e il merge avviene in build_shards per idImpianto. fuelType filtra gli
    impianti -> unione famiglie. RuntimeError se un run nazionale e' incompleto
    (< API_MIN_IMPIANTI) -> attiva fallback.
    """
    full_run = region_ids is None
    if full_run:
        regs = _api_get("/registry/region")
        L = regs.get("results", regs) if isinstance(regs, dict) else regs
        region_ids = [r["id"] for r in L]

    stations: dict = {}
    n_req = 0
    for rid in region_ids:
        for ft in API_FUEL_FAMILIES:
            data = _api_post("/search/area",
                            {"region": rid, "fuelType": ft, "priceOrder": "asc"})
            n_req += 1
            for r in data.get("results", []):
                sid = r.get("id")
                if sid is not None:
                    stations.setdefault(sid, r)

    if full_run and len(stations) < API_MIN_IMPIANTI:
        raise RuntimeError(
            f"fetch_api_prezzi incompleto: {len(stations)} impianti "
            f"(< {API_MIN_IMPIANTI})")

    prezzi_rows: list[dict] = []
    for sid, r in stations.items():
        dtcomu = _api_fmt_dtcomu(r.get("insertDate"))
        for f in r.get("fuels", []):
            if f.get("price") is None:
                continue
            prezzi_rows.append({
                "idImpianto": str(sid),
                "descCarburante": (f.get("name") or "").strip(),
                "prezzo": str(f.get("price")),
                "isSelf": "1" if f.get("isSelf") else "0",
                "dtComu": dtcomu,
            })
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    diag = {"impianti": len(stations), "righe": len(prezzi_rows), "richieste": n_req}
    return prezzi_rows, snapshot, diag


def _prezzi_fingerprint(prezzi_rows: list[dict]) -> bytes:
    """Impronta deterministica (ordine-indipendente) per skip-check: include
    prezzo E dtComu -> un aggiornamento infrasettimanale forza il rebuild."""
    parts = sorted(
        f"{r['idImpianto']}|{r['descCarburante']}|{r['isSelf']}|"
        f"{r['prezzo']}|{r['dtComu']}"
        for r in prezzi_rows
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).digest()


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


# ──────────────────────── SKIP LOGIC (meta locale) ─────────────────

def _load_meta() -> dict:
    """Meta locale in DATA_DIR/carburanti/_meta.json."""
    return local_lookup.load_meta(SHARD_PREFIX) or {}


def _save_meta(**fields) -> None:
    """Merge non distruttivo dei campi meta (preserva quelli non passati)."""
    meta = _load_meta()
    meta.update({k: v for k, v in fields.items() if v is not None})
    meta["checked_at"] = datetime.now(timezone.utc).isoformat()
    local_lookup.save_meta(SHARD_PREFIX, meta)


def read_last_known_hash() -> str | None:
    """Hash dell'ultimo run (skip-check anagrafica+prezzi invariati)."""
    return _load_meta().get("hash")


def read_last_success_date() -> str | None:
    """Data 'YYYY-MM-DD' dell'ultimo fetch prezzi API riuscito (timer fallback)."""
    return _load_meta().get("last_success_date")


def _days_since(date_str: str | None) -> int | None:
    """Giorni di calendario da date_str a oggi (None se assente/non valida)."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (datetime.now(timezone.utc).date() - d).days
    except ValueError:
        return None


# ──────────────────────── WRITE LOCAL ──────────────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def write_shards_local(shards: dict[str, dict],
                       aggregati: dict,
                       outdir: Path,
                       force: bool = False) -> tuple[int, int]:
    """Scrive shard local con skip-by-md5 per file invariati.

    Ritorna (written, skipped). Anche local: se il file esiste con lo
    stesso md5 del payload da scrivere, non lo riscrive (riduce I/O e
    preserva i mtime usefull per stat).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    total = len(shards)
    written = 0
    skipped = 0
    files_for_manifest: list[dict] = []
    t0 = time.time()

    for istat, shard in shards.items():
        body = json.dumps(shard, ensure_ascii=False).encode("utf-8")
        new_md5 = _md5_of_bytes(body)
        target = outdir / f"{istat}.json"

        if not force and target.exists():
            try:
                cur_md5 = _md5_of_bytes(target.read_bytes())
                if cur_md5 == new_md5:
                    skipped += 1
                    files_for_manifest.append(
                        {"key": f"{SHARD_PREFIX}/{istat}.json",
                         "size": len(body), "md5": new_md5}
                    )
                    continue
            except OSError:
                pass  # se errore in lettura, ri-scrivi

        # write atomic
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        written += 1
        files_for_manifest.append(
            {"key": f"{SHARD_PREFIX}/{istat}.json",
             "size": len(body), "md5": new_md5}
        )
        if (written + skipped) % 500 == 0:
            log.info("shards_local_progress",
                     written=written, skipped=skipped, total=total)

    # Aggregati nazionali (sempre riscritti, file singolo)
    nz_body = json.dumps(aggregati, ensure_ascii=False, indent=2).encode("utf-8")
    nz_path = outdir / "_nazionale.json"
    tmp = nz_path.with_suffix(nz_path.suffix + ".tmp")
    tmp.write_bytes(nz_body)
    tmp.replace(nz_path)
    log.info("aggregati_nazionali_written", bytes=len(nz_body), path=str(nz_path))

    elapsed = round(time.time() - t0, 1)
    log.info("shards_local_done", written=written, skipped=skipped,
             total=total, elapsed_s=elapsed, dir=str(outdir))

    if files_for_manifest:
        manifest.update_source(SHARD_PREFIX, files_for_manifest, status="ok")

    return written, skipped


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Carburanti — anagrafica impianti + prezzi MIMIT"
    )
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output dir (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--force", action="store_true",
                        help="Forza esecuzione anche se hash CSV invariato + riscrittura totale")
    parser.add_argument("--no-cache", action="store_true",
                        help="Forza ridownload del CSV ISTAT comuni")
    parser.add_argument("--prezzi", choices=["api", "csv"], default="api",
                        help="Sorgente prezzi: 'api' portale MIMIT (default) "
                             "o 'csv' fallback storico")
    args = parser.parse_args()

    log.info("etl_carburanti_start", version=ETL_VERSION, outdir=str(args.outdir))

    # -- Anagrafica: sempre dai CSV MIMIT (dato statico; la staleness del CSV
    #    e' irrilevante per comune/coord/insegna/tipo). --
    body_anag = fetch_csv(ANAG_URL)

    # -- Prezzi: LIVE dal portale (ospzApi). Fallback ai CSV solo dopo >=2 giorni
    #    di calendario senza alcun fetch API riuscito (robusto al cron 2x/giorno).
    #    --prezzi csv forza il metodo storico. --
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.prezzi == "csv":
        body_prezzi = fetch_csv(PREZZI_URL)
        prezzi_rows, snap_p = parse_prezzi(body_prezzi)
        snapshot = snap_p or today
        price_source = "csv_manual"
        log.info("prezzi_csv_manual", snapshot=snapshot, righe=len(prezzi_rows))
    else:
        try:
            prezzi_rows, snapshot, diag = fetch_api_prezzi()
            price_source = "api"
            log.info("prezzi_api_ok", snapshot=snapshot, **diag)
        except Exception as e:
            last_success = read_last_success_date()
            giorni = _days_since(last_success)
            log.warning("prezzi_api_fail", error=str(e),
                        last_success=last_success, giorni=giorni)
            if last_success is None or (giorni is not None and giorni >= 2):
                log.warning("prezzi_fallback_csv", reason="api_down_ge_2gg")
                body_prezzi = fetch_csv(PREZZI_URL)
                prezzi_rows, snap_p = parse_prezzi(body_prezzi)
                snapshot = snap_p or today
                price_source = "csv_fallback"
            else:
                log.warning("prezzi_keep_previous", reason="api_down_lt_2gg")
                return 0

    # Parse anagrafica
    anag_rows, snapshot_anag = parse_anagrafica(body_anag)
    log.info("anagrafica_parsed", impianti=len(anag_rows),
             righe_prezzi=len(prezzi_rows), snapshot=snapshot, source=price_source)

    # -- Skip-check: anagrafica + impronta prezzi invariate -> niente rebuild. --
    combined_hash = hashlib.sha256(
        body_anag + _prezzi_fingerprint(prezzi_rows)).hexdigest()
    if not args.force:
        last_known = read_last_known_hash()
        if last_known == combined_hash:
            log.info("carburanti_skip", reason="hash_unchanged",
                     hash=combined_hash[:16], source=price_source)
            if price_source == "api":
                _save_meta(last_success_date=today)
            return 0
        log.info("carburanti_run", last_known=(last_known or "")[:16],
                 current=combined_hash[:16])

    # Index ISTAT
    if args.no_cache and CACHE_DIR.exists():
        for p in CACHE_DIR.glob("istat-comuni.csv"):
            p.unlink()
    istat_csv = pull_istat_comuni(CACHE_DIR)
    by_ns, by_name, by_tokens, region_by_istat = build_istat_index(istat_csv)
    prov_full_map = build_prov_full_map(istat_csv)

    # Build shards (build_shards INVARIATO: anagrafica CSV + prezzi API per id)
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

    # Write local + meta (last_success_date aggiornato solo su successo API)
    write_shards_local(shards, aggregati, args.outdir, force=args.force)
    if price_source == "api":
        _save_meta(hash=combined_hash, snapshot_date=snapshot,
                   last_success_date=today)
    else:
        _save_meta(hash=combined_hash, snapshot_date=snapshot)

    log.info("etl_carburanti_done", comuni=len(shards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
