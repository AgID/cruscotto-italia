#!/usr/bin/env python3
"""freshness_check.py — verifica che gli ETL stiano davvero aggiornando i dati.

PERCHE ESISTE
-------------
Il 28/07/2026 sono emersi tre guasti con la stessa firma: il sistema sapeva di
aver fallito, lo aveva scritto, e nessuno lo leggeva.
  - la cache SIOPE senza TTL: _generated_at si aggiornava a ogni run, ma il dato
    era fermo al 5 giugno. Nessun controllo lo avrebbe rilevato.
  - l'ETL anagrafica con status "failed" nel manifest da quasi due mesi.
  - alert_attacks.py che scrive "TELEGRAM_* mancanti" ogni ora dal deploy.

Questo script chiude il cerchio: confronta l'eta reale dei dati con la cadenza
dichiarata dal cron e segnala chi e' indietro.

COSA CONTROLLA
--------------
1. status != ok nel manifest
2. last_run piu vecchio di TOLLERANZA volte la cadenza del cron
3. mtime reale degli shard: un ETL puo' scrivere last_run senza rigenerare nulla
   (e' esattamente il caso della cache stale)

DOVE SCRIVE
-----------
/var/www/cruscotto-italia/data/_diagnostics/freshness.json — NON servito da
nginx (/data/ e' una whitelist per-sottocartella): i fallimenti di
aggiornamento non devono essere leggibili dall'esterno, ne' per immagine ne'
per ricognizione.

Telegram solo se TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono presenti: la
notifica e' un di piu', non una dipendenza. Il file e l'exit code restano
comunque.

USO
---
  python3 -u scripts/etl/freshness_check.py             # controllo
  python3 -u scripts/etl/freshness_check.py --quiet     # solo anomalie
  python3 -u scripts/etl/freshness_check.py --no-telegram

EXIT CODE: 0 tutto ok, 1 anomalie, 2 errore dello script.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CRUSCOTTO_DATA_DIR", "/var/www/cruscotto-italia/data"))
MANIFEST = DATA_DIR / "manifest.json"
OUT = DATA_DIR / "_diagnostics" / "freshness.json"
CRON = Path("/etc/cron.d/cruscotto-etl")
LOGDIR = Path(os.environ.get("CRUSCOTTO_LOG_DIR", "/var/log/cruscotto-etl"))

# Quante volte la cadenza prima di gridare. 2.5 tollera un run saltato senza
# generare rumore: sotto questa soglia un ritardo di poche ore diventerebbe un
# falso allarme quotidiano, e un alert che grida sempre viene ignorato.
TOLLERANZA = 2.5

# Fonti senza cron: aggiornamento manuale o statico. Non sono un'anomalia.
SENZA_CRON_ATTESE = {"catasto_age", "classificazione_sismica", "censimento",
                     "cultural_on", "dcat_catalog", "pendolarismo"}


def cadenze_da_cron() -> tuple[dict[str, float], dict[str, str]]:
    """Dal crontab: cadenza in giorni e PREFISSO DEL FILE DI LOG per ogni ETL.

    Il nome del log non e' derivabile dal modulo: il cron scrive
    `tee $LOG/pnrr-$(date ...).log` per etl.sources.pnrr_progetti. Dedurlo
    generava un falso allarme "nessun log di esecuzione" su una fonte sana.
    """
    if not CRON.exists():
        return {}, {}
    out: dict[str, float] = {}
    logpfx: dict[str, str] = {}
    for riga in CRON.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+(.*)$", riga)
        if not m:
            continue
        _mi, _ho, dom, mon, dow, cmd = m.groups()
        _lg = re.search(r"\$LOG/([A-Za-z0-9_.-]+?)-\$\(date", cmd)
        for src in re.findall(r"etl\.sources\.([a-z_]+)", cmd):
            if _lg:
                logpfx.setdefault(src, _lg.group(1))
            if dom != "*" and mon != "*":
                g = 365.0 / max(1, len([x for x in mon.split(",") if x]))
            elif dom != "*":
                g = 31.0
            elif dow != "*":
                g = 7.0
            else:
                g = 1.0
            out[src] = min(out.get(src, 1e9), g)
    return out, logpfx


def eta_giorni(iso: str | None, ora: datetime) -> float | None:
    if not iso:
        return None
    try:
        return (ora - datetime.fromisoformat(iso)).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def eta_ultimo_run(source: str, ora: datetime, prefisso: str | None = None) -> float | None:
    """Da quanti giorni l'ETL non VIENE ESEGUITO, dai log del cron.

    Distinzione essenziale: manifest["last_run"] NON si aggiorna quando l'ETL
    fa skip per hash invariato. agcom_bbmap risultava fermo dal 13 maggio, ma
    era girato il 5 luglio trovando lo stesso SHA256 della fonte AGCOM: nessun
    guasto, semplicemente la fonte non pubblica.
      - log del cron  -> "l'ETL e' girato?"      (guasto NOSTRO se manca)
      - manifest      -> "il dato e' cambiato?"  (fermo = spesso la fonte)
    Solo il primo e' un allarme.
    """
    if not LOGDIR.is_dir():
        return None
    recente = None
    for f in LOGDIR.glob(f"{prefisso or source}-*.log*"):
        mt = f.stat().st_mtime
        if recente is None or mt > recente:
            recente = mt
    return None if recente is None else (ora.timestamp() - recente) / 86400.0


def eta_shard(source: str, ora: datetime) -> tuple[float | None, int]:
    """Eta del file piu recente scritto dalla fonte, e quanti file ha.

    Serve perche last_run puo' aggiornarsi senza che i dati cambino: era
    esattamente il caso della cache SIOPE congelata.
    """
    for d in (DATA_DIR / source, DATA_DIR / "lookup"):
        if not d.is_dir():
            continue
        recente, n = None, 0
        for f in d.iterdir():
            if not f.is_file():
                continue
            n += 1
            mt = f.stat().st_mtime
            if recente is None or mt > recente:
                recente = mt
        if recente:
            return (ora.timestamp() - recente) / 86400.0, n
    return None, 0


def controlli_contenuto(source: str, ora: datetime) -> list[str]:
    """Verifica che il CONTENUTO avanzi, non solo che l'ETL giri.

    PERCHE SERVE, e perche i controlli temporali non bastano.
    Nel guasto della cache SIOPE (28/07/2026) tutti i segnali di tempo erano
    verdi: il cron girava ogni mese, il log si scriveva, update_source()
    registrava status ok, gli shard venivano riscritti e l'mtime avanzava.
    L'unica cosa ferma era il DATO, congelato al 5 giugno perche' la cache non
    aveva TTL. Nessun controllo su last_run o mtime poteva accorgersene.

    Serve quindi un'asserzione specifica per fonte su un campo che DEVE
    avanzare. Registro esplicito, mai euristica: ogni voce e' una promessa
    verificabile su quella fonte.
    """
    out: list[str] = []
    fn = CONTROLLI_CONTENUTO.get(source)
    if not fn:
        return out
    try:
        msg = fn(ora)
        if msg:
            out.append(msg)
    except Exception as e:
        out.append(f"controllo contenuto fallito: {type(e).__name__}")
    return out


def _check_siope(ora: datetime) -> str | None:
    """ultimo_mese dell'anno corrente deve avanzare col calendario.

    Taratura sul comportamento osservato: il 23/07/2026 siope.it pubblicava gia
    i movimenti di luglio, quindi il ritardo tipico e' di poche settimane.
    Soglia a 45 giorni: nel guasto reale il dato era fermo a 2026/05 mentre era
    il 28/07, cioe' 61 giorni. Una soglia a 75 giorni NON lo avrebbe
    intercettato - verificato per simulazione, non per congettura.
    """
    f = DATA_DIR / "siope" / "077014.json"          # Matera, comune di controllo
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    anni = d.get("anni_disponibili") or []
    if not anni:
        return "shard SIOPE senza anni_disponibili"
    blocco = (d.get("per_anno") or {}).get(str(max(anni))) or {}
    um = blocco.get("ultimo_mese") or ""
    m = re.match(r"^(\d{4})/(\d{2})$", um)
    if not m:
        return f"ultimo_mese illeggibile ({um!r})"
    fine = datetime(int(m.group(1)), int(m.group(2)), 28, tzinfo=timezone.utc)
    gg = (ora - fine).days
    if gg > 45:
        return (f"ultimo_mese fermo a {um} ({gg}gg fa): cache stale o fonte ferma. "
                f"Rilanciare con --no-cache e confrontare i totali.")
    return None


def _check_dashboard(ora: datetime) -> str | None:
    """Il dashboard deve essere piu recente degli shard che accorpa."""
    dash = DATA_DIR / "dashboard" / "077014.json"
    if not dash.exists():
        return "dashboard di controllo assente"
    t_dash = dash.stat().st_mtime
    piu_recenti = []
    for sub in ("siope", "bdap/dettaglio", "demografia", "censimento"):
        f = DATA_DIR / sub / "077014.json"
        if f.exists() and f.stat().st_mtime > t_dash + 3600:
            piu_recenti.append(sub)
    if piu_recenti:
        return ("dashboard piu vecchio degli shard "
                f"({', '.join(piu_recenti)}): serve un rebuild")
    return None


CONTROLLI_CONTENUTO = {
    "siope": _check_siope,
    "dashboard": _check_dashboard,
}


def telegram(testo: str) -> bool:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    try:
        dati = urllib.parse.urlencode({
            "chat_id": chat, "text": testo, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=dati)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"  telegram KO: {type(e).__name__}: {e}", flush=True)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Freshness check degli ETL Cruscotto Italia")
    ap.add_argument("--quiet", action="store_true", help="stampa solo le anomalie")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--tolleranza", type=float, default=TOLLERANZA)
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"manifest assente: {MANIFEST}", file=sys.stderr)
        return 2
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"manifest illeggibile: {e}", file=sys.stderr)
        return 2

    sources = m.get("sources") or {}
    cad, logpfx = cadenze_da_cron()
    ora = datetime.now(timezone.utc)
    righe, anomalie = [], []

    for nome in sorted(sources):
        v = sources[nome] or {}
        st = str(v.get("status") or "")
        eta = eta_giorni(v.get("last_run"), ora)
        c = cad.get(nome)
        e_sh, n_file = eta_shard(nome, ora)

        e_run = eta_ultimo_run(nome, ora, logpfx.get(nome))

        problemi, note = [], []
        if st != "ok":
            problemi.append(f"status={st}")

        # ANOMALIA: l'ETL non e' stato eseguito. E' il solo caso che grida.
        if c and e_run is not None and e_run > c * args.tolleranza:
            problemi.append(f"non eseguito da {e_run:.0f}gg (cadenza {c:.0f}gg)")
        elif c and e_run is None and c <= 31:
            # fonti frequenti senza alcun log: sospetto. Le annuali no, il log
            # e' stato ruotato via da tempo.
            problemi.append("nessun log di esecuzione")

        # Gira ma il dato non cambia: quasi sempre e' la fonte che non pubblica
        # (agcom_bbmap, stesso SHA256 da maggio). Nota, non allarme.
        if c and eta is not None and eta > c * args.tolleranza and not problemi:
            note.append(f"dato invariato da {eta:.0f}gg (la fonte non pubblica?)")

        # Controllo di CONTENUTO (vedi CONTROLLI_CONTENUTO).
        for msg in controlli_contenuto(nome, ora):
            problemi.append(msg)

        if c is None and nome not in SENZA_CRON_ATTESE:
            problemi.append("nessun cron")

        r = {"fonte": nome, "status": st,
             "eta_ultimo_run_gg": round(e_run, 1) if e_run is not None else None,
             "eta_dato_gg": round(eta, 1) if eta is not None else None,
             "eta_shard_gg": round(e_sh, 1) if e_sh is not None else None,
             "cadenza_gg": c, "n_file": n_file,
             "problemi": problemi, "note": note}
        righe.append(r)
        if problemi:
            anomalie.append(r)

    if not args.quiet:
        print(f"{'fonte':<24}{'status':<9}{'eseguito':>9}{'dato':>7}{'cadenza':>8}   note", flush=True)
        for r in righe:
            er = f"{r['eta_ultimo_run_gg']}g" if r["eta_ultimo_run_gg"] is not None else "-"
            dt = f"{r['eta_dato_gg']}g" if r["eta_dato_gg"] is not None else "-"
            cc = f"{r['cadenza_gg']:.0f}g" if r["cadenza_gg"] else "-"
            seg = "!! " if r["problemi"] else ("   " if not r["note"] else " ~ ")
            print(f"{r['fonte']:<24}{r['status']:<9}{er:>9}{dt:>7}{cc:>8}{seg}"
                  f"{'; '.join(r['problemi'] + r['note'])}", flush=True)

    _con_note = [r for r in righe if r["note"] and not r["problemi"]]
    esito = {"generated_at": ora.isoformat(), "tolleranza": args.tolleranza,
             "n_fonti": len(righe), "n_anomalie": len(anomalie),
             "n_note": len(_con_note),
             "anomalie": anomalie, "dettaglio": righe}
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(esito, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT)
        print(f"\nreport: {OUT}", flush=True)
    except OSError as e:
        print(f"scrittura report fallita: {e}", file=sys.stderr)

    if _con_note:
        print(f"\nNote (non allarmi): {len(_con_note)}", flush=True)
        for r in _con_note:
            print(f"  ~ {r['fonte']}: {'; '.join(r['note'])}", flush=True)

    if not anomalie:
        print(f"\nOK: {len(righe)} fonti, nessuna anomalia", flush=True)
        return 0

    print(f"\nANOMALIE: {len(anomalie)}/{len(righe)}", flush=True)
    for r in anomalie:
        print(f"  - {r['fonte']}: {'; '.join(r['problemi'])}", flush=True)

    if not args.no_telegram:
        corpo = "\n".join(f"• <b>{r['fonte']}</b>: {'; '.join(r['problemi'])}"
                          for r in anomalie[:15])
        if len(anomalie) > 15:
            corpo += f"\n… e altre {len(anomalie) - 15}"
        if telegram(f"⚠️ <b>Cruscotto Italia — ETL non aggiornati</b>\n"
                    f"{len(anomalie)} fonti su {len(righe)}\n\n{corpo}"):
            print("  telegram inviato", flush=True)
        else:
            print("  telegram non configurato (report su file comunque scritto)", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
