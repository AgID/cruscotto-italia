"""ETL Scuole MIUR - Anagrafe scuole statali per anno scolastico.

Dataset upstream: DS0400SCUANAGRAFESTAT (Portale Unico dei Dati della Scuola,
dati.istruzione.it). CSV ~13MB con ~50k righe (1 per plesso scolastico).
Copertura nazionale COMPLETA combinando 2 dataset MIUR:
  - DS0400SCUANAGRAFESTAT  : scuole statali (resto d'Italia, ~50k righe, 13 MB)
  - DS0420SCUANAAUTSTAT    : scuole equiparate PA Trento, Bolzano, Aosta
                              (~1.5k righe, 312 KB)
Schema CSV identico tranne `SEDESCOLASTICA` assente nel secondo.

Aggiornamento upstream: annuale (settembre).

Strategia:
  1) Download CSV anno scolastico corrente (file URL pattern:
     SCUANAGRAFESTAT<YYYYYY><YYYYMMDD>.csv, es 2025/26 al 01/09/2025).
  2) Mappa codice catastale -> codice ISTAT via lookup/comuni-bundle.json.
  3) Aggrega per comune: lista scuole + KPI (totale, sedi direttivo,
     distribuzione per macro-ordine).
  4) Output: scuole/<istat>.json per ognuno dei ~6657 comuni con scuole +
     comuni senza scuole (shard vuoto coerente).

Input upstream: dati.istruzione.it CSV (IODL 2.0).

Output schema:
{
  "_etl_version": "0.1.0",
  "_source": "MIUR DS0400SCUANAGRAFESTAT",
  "_generated_at": "ISO-8601",
  "anno_scolastico": "2025/26",
  "data_estrazione": "2025-09-01",
  "kpi": {
    "n_scuole": 12,
    "n_sedi_direttivo": 5,
    "per_ordine": {"infanzia": 3, "primaria": 4, "sec1": 2, "sec2": 2, "altro": 1}
  },
  "scuole": [
    {
      "codice_scuola": "...",
      "codice_istituto_riferimento": "...",
      "denominazione": "...",
      "denominazione_istituto": "...",
      "indirizzo": "...",
      "cap": "...",
      "tipologia": "SCUOLA PRIMARIA",
      "macro_ordine": "primaria",
      "caratteristica": "NORMALE",
      "sede_direttivo": false,
      "sede_scolastica": false,
      "email": "...",
      "pec": "...",
      "sito_web": "..."
    }
  ]
}

Usage:
  python -m etl.sources.scuole
  python -m etl.sources.scuole --anno=202425   # forza anno specifico
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import requests
import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger(__name__)

# -----------------------------------------------------------------------------
SOURCE_NAME = "MIUR DS0400SCUANAGRAFESTAT + DS0420SCUANAAUTSTAT"
ETL_VERSION = "0.1.0"

# URL pattern: per anno scolastico 2025/26 (dati al 01/09/2025) il file e':
#   SCUANAGRAFESTAT20252620250901.csv
# I file sono pubblicati il 01/09 dell'anno di inizio per l'A.S. corrente.
BASE_URL = "https://dati.istruzione.it/opendata/opendata/catalogo/elements1/leaf"

CACHE_DIR = Path("/tmp/cruscotto-scuole-cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def macro_ordine(tipologia: str) -> str:
    """Raggruppa le ~45 tipologie MIUR in 5 macro-ordini per il rendering."""
    t = (tipologia or "").upper().strip()
    if t == "SCUOLA INFANZIA":
        return "infanzia"
    if t == "SCUOLA PRIMARIA":
        return "primaria"
    if t == "SCUOLA PRIMO GRADO":
        return "sec1"
    if t.startswith("LICEO") or t.startswith("ISTITUTO TECNICO") or \
       t.startswith("IST TEC") or t.startswith("ISTITUTO PROFESSIONALE") or \
       t.startswith("IST PROF") or t == "ISTITUTO MAGISTRALE" or \
       t == "ISTITUTO SUPERIORE" or t == "ISTITUTO D'ARTE":
        return "sec2"
    # ISTITUTO COMPRENSIVO non e' una scuola ma un raggruppamento
    # amministrativo. Lo classifichiamo come "altro" per non gonfiare i numeri.
    return "altro"


def default_anno_scolastico() -> tuple[str, str, str]:
    """Anno scolastico corrente (es. '202526' / '20250901' / '2025/26').

    Logica: se siamo a o dopo settembre, A.S. e' anno_corrente/anno_corrente+1.
    Altrimenti anno_corrente-1/anno_corrente.
    """
    today = dt.date.today()
    if today.month >= 9:
        y0 = today.year
    else:
        y0 = today.year - 1
    y1 = y0 + 1
    anno_codice = f"{y0}{str(y1)[-2:]}"          # '202526'
    data_estrazione = f"{y0}0901"                 # '20250901'
    anno_label = f"{y0}/{str(y1)[-2:]}"           # '2025/26'
    return anno_codice, data_estrazione, anno_label


def _download_one(fname: str, force: bool) -> Path:
    """Scarica singolo CSV upstream. Cache su disco locale."""
    out = CACHE_DIR / fname
    if out.exists() and not force:
        log.info("scuole_cached", path=str(out), size=out.stat().st_size)
        return out
    url = f"{BASE_URL}/{fname}"
    log.info("scuole_downloading", url=url)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    with open(out, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    log.info("scuole_downloaded", size=out.stat().st_size, path=str(out))
    return out


def download_csvs(anno_codice: str, data_estrazione: str, force: bool = False) -> list[Path]:
    """Scarica entrambi i CSV anagrafe scuole per l'A.S. specificato.

    Filename pattern MIUR:
      - SCUANAGRAFESTAT<anno_codice><data_estrazione>.csv  (Italia esclusa PA)
      - SCUANAAUTSTAT<anno_codice><data_estrazione>.csv    (PA TN/BZ/AO)
    """
    paths = []
    for prefix in ("SCUANAGRAFESTAT", "SCUANAAUTSTAT"):
        fname = f"{prefix}{anno_codice}{data_estrazione}.csv"
        paths.append(_download_one(fname, force))
    return paths


def load_cat_to_istat() -> dict[str, str]:
    """Mappa codice catastale -> codice ISTAT dal bundle anagrafica locale."""
    log.info("anagrafica_loading")
    bundle = local_lookup.load_comuni_bundle()
    if bundle is None:
        raise SystemExit(
            "Local lookup 'comuni-bundle.json' assente in "
            f"{local_lookup.get_lookup_dir()}. "
            "Esegui prima 'python -m etl.sources.anagrafica'."
        )
    cat_to_istat = {}
    for istat, c in bundle.items():
        cc = (c.get("codice_catastale") or "").strip()
        if cc:
            cat_to_istat[cc] = istat
    log.info("anagrafica_loaded", n_comuni=len(cat_to_istat))
    return cat_to_istat


def normalize_field(s) -> str | None:
    """Normalizza un campo CSV: 'Non Disponibile'/'NON DISPONIBILE'/empty -> None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.lower() in ("non disponibile", "n.d.", "nd"):
        return None
    return s


