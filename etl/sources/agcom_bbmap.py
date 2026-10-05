"""ETL AGCOM Broadband Map — copertura banda larga per comune.

Sorgente
--------
AGCOM (Autorità per le Garanzie nelle Comunicazioni) — BBmap, sistema di
mappatura nazionale delle reti di accesso a Internet ex art. 22 Codice
delle Comunicazioni Elettroniche.

Pagina indice "AI-Ready": https://geo.agcom.it/reportistica/ai/index.html

Acquisizione
------------
Singolo CSV nazionale a livello comunale, pubblicato su ArcGIS sharing
come item NUOVO a ogni rilascio (item ID diverso ogni trimestre). L'item
corrente si risolve a runtime con l'API di ricerca ArcGIS: titolo
"Reportistica AAMMGG Comuni", tipo CSV, periodo AAMMGG piu recente.
Se la ricerca fallisce si usa FALLBACK_ITEM_ID con warning nel log.

Encoding: fino al 31/12/2025 CP1252, dal 31/03/2026 UTF-8 con BOM.
Codici: dal 31/03/2026 AGCOM usa i nuovi codici delle province sarde
(prefissi 112-119) per 377 comuni; il Cruscotto usa i codici storici
dell'anagrafica, quindi i codici assenti dall'anagrafica vengono
ricondotti per nome dentro la stessa regione (lookup/comuni-index.json).

Aggiornamento trimestrale. Dato corrente al 31/12/2025, rilascio 10/02/2026.
Encoding effettivo CP1252 (la pagina dichiara UTF-8 ma il file è Latin-1).
Separatore ';'. 7896 righe = 7896/7896 comuni italiani (100% coverage).
La chiave 'pro_com' è il codice ISTAT comune (può avere 4/5/6 cifre, va
pad-left a 6).

Licenza
-------
CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) — principio "open data by
default": dati PA senza licenza espressa = CC-BY 4.0 con attribuzione al
titolare (AGCOM). Riferimenti AGCOM: https://www.agcom.it/termini-e-condizioni

Schema agcom_bbmap/<istat>.json
-------------------------------
{
  "_etl_version": "0.1.0",
  "_source": "AGCOM - Broadband Map",
  "_source_url": "https://geo.agcom.it/reportistica/",
  "_license": "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)",
  "_data_period": "31/12/2025",
  "_generated_at": "ISO-8601",
  "kpi": {
    "famiglie_residenti": int,
    "famiglie_ftth": int,
    "famiglie_ftth_20m": int,
    "copertura_ftth_desi_pct": float,    # %
    "copertura_ftth_20m_pct": float,     # %
    "confidenza_desi_pct": float,        # %
    "celle_20m_raggiunte": int,
    "celle_20m_ftth": int,
    "celle_20m_fttc": int,
    "punti_dichiarati": int,
    "punti_dichiarati_ftth": int,
    "punti_geo_distinti": int,
    "punti_geo_distinti_ftth": int,
    "indirizzi_postali_distinti": int,
    "indirizzi_postali_distinti_ftth": int
  },
  "mappa_ufficiale": {
    "url": "https://geo.agcom.it/agcomapps/BB4/...?center=lon,lat&level=14",
    "level": 14
  }
}

Nota: lo shard NON include geometria. La rete dettagliata (polilinee
strade FTTH/rame) è esposta dal FeatureServer ArcGIS ufficiale AGCOM,
e nella tab "Connettività" del frontend viene linkata come deep-link
alla mappa ufficiale AGCOM. Il volume del FeatureServer (192k+ segmenti
solo per Roma) rende impraticabile l'ingestion massiva.

Uso
---
  python -m etl.sources.agcom_bbmap
  python -m etl.sources.agcom_bbmap --force

Cadenza: trimestrale (allinearsi al rilascio AGCOM, tipicamente fine
quartile + 30-45 giorni). Skip automatico se hash del CSV è invariato
rispetto all'ultima run (persistito in data/agcom_bbmap/_meta.json).
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
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger()

ETL_VERSION = "0.2.0"

# ──────────────────────── COSTANTI ─────────────────────────────────

SOURCE_LABEL = "AGCOM - Broadband Map"
SOURCE_URL = "https://geo.agcom.it/reportistica/"
LICENSE_STR = "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)"

# ArcGIS sharing: ogni rilascio trimestrale e un item nuovo.
ARCGIS_REST = "https://geo.agcom.it/arcgis/sharing/rest"
# Riserva usata solo se la ricerca dinamica fallisce (ultimo noto: 30/06/2026)
FALLBACK_ITEM_ID = "25830559c5784c1eb5eb1cf748889f4c"
FALLBACK_PERIOD = "260630"
_TITLE_RE = re.compile(r"^Reportistica\s+(\d{6})\s+Comuni$")

# Codici assenti dall'anagrafica e non ricondotti per nome: oltre questa
# soglia l'ETL si ferma senza scrivere.
MAX_UNRESOLVED = 5

# Mappa ufficiale AGCOM (Web AppBuilder 2.15)
# Pattern: ?center=lon,lat&level=N (WGS84, level 13-15 per scala comunale)
AGCOM_MAP_BASE = (
    "https://geo.agcom.it/agcomapps/BB4/BB4_BBwired_na_app16_4/"
)

# Output locale (default produzione VM AgID; override via --outdir)
DEFAULT_OUTDIR = Path("/var/www/cruscotto-italia/data/agcom_bbmap")
CACHE_DIR = Path(".cache/agcom_bbmap")

USER_AGENT = (
    "cruscotto-italia-etl/0.1 "
    "(+https://github.com/AgID/cruscotto-italia)"
)

# Anagrafica ISTAT per ricavare centroide lat/lon (per deep-link mappa).
# Legge file in data/anagrafica/<istat>.json se esistono.
ANAGRAFICA_DIR_LOCAL = Path("/var/www/cruscotto-italia/data/anagrafica")


# ──────────────────────── FETCH CSV AGCOM ──────────────────────────

def period_key(aammgg: str) -> str:
    """'260630' -> '20260630' (ordinabile)."""
    return "20" + aammgg


def period_label(aammgg: str) -> str:
    """'260630' -> '30/06/2026'."""
    return f"{aammgg[4:6]}/{aammgg[2:4]}/20{aammgg[0:2]}"


def label_to_key(label: str | None) -> str | None:
    """'31/12/2025' -> '20251231'; None se non parsabile."""
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", label or "")
    return f"{m.group(3)}{m.group(2)}{m.group(1)}" if m else None


def resolve_latest_item() -> tuple[str, str, bool]:
    """Trova l'item CSV comunale piu recente via API di ricerca ArcGIS.

    Returns (item_id, aammgg, dinamico). dinamico=False se si e usato il
    fallback cablato.
    """
    try:
        r = requests.get(
            f"{ARCGIS_REST}/search",
            params={"q": "title:Reportistica Comuni", "num": 100, "f": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        cand = []
        for it in data.get("results", []):
            m = _TITLE_RE.match((it.get("title") or "").strip())
            if m and it.get("type") == "CSV":
                cand.append((m.group(1), it["id"]))
        if data.get("nextStart", -1) not in (-1, None):
            log.warning("agcom_search_paginata", total=data.get("total"))
        if not cand:
            raise ValueError("nessun item 'Reportistica AAMMGG Comuni' di tipo CSV")
        aammgg, item_id = max(cand)
        log.info("agcom_item_resolved", item_id=item_id,
                 period=period_label(aammgg), candidati=len(cand))
        return item_id, aammgg, True
    except Exception as e:
        log.warning("agcom_item_resolve_failed", error=str(e),
                    fallback_item=FALLBACK_ITEM_ID,
                    fallback_period=period_label(FALLBACK_PERIOD))
        return FALLBACK_ITEM_ID, FALLBACK_PERIOD, False


def fetch_agcom_csv(item_id: str) -> tuple[bytes, str]:
    """Scarica il CSV AGCOM comunale dell'item indicato.

    Returns (body_bytes, sha256_hex).
    """
    url = f"{ARCGIS_REST}/content/items/{item_id}/data"
    log.info("agcom_fetch_start", url=url)
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"},
        timeout=120,
    )
    r.raise_for_status()
    body = r.content
    sha = hashlib.sha256(body).hexdigest()
    log.info("agcom_fetch_done", bytes=len(body), sha256=sha[:16])
    return body, sha


# ──────────────────────── PARSE CSV ────────────────────────────────

def _parse_pct(s: str) -> float | None:
    """'97%' -> 97.0, '' -> None."""
    if not s:
        return None
    s = s.strip().rstrip("%").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> int | None:
    """'1234' -> 1234, '' -> None."""
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", "")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_csv(body: bytes) -> list[dict]:
    """Parsa il CSV AGCOM (separatore ';', 19 colonne posizionali).

    Encoding: UTF-8 con BOM dal 31/03/2026, CP1252 nei rilasci precedenti.
    UTF-8 va provato per primo: CP1252 non fallisce su byte UTF-8 e
    produrrebbe nomi corrotti senza errori.
    """
    try:
        text = body.decode("utf-8-sig")
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        text = body.decode("cp1252", errors="replace")
        enc = "cp1252"
    log.info("agcom_csv_encoding", encoding=enc)
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader)
    if len(header) != 19:
        log.warning("agcom_csv_unexpected_cols",
                    expected=19, got=len(header))
    rows: list[dict] = []
    skipped_no_procom = 0
    for raw in reader:
        if len(raw) < 19:
            # estendi con stringhe vuote per evitare IndexError
            raw = raw + [""] * (19 - len(raw))
        pro_com = raw[3].strip()
        if not pro_com:
            skipped_no_procom += 1
            continue
        # Pad-left a 6 cifre per match ISTAT canonico
        istat6 = pro_com.zfill(6)
        rows.append({
            "istat": istat6,
            "regione":  raw[0].strip(),
            "provincia": raw[1].strip(),
            "comune":   raw[2].strip(),
            "celle_20m_raggiunte":          _parse_int(raw[4]),
            "punti_dichiarati":             _parse_int(raw[5]),
            "punti_geo_distinti":           _parse_int(raw[6]),
            "indirizzi_postali_distinti":   _parse_int(raw[7]),
            "celle_20m_ftth":               _parse_int(raw[8]),
            "punti_dichiarati_ftth":        _parse_int(raw[9]),
            "punti_geo_distinti_ftth":      _parse_int(raw[10]),
            "indirizzi_postali_distinti_ftth": _parse_int(raw[11]),
            "celle_20m_fttc":               _parse_int(raw[12]),
            "famiglie_residenti":           _parse_int(raw[13]),
            "famiglie_ftth":                _parse_int(raw[14]),
            "famiglie_ftth_20m":            _parse_int(raw[15]),
            "copertura_ftth_desi_pct":      _parse_pct(raw[16]),
            "confidenza_desi_pct":          _parse_pct(raw[17]),
            "copertura_ftth_20m_pct":       _parse_pct(raw[18]),
        })
    log.info("agcom_csv_parsed",
             rows=len(rows), skipped_no_procom=skipped_no_procom)
    return rows


# ──────────────────────── CODICI ISTAT ──────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def remap_codes(rows: list[dict]) -> tuple[int, list[dict]] | None:
    """Riconduce ai codici dell'anagrafica Cruscotto le righe con codice ignoto.

    Match per nome normalizzato dentro la stessa regione, solo se univoco e
    se il codice di destinazione non e gia usato da un'altra riga.
    Modifica rows in place. Returns (n_ricondotti, irrisolti) oppure None
    se l'anagrafica non e disponibile.
    """
    path = local_lookup.get_lookup_dir() / "comuni-index.json"
    try:
        idx = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("agcom_comuni_index_missing", path=str(path), error=str(e))
        return None
    keys = {c["i"] for c in idx}
    by_name: dict[tuple[str, str], list[str]] = {}
    for c in idx:
        by_name.setdefault((_norm(c.get("r", "")), _norm(c.get("n", ""))), []).append(c["i"])
    used = {r["istat"] for r in rows if r["istat"] in keys}
    n_ok, irrisolti = 0, []
    for r in rows:
        if r["istat"] in keys:
            continue
        m = by_name.get((_norm(r["regione"]), _norm(r["comune"])), [])
        if len(m) == 1 and m[0] not in used:
            r["istat_agcom"] = r["istat"]
            r["istat"] = m[0]
            used.add(m[0])
            n_ok += 1
        else:
            irrisolti.append({"pro_com": r["istat"], "comune": r["comune"],
                              "regione": r["regione"], "match": m})
    log.info("agcom_codici_ricondotti", ricondotti=n_ok,
             irrisolti=len(irrisolti), esempi=irrisolti[:5])
    return n_ok, irrisolti


# ──────────────────────── CENTROIDE COMUNE ─────────────────────────

def load_anagrafica_centroids() -> dict[str, tuple[float, float]]:
    """Carica un mapping istat6 -> (lat, lon) dai file anagrafica locali.

    Legge data/anagrafica/<istat>.json. Se la directory non esiste o e' vuota,
    ritorna dict vuoto e il deep-link mappa AGCOM sara' senza zoom (URL base).
    """
    centroids: dict[str, tuple[float, float]] = {}
    if not ANAGRAFICA_DIR_LOCAL.exists():
        log.warning("agcom_centroids_no_dir",
                    path=str(ANAGRAFICA_DIR_LOCAL))
        return centroids
    n = 0
    for p in ANAGRAFICA_DIR_LOCAL.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            coord = d.get("coordinate") or d.get("kpi", {}).get("coordinate")
            if coord and "lat" in coord and "lon" in coord:
                centroids[p.stem] = (float(coord["lat"]), float(coord["lon"]))
                n += 1
        except Exception:
            continue
    log.info("agcom_centroids_loaded", n=n)
    return centroids


def build_deeplink(lat: float | None, lon: float | None,
                   level: int = 14) -> dict:
    """Costruisce il dict mappa_ufficiale per lo shard.

    Se lat/lon disponibili, include URL deep-link con center+level.
    Altrimenti URL base senza parametri (l'utente vedrà la mappa Italia
    completa).
    """
    if lat is None or lon is None:
        return {"url": AGCOM_MAP_BASE, "level": None}
    # WAB 2.15 accetta center=lon,lat (geographic coords)
    url = f"{AGCOM_MAP_BASE}?center={lon:.6f},{lat:.6f}&level={level}"
    return {"url": url, "level": level}


# ──────────────────────── BUILD SHARDS ─────────────────────────────

def _choose_level(famiglie_residenti: int | None) -> int:
    """Scegli zoom level AGCOM in base alla taglia del comune.

    AGCOM WebAppBuilder usa scale ESRI:
      level 12 ≈ 1:73k   (Roma, Milano: si vede tutto il comune)
      level 13 ≈ 1:36k   (capoluoghi medi)
      level 14 ≈ 1:18k   (comuni medi)
      level 15 ≈ 1:9k    (comuni piccoli)
    """
    if famiglie_residenti is None:
        return 14
    if famiglie_residenti >= 200_000:
        return 12
    if famiglie_residenti >= 30_000:
        return 13
    if famiglie_residenti >= 3_000:
        return 14
    return 15


def build_shards(rows: list[dict],
                 centroids: dict[str, tuple[float, float]],
                 now_iso: str, data_period: str) -> dict[str, dict]:
    """Costruisce dict istat6 -> payload shard."""
    shards: dict[str, dict] = {}
    for r in rows:
        istat = r["istat"]
        fam_res = r["famiglie_residenti"]
        level = _choose_level(fam_res)
        lat_lon = centroids.get(istat)
        if lat_lon:
            mappa = build_deeplink(lat_lon[0], lat_lon[1], level=level)
        else:
            mappa = build_deeplink(None, None, level=level)
        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_LABEL,
            "_source_url": SOURCE_URL,
            "_license": LICENSE_STR,
            "_data_period": data_period,
            "_generated_at": now_iso,
            "kpi": {
                "famiglie_residenti":        fam_res,
                "famiglie_ftth":             r["famiglie_ftth"],
                "famiglie_ftth_20m":         r["famiglie_ftth_20m"],
                "copertura_ftth_desi_pct":   r["copertura_ftth_desi_pct"],
                "copertura_ftth_20m_pct":    r["copertura_ftth_20m_pct"],
                "confidenza_desi_pct":       r["confidenza_desi_pct"],
                "celle_20m_raggiunte":       r["celle_20m_raggiunte"],
                "celle_20m_ftth":            r["celle_20m_ftth"],
                "celle_20m_fttc":            r["celle_20m_fttc"],
                "punti_dichiarati":          r["punti_dichiarati"],
                "punti_dichiarati_ftth":     r["punti_dichiarati_ftth"],
                "punti_geo_distinti":        r["punti_geo_distinti"],
                "punti_geo_distinti_ftth":   r["punti_geo_distinti_ftth"],
                "indirizzi_postali_distinti": r["indirizzi_postali_distinti"],
                "indirizzi_postali_distinti_ftth": r["indirizzi_postali_distinti_ftth"],
            },
            "anagrafica_locale": {
                "regione":   r["regione"],
                "provincia": r["provincia"],
                "comune":    r["comune"],
            },
            "mappa_ufficiale": mappa,
        }
    log.info("agcom_shards_built", n=len(shards))
    return shards


# ──────────────────────── META / SKIP CHECK ─────────────────────────

def read_last_known_sha() -> str | None:
    """Legge l'ultimo SHA dal _meta.json locale."""
    meta = local_lookup.load_meta("agcom_bbmap")
    return meta.get("sha256")


