#!/usr/bin/env python3
"""genera_lookup_belfiore.py — lookup codice catastale (Belfiore) -> comune ISTAT vigente.

Sostituisce /home/ubuntu/catasto_test/lookup_belfiore_to_istat.json, fatto a
mano il 28/05/2026 dal CSV ISTAT fermo al 26/01/2024 (quindi senza la
ricodifica sarda 2026, senza Castegnero Nanto M439, con Castegnero, Nanto e
Lirio ancora presenti). La usano catasto_age.py, omi_fetch_perimetri.py e
omi_fetch_quotazioni.py.

Formato invariato (dict piatto, nessuna chiave di servizio):
    {"A192": {"istat": "112001", "name": "Alghero", "regione": "Sardegna"}, ...}

Legge l'elenco vigente ISTAT (.xlsx) con elenco() di genera_variazioni_istat.py
(stessa lettura, stesse colonne). Con --confronta stampa le differenze con la
lookup esistente, divise per tipo. Ogni Belfiore duplicato o codice non valido
-> exit 1. Non sovrascrive nulla oltre a --out.

USO
  python3 -u scripts/etl/genera_lookup_belfiore.py --out /tmp/lookup_belfiore_to_istat.json \\
      --confronta /home/ubuntu/catasto_test/lookup_belfiore_to_istat.json

EXIT CODE: 0 ok, 1 dati incoerenti, 2 errore di download/lettura.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

_QUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("genera_variazioni_istat", _QUI / "genera_variazioni_istat.py")
_gv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gv)

_RE_BEL = re.compile(r"^[A-Z]\d{3}$")
_RE_IST = re.compile(r"^\d{6}$")


def costruisci(comuni: dict[str, dict]) -> tuple[dict, list[str]]:
    out, problemi = {}, []
    for istat, v in sorted(comuni.items()):
        bel = v["belfiore"]
        if not _RE_BEL.match(bel) or not _RE_IST.match(istat):
            problemi.append(f"valori non validi: {istat} {bel!r}")
            continue
        if bel in out:
            problemi.append(f"Belfiore duplicato {bel}: {out[bel]['istat']} e {istat}")
            continue
        out[bel] = {"istat": istat, "name": v["denominazione"], "regione": v["regione"]}
    return out, problemi


def confronta(vecchia: dict, nuova: dict) -> None:
    ricod, nomi, regioni = [], [], []
    for bel in sorted(set(vecchia) & set(nuova)):
        a, b = vecchia[bel], nuova[bel]
        if a["istat"] != b["istat"]:
            ricod.append(f"{bel} {a['istat']}->{b['istat']} {b['name']}")
        if a["name"] != b["name"]:
            nomi.append(f"{bel} {a['name']!r}->{b['name']!r}")
        if a["regione"] != b["regione"]:
            regioni.append(f"{bel} {a['regione']!r}->{b['regione']!r}")
    rimossi = [f"{b} {vecchia[b]['istat']} {vecchia[b]['name']}" for b in sorted(set(vecchia) - set(nuova))]
    nuovi = [f"{b} {nuova[b]['istat']} {nuova[b]['name']}" for b in sorted(set(nuova) - set(vecchia))]
    for titolo, righe, max_righe in (("codice ISTAT cambiato", ricod, 5), ("Belfiore rimossi", rimossi, 20),
                                     ("Belfiore nuovi", nuovi, 20), ("denominazione cambiata", nomi, 20),
                                     ("regione cambiata", regioni, 20)):
        print(f"{titolo}: {len(righe)}", flush=True)
        for r in righe[:max_righe]:
            print(f"  {r}", flush=True)
    if ricod:
        prefissi = sorted({r.split()[1][:3] + '->' + r.split()[1].split('->')[1][:3] for r in ricod})
        print(f"  prefissi: {', '.join(prefissi)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--elenco", default=_gv.URL_NUOVO, help="elenco ISTAT vigente (URL o file .xlsx)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--confronta", type=Path, help="lookup esistente da confrontare (solo lettura)")
    a = ap.parse_args()

    try:
        comuni, lm = _gv.elenco(a.elenco)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERRORE lettura elenco: {type(e).__name__}: {e}", flush=True)
        return 2
    print(f"elenco vigente: {len(comuni)} comuni (ISTAT {lm})", flush=True)

    lookup, problemi = costruisci(comuni)
    if problemi:
        print(f"ERRORE: {len(problemi)} problemi:", flush=True)
        for p in problemi[:30]:
            print(f"  - {p}", flush=True)
        return 1

    if a.confronta:
        confronta(json.loads(a.confronta.read_text(encoding="utf-8")), lookup)

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lookup, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, a.out)
    print(f"OK: {len(lookup)} Belfiore -> {a.out}", flush=True)
    for bel in ("A192", "B354", "E506", "M439", "F417"):
        print(f"  {bel} {lookup.get(bel)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