def extract_url(s) -> str | None:
    """Il MIUR pubblica i siti web in formato markdown: [text](url).
    Esempio: '[www.icpucciano.it](https://www.icpucciano.it)'.
    Estrae l'URL pulito o ritorna la stringa originale se non e' in markdown.
    """
    s = normalize_field(s)
    if not s:
        return None
    # Pattern markdown ovunque nella stringa: [text](url)
    import re as _re
    m = _re.search(r'\[([^\]]+)\]\(([^)]+)\)', s)
    if m:
        url = m.group(2).strip()
        url = url.replace("https//", "https://").replace("http//", "http://")
        return url
    # Stringa libera: gestisco edge case "https//"
    s2 = s.replace("https//", "https://").replace("http//", "http://")
    return s2


def build_shards(csv_paths: list[Path], cat_to_istat: dict[str, str],
                 anno_label: str, data_estrazione: str) -> dict[str, dict]:
    """Costruisce un dict istat -> shard JSON dai CSV."""
    log.info("scuole_aggregating", n_csv=len(csv_paths))

    by_istat: dict[str, list[dict]] = defaultdict(list)
    unmapped = 0
    total = 0

    for csv_path in csv_paths:
      with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            total += 1
            cat = (r.get("CODICECOMUNESCUOLA") or "").strip()
            istat = cat_to_istat.get(cat)
            if not istat:
                unmapped += 1
                continue
            tip = normalize_field(r.get("DESCRIZIONETIPOLOGIAGRADOISTRUZIONESCUOLA"))
            sito = extract_url(r.get("SITOWEBSCUOLA"))
            scuola = {
                "codice_scuola":               normalize_field(r.get("CODICESCUOLA")),
                "codice_istituto_riferimento": normalize_field(r.get("CODICEISTITUTORIFERIMENTO")),
                "denominazione":               normalize_field(r.get("DENOMINAZIONESCUOLA")),
                "denominazione_istituto":      normalize_field(r.get("DENOMINAZIONEISTITUTORIFERIMENTO")),
                "indirizzo":                   normalize_field(r.get("INDIRIZZOSCUOLA")),
                "cap":                         normalize_field(r.get("CAPSCUOLA")),
                "tipologia":                   tip,
                "macro_ordine":                macro_ordine(tip or ""),
                "caratteristica":              normalize_field(r.get("DESCRIZIONECARATTERISTICASCUOLA")),
                "sede_direttivo":              (r.get("INDICAZIONESEDEDIRETTIVO") or "").strip().upper() == "SI",
                "sede_scolastica":             (r.get("SEDESCOLASTICA") or "").strip().upper() == "SI",
                "email":                       normalize_field(r.get("INDIRIZZOEMAILSCUOLA")),
                "pec":                         normalize_field(r.get("INDIRIZZOPECSCUOLA")),
                "sito_web":                    sito,
            }
            by_istat[istat].append(scuola)

    log.info("scuole_aggregated", total_rows=total, mapped=total-unmapped,
             unmapped=unmapped, comuni_distinct=len(by_istat))

    # Costruzione shard finali (con KPI)
    shards = {}
    for istat, scuole in by_istat.items():
        # Ordino per macro_ordine (infanzia, primaria, sec1, sec2, altro) e poi denominazione
        order_rank = {"infanzia":0, "primaria":1, "sec1":2, "sec2":3, "altro":4}
        scuole.sort(key=lambda s: (order_rank.get(s["macro_ordine"], 99),
                                    s["denominazione"] or ""))
        per_ordine = {"infanzia":0, "primaria":0, "sec1":0, "sec2":0, "altro":0}
        n_sedi_direttivo = 0
        for s in scuole:
            per_ordine[s["macro_ordine"]] += 1
            if s["sede_direttivo"]:
                n_sedi_direttivo += 1
        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_NAME,
            "_generated_at": dt.datetime.utcnow().isoformat() + "Z",
            "anno_scolastico": anno_label,
            "data_estrazione": f"{data_estrazione[:4]}-{data_estrazione[4:6]}-{data_estrazione[6:8]}",
            "kpi": {
                "n_scuole": len(scuole),
                "n_sedi_direttivo": n_sedi_direttivo,
                "per_ordine": per_ordine,
            },
            "scuole": scuole,
        }
    return shards


