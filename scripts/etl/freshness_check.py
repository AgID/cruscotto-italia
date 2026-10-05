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

# Fonti senza cron RILEVABILE: aggiornamento manuale, statico, oppure avviato
# da uno script wrapper .sh. Il parser qui sopra cerca "etl.sources.<nome>"
# nella riga di cron, quindi non vede le fonti lanciate da uno script
# (catasto_semestrale.sh, omi_semestrale.sh): il modulo non compare mai nel
# comando. Non sono un'anomalia.
SENZA_CRON_ATTESE = {"catasto_age", "classificazione_sismica", "censimento",
                     "cultural_on", "dcat_catalog", "pendolarismo", "omi"}
# NB omi e catasto_age UN CRON CE L'HANNO: girano da uno script wrapper
# (omi_semestrale.sh, catasto_semestrale.sh) e il parser qui sopra cerca
# "etl.sources.<nome>" nel comando, quindi non li vede. Stare in questa lista
# li toglie pero' da ogni sorveglianza: per omi il presidio e' il controllo di
# contenuto _check_omi, che verifica il semestre invece dell'esecuzione.


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


def eta_ultimo_run(source: str, ora: datetime, prefisso: str | None = None,
                   last_run_iso: str | None = None) -> float | None:
    """Da quanti giorni l'ETL non VIENE ESEGUITO, dai log del cron.

    Distinzione essenziale: manifest["last_run"] NON si aggiorna quando l'ETL
    fa skip per hash invariato. agcom_bbmap risultava fermo dal 13 maggio, ma
    era girato il 5 luglio trovando lo stesso SHA256. Allora fu letto come
    "la fonte non pubblica"; il 05/10/2026 si e visto che era l'item ArcGIS
    cablato (AGCOM pubblica ogni rilascio come item nuovo): vedi _check_agcom.
      - log del cron  -> "l'ETL e' girato?"      (guasto NOSTRO se manca)
      - manifest      -> "il dato e' cambiato?"  (fermo = spesso la fonte)
    Solo il primo e' un allarme.
    """
    recente = None
    if LOGDIR.is_dir():
        for f in LOGDIR.glob(f"{prefisso or source}-*.log*"):
            mt = f.stat().st_mtime
            if recente is None or mt > recente:
                recente = mt
    # Un run manuale (o un rerun fuori cron) non scrive in $LOG ma aggiorna il
    # manifest: senza questa evidenza il check grida "non eseguito" su un ETL
    # appena rilanciato a mano. E' successo il 29-31/07/2026 su siope.
    # Non nasconde nulla: se l'ETL non gira davvero, nessuna delle due avanza.
    if last_run_iso:
        try:
            t = datetime.fromisoformat(last_run_iso).timestamp()
            if recente is None or t > recente:
                recente = t
        except (ValueError, TypeError):
            pass
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


def _check_omi(ora: datetime) -> str | None:
    """Il semestre pubblicato deve stare al passo col calendario dell'Agenzia.

    Serve perche' omi non e' sorvegliabile per esecuzione: la sentinella
    giornaliera gira sempre e uscirebbe "verde" anche se la raccolta
    semestrale fallisse, e il dato si aggiorna due volte l'anno, quindi
    nessuna soglia su last_run sarebbe insieme sensibile e non rumorosa.

    L'Agenzia pubblica entro il 15 marzo (2o semestre dell'anno precedente) ed
    entro il 15 ottobre (1o semestre corrente). Soglia a 45 giorni DOPO quelle
    date: tollera un rilascio in ritardo di qualche settimana senza gridare, e
    intercetta un semestre saltato ben prima del successivo.
    """
    f = DATA_DIR / "omi" / "077014.json"           # Matera, comune di controllo
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    per = str(d.get("_data_period") or "")
    m = re.match(r"^(\d{4})/([12])$", per)
    if not m:
        return f"_data_period illeggibile ({per!r})"
    anno, sem = int(m.group(1)), int(m.group(2))

    if (ora.month, ora.day) >= (11, 29):           # 15/10 + 45gg
        atteso = (ora.year, 1)
    elif (ora.month, ora.day) >= (4, 29):          # 15/03 + 45gg
        atteso = (ora.year - 1, 2)
    else:
        atteso = (ora.year - 1, 1)

    if (anno, sem) < atteso:
        return (f"semestre fermo a {per}, atteso almeno {atteso[0]}/{atteso[1]}: "
                f"la raccolta semestrale non e' andata a buon fine. "
                f"Controllare /var/log/cruscotto-etl/omi-semestrale.log")
    return None


