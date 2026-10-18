"""ETL ANAC BDNCP — contratti pubblici aggregati per comune.

Fonte: Banca Dati Nazionale dei Contratti Pubblici (ANAC), dataset open data
`aggiudicazioni` e `cig` (dati.anticorruzione.it/opendata). Licenza CC BY 4.0.

Sostituisce l'ETL sui bulk OCDS (v0.1.0) dal 19/10/2026:
  - ANAC non rigenera l'OCDS da aprile 2026 (2026/04 in poi: 404);
  - i 12 file OCDS usati erano 4 file distinti e contenevano ~1/5 degli
    affidamenti presenti in BDNCP (award_id OCDS = id_aggiudicazione BDNCP,
    stesso importo nel 99,9% dei casi).

Lettura SOLO dall'archivio locale (nessun download qui): ANAC espone i file
mensili per ~7 mesi, li conserva scripts/anac_archivio_mensile.sh (cron
giornaliero). Struttura attesa in --archivio:
  aggiudicazioni/aggiudicazioni_csv_AAAAMMGG.zip   storico (si usa il piu recente)
  aggiudicazioni/AAAAMM01-aggiudicazioni_csv.zip   mensili
  cig/AAAAMM01-cig_csv.zip                         mensili (dal 2026)
  cig_annuali/cig_csv_AAAA_MM.zip                  dataset cig-AAAA (fino al 2025)

Regole (decise il 19/10/2026, vedi claude/HANDOFF_ANAC_FONTE_BDNCP_2026-10-19.md):
  1. Finestra: 12 mesi di data_aggiudicazione_definitiva che terminano con il
     mese precedente l'ultimo file mensile archiviato (il file AAAAMM01
     contiene le comunicazioni del mese prima).
  2. Date: scartate quelle successive a oggi o precedenti al 2000.
  3. Deduplica per CIG: vince la sorgente piu recente (storico < mensili in
     ordine), poi data_comunicazione_esito maggiore, poi l'ultima riga.
  4. Comune = CF della stazione appaltante (cig.cf_amministrazione_appaltante)
     presente in lookup/comuni-index.json. Riga cig: quella con
     flag_prevalente=1, dal file piu recente.
  5. Importo anomalo: una singola aggiudicazione piu grande della spesa annua
     del comune (SIOPE totale_anno, ultimo anno completo) esce dai totali ed
     e elencata in `anomalie`. Senza SIOPE: soglia di riserva in euro per
     abitante (SOGLIA_EURO_AB).

Output: lookup/anac-aggregato.json, mappa CF comune -> KPI, stesso formato
della v0.1.0 (count, importo_totale, first/last_award_date, distinct_cpv,
top_cpv, cpv) piu count_anomalie, importo_anomalie, anomalie.

Usage:
  python -m etl.sources.anac                      # finestra automatica
  python -m etl.sources.anac --outdir /tmp/prova  # output di prova
  python -m etl.sources.anac --fine 2026-09       # ultimo mese della finestra
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from etl.lib import manifest

log = structlog.get_logger()

ETL_VERSION = "0.2.0"
SOURCE_LABEL = "ANAC BDNCP (dataset aggiudicazioni e cig)"
LICENZA = "CC BY 4.0"
ARCHIVIO_DEFAULT = Path("/home/ubuntu/anac_bdncp_archivio")
DATA_DEFAULT = Path("/var/www/cruscotto-italia/data")
MESI_FINESTRA = 12
SOGLIA_EURO_AB = 20_000.0      # riserva se il comune non ha SIOPE completo
DATA_MIN = "2000-01-01"
MAX_ANOMALIE = 50              # per comune, in ordine di importo

csv.field_size_limit(10**9)
_RE_MENSILE = re.compile(r"^(\d{6})01-")


# ----------------------------------------------------------------------------
# Lettura
# ----------------------------------------------------------------------------
def righe_zip(path: Path):
    """Righe (dict) del primo CSV dentro lo zip, separatore ';'."""
    with zipfile.ZipFile(path) as zf:
        interni = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not interni:
            raise RuntimeError(f"{path.name}: nessun CSV nello zip")
        with zf.open(interni[0]) as fb:
            testo = io.TextIOWrapper(fb, encoding="utf-8-sig", newline="")
            yield from csv.DictReader(testo, delimiter=";")


def numero(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def sorgenti_aggiudicazioni(archivio: Path) -> list[Path]:
    """Storico piu recente + mensili in ordine: l'ordine e la priorita."""
    d = archivio / "aggiudicazioni"
    storici = sorted(d.glob("aggiudicazioni_csv_*.zip"))
    mensili = sorted(p for p in d.glob("*-aggiudicazioni_csv.zip") if _RE_MENSILE.match(p.name))
    return storici[-1:] + mensili


