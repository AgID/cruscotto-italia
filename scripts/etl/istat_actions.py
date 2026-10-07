#!/usr/bin/env python3
"""Download dei dati ISTAT SDMX su GitHub Actions, elaborazione sulla VM.

Perche (07/10/2026): ISTAT blocca l'IP della VM agid01 dopo ~100-130
richieste SDMX, anche rispettando le 5 query al minuto dichiarate. I runner
GitHub hanno un IP diverso a ogni run e lo raggiungono (prova del 07/10:
metadati 1,3 s, incidenti 1,9 s, turismo 12,9 s). Il workflow
`istat-download.yml` divide i comuni in parti, ogni parte gira su un runner
diverso (al massimo ~50 richieste ciascuno), e pubblica i CSV grezzi come
artifact. La VM li scarica con `fetch_istat_artifact.py` nella cache degli
ETL, che poi girano con `--solo-cache` senza alcuna richiesta a ISTAT.

Comandi (eseguiti dal workflow):
  anni                      ultimo anno pubblicato per capacita ricettiva, flussi
                            turistici, parco veicoli e incidenti (righe
                            chiave=valore per $GITHUB_OUTPUT)
  scarica DATASET PARTE     scarica la parte PARTE (di PARTI[DATASET]) in --out
  provinciali               flussi turistici provinciali + codelist (piccoli)
  unisci                    unisce le parti in un CSV per dataset + manifest.json

Il comuni-bundle si legge da $DATA_DIR/lookup (il workflow lo scarica dal
sito pubblico prima di partire).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
import structlog

from etl.lib.istat_sdmx import ACCEPT_CSV, attendi_turno_istat, scarica_a_blocchi
from etl.sources import istat_turismo as T
from etl.sources import veicoli as V

log = structlog.get_logger()

# Cartelle di cache degli ETL sulla VM (fetch_istat_artifact.py le usa per
# rimettere ogni file dove l'ETL lo cerca)
CACHE = {"veicoli": str(V.CACHE_DIR), "turismo": "/tmp/cruscotto-istat-turismo-cache"}


# Parti e blocco per dataset. Capacita ricettiva (TUR_1): ~4 s per comune
# lato ISTAT, che chiude le richieste oltre ~4,5 minuti (07/10/2026: con 8
# parti da blocchi di 35 comuni le richieste scadevano). Blocchi da 10
# comuni (~40 s) e 16 parti: ~50 richieste per runner.
PARTI = {"incidenti": 8, "parco": 8, "capacita": 16}
BLOCCO = {"incidenti": 35, "parco": 35, "capacita": 10}

# Anni di incidenti nella serie (finestra mobile che termina all'ultimo anno)
FINESTRA_INCIDENTI = len(V.ANNI_INCIDENTI)
# Sonde veicoli su un solo comune (Lecce): chiave con REF_AREA valorizzata
SONDE_VEICOLI = {"anno_parco": ("41_993", "A.075035.VEHICFLEET.", V.ANNO_PARCO),
                 "anno_inc": ("41_983", "A.075035..", max(V.ANNI_INCIDENTI))}


def specifiche(anno_cap: int, anno_parco: int = V.ANNO_PARCO,
               anno_inc: int = max(V.ANNI_INCIDENTI)) -> dict[str, dict]:
    """Dataset comunali scaricati a parti: url, chiave, anni, file e cache."""
    a_min, a_max = anno_inc - FINESTRA_INCIDENTI + 1, anno_inc
    cap = T.DATAFLOWS[0]
    return {
        "incidenti": {"base": f"{V.ISTAT_BASE}/41_983", "chiave": "A...",
                      "anni": (a_min, a_max), "cache": "veicoli",
                      "file": f"istat_41_983_incidenti_{a_min}_{a_max}.csv"},
        "parco": {"base": f"{V.ISTAT_BASE}/41_993", "chiave": "A..VEHICFLEET.",
                  "anni": (anno_parco, anno_parco), "cache": "veicoli",
                  "file": f"istat_41_993_parco_{anno_parco}.csv"},
        "capacita": {"base": f"{T.SDMX_BASE}/data/{T.SDMX_AGENCY},{cap['id']},{T.SDMX_VERSION}",
                     "chiave": cap["key"], "anni": (anno_cap, anno_cap), "cache": "turismo",
                     "file": f"capacita_{anno_cap}.csv"},
    }


def _anno_veicoli(dataflow: str, chiave: str, minimo: int) -> int:
    """Ultimo anno con osservazioni per la sonda, dall'anno precedente
    all'indietro fino a `minimo` (l'anno gia in produzione)."""
    for anno in range(datetime.now().year - 1, minimo, -1):
        attendi_turno_istat()
        r = requests.get(f"{V.ISTAT_BASE}/{dataflow}/{chiave}?startPeriod={anno}&endPeriod={anno}",
                         headers={"Accept": ACCEPT_CSV, "User-Agent": T.UA}, timeout=300)
        if r.status_code == 404:          # NoRecordsFound: anno non pubblicato
            continue
        r.raise_for_status()
        righe = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))
        if any(x.get("TIME_PERIOD") == str(anno) and (x.get("OBS_VALUE") or "").strip()
               for x in righe):
            return anno
    return minimo


def cmd_anni(_args) -> int:
    T.resolve_anni()
    print(f"anno_cap={T.ANNO_CAP}")
    print(f"anno_fl={T.ANNO_FL}")
    for chiave, (dataflow, sonda, minimo) in SONDE_VEICOLI.items():
        anno = _anno_veicoli(dataflow, sonda, minimo)
        log.info("anno_veicoli", dataset=dataflow, anno=anno)
        print(f"{chiave}={anno}")
    return 0


def cmd_scarica(args) -> int:
    spec = specifiche(args.anno_cap, args.anno_parco, args.anno_inc)[args.dataset]
    codici = T.codici_comuni()
    parti = PARTI[args.dataset]
    if not 1 <= args.parte <= parti:
        raise SystemExit(f"{args.dataset}: parte {args.parte} fuori da 1..{parti}")
    parte = [c for i, c in enumerate(codici) if i % parti == args.parte - 1]
    out = Path(args.out) / f"{spec['file']}.parte{args.parte:02d}"
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("parte_start", dataset=args.dataset, parte=args.parte, parti=parti,
             comuni=len(parte), anni=spec["anni"])
    scarica_a_blocchi(spec["base"], spec["chiave"], spec["anni"][0], spec["anni"][1],
                      parte, out, blocco=BLOCCO[args.dataset], tentativi=4,
                      user_agent=T.UA, log=log)
    return 0


def cmd_provinciali(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fl = T.DATAFLOWS[1]
    fl["year_start"] = fl["year_end"] = args.anno_fl
    T.download_dataflow_csv(fl, out)
    T.download_codelist_xml(out)
    return 0


def cmd_unisci(args) -> int:
    src, dst = Path(args.dir), Path(args.out)
    dst.mkdir(parents=True, exist_ok=True)
    manifest = {"anno_cap": args.anno_cap, "anno_fl": args.anno_fl,
                "anno_parco": args.anno_parco, "anno_inc": args.anno_inc, "file": []}
    for nome, spec in specifiche(args.anno_cap, args.anno_parco, args.anno_inc).items():
        parti = sorted(p for p in src.rglob(f"{spec['file']}.parte[0-9][0-9]") if p.is_file())
        if len(parti) != PARTI[nome]:
            raise SystemExit(f"{nome}: attese {PARTI[nome]} parti, trovate {len(parti)}")
        intestazione, righe = None, 0
        with open(dst / spec["file"], "w", encoding="utf-8", newline="") as fo:
            for p in parti:
                linee = p.read_text(encoding="utf-8").splitlines()
                if not linee:
                    continue
                if intestazione is None:
                    intestazione = linee[0]
                    fo.write(intestazione + "\n")
                elif linee[0] != intestazione:
                    raise SystemExit(f"{nome}: intestazione diversa in {p.name}")
                for r in linee[1:]:
                    if r:
                        fo.write(r + "\n")
                        righe += 1
        if not righe:
            raise SystemExit(f"{nome}: nessuna riga")
        manifest["file"].append({"cache": CACHE[spec["cache"]], "nome": spec["file"], "righe": righe})
        log.info("unito", dataset=nome, parti=len(parti), righe=righe)
    for nome in (f"flussi_{args.anno_fl}.csv", "itter107.xml"):
        p = next(iter(src.rglob(nome)), None)
        if p is None:
            raise SystemExit(f"manca {nome}")
        (dst / nome).write_bytes(p.read_bytes())
        manifest["file"].append({"cache": CACHE["turismo"], "nome": nome})
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    # Log su stderr: lo stdout di `anni` finisce in $GITHUB_OUTPUT
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.add_log_level,
                                    structlog.dev.ConsoleRenderer(colors=False)],
                        logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("anni")
    s = sub.add_parser("scarica")
    s.add_argument("dataset", choices=["incidenti", "parco", "capacita"])
    s.add_argument("parte", type=int)
    s.add_argument("--anno-cap", type=int, required=True)
    s.add_argument("--anno-parco", type=int, required=True)
    s.add_argument("--anno-inc", type=int, required=True)
    s.add_argument("--out", default="parti")
    p = sub.add_parser("provinciali")
    p.add_argument("--anno-fl", type=int, required=True)
    p.add_argument("--out", default="parti")
    u = sub.add_parser("unisci")
    u.add_argument("--dir", default="parti")
    u.add_argument("--out", default="istat-raw")
    u.add_argument("--anno-cap", type=int, required=True)
    u.add_argument("--anno-fl", type=int, required=True)
    u.add_argument("--anno-parco", type=int, required=True)
    u.add_argument("--anno-inc", type=int, required=True)
    args = ap.parse_args()
    return {"anni": cmd_anni, "scarica": cmd_scarica, "provinciali": cmd_provinciali,
            "unisci": cmd_unisci}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