def _check_agcom(ora: datetime) -> str | None:
    """Il periodo AGCOM non deve essere piu vecchio di 170 giorni.

    Guasto del 2026: item ArcGIS cablato, l'ETL scaricava sempre il rilascio
    al 31/12/2025 mentre AGCOM aveva gia pubblicato 31/03 e 30/06. Ogni
    segnale di tempo era verde; solo il periodo del dato era fermo.

    Taratura sullo storico dei rilasci (2023-2026, API di ricerca ArcGIS):
    ritardo di pubblicazione 9-65 giorni dalla fine del trimestre; eta massima
    del periodo subito prima del rilascio successivo = 155 giorni (31/12/2025,
    successivo pubblicato il 04/06/2026). Una soglia a 150 avrebbe dato un
    falso allarme; 170 = 155 + 15 di margine. Con questa soglia il guasto
    sarebbe emerso il 20/06/2026.
    """
    f = DATA_DIR / "agcom_bbmap" / "077014.json"    # Matera, comune di controllo
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    per = str(d.get("_data_period") or "")
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", per)
    if not m:
        return f"_data_period illeggibile ({per!r})"
    fine = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                    tzinfo=timezone.utc)
    gg = (ora - fine).days
    if gg > 170:
        return (f"periodo fermo a {per} ({gg}gg fa, soglia 170): AGCOM "
                f"pubblica ogni rilascio come item ArcGIS nuovo. Controllare "
                f"agcom_item_resolved / agcom_item_resolve_failed nel log ETL")
    return None


# --- Controlli aggiunti dopo l'audit del 05/10/2026 ---------------------------
# Nell'audit diverse fonti risultavano "ok" con dati fermi: anni o URL
# cablati, cache senza scadenza, download troncati. Questi controlli
# guardano il PERIODO DEL DATO (non l'ora del run) sullo shard di Matera o
# sul lookup della fonte. Soglie tarate sul calendario di pubblicazione di
# ciascuna fonte: tutte verdi al 05/10/2026, senza scattare nei mesi
# d'attesa normali tra un rilascio e l'altro.

def _leggi(rel: str) -> dict | None:
    f = DATA_DIR / rel
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _giorni_da(data_iso: str | None, ora: datetime) -> int | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(data_iso or ""))
    if not m:
        return None
    d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    return (ora - d).days


def _eta_massima(rel: str, campo, soglia_gg: int, cosa: str):
    """Controllo su una data ISO dello shard: non oltre soglia_gg giorni."""
    def chk(ora: datetime) -> str | None:
        d = _leggi(rel)
        if d is None:
            return None
        val = campo(d)
        gg = _giorni_da(val, ora)
        if gg is None:
            return f"{cosa}: data illeggibile ({val!r})"
        if gg > soglia_gg:
            return f"{cosa} fermo al {val} ({gg}gg, soglia {soglia_gg})"
        return None
    return chk


def _anno_minimo(rel: str, campo, ritardo_anni: int, cosa: str):
    """Controllo annuale: l'anno del dato non deve essere < anno corrente - ritardo."""
    def chk(ora: datetime) -> str | None:
        d = _leggi(rel)
        if d is None:
            return None
        try:
            anno = int(campo(d))
        except (TypeError, ValueError):
            return f"{cosa}: anno illeggibile"
        if anno < ora.year - ritardo_anni:
            return (f"{cosa} fermo al {anno} (atteso >= {ora.year - ritardo_anni}): "
                    f"controllare la scoperta dell'anno nel log ETL")
        return None
    return chk


def _check_anac(ora: datetime) -> str | None:
    """Finestra ANAC: almeno 6 mesi e il piu recente entro 9 mesi.

    05/10/2026: si serviva un solo mese (cron con --years=<anno corrente>).
    ANAC OCDS e ferma a marzo 2026 lato fonte: 9 mesi lasciano margine alla
    pubblicazione a lotti e segnalano un fermo che dura."""
    d = _leggi("lookup/anac-aggregato.json")
    if d is None:
        return None
    mesi = sorted(str(x)[:7] for x in d.get("_period_files") or [])
    if len(mesi) < 6:
        return f"finestra contratti di soli {len(mesi)} mesi ({mesi}): controllare anac --ultimi-mesi"
    gg = _giorni_da(mesi[-1] + "-01", ora)
    if gg is not None and gg > 300:
        return f"mese ANAC piu recente {mesi[-1]} ({gg}gg): fonte OCDS ferma o download fallito"
    return None