def sorgenti_cig(archivio: Path) -> list[Path]:
    annuali = sorted((archivio / "cig_annuali").glob("cig_csv_*.zip"))
    mensili = sorted(p for p in (archivio / "cig").glob("*-cig_csv.zip") if _RE_MENSILE.match(p.name))
    return annuali + mensili


# ----------------------------------------------------------------------------
# Finestra
# ----------------------------------------------------------------------------
def finestra(ultimo_mensile: str, fine: str | None = None, mesi: int = MESI_FINESTRA):
    """(da, a, elenco AAAA-MM). ultimo_mensile = 'AAAAMM' dell'ultimo file:
    la finestra termina con il mese precedente, salvo --fine esplicito."""
    if fine:
        y, m = int(fine[:4]), int(fine[5:7])
    else:
        y, m = int(ultimo_mensile[:4]), int(ultimo_mensile[4:6])
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    elenco = []
    for _ in range(mesi):
        elenco.append(f"{y}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    elenco.reverse()
    da = elenco[0] + "-01"
    ya, ma = int(elenco[-1][:4]), int(elenco[-1][5:7])
    yn, mn = (ya, ma + 1) if ma < 12 else (ya + 1, 1)
    a = date.fromordinal(date(yn, mn, 1).toordinal() - 1).isoformat()
    return da, a, elenco


# ----------------------------------------------------------------------------
# Aggiudicazioni: deduplica per CIG
# ----------------------------------------------------------------------------
def dedup_aggiudicazioni(sorgenti: list[Path], oggi: str) -> dict[str, tuple]:
    """CIG -> (priorita, data_aggiudicazione, importo).

    Priorita = (ordine sorgente, data comunicazione, numero riga): vince la
    tupla maggiore. Le righe con data di aggiudicazione fuori da
    [DATA_MIN, oggi] sono scartate PRIMA della deduplica.
    """
    best: dict[str, tuple] = {}
    for o, z in enumerate(sorgenti):
        n_righe = n_scartate = 0
        for n, r in enumerate(righe_zip(z)):
            n_righe += 1
            d = (r.get("data_aggiudicazione_definitiva") or "")[:10]
            cig = (r.get("cig") or "").strip()
            if not (DATA_MIN <= d <= oggi) or not cig:
                n_scartate += 1
                continue
            v = ((o, (r.get("data_comunicazione_esito") or "")[:10], n), d,
                 numero(r.get("importo_aggiudicazione")))
            vecchio = best.get(cig)
            if vecchio is None or v[0] > vecchio[0]:
                best[cig] = v
        log.info("anac_aggiudicazioni_lette", file=z.name, righe=n_righe,
                 scartate=n_scartate, cig_distinti=len(best))
    return best


def info_cig(sorgenti: list[Path], servono: set[str]) -> dict[str, dict]:
    """CIG -> campi della stazione appaltante e del lotto. Riga prevalente,
    dal file piu recente."""
    info: dict[str, dict] = {}
    for z in sorgenti:
        for r in righe_zip(z):
            c = (r.get("cig") or "").strip()
            if c not in servono:
                continue
            if c in info and r.get("flag_prevalente") != "1":
                continue
            info[c] = {
                "cf": (r.get("cf_amministrazione_appaltante") or "").strip(),
                "nome": (r.get("denominazione_amministrazione_appaltante") or "").strip(),
                "cpv": (r.get("cod_cpv") or "").strip() or None,
                "cpv_desc": (r.get("descrizione_cpv") or "").strip() or None,
                "oggetto": (r.get("oggetto_lotto") or r.get("oggetto_gara") or "").strip()[:200],
            }
        log.info("anac_cig_letti", file=z.name, cig_trovati=len(info))
    return info


# ----------------------------------------------------------------------------
# Riferimenti per il comune: CF e spesa SIOPE
# ----------------------------------------------------------------------------
def carica_comuni(data_dir: Path) -> dict[str, dict]:
    """CF comune -> {istat, nome, pop} da lookup/comuni-index.json."""
    righe = json.loads((data_dir / "lookup" / "comuni-index.json").read_text(encoding="utf-8"))
    out = {}
    for r in righe:
        cf = (r.get("cf") or "").strip()
        if cf:
            out[cf] = {"istat": r["i"], "nome": r.get("n"), "pop": r.get("p")}
    return out


def spesa_siope(data_dir: Path, istat: str) -> tuple[str, float] | None:
    """(anno, totale_anno uscite) dell'ultimo anno SIOPE completo, o None."""
    f = data_dir / "siope" / f"{istat}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    anni = sorted((a for a, b in (d.get("per_anno") or {}).items()
                   if not b.get("parziale") and b.get("totale_anno")), reverse=True)
    if not anni:
        return None
    return anni[0], float(d["per_anno"][anni[0]]["totale_anno"])


