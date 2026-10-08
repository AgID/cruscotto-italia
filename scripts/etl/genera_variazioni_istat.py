#!/usr/bin/env python3
"""genera_variazioni_istat.py — tabella dei codici ISTAT comunali non piu vigenti.

PERCHE ESISTE
-------------
Fino all'08/10/2026 l'anagrafica di Cruscotto leggeva
Elenco-comuni-italiani.csv, che ISTAT non aggiorna dal 26/01/2024 (aggiorna
solo l'.xlsx allo stesso percorso). Cruscotto e' quindi rimasto sulla
codifica 2024 mentre nel 2026:
  - 01/01/2026: riforma delle province sarde, tutti i 377 comuni della
    Sardegna ricodificati (prefissi 090/091/092/095/111 -> 112..119);
  - 31/01/2026: Lirio (PV) incorporato in Montalto Pavese;
  - 21/02/2026: Castegnero e Nanto (VI) fusi in Castegnero Nanto.

Questo script confronta due elenchi ISTAT (il vecchio universo e quello
vigente) e produce la tabella codice_vecchio -> codice_vigente usata da ETL,
frontend, MCP e CICO per normalizzare i codici e reindirizzare i vecchi link.

COME DECIDE
-----------
- Stesso codice catastale (Belfiore), codice ISTAT diverso -> "ricodifica".
  Ammesse SOLO dove la decorrenza e' censita in RICODIFICHE_REGIONE: una
  ricodifica in un'altra regione fa fallire lo script.
- Codice catastale sparito -> comune soppresso. Il comune di destinazione NON
  e' deducibile dai due elenchi: deve stare in VARIAZIONI_TERRITORIALI,
  altrimenti lo script fallisce con l'elenco dei casi da censire.
- Comune nuovo nell'elenco vigente: deve essere la destinazione di una voce di
  VARIAZIONI_TERRITORIALI, altrimenti lo script fallisce.
Nessun caso viene risolto per euristica: un elenco ISTAT che cambia senza che
qualcuno l'abbia censito qui deve fermare la catena, non essere indovinato.

USO
---
  python3 -u scripts/etl/genera_variazioni_istat.py --out /tmp/variazioni_istat.json
  (--vecchio e --nuovo accettano URL o file locale; .csv ';' latin-1 o .xlsx)

EXIT CODE: 0 ok, 1 variazioni non censite o incoerenti, 2 errore di download/lettura.
Solo stdlib: l'xlsx viene letto direttamente dall'XML.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

URL_VECCHIO = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
URL_NUOVO = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.xlsx"
USER_AGENT = "CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it; AgID)"

# Ricodifiche ammesse (stesso Belfiore, codice ISTAT nuovo), per regione.
RICODIFICHE_REGIONE = {
    "Sardegna": {"decorrenza": "2026-01-01",
                 "nota": "riforma delle province sarde (L.R. Sardegna 9/2023): nuovi codici provinciali 112-119"},
}

# Comuni soppressi: destinazione censita a mano dalle fonti ufficiali.
# Fonte: ISTAT, "Codici statistici delle unita amministrative territoriali -
# Novita per l'anno 2026" e pagina "Codici dei comuni" (aggiornata al 21/02/2026).
VARIAZIONI_TERRITORIALI = {
    "018082": {"nuovo": "018094", "tipo": "incorporazione", "decorrenza": "2026-01-31",
               "nota": "Lirio incorporato in Montalto Pavese (PV)"},
    "024027": {"nuovo": "024129", "tipo": "fusione", "decorrenza": "2026-02-21",
               "nota": "Castegnero e Nanto fusi in Castegnero Nanto (VI), L.R. Veneto 1/2026"},
    "024071": {"nuovo": "024129", "tipo": "fusione", "decorrenza": "2026-02-21",
               "nota": "Castegnero e Nanto fusi in Castegnero Nanto (VI), L.R. Veneto 1/2026"},
}


def scarica(src: str) -> tuple[bytes, str | None]:
    """Contenuto e data Last-Modified (ISO) da URL o file locale."""
    if not re.match(r"^https?://", src):
        with open(src, "rb") as f:
            return f.read(), None
    req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        lm = r.headers.get("Last-Modified")
        data = r.read()
    try:
        lm_iso = parsedate_to_datetime(lm).date().isoformat() if lm else None
    except (TypeError, ValueError):
        lm_iso = None
    return data, lm_iso


def _col_idx(ref: str) -> int:
    n = 0
    for ch in re.match(r"^([A-Z]+)", ref).group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def righe_xlsx(data: bytes) -> list[list[str]]:
    """Prima sheet di un .xlsx come lista di righe di stringhe (solo stdlib)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
    fogli = sorted(n for n in z.namelist() if re.match(r"^xl/worksheets/sheet\d+\.xml$", n))
    root = ET.fromstring(z.read(fogli[0]))
    out = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        celle = {}
        for c in row.findall("m:c", ns):
            t = c.get("t")
            v = c.find("m:v", ns)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            celle[_col_idx(c.get("r"))] = (val or "").strip()
        if celle:
            out.append([celle.get(i, "") for i in range(max(celle) + 1)])
    return out