def _anno_suolo(d: dict) -> int | None:
    su = d.get("suolo") or {}
    if (su.get("stock_ultimo") or {}).get("anno"):
        return su["stock_ultimo"]["anno"]
    return 2024 if "stock_2024" in su else None


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
    "omi": _check_omi,
    "agcom_bbmap": _check_agcom,
    "anac": _check_anac,
    # mensili / settimanali: eta della data del dato
    "pnrr_progetti": _eta_massima("pnrr/077014.json", lambda d: d.get("data_estrazione"),
                                  150, "estrazione PNRR"),   # Italia Domani ~ ogni 2-3 mesi
    "bdap": _eta_massima("bdap/dettaglio/077014.json", lambda d: d.get("_data_download"),
                         40, "download BDAP"),               # cron mensile + cache 20gg
    "anncsu": _eta_massima("anncsu/077014.json", lambda d: d.get("_snapshot_date"),
                           50, "snapshot ANNCSU"),            # flusso mensile AdE
    # annuali: anno del dato non piu vecchio di (anno corrente - ritardo)
    "istat_turismo": _anno_minimo("turismo/077014.json",
                                  lambda d: d["capacita_comune"]["anno"], 2, "capacita turistica"),
    "istat_profilo": _anno_minimo("profilo/077014.json",
                                  lambda d: d["istruzione"]["anno"], 3, "profilo censuario"),
    "asia": _anno_minimo("asia/077014.json", lambda d: d["_latest_year"], 4, "ASIA"),   # ISTAT ~3 anni di ritardo
    "redditi": _anno_minimo("redditi/077014.json",
                            lambda d: max(map(int, d["anni"])), 3, "redditi IRPEF"),
    "veicoli": _anno_minimo("veicoli/077014.json",
                            lambda d: d["_anno_dati_iscrizioni"], 2, "iscrizioni ACI"),
    "immobili_pa": _anno_minimo("immobili_pa/077014.json",
                                lambda d: d["anno_rilevazione"], 4, "rilevazione immobili MEF"),
    "territorio": _anno_minimo("territorio/077014.json", _anno_suolo, 2, "consumo di suolo"),
    "demografia": _anno_minimo("demografia/077014.json",
                               lambda d: d["_anno_riferimento"], 1, "popolazione POSAS"),
    "sanita_mds": _anno_minimo("sanita_mds/077014.json",
                               lambda d: d["_fonti"]["ospedali"]["anno_dati"], 4, "posti letto"),  # 2024 atteso a lug 2025, ancora assente
    "dashboard": _check_dashboard,
}


STATO = OUT.parent / "freshness_state.json"


def carica_stato() -> dict:
    try:
        return json.loads(STATO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def salva_stato(stato: dict) -> None:
    try:
        STATO.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATO.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stato, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATO)
    except OSError:
        pass


def grazia_per_fonte(nome: str, cadenza: float | None, stato: dict,
                     ora: datetime) -> float | None:
    """Giorni trascorsi da un cambio di cadenza, se la grazia e' ancora dovuta.

    Registra la cadenza osservata per ogni fonte. Quando cambia, annota il
    momento e concede un ciclo pieno prima di pretendere il rispetto della nuova
    frequenza: nel frattempo il ritardo diventa una nota, non un allarme.
    La grazia vale SOLO per la fonte che ha cambiato cadenza.
    """
    if cadenza is None:
        return None
    v = stato.get(nome) or {}
    prec = v.get("cadenza")
    if prec is None or abs(float(prec) - cadenza) > 0.01:
        stato[nome] = {"cadenza": cadenza, "dal": ora.isoformat()}
        return 0.0
    try:
        gg = (ora - datetime.fromisoformat(v["dal"])).total_seconds() / 86400.0
    except (KeyError, ValueError, TypeError):
        return None
    return gg if gg < cadenza else None


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
    # Quando si AUMENTA la frequenza di un job, il passato viene giudicato con
    # la cadenza nuova: portando siope da mensile a settimanale il 29/07/2026 il
    # check ha suonato per tre mattine su un ETL sano, perche' il primo lunedi
    # utile non era ancora arrivato.
    # La grazia e PER FONTE, non globale: un primo tentativo basato sull'mtime
    # del crontab silenziava tutte le 25 fonti a ogni modifica del file, e nel
    # test un bdap fermo da 90 giorni non veniva piu rilevato. Toccare il cron
    # per una fonte non deve spegnere l'allarme sulle altre.
    stato = carica_stato()
    righe, anomalie = [], []

    for nome in sorted(sources):
        v = sources[nome] or {}
        st = str(v.get("status") or "")
        eta = eta_giorni(v.get("last_run"), ora)
        c = cad.get(nome)
        e_sh, n_file = eta_shard(nome, ora)

        e_run = eta_ultimo_run(nome, ora, logpfx.get(nome), v.get("last_run"))
        grazia = grazia_per_fonte(nome, c, stato, ora)

        problemi, note = [], []
        if st != "ok":
            problemi.append(f"status={st}")

        # ANOMALIA: l'ETL non e' stato eseguito. E' il solo caso che grida.
        if c and e_run is not None and e_run > c * args.tolleranza:
            if grazia is not None:
                note.append(f"non eseguito da {e_run:.0f}gg, ma la cadenza e' cambiata "
                            f"{grazia:.0f}gg fa: attendo un ciclo ({c:.0f}gg)")
            else:
                problemi.append(f"non eseguito da {e_run:.0f}gg (cadenza {c:.0f}gg)")
        elif c and e_run is None and c <= 31:
            # fonti frequenti senza alcun log: sospetto. Le annuali no, il log
            # e' stato ruotato via da tempo.
            problemi.append("nessun log di esecuzione")

        # Gira ma il dato non cambia: spesso e' la fonte che non pubblica, ma
        # non sempre (agcom_bbmap 2026: era l'URL cablato). Nota, non allarme:
        # i casi in cui il dato DEVE avanzare stanno in CONTROLLI_CONTENUTO.
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

    salva_stato(stato)
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