def soglia_anomalia(rif: tuple[str, float] | None, pop) -> tuple[float | None, dict | None]:
    """(soglia in euro, descrizione del riferimento)."""
    if rif:
        return rif[1], {"tipo": "spesa_siope", "anno": rif[0], "valore": round(rif[1], 2)}
    try:
        p = float(pop)
    except (TypeError, ValueError):
        p = 0.0
    if p > 0:
        s = SOGLIA_EURO_AB * p
        return s, {"tipo": "euro_per_abitante", "soglia_euro_ab": SOGLIA_EURO_AB,
                   "popolazione": int(p), "valore": round(s, 2)}
    return None, None


# ----------------------------------------------------------------------------
# Aggregazione per comune
# ----------------------------------------------------------------------------
def aggrega(best: dict, info: dict, comuni: dict, data_dir: Path, da: str, a: str) -> dict:
    per_cf: dict[str, list] = defaultdict(list)
    for cig, (_, d, imp) in best.items():
        if not (da <= d <= a):
            continue
        i = info.get(cig)
        if not i or i["cf"] not in comuni:
            continue
        per_cf[i["cf"]].append((cig, d, imp, i))

    out: dict[str, dict] = {}
    n_anom = 0
    for cf, lista in per_cf.items():
        com = comuni[cf]
        soglia, rif = soglia_anomalia(spesa_siope(data_dir, com["istat"]), com.get("pop"))
        tenute, anomalie = [], []
        for x in lista:
            (anomalie if soglia is not None and x[2] > soglia else tenute).append(x)
        n_anom += len(anomalie)

        cpv_agg: dict[tuple, list] = defaultdict(lambda: [0, 0.0])
        for _, _, imp, i in tenute:
            if i["cpv"]:
                k = (i["cpv"], i["cpv_desc"])
                cpv_agg[k][0] += 1
                cpv_agg[k][1] += imp
        cpv = sorted(({"code": k[0], "desc": (k[1] or "")[:120] or None, "count": v[0],
                       "importo": round(v[1], 2)} for k, v in cpv_agg.items()),
                     key=lambda c: -c["importo"])
        date_t = sorted(x[1] for x in tenute)
        nome = max(lista, key=lambda x: x[1])[3]["nome"] or com["nome"]
        out[cf] = {
            "buyer_name": nome,
            "istat": com["istat"],
            "count": len(tenute),
            "importo_totale": round(sum(x[2] for x in tenute), 2),
            "first_award_date": date_t[0] if date_t else None,
            "last_award_date": date_t[-1] if date_t else None,
            "distinct_cpv": len(cpv),
            "top_cpv": [dict(c, desc=(c["desc"] or "")[:80] or None) for c in cpv[:5]],
            "cpv": cpv,
            "count_anomalie": len(anomalie),
            "importo_anomalie": round(sum(x[2] for x in anomalie), 2),
            "riferimento_anomalie": rif,
            "anomalie": [
                {"cig": c, "data": d, "importo": round(imp, 2), "oggetto": i["oggetto"],
                 "cpv": i["cpv"],
                 "rapporto_su_riferimento": round(imp / soglia, 1) if soglia else None}
                for c, d, imp, i in sorted(anomalie, key=lambda x: -x[2])[:MAX_ANOMALIE]
            ],
        }
    log.info("anac_aggregato_comuni", comuni=len(out), anomalie=n_anom,
             aggiudicazioni=sum(v["count"] for v in out.values()))
    return out


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ETL ANAC BDNCP — contratti aggregati per comune")
    p.add_argument("--target", choices=["local"], default="local",
                   help="Solo 'local' (compatibilita con i workflow esistenti)")
    p.add_argument("--outdir", type=Path, default=DATA_DEFAULT,
                   help="Radice di output: scrive <outdir>/lookup/anac-aggregato.json")
    p.add_argument("--datadir", type=Path, default=DATA_DEFAULT,
                   help="Radice dati da cui leggere comuni-index e SIOPE")
    p.add_argument("--archivio", type=Path, default=ARCHIVIO_DEFAULT)
    p.add_argument("--fine", type=str, default=None, help="Ultimo mese della finestra, AAAA-MM")
    p.add_argument("--mesi", type=int, default=MESI_FINESTRA)
    # argomenti della v0.1.0 (OCDS), accettati e ignorati
    p.add_argument("--years", type=str, default=None, help=argparse.SUPPRESS)
    p.add_argument("--months", type=str, default=None, help=argparse.SUPPRESS)
    p.add_argument("--ultimi-mesi", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--workdir", type=Path, default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])
    if args.years or args.months or args.ultimi_mesi or args.workdir:
        log.warning("anac_argomenti_ocds_ignorati")
    produzione = args.outdir == DATA_DEFAULT

    try:
        oggi = date.today().isoformat()
        src_agg = sorgenti_aggiudicazioni(args.archivio)
        src_cig = sorgenti_cig(args.archivio)
        mensili = [s for s in src_agg if _RE_MENSILE.match(s.name)]
        if not mensili or not src_cig:
            raise RuntimeError(f"archivio incompleto in {args.archivio}: "
                               f"{len(mensili)} mensili aggiudicazioni, {len(src_cig)} file cig")
        da, a, elenco = finestra(_RE_MENSILE.match(mensili[-1].name).group(1), args.fine, args.mesi)
        log.info("etl_start", archivio=str(args.archivio), finestra_da=da, finestra_a=a,
                 sorgenti_aggiudicazioni=[s.name for s in src_agg], file_cig=len(src_cig))

        best = dedup_aggiudicazioni(src_agg, oggi)
        servono = {c for c, v in best.items() if da <= v[1] <= a}
        log.info("anac_finestra", aggiudicazioni_nella_finestra=len(servono))
        info = info_cig(src_cig, servono)
        log.info("anac_unione_cig", trovati=len(info), senza_cig=len(servono) - len(info),
                 copertura_pct=round(100 * len(info) / max(len(servono), 1), 2))
        if len(info) < 0.95 * len(servono):
            raise RuntimeError(f"unione con i CIG troppo bassa: {len(info)}/{len(servono)}")

        comuni = carica_comuni(args.datadir)
        dati = aggrega(best, info, comuni, args.datadir, da, a)
        if produzione and len(dati) < 5000:
            raise RuntimeError(f"solo {len(dati)} comuni con aggiudicazioni: output non scritto")

        lookup_dir = args.outdir / "lookup"
        lookup_dir.mkdir(parents=True, exist_ok=True)
        out_path = lookup_dir / "anac-aggregato.json"
        doc = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_LABEL,
            "_licenza": LICENZA,
            "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "_finestra": {"da": da, "a": a, "campo": "data_aggiudicazione_definitiva"},
            "_period_files": elenco,
            "_sorgenti": {"aggiudicazioni": [s.name for s in src_agg],
                          "cig": [s.name for s in src_cig]},
            "_regole": {"deduplica": "per CIG, versione piu recente",
                        "anomalie": "singola aggiudicazione > spesa SIOPE annua del comune "
                                    f"(riserva {SOGLIA_EURO_AB:.0f} euro/abitante): esclusa dai totali"},
            "data": dati,
        }
        tmp = out_path.with_suffix(".json.part")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, out_path)
        log.info("anac_aggregato_built", path=str(out_path), bytes=out_path.stat().st_size,
                 n_comuni=len(dati))

        if produzione:
            try:
                manifest.update_source("anac", [{"key": "lookup/anac-aggregato.json",
                                                 "size": out_path.stat().st_size}], status="ok")
                log.info("manifest_updated")
            except Exception as e:
                log.warning("manifest_update_skipped", error=str(e))
        log.info("etl_done")
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        if produzione:
            try:
                manifest.update_source("anac", [], status=f"failed: {e}")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
