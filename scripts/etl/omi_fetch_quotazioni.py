#!/usr/bin/env python3
"""Raccolta quotazioni immobiliari OMI — Agenzia delle Entrate.

Stadio 2 di 2 della raccolta semestrale (lo stadio 1 e' omi_fetch_perimetri.py).
Scrive un file grezzo per comune, che etl/sources/omi.py trasforma in shard.

Durata e volume
---------------
~98.000 richieste, ~12 ore con 2 worker e 0,7 s di pausa. E' il costo di una
fonte senza bulk pubblico: il download massivo esiste ma passa dall'area
riservata dell'Agenzia (Fisconline/Entratel), quindi non e' automatizzabile.

Il ritmo e' deliberatamente basso. Misurato il 03/10/2026: il servizio regge
1,4 req/s senza degrado e senza limiti di concorrenza fino a 3 richieste
simultanee, ma le condizioni d'uso dell'Agenzia prevedono la limitazione
dell'accesso "nel caso di attivita' dell'utente che possano compromettere,
limitare o disturbare il corretto funzionamento dei servizi". A 2 worker e
0,7 s il carico resta sotto quello di un singolo utente che naviga.
Non alzare questi valori senza una ragione e senza riconcordarlo.

Lo User-Agent e' dichiarativo di proposito: nei log dell'Agenzia deve essere
riconoscibile una PA che preleva dati CC-BY, non un client anonimo.

Catena delle richieste, per comune
----------------------------------
  richiesta=3  -> zone del comune (ZONA, FASCIA, DIZIONE, LINK_ZONA)
  richiesta=8  -> destinazioni d'uso presenti nella zona
  stampaomi.php -> la tabella dei valori (HTML)

stampaomi.php usa path posizionali:
  {codcom}/{LINK_ZONA}/{semestre}/{inizialeDestinazione}/{zona}/{E}/{N}
Le coordinate E/N servono solo a centrare le mappe di corredo e non
influiscono sui dati: si passano a zero.

Parsing
-------
I valori stanno in celle marcate da attributi headers con APICI SINGOLI
(headers='vm vmmin'), non doppi. Il parser si aggancia al numero di celle
della riga (8) e alla posizione, e verifica gli headers quando presenti:
  0 tipologia · 1 stato · 2-3 compravendita min/max · 4 superficie
  5-6 locazione min/max · 7 superficie
Decimali con la virgola. Lo stato conservativo arriva con maiuscole
incoerenti dalla fonte (NORMALE / Normale / OTTIMO / Ottimo): si
normalizza a maiuscolo, altrimenti i raggruppamenti si spaccano.
La chiave di una riga e' tipologia + stato, non la sola tipologia: la
stessa tipologia puo' comparire piu' volte con stati diversi.

Uso:
    python3 -u scripts/etl/omi_fetch_quotazioni.py --semestre 20252
    setsid nohup python3 -u scripts/etl/omi_fetch_quotazioni.py \
        --semestre 20261 > /var/log/cruscotto-etl/omi-quotazioni.log 2>&1 &
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi"
UA = "CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it; AgID)"
REFERER = f"{BASE}/index.htm"

DEFAULT_OUTDIR = Path("/home/ubuntu/omi_build/shard")
DEFAULT_GEODIR = Path("/home/ubuntu/omi_build/geojson")
DEFAULT_LOOKUP = Path("/home/ubuntu/catasto_test/lookup_belfiore_to_istat.json")
DEFAULT_ANAG = Path("/var/www/cruscotto-italia/data/lookup/istat_comuni.parquet")

PAUSA = 0.7
WORKER = 2
TIMEOUT = 30
MAX_FALLIMENTI = 5   # consecutivi: oltre, il servizio non risponde e ci si ferma

SIGLA = {"Residenziale": "R", "Commerciale": "C",
         "Produttiva": "P", "Terziaria": "T"}

# Statistiche residenziali sulle sole tipologie abitative: box e posti auto
# abbasserebbero i valori senza rappresentare il mercato della casa.
ABITATIVE = {"Abitazioni civili", "Abitazioni di tipo economico",
             "Abitazioni signorili", "Ville e Villini"}

_falliti_consecutivi = 0


def get(url: str, tentativi: int = 4):
    global _falliti_consecutivi
    for n in range(tentativi):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
                _falliti_consecutivi = 0
                return f.read().decode("utf-8", "replace")
        except Exception as e:
            if n == tentativi - 1:
                print(f"      KO {e}", flush=True)
            time.sleep(2 * (2 ** n))
    _falliti_consecutivi += 1
    if _falliti_consecutivi >= MAX_FALLIMENTI:
        sys.exit(f"STOP: {MAX_FALLIMENTI} fallimenti consecutivi, il servizio non risponde")
    return None


def parse(html: str) -> list:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        celle = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S)
        if len(celle) != 8:
            continue
        txt = [re.sub(r"<[^>]*>", "", v).replace("&nbsp;", " ").strip()
               for _, v in celle]
        attr = [a for a, _ in celle]

        def num(i):
            try:
                return float(txt[i].replace(".", "").replace(",", "."))
            except ValueError:
                return None

        # controllo non bloccante: se il markup dell'Agenzia cambia, lo si
        # vede nel log invece di scoprirlo dai dati sbagliati
        h2 = re.search(r"headers='([^']*)'", attr[2])
        if h2 and "vmmin" not in h2.group(1):
            print(f"      ATTENZIONE: headers inatteso {h2.group(1)!r}", flush=True)

        out.append({"tipologia": txt[0], "stato": txt[1].upper(),
                    "cv_min": num(2), "cv_max": num(3), "sup_cv": txt[4],
                    "loc_min": num(5), "loc_max": num(6), "sup_loc": txt[7]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Raccolta quotazioni OMI per comune")
    ap.add_argument("--semestre", required=True, help="Formato AAAAS, es. 20252")
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--geodir", type=Path, default=DEFAULT_GEODIR)
    ap.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP)
    ap.add_argument("--anagrafica", type=Path, default=DEFAULT_ANAG)
    ap.add_argument("--worker", type=int, default=WORKER)
    ap.add_argument("--pausa", type=float, default=PAUSA)
    args = ap.parse_args()

    if not re.fullmatch(r"\d{5}", args.semestre):
        print(f"semestre non valido: {args.semestre}", file=sys.stderr)
        return 2
    if args.worker > 3 or args.pausa < 0.5:
        print("worker > 3 o pausa < 0,5 s: valori non concordati con la fonte",
              file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    lookup = json.loads(args.lookup.read_text(encoding="utf-8"))

    anag = None
    if args.anagrafica.is_file():
        try:
            import pandas as pd
            anag = pd.read_parquet(args.anagrafica).set_index("codice_istat")
        except Exception as e:
            print(f"anagrafica non leggibile ({e}): provincia dal servizio", flush=True)

    prov = json.loads(get(f"{BASE}/zoneomi.php?richiesta=1") or "[]")
    comuni: dict[str, dict] = {}
    for p in prov:
        for c in json.loads(get(f"{BASE}/zoneomi.php?richiesta=2&prov={p['PROVINCIA']}") or "[]"):
            comuni[c["CODCOM"]] = {"nome": c["DIZIONE"], "prov": p["PROVINCIA"]}
        time.sleep(args.pausa)
    print(f"comuni da processare: {len(comuni)} | semestre {args.semestre}", flush=True)

    def lavora(item):
        bel, meta = item
        info = lookup.get(bel)
        if not info:
            print(f"  SALTO {bel} {meta['nome']} (assente dal lookup)", flush=True)
            return
        istat = info["istat"]
        if (args.outdir / f"{istat}.json").exists():
            return

        zs = get(f"{BASE}/zoneomi.php?richiesta=3&codcom={bel}")
        if zs is None:
            return
        zone_out, sup = [], set()
        for z in json.loads(zs):
            dest = {}
            d = get(f"{BASE}/zoneomi.php?richiesta=8&codcom={bel}"
                    f"&semestre={args.semestre}&zo={z['ZONA']}")
            for t in (json.loads(d) if d else []):
                s = SIGLA.get(t["DESCR_TIPOLOGIA"])
                if not s:
                    continue
                time.sleep(args.pausa)
                h = get(f"{BASE}/stampaomi.php?{bel}/{z['LINK_ZONA']}/"
                        f"{args.semestre}/{s}/{z['ZONA']}/0/0")
                v = parse(h) if h else []
                if v:
                    dest[t["DESCR_TIPOLOGIA"]] = v
                    sup.update(x["sup_cv"] for x in v)
            if not dest:
                continue
            primo = "R" if "Residenziale" in dest else SIGLA[next(iter(dest))]
            zone_out.append({
                "zona": z["ZONA"], "fascia": z["FASCIA"], "dizione": z["DIZIONE"],
                "link_zona": z["LINK_ZONA"],
                "url_omi": (f"{BASE}/stampaomi.php?{bel}/{z['LINK_ZONA']}/"
                            f"{args.semestre}/{primo}/{z['ZONA']}/0/0"),
                "destinazioni": dest})

        if not zone_out:
            print(f"  {bel} {meta['nome']}: nessuna quotazione", flush=True)
            return

        ab = [x for z in zone_out
              for x in z["destinazioni"].get("Residenziale", [])
              if x["tipologia"] in ABITATIVE]
        vals = ([x["cv_min"] for x in ab if x["cv_min"]] +
                [x["cv_max"] for x in ab if x["cv_max"]])
        per_zona = {}
        for z in zone_out:
            v = [x["cv_max"] for x in z["destinazioni"].get("Residenziale", [])
                 if x["tipologia"] in ABITATIVE and x["cv_max"]]
            if v:
                per_zona[z["zona"]] = (max(v), z["dizione"])

        provincia = meta["prov"]
        if anag is not None:
            try:
                provincia = anag.loc[istat]["provincia"]
            except KeyError:
                pass

        kpi = {"n_zone": len(zone_out),
               "ha_perimetri": (args.geodir / f"{istat}.geojson").exists(),
               "residenziale_min": min(vals) if vals else None,
               "residenziale_max": max(vals) if vals else None,
               "residenziale_medio": round(sum(vals) / len(vals), 1) if vals else None,
               "sup_mista": len(sup) > 1}
        if per_zona:
            mx = max(per_zona.items(), key=lambda x: x[1][0])
            mn = min(per_zona.items(), key=lambda x: x[1][0])
            kpi["zona_piu_cara"] = {"zona": mx[0], "dizione": mx[1][1], "valore": mx[1][0]}
            kpi["zona_piu_economica"] = {"zona": mn[0], "dizione": mn[1][1], "valore": mn[1][0]}

        (args.outdir / f"{istat}.json").write_text(json.dumps({
            "istat_code": istat, "comune": info["name"], "provincia": provincia,
            "regione": info["regione"], "codcom": bel, "semestre": args.semestre,
            "kpi": kpi, "zone": zone_out}, ensure_ascii=False), encoding="utf-8")

        n = len(list(args.outdir.glob("*.json")))
        print(f"  [{n}/{len(comuni)}] {bel} {info['name'][:22]:22} "
              f"{len(zone_out):>3} zone", flush=True)

    t0 = time.time()
    print(f"avvio: {args.worker} worker, pausa {args.pausa}s - "
          f"{time.strftime('%F %T')}", flush=True)
    with ThreadPoolExecutor(max_workers=args.worker) as ex:
        list(ex.map(lavora, sorted(comuni.items())))

    prodotti = len(list(args.outdir.glob("*.json")))
    print(f"\ncompletato in {(time.time() - t0) / 3600:.1f} h - {prodotti} shard grezzi",
          flush=True)
    return 0 if prodotti >= 7000 else 1


if __name__ == "__main__":
    sys.exit(main())
