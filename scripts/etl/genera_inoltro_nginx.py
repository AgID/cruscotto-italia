#!/usr/bin/env python3
"""genera_inoltro_nginx.py — map nginx per l'inoltro dei codici ISTAT comunali.

Decisione 08/10/2026 (migrazione ai codici ISTAT vigenti): le fonti restano
scritte col codice che usano (dati 2021-2025 coi codici sardi pre-2026), e chi
legge via HTTPS (frontend, Worker MCP) deve trovare lo stesso comune con
entrambi i codici. Lato nginx: se /data/<dir>/<codice>... non esiste, si prova
UNA volta l'altro codice dello stesso comune.

Genera un file di sole direttive `map` (contesto http), da installare in
/etc/nginx/conf.d/ prima del vhost (nome con prefisso numerico):

  $ist_alt_dati  ricodifiche in entrambi i versi (112001 <-> 090003)
  $ist_alt_dash  come sopra + comuni soppressi -> comune vigente, SOLO per
                 /data/dashboard/ (il vecchio link apre il comune vigente; i
                 DATI dei soppressi non vengono mai attribuiti al vigente)
  $ist_alt       sceglie fra i due in base alla cartella ($ist_dir)

$ist_cod e $ist_dir sono catture del rewrite in `location @inoltro_istat` del
vhost. Valore di default "-": non e' un codice, il rewrite non trova nulla e
la richiesta finisce in 404.

Fonte unica: lookup/variazioni_istat.json (scripts/etl/genera_variazioni_istat.py).
Mai modificare a mano il file generato.

USO
  python3 scripts/etl/genera_inoltro_nginx.py --out /tmp/01-inoltro-istat.conf
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_TABELLA = "/var/www/cruscotto-italia/data/lookup/variazioni_istat.json"
_RE = re.compile(r"^\d{6}$")


def genera(var: dict, fonte: str) -> str:
    ricod = sorted((v, d["nuovo"]) for v, d in var.items() if d["tipo"] == "ricodifica")
    soppr = sorted((v, d["nuovo"]) for v, d in var.items() if d["tipo"] in ("fusione", "incorporazione"))
    for a, b in ricod + soppr:
        if not (_RE.match(a) and _RE.match(b)):
            raise SystemExit(f"codice non valido nella tabella: {a} -> {b}")
    dati = {}
    for vecchio, nuovo in ricod:
        dati[nuovo] = vecchio
        dati[vecchio] = nuovo
    if len(dati) != 2 * len(ricod):
        raise SystemExit("ricodifiche non biunivoche: un codice compare due volte")
    dash = dict(dati)
    for vecchio, nuovo in soppr:
        if vecchio in dash:
            raise SystemExit(f"{vecchio} sia ricodificato sia soppresso")
        dash[vecchio] = nuovo

    def blocco(nome, coppie):
        righe = [f"map $ist_cod {nome} {{", '    default "-";']
        righe += [f"    {k} {v};" for k, v in sorted(coppie.items())]
        righe.append("}")
        return "\n".join(righe)

    testa = (
        "# GENERATO da scripts/etl/genera_inoltro_nginx.py - NON MODIFICARE A MANO\n"
        f"# fonte: {fonte}\n"
        f"# ricodifiche: {len(ricod)}  soppressi (solo dashboard): {len(soppr)}\n"
        "# Usato da location @inoltro_istat nel vhost cruscotto-italia.dati.gov.it\n"
    )
    scelta = "map $ist_dir $ist_alt {\n    default $ist_alt_dati;\n    dashboard $ist_alt_dash;\n}"
    return "\n\n".join([testa.rstrip("\n"), blocco("$ist_alt_dati", dati),
                        blocco("$ist_alt_dash", dash), scelta]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tabella", default=DEFAULT_TABELLA)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    payload = json.loads(Path(a.tabella).read_text(encoding="utf-8"))
    var = payload.get("variazioni") or {}
    if not var:
        print(f"ERRORE: nessuna variazione in {a.tabella}", flush=True)
        return 1
    testo = genera(var, a.tabella)
    tmp = a.out + ".tmp"
    Path(tmp).write_text(testo, encoding="utf-8")
    os.replace(tmp, a.out)
    n = sum(1 for r in testo.splitlines() if re.match(r"^\s+\d{6} \d{6};$", r))
    print(f"OK: {a.out} ({n} righe di map)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