def write_meta(sha: str, n_shards: int, data_period: str, item_id: str) -> None:
    """Scrive _meta.json locale con SHA + metadati."""
    local_lookup.save_meta("agcom_bbmap", {
        "sha256": sha,
        "n_shards": n_shards,
        "data_period": data_period,
        "item_id": item_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "etl_version": ETL_VERSION,
    })


# ──────────────────────── PUSH LOCAL ────────────────────────────────

def _md5_of_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def write_shards_local(shards: dict[str, dict], outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, payload in shards.items():
        (outdir / f"{istat}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        n += 1
    log.info("agcom_local_done", written=n, dir=str(outdir))
    return n


# ──────────────────────── MAIN ──────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL AGCOM Broadband Map — copertura banda larga comunale")
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help=f"Output dir (default: {DEFAULT_OUTDIR})")
    parser.add_argument("--force", action="store_true",
                        help="Forza rebuild anche se SHA del CSV invariato")
    args = parser.parse_args()

    log.info("etl_agcom_bbmap_start", version=ETL_VERSION)

    # Item corrente + periodo
    item_id, aammgg, dinamico = resolve_latest_item()
    data_period = period_label(aammgg)

    # Mai regredire: un periodo piu vecchio di quello in produzione non
    # sovrascrive gli shard (vale anche con --force).
    last_period = local_lookup.load_meta("agcom_bbmap").get("data_period")
    last_key = label_to_key(last_period)
    if last_key and period_key(aammgg) < last_key:
        log.error("agcom_regressione_bloccata", periodo_risolto=data_period,
                  periodo_in_produzione=last_period, dinamico=dinamico)
        return 3

    # Fetch + sha
    body, sha = fetch_agcom_csv(item_id)

    # Skip check
    if not args.force:
        last = read_last_known_sha()
        if last == sha:
            log.info("agcom_skip", reason="sha_unchanged", sha=sha[:16])
            return 0
        log.info("agcom_run", last_sha=(last or "")[:16], current_sha=sha[:16])

    # Parse
    rows = parse_csv(body)
    if len(rows) < 7000:
        log.error("agcom_too_few_rows", got=len(rows),
                  expected_min=7000)
        return 2

    # Codici assenti dall'anagrafica (es. nuove province sarde) -> per nome
    esito = remap_codes(rows)
    if esito is None:
        return 4
    _, irrisolti = esito
    if len(irrisolti) > MAX_UNRESOLVED:
        log.error("agcom_troppi_irrisolti", n=len(irrisolti),
                  soglia=MAX_UNRESOLVED, esempi=irrisolti[:10])
        return 5
    if irrisolti:
        rows = [r for r in rows if r["istat"] not in
                {x["pro_com"] for x in irrisolti}]

    # Centroidi locali (fallback se anagrafica non disponibile)
    centroids = load_anagrafica_centroids()

    # Build shards
    now_iso = datetime.now(timezone.utc).isoformat()
    shards = build_shards(rows, centroids, now_iso, data_period)

    # Sample log (comuni notevoli)
    for sample in ["058091", "015146", "077014", "075035", "097055", "007003"]:
        sh = shards.get(sample)
        if sh:
            kpi = sh["kpi"]
            log.info("sample", istat=sample,
                     nome=sh["anagrafica_locale"]["comune"],
                     ftth_desi=kpi["copertura_ftth_desi_pct"],
                     ftth_20m=kpi["copertura_ftth_20m_pct"],
                     fam_res=kpi["famiglie_residenti"],
                     fam_ftth=kpi["famiglie_ftth"])
        else:
            log.info("sample_no_data", istat=sample)

    # Write local
    n_written = write_shards_local(shards, args.outdir)
    write_meta(sha, n_written, data_period, item_id)

    # Manifest update best-effort
    try:
        manifest.update_source(
            "agcom_bbmap",
            [{"key": "agcom_bbmap/*", "count": n_written}],
            status="ok",
        )
    except Exception as e:
        log.warning("manifest_update_skipped", error=str(e))

    log.info("etl_agcom_bbmap_done", comuni=n_written, periodo=data_period,
             item_id=item_id, dinamico=dinamico)
    return 0


if __name__ == "__main__":
    sys.exit(main())