def righe_csv(data: bytes) -> list[list[str]]:
    return [[x.strip() for x in r] for r in csv.reader(io.StringIO(data.decode("latin-1")), delimiter=";")]


def elenco(src: str) -> tuple[dict[str, dict], str | None]:
    data, lm = scarica(src)
    righe = righe_xlsx(data) if src.lower().endswith(".xlsx") else righe_csv(data)
    h = [c.lower() for c in righe[0]]

    def col(pred):
        for i, c in enumerate(h):
            if pred(c):
                return i
        raise SystemExit(f"colonna non trovata in {src}")

    ic = col(lambda c: "codice comune formato alfanumerico" in c)
    idn = col(lambda c: c.startswith("denominazione in italiano"))
    ib = col(lambda c: "catastale" in c)
    ir = col(lambda c: c.startswith("denominazione regione"))
    out = {}
    for r in righe[1:]:
        if len(r) <= max(ic, idn, ib, ir) or not r[ic]:
            continue
        codice = r[ic].split(".")[0].zfill(6)
        out[codice] = {"denominazione": r[idn], "belfiore": r[ib].upper(), "regione": r[ir]}
    return out, lm


def main() -> int:
    ap = argparse.ArgumentParser(description="Tabella codici ISTAT comunali non piu vigenti")
    ap.add_argument("--vecchio", default=URL_VECCHIO, help="elenco del vecchio universo (default: CSV ISTAT fermo al 2024)")
    ap.add_argument("--nuovo", default=URL_NUOVO, help="elenco vigente (default: xlsx ISTAT)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    try:
        old, lm_old = elenco(a.vecchio)
        new, lm_new = elenco(a.nuovo)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERRORE lettura elenchi: {type(e).__name__}: {e}", flush=True)
        return 2
    print(f"vecchio: {len(old)} comuni (ISTAT {lm_old}) | vigente: {len(new)} comuni (ISTAT {lm_new})", flush=True)

    bel_new = {v["belfiore"]: k for k, v in new.items()}
    variazioni, problemi = {}, []

    for cod, v in sorted(old.items()):
        if cod in new and new[cod]["belfiore"] == v["belfiore"]:
            continue                                   # invariato
        dest = bel_new.get(v["belfiore"])
        if dest and dest != cod:                       # stesso Belfiore, codice diverso
            reg = RICODIFICHE_REGIONE.get(v["regione"])
            if not reg:
                problemi.append(f"ricodifica non censita: {cod} {v['denominazione']} ({v['regione']}) -> {dest}")
                continue
            variazioni[cod] = {"nuovo": dest, "tipo": "ricodifica", "decorrenza": reg["decorrenza"],
                               "denominazione": v["denominazione"], "denominazione_nuovo": new[dest]["denominazione"],
                               "nota": reg["nota"]}
        elif cod in VARIAZIONI_TERRITORIALI:
            t = VARIAZIONI_TERRITORIALI[cod]
            if t["nuovo"] not in new:
                problemi.append(f"destinazione {t['nuovo']} di {cod} assente dall'elenco vigente")
                continue
            variazioni[cod] = {"nuovo": t["nuovo"], "tipo": t["tipo"], "decorrenza": t["decorrenza"],
                               "denominazione": v["denominazione"],
                               "denominazione_nuovo": new[t["nuovo"]]["denominazione"], "nota": t["nota"]}
        else:
            problemi.append(f"comune soppresso non censito: {cod} {v['denominazione']} ({v['belfiore']})")

    for cod in VARIAZIONI_TERRITORIALI:
        if cod not in old:
            problemi.append(f"voce censita assente dal vecchio elenco: {cod}")
        elif cod in new:
            problemi.append(f"voce censita ma ancora vigente nell'elenco ISTAT: {cod}")

    destinazioni = {v["nuovo"] for v in variazioni.values()}
    for cod, v in sorted(new.items()):
        if cod not in old and cod not in destinazioni:
            problemi.append(f"comune nuovo non spiegato: {cod} {v['denominazione']} ({v['belfiore']})")

    if problemi:
        print(f"ERRORE: {len(problemi)} casi da censire o incoerenti:", flush=True)
        for p in problemi:
            print(f"  - {p}", flush=True)
        return 1

    from collections import Counter
    conteggi = dict(Counter(v["tipo"] for v in variazioni.values()))
    out = {
        "_fonte": "ISTAT - Codici statistici delle unita amministrative territoriali",
        "_elenco_vigente": a.nuovo, "_elenco_vigente_istat_del": lm_new,
        "_elenco_vecchio": a.vecchio, "_elenco_vecchio_istat_del": lm_old,
        "_n_comuni_vigenti": len(new), "_n_comuni_vecchio_universo": len(old),
        "_conteggi": conteggi,
        "variazioni": variazioni,
    }
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    import os
    os.replace(tmp, a.out)
    print(f"OK: {len(variazioni)} variazioni {conteggi} -> {a.out}", flush=True)
    for cod in ("090003", "092009", "018082", "024027", "024071"):
        if cod in variazioni:
            v = variazioni[cod]
            print(f"  {cod} {v['denominazione']} -> {v['nuovo']} {v['denominazione_nuovo']} ({v['tipo']}, {v['decorrenza']})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
