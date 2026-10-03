#!/usr/bin/env python3
"""Raccolta perimetri delle zone OMI — Agenzia delle Entrate.

Scarica un ZIP per provincia dal servizio pubblico `perimetri.php` (107
richieste in tutto, ~10 minuti) e converte i KML in GeoJSON per comune,
indicizzati per codice ISTAT.

Il download provinciale e' una funzione ufficiale, documentata nella
"Guida alla Consultazione delle Quotazioni OMI" dell'Agenzia (marzo 2026):
"e' possibile scaricare gratuitamente i perimetri delle zone OMI in tutti
i comuni [...] a livello comunale o a livello provinciale".

Stadio 1 di 2 della raccolta semestrale. Lo stadio 2 e' omi_fetch_quotazioni.py;
entrambi scrivono dati grezzi che etl/sources/omi.py trasforma in shard.

Note sui dati
-------------
- LINKZONA negli ExtendedData e' SEMPRE vuoto: la chiave di join con le
  quotazioni e' CODCOM + CODZONA.
- Un Placemark = una zona; i multipoligoni e gli anelli interni stanno
  dentro il singolo Placemark, quindi non serve unire feature diverse.
- Gli ZIP contengono anche comuni soppressi per fusione, che l'elenco
  comuni (richiesta=2) non espone piu': vengono saltati perche' assenti
  dal lookup Belfiore.
- Le province OMI sono quelle CATASTALI (103, non 107): mancano le
  province istituite di recente, i cui comuni restano sotto quelle
  storiche. Irrilevante, perche' si mappa per Belfiore.

Uso:
    python3 -u scripts/etl/omi_fetch_perimetri.py --semestre 20252
    python3 -u scripts/etl/omi_fetch_perimetri.py --semestre 20261 --outdir /tmp/omi_geo
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi"
UA = "CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it; AgID)"
REFERER = f"{BASE}/index.htm"

DEFAULT_OUTDIR = Path("/home/ubuntu/omi_build/geojson")
DEFAULT_LOOKUP = Path("/home/ubuntu/catasto_test/lookup_belfiore_to_istat.json")

PAUSA = 1.5          # fra una provincia e l'altra
TIMEOUT = 180        # gli ZIP provinciali arrivano a qualche MB


def get(url: str, binario: bool = False, tentativi: int = 4):
    for n in range(tentativi):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
                b = f.read()
                return b if binario else b.decode("utf-8", "replace")
        except Exception as e:
            print(f"      ritento ({n + 1}/{tentativi}): {e}", flush=True)
            time.sleep(2 * (2 ** n))
    return None


def anelli(blocco: str) -> list:
    """[anello esterno, anelli interni...] da un <Polygon> KML."""
    out = []
    for tag in ("outerBoundaryIs", "innerBoundaryIs"):
        for b in re.findall(rf"<{tag}>(.*?)</{tag}>", blocco, re.S):
            c = re.search(r"<coordinates>(.*?)</coordinates>", b, re.S)
            if not c:
                continue
            punti = [p.split(",") for p in c.group(1).split()]
            out.append([[float(p[0]), float(p[1])] for p in punti if len(p) >= 2])
    return out


def converti(kml: str) -> list:
    """KML di un comune -> lista di feature GeoJSON."""
    feats = []
    for p in re.findall(r"<Placemark>(.*?)</Placemark>", kml, re.S):
        d = dict(re.findall(
            r'<Data name="([^"]+)">\s*<displayName>[^<]*</displayName>\s*<value>(.*?)</value>',
            p, re.S))
        poly = [a for a in (anelli(b) for b in re.findall(r"<Polygon>(.*?)</Polygon>", p, re.S)) if a]
        if not poly:
            continue
        nome = re.search(r"<name>(.*?)</name>", p, re.S)
        geom = ({"type": "Polygon", "coordinates": poly[0]} if len(poly) == 1
                else {"type": "MultiPolygon", "coordinates": poly})
        feats.append({
            "type": "Feature",
            "properties": {"codcom": d.get("CODCOM", "").strip(),
                           "zona": d.get("CODZONA", "").strip(),
                           "nome": (nome.group(1).strip() if nome else "")},
            "geometry": geom,
        })
    return feats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Raccolta perimetri zone OMI per provincia")
    ap.add_argument("--semestre", required=True,
                    help="Semestre nel formato AAAAS, es. 20252")
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP,
                    help="lookup_belfiore_to_istat.json")
    ap.add_argument("--ckpt", type=Path, default=None,
                    help="File di ripresa (default: <outdir>/.perimetri.ckpt)")
    args = ap.parse_args()

    if not re.fullmatch(r"\d{5}", args.semestre):
        print(f"semestre non valido: {args.semestre} (atteso AAAAS)", file=sys.stderr)
        return 2
    if not args.lookup.is_file():
        print(f"lookup assente: {args.lookup}", file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    ckpt = args.ckpt or (args.outdir / ".perimetri.ckpt")
    lookup = json.loads(args.lookup.read_text(encoding="utf-8"))

    fatte = set(ckpt.read_text().split()) if ckpt.exists() else set()
    if fatte:
        print(f"ripresa: {len(fatte)} province gia' fatte", flush=True)

    prov_raw = get(f"{BASE}/zoneomi.php?richiesta=1")
    if prov_raw is None:
        print("impossibile leggere l'elenco province", file=sys.stderr)
        return 1
    prov = json.loads(prov_raw)
    print(f"province: {len(prov)} | semestre {args.semestre}\n", flush=True)

    tot_com = tot_zone = saltati = falliti = 0
    for i, p in enumerate(prov, 1):
        sigla = p["PROVINCIA"]
        if sigla in fatte:
            print(f"[{i}/{len(prov)}] {sigla} gia' fatta", flush=True)
            continue
        b = get(f"{BASE}/perimetri.php?id=1&prov={sigla}&codcom=&"
                f"semestre={args.semestre}&formato=kml", binario=True)
        if b is None:
            falliti += 1
            print(f"[{i}/{len(prov)}] {sigla} FALLITA", flush=True)
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(b))
        except Exception as e:
            falliti += 1
            print(f"[{i}/{len(prov)}] {sigla} ZIP illeggibile: {e}", flush=True)
            continue

        nc = nz = 0
        for n in z.namelist():
            bel = n[:4]
            info = lookup.get(bel)
            if not info:
                # comune soppresso per fusione, o lookup non ancora allineato
                saltati += 1
                print(f"      saltato {bel} ({n[:40]}) assente dal lookup", flush=True)
                continue
            feats = converti(z.read(n).decode("utf-8", "replace"))
            if not feats:
                continue
            (args.outdir / f"{info['istat']}.geojson").write_text(
                json.dumps({"type": "FeatureCollection", "semestre": args.semestre,
                            "codcom": bel, "istat": info["istat"],
                            "comune": info["name"], "features": feats},
                           ensure_ascii=False), encoding="utf-8")
            nc += 1
            nz += len(feats)

        tot_com += nc
        tot_zone += nz
        with ckpt.open("a") as f:
            f.write(sigla + "\n")
        print(f"[{i}/{len(prov)}] {sigla} {p['DIZIONE'][:20]:20} "
              f"{nc:>4} comuni {nz:>5} zone   (tot {tot_com}/{tot_zone})", flush=True)
        time.sleep(PAUSA)

    peso = sum(f.stat().st_size for f in args.outdir.glob("*.geojson")) / 1e6
    print(f"\ncompletato: {tot_com} comuni, {tot_zone} zone, "
          f"{saltati} saltati, {falliti} province fallite", flush=True)
    print(f"peso cartella: {peso:.1f} MB", flush=True)
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
