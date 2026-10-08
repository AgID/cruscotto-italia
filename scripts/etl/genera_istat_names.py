#!/usr/bin/env python3
"""genera_istat_names.py — istat-names.json: codice ISTAT comunale -> denominazione.

Il file (/var/www/cruscotto-stats/istat-names.json, servito come /istat-names.json)
era statico dal 19/05/2026, sulla codifica ISTAT 2024. Lo leggono:
  - il frontend (tab Pendolarismo: nomi dei comuni nelle top-10 dei flussi,
    che sono codici della matrice 2021);
  - CICO (risoluzione nome -> comune e nomi nel pendolarismo);
  - scripts/analytics e detect_catasto_anomalies.

Contenuto:
  - tutti i comuni vigenti del bundle anagrafica (codice -> denominazione);
  - i codici non piu vigenti della tabella delle variazioni, ciascuno col SUO
    nome storico (090003 -> Alghero, 024027 -> Castegnero), perche le fonti
    con dati anteriori al 2026 li contengono ancora.
CICO non deve indicizzare questi ultimi come comuni (nome -> codice): lo
gestisce app_v2.py escludendo i codici della tabella delle variazioni.

USO
  python3 -u scripts/etl/genera_istat_names.py --out /tmp/istat-names.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DATA = Path("/var/www/cruscotto-italia/data")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, default=DATA / "lookup" / "comuni-bundle.json")
    ap.add_argument("--variazioni", type=Path, default=DATA / "lookup" / "variazioni_istat.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    comuni = json.loads(a.bundle.read_text(encoding="utf-8"))["comuni"]
    var = json.loads(a.variazioni.read_text(encoding="utf-8"))["variazioni"]
    nomi = {k: v["denominazione"] for k, v in comuni.items() if v.get("denominazione")}
    if len(nomi) < 7800:
        print(f"ERRORE: solo {len(nomi)} comuni con denominazione nel bundle", flush=True)
        return 1
    vigenti_nel_bundle = sum(1 for v in var.values() if v["nuovo"] in nomi)
    storici = 0
    for vecchio, v in sorted(var.items()):
        if vecchio in nomi:
            continue        # bundle ancora sulla codifica precedente: nome gia presente
        nomi[vecchio] = v["denominazione"]
        storici += 1
    tmp = a.out + ".tmp"
    Path(tmp).write_text(json.dumps(dict(sorted(nomi.items())), ensure_ascii=False,
                                    separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, a.out)
    print(f"OK: {len(nomi)} codici ({len(comuni)} dal bundle, {storici} storici; "
          f"destinazioni delle variazioni presenti nel bundle: {vigenti_nel_bundle}/{len(var)}) -> {a.out}",
          flush=True)
    for k in ("112001", "090003", "024129", "024027", "031019", "075035"):
        print(f"  {k} {nomi.get(k)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