def write_local(shards: dict[str, dict], outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for istat, data in shards.items():
        with open(outdir / f"{istat}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        n += 1
    log.info("scuole_local_done", n=n, outdir=str(outdir))
    return n


def main() -> int:
    p = argparse.ArgumentParser(description="ETL Scuole MIUR")
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    p.add_argument("--target", choices=["local"], default="local",
                   help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    p.add_argument("--anno", default=None,
                   help="A.S. codice (es '202526'). Default: A.S. corrente.")
    p.add_argument("--no-cache", action="store_true",
                   help="Forza re-download CSV upstream.")
    p.add_argument("--outdir", default="/var/www/cruscotto-italia/data/scuole",
                   help="Output dir")
    args = p.parse_args()

    if args.anno:
        # Default 1 settembre dell'anno y0
        y0 = int(args.anno[:4])
        anno_codice = args.anno
        data_estrazione = f"{y0}0901"
        anno_label = f"{y0}/{str(y0+1)[-2:]}"
    else:
        anno_codice, data_estrazione, anno_label = default_anno_scolastico()

    log.info("etl_start", anno_scolastico=anno_label)

    csv_paths = download_csvs(anno_codice, data_estrazione, force=args.no_cache)
    cat_to_istat = load_cat_to_istat()
    shards = build_shards(csv_paths, cat_to_istat, anno_label, data_estrazione)

    write_local(shards, Path(args.outdir))

    # Manifest update best-effort
    try:
        out_dir = Path(args.outdir)
        files = [{"name": f.name,
                  "size": f.stat().st_size,
                  "key": f"scuole/{f.name}"}
                 for f in sorted(out_dir.glob("*.json"))]
        manifest.update_source("scuole", files, status="ok")
        log.info("scuole_manifest_updated", n_files=len(files))
    except Exception as e:
        log.warning("scuole_manifest_update_skipped", err=str(e))

    log.info("etl_done", comuni_with_data=len(shards),
             anno_scolastico=anno_label, data_estrazione=data_estrazione)
    return 0


if __name__ == "__main__":
    sys.exit(main())
