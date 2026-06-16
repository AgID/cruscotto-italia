#!/usr/bin/env python3
"""ETL classificazione_sismica — Dipartimento Protezione Civile (DPC).

Fonte: https://rischi.protezionecivile.gov.it/it/sismico/attivita/classificazione-sismica/
Licenza: CC-BY 4.0 (Note legali DPC). Elaborazione da dato DPC.
Cadenza: annuale (allineata al ciclo Territorio/ISPRA).

Scrive uno shard per-comune /data/classificazione_sismica/<istat>.json
(scalare: zona principale 1-4 + sottozona regionale), letto dal builder
Territorio in fase di esposizione. ETL indipendente: non scrive in altri shard.

Override dir dati per test: env CRUSCOTTO_DATA_DIR.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SOURCE = "classificazione_sismica"
BASE = "https://rischi.protezionecivile.gov.it"
PAGE = BASE + "/it/sismico/attivita/classificazione-sismica/"
UA = {"User-Agent": "cruscotto-italia-etl (+https://cruscotto-italia.dati.gov.it)"}
DATA_DIR = Path(os.environ.get("CRUSCOTTO_DATA_DIR", "/var/www/cruscotto-italia/data"))

# Banda di sanita': il conteggio comuni varia ogni anno (fusioni), quindi NO ==
# fisso. La banda intercetta download troncati senza falsi allarmi sulle fusioni.
MIN_COMUNI, MAX_COMUNI = 7000, 8200

# Legenda zone (OPCM 3519/06). Unica fonte di verita' per il rendering frontend.
LEGENDA = {
    "1": {"label": "Zona 1", "ag": "0,25 < ag <= 0,35 g",
          "desc": "Alta pericolosita': forti terremoti probabili."},
    "2": {"label": "Zona 2", "ag": "0,15 < ag <= 0,25 g",
          "desc": "Forti terremoti possibili."},
    "3": {"label": "Zona 3", "ag": "0,05 < ag <= 0,15 g",
          "desc": "Forti terremoti meno probabili rispetto a zona 1 e 2."},
    "4": {"label": "Zona 4", "ag": "ag <= 0,05 g",
          "desc": "Pericolosita' piu' bassa."},
}


def log(level, msg, **kw):
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
           "level": level, "src": SOURCE, "msg": msg}
    rec.update(kw)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def discover_csv():
    """Scopre l'URL del CSV dalla pagina DPC, robusto all'hash variabile."""
    html = http_get(PAGE, timeout=30).decode("utf-8", "ignore")
    hits = sorted(set(re.findall(
        r"/static/[a-f0-9]+/classificazione-sismica[^\"]*\.csv", html)))
    if not hits:
        raise SystemExit("[FATAL] link CSV non trovato nella pagina DPC "
                         "(naming pagina cambiato?). Shard NON toccati.")
    if len(hits) > 1:
        log("WARN", "piu' link CSV trovati, uso il primo", candidati=hits)
    return BASE + hits[0]


def version_label(url):
    m = re.search(r"classificazione-sismica-aggiornata-([a-z]+-\d{4})\.csv", url)
    return m.group(1) if m else "sconosciuta"


def load_meta():
    f = DATA_DIR / SOURCE / "_meta.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def parse(raw):
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    recs, scartate = [], 0
    for row in reader:
        istat = (row.get("COD_ISTAT_COMUNE") or "").strip().zfill(6)
        zona = (row.get("ZONA_SISMICA") or "").strip()
        zp = re.match(r"(\d)", zona)
        if not istat.isdigit() or not zona or not zp:
            scartate += 1
            log("WARN", "riga scartata", row=row)
            continue
        recs.append({
            "istat_code": istat,
            "comune": (row.get("COMUNE") or "").strip(),
            "provincia": (row.get("PROV_CITTA_METROPOLITANA") or "").strip(),
            "sigla_prov": (row.get("SIGLA_PROV") or "").strip(),
            "regione": (row.get("REGIONE") or "").strip(),
            "zona_sismica": zona,
            "zona_principale": int(zp.group(1)),
        })
    if scartate:
        log("WARN", "righe scartate totali", n=scartate)
    return recs


def push_to_r2(shard_dir, force_shard_upload=False):
    """Push shard + _meta su R2 (prefix classificazione_sismica/), skip-by-md5.
    Pattern allineato a aria.py: list_objects_v2 + diff md5/ETag + pool 24.
    NB: import lazy di r2 cosi' su AgID (--target=local) il killswitch non scatta mai."""
    from etl.lib import r2
    client = r2.get_r2_client()
    bucket = r2.get_bucket()
    r2.upload_file(shard_dir / "_meta.json",
                   "classificazione_sismica/_meta.json",
                   content_type="application/json")
    shard_files = sorted(x for x in shard_dir.glob("*.json")
                         if x.name != "_meta.json")
    remote_etag = {}
    try:
        pag = client.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=bucket,
                                 Prefix="classificazione_sismica/"):
            for o in page.get("Contents", []):
                remote_etag[o["Key"].split("/")[-1]] = (
                    o.get("ETag") or "").strip('"').lower()
        log("INFO", "remote elencati", count=len(remote_etag))
    except Exception as e:
        log("WARN", "list remota fallita", error=str(e))

    def _md5(path):
        h = hashlib.md5()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    if force_shard_upload:
        to_up = list(shard_files)
    else:
        to_up = [sf for sf in shard_files
                 if remote_etag.get(sf.name) != _md5(sf)]
    log("INFO", "shard da caricare",
        totale=len(shard_files), da_caricare=len(to_up))

    def _one(sf):
        r2.upload_file(sf, f"classificazione_sismica/{sf.name}",
                       content_type="application/json")

    uploaded = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(_one, sf): sf for sf in to_up}
        for fut in as_completed(futs):
            try:
                fut.result()
                uploaded += 1
                if uploaded % 200 == 0:
                    log("INFO", "push progress",
                        uploaded=uploaded, totale=len(to_up))
            except Exception as e:
                log("ERROR", "upload fallito",
                    file=str(futs[fut]), error=str(e))
    return uploaded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="valida soltanto, non scrive shard")
    ap.add_argument("--target", choices=["local", "r2"], default="local",
                    help="local: solo filesystem (AgID); r2: filesystem + push R2 (Aruba)")
    ap.add_argument("--force-shard-upload", action="store_true",
                    help="bypassa il check md5, ricarica tutti gli shard (target=r2)")
    args = ap.parse_args()

    url = discover_csv()
    ver = version_label(url)
    log("INFO", "link scoperto", url=url, versione=ver)

    raw = http_get(url)
    sha = hashlib.sha256(raw).hexdigest()[:16]
    recs = parse(raw)
    n = len(recs)
    log("INFO", "record parsati", n=n, versione=ver, sha=sha)

    # --- Validazioni atomiche: nessuna scrittura se falliscono ---
    if not (MIN_COMUNI <= n <= MAX_COMUNI):
        raise SystemExit(f"[FATAL] conteggio comuni fuori banda ({n}); "
                         "download troncato? Shard NON toccati.")
    n_uniq = len({r["istat_code"] for r in recs})
    if n_uniq != n:
        raise SystemExit(f"[FATAL] {n - n_uniq} codici ISTAT duplicati; "
                         "shard NON toccati.")

    meta_old = load_meta()
    if meta_old.get("versione") and meta_old["versione"] != ver:
        log("INFO", "NUOVA RELEASE rilevata",
            precedente=meta_old.get("versione"), attuale=ver)
    elif meta_old.get("versione") == ver:
        log("INFO", "versione invariata rispetto all'ultimo run", versione=ver)

    dist = {}
    for r in recs:
        dist[r["zona_principale"]] = dist.get(r["zona_principale"], 0) + 1
    log("INFO", "distribuzione zona_principale", dist=dist)

    if args.dry_run:
        log("INFO", "dry-run: nessuna scrittura", n=n)
        return

    out = DATA_DIR / SOURCE
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for r in recs:
        (out / f"{r['istat_code']}.json").write_text(
            json.dumps(r, ensure_ascii=False), encoding="utf-8")
        written += 1

    meta = {
        "source": SOURCE,
        "fonte": "Dipartimento della Protezione Civile",
        "licenza": "CC-BY 4.0",
        "licenza_url": "https://creativecommons.org/licenses/by/4.0/",
        "pagina_fonte": PAGE,
        "url_dato": url,
        "versione": ver,
        "sha256_16": sha,
        "n_comuni": written,
        "aggiornato_il": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        "elaborazione": ("zfill(6) su codice ISTAT; "
                         "zona_principale estratta dalla sottozona"),
        "legenda": LEGENDA,
    }
    (out / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("INFO", "completato", shard_scritti=written, dir=str(out), versione=ver)

    if args.target == "r2":
        n = push_to_r2(out, force_shard_upload=args.force_shard_upload)
        log("INFO", "push R2 completato", caricati=n)


if __name__ == "__main__":
    main()
