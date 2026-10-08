#!/usr/bin/env python3
"""pulizia_codici_istat.py — rimuove i file dei comuni con codici ISTAT non piu vigenti.

Da lanciare DOPO il passaggio dell'anagrafica ai codici vigenti e DOPO il
rebuild del dashboard (migrazione 08/10/2026). Regole:

  dashboard/        rimuove <vecchio>.json per ogni codice non vigente
                    (ricodifiche e soppressi) SOLO se esiste dashboard/<vigente>.json:
                    i vecchi link sono serviti dall'inoltro nginx verso il vigente.
  altre cartelle    ricodifiche: rimuove il file/la cartella col codice vecchio
                    SOLO se esiste lo stesso file col codice vigente (doppione
                    vecchio di una fonte che e' passata ai codici 2026). Se esiste
                    solo il vecchio NON si tocca: e' il dato letto via inoltro.
                    Soppressi: non si toccano (non vengono mai inoltrati).

Riconosce i nomi che iniziano col codice seguito da '.', '_' o fine nome
(es. 090003.json, 090003.geojson, 090003_ple.geojson.gz, 090003_ple/,
morfologia/090003/). Cartelle escluse: vedi ESCLUSE.

Rifiuta di partire se il bundle anagrafica non e' gia sui codici vigenti.
Default: prova a secco (non cancella nulla). --esegui per cancellare.
Log progressivo: una riga per azione + riepilogo per cartella.

USO
  python3 -u scripts/etl/pulizia_codici_istat.py            # prova a secco
  python3 -u scripts/etl/pulizia_codici_istat.py --esegui
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ESCLUSE = {"lookup", "dcat", "skills", "_work", "raw", "_diagnostics", "monthly", "bdap"}
EXTRA = ["bdap/dettaglio"]


def codice_iniziale(nome: str, codici: set[str]) -> str | None:
    c = nome[:6]
    if c in codici and (len(nome) == 6 or nome[6] in "._"):
        return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=Path("/var/www/cruscotto-italia/data"))
    ap.add_argument("--esegui", action="store_true", help="cancella davvero (default: prova a secco)")
    a = ap.parse_args()
    data = a.data

    var = json.loads((data / "lookup" / "variazioni_istat.json").read_text(encoding="utf-8"))["variazioni"]
    bundle = json.loads((data / "lookup" / "comuni-bundle.json").read_text(encoding="utf-8"))["comuni"]
    mancanti = sorted({v["nuovo"] for v in var.values()} - set(bundle))
    vecchi_nel_bundle = sorted(set(var) & set(bundle))
    if mancanti or vecchi_nel_bundle:
        print(f"ERRORE: bundle non ancora sui codici vigenti (vigenti mancanti: {len(mancanti)}, "
              f"codici vecchi presenti: {len(vecchi_nel_bundle)}). Nessuna azione.", flush=True)
        return 1

    ricod = {k: v["nuovo"] for k, v in var.items() if v["tipo"] == "ricodifica"}
    tutti = {k: v["nuovo"] for k, v in var.items()}
    modo = "ESEGUO" if a.esegui else "PROVA A SECCO"
    print(f"{modo}: {len(ricod)} ricodifiche, {len(tutti) - len(ricod)} soppressi; data={data}", flush=True)

    cartelle = sorted(p for p in data.iterdir() if p.is_dir() and p.name not in ESCLUSE and not p.name.startswith("."))
    cartelle += [data / e for e in EXTRA if (data / e).is_dir()]
    riepilogo = {}
    for cart in cartelle:
        dash = cart.name == "dashboard"
        mappa = tutti if dash else ricod
        cont = Counter()
        for voce in sorted(cart.iterdir()):
            vecchio = codice_iniziale(voce.name, set(mappa))
            if not vecchio:
                continue
            nuovo_nome = mappa[vecchio] + voce.name[6:]
            gemello = cart / nuovo_nome
            if not gemello.exists():
                cont["tenuto_solo_vecchio"] += 1
                continue
            rel = voce.relative_to(data)
            print(f"  rimuovo {rel}{'/' if voce.is_dir() else ''}  (esiste {gemello.relative_to(data)})", flush=True)
            if a.esegui:
                if voce.is_dir() and not voce.is_symlink():
                    shutil.rmtree(voce)
                else:
                    voce.unlink()
            cont["rimossi"] += 1
        if cont:
            riepilogo[str(cart.relative_to(data))] = dict(cont)
            print(f"[{cart.relative_to(data)}] {dict(cont)}", flush=True)
    tot = sum(c.get("rimossi", 0) for c in riepilogo.values())
    print(f"FINE {modo}: {tot} elementi {'rimossi' if a.esegui else 'da rimuovere'} in {len(riepilogo)} cartelle", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
