"""Download SDMX ISTAT a blocchi di comuni.

Perche' esiste (05/10/2026): le richieste nazionali in un colpo solo
(tutti i comuni, es. incidenti 41_983 2020-2024 o capacita ricettiva
TUR_1) andavano in ReadTimeout dopo 5 minuti di silenzio, mentre la stessa
sorgente rispondeva in ~14 s per un singolo comune. ASIA scaricava gia' a
blocchi ed era l'unico ETL ISTAT senza problemi.

Limite ISTAT sui codici per richiesta: 35 passano, 50 danno 400 Bad
Request (misurato il 05/10/2026 su 41_983). Il blocco predefinito e 35,
come asia.py.

LIMITE UFFICIALE ISTAT: 5 query al minuto per IP, oltre scatta un blocco
di 1-2 giorni (istat.it, pagina "Web Services SDMX"). Il 05/10/2026 a ~8
richieste/minuto la VM e stata bloccata. Ogni richiesta ISTAT del progetto
deve passare da attendi_turno_istat(): intervallo minimo 13 s (~4,6/min),
condiviso tra PROCESSI con un file di lock, perche nelle finestre annuali
del cron profilo, ASIA, turismo e veicoli possono sovrapporsi.

Regole ISTAT da rispettare: richieste in SEQUENZA (il parallelismo ha gia'
portato a un blocco dell'IP, vedi asia.py) e pausa tra un blocco e l'altro.

Uso:
    from etl.lib.istat_sdmx import scarica_a_blocchi
    scarica_a_blocchi("https://esploradati.istat.it/SDMXWS/rest/data/41_983",
                      "A...", 2020, 2024, codici, out_path, log=log)

La chiave SDMX va passata con REF_AREA vuota in seconda posizione (come
"A..." o "A.........."): i codici del blocco vengono inseriti li', separati
da '+'. Il CSV finale ha una sola intestazione ed e' scritto su .part e
rinominato solo a fine download: un errore non lascia file troncati.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

ACCEPT_CSV = "application/vnd.sdmx.data+csv;version=1.0.0"
INTERVALLO_MIN_S = 13.0
_FILE_TURNO = Path(os.environ.get("ISTAT_TURNO_FILE", "/tmp/cruscotto-istat-turno"))


def attendi_turno_istat() -> None:
    """Blocca finche non sono passati INTERVALLO_MIN_S dall'ultima richiesta
    ISTAT fatta da QUALSIASI processo sulla macchina, poi registra l'ora.

    Il file contiene il timestamp dell'ultima richiesta; flock rende atomico
    leggi-attendi-scrivi tra processi diversi.
    """
    import fcntl
    _FILE_TURNO.touch(exist_ok=True)
    with open(_FILE_TURNO, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            ultimo = float(fh.read().strip() or 0)
        except ValueError:
            ultimo = 0.0
        attesa = ultimo + INTERVALLO_MIN_S - time.time()
        if attesa > 0:
            time.sleep(attesa)
        fh.seek(0)
        fh.truncate()
        fh.write(f"{time.time():.3f}")
        fh.flush()
        fcntl.flock(fh, fcntl.LOCK_UN)


def chiave_con_codici(chiave: str, codici: list[str]) -> str:
    """Inserisce i codici nella posizione REF_AREA (seconda) della chiave."""
    parti = chiave.split(".")
    if len(parti) < 2 or parti[1] != "":
        raise ValueError(f"chiave senza REF_AREA vuota in seconda posizione: {chiave!r}")
    parti[1] = "+".join(codici)
    return ".".join(parti)


def scarica_a_blocchi(base_dataflow: str, chiave: str, anno_inizio: int, anno_fine: int,
                      codici: list[str], out: Path, *, blocco: int = 35,
                      tentativi: int = 3, pausa: float = 2.0, timeout: int = 300,
                      user_agent: str = "cruscotto-italia-etl", log=None) -> Path:
    """Scarica il dataflow per tutti i codici, a blocchi, in un unico CSV.

    Riprendibile: ogni blocco e salvato in <out>.blocchi/ appena arriva; se
    il download si interrompe, il run successivo salta i blocchi gia
    presenti (05/10/2026: un errore di rete al blocco 127 di 226 avrebbe
    buttato 15 minuti di download). La cartella si cancella solo dopo aver
    scritto il CSV completo. I blocchi dipendono da dataflow, chiave, anni e
    codici: un cambio di parametri usa una cartella diversa.
    """
    import hashlib
    import shutil
    headers = {"Accept": ACCEPT_CSV, "User-Agent": user_agent}
    firma = hashlib.sha1(f"{base_dataflow}|{chiave}|{anno_inizio}|{anno_fine}|{blocco}|"
                         f"{','.join(codici)}".encode()).hexdigest()[:12]
    cartella = out.with_name(f"{out.name}.blocchi-{firma}")
    cartella.mkdir(parents=True, exist_ok=True)
    n_blocchi = (len(codici) + blocco - 1) // blocco
    t0 = time.time()
    riusati = 0
    for k in range(1, n_blocchi + 1):
        f_blocco = cartella / f"{k:05d}.csv"
        if f_blocco.exists():
            riusati += 1
            continue
        gruppo = codici[(k - 1) * blocco:k * blocco]
        url = (f"{base_dataflow}/{chiave_con_codici(chiave, gruppo)}"
               f"?startPeriod={anno_inizio}&endPeriod={anno_fine}")
        testo = None
        for n in range(1, tentativi + 1):
            try:
                attendi_turno_istat()
                r = requests.get(url, headers=headers, timeout=timeout)
                if r.status_code == 404:      # nessun dato per il blocco
                    testo = ""
                    break
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    # errore della richiesta: riprovare non serve
                    raise RuntimeError(f"ISTAT HTTP {r.status_code} sul blocco "
                                       f"{k} ({len(gruppo)} codici)")
                r.raise_for_status()
                testo = r.content.decode("utf-8-sig")
                break
            except requests.RequestException as e:
                if log:
                    log.warning("istat_blocco_retry", blocco=k, tentativo=n,
                                error=str(e)[:200])
                if n == tentativi:
                    if log:
                        log.error("istat_blocchi_interrotto", blocchi_salvati=k - 1,
                                  totale=n_blocchi, cartella=str(cartella),
                                  nota="rilanciare: i blocchi salvati verranno riusati")
                    raise
                time.sleep(30 * n)
        tmp = f_blocco.with_suffix(".part")
        tmp.write_text(testo, encoding="utf-8")
        os.replace(tmp, f_blocco)
        if log and (k % 10 == 0 or k == n_blocchi):
            log.info("istat_blocchi_progress", blocchi=f"{k}/{n_blocchi}",
                     riusati=riusati, secondi=round(time.time() - t0))

    # Unione dei blocchi in un unico CSV con una sola intestazione
    part = out.with_suffix(out.suffix + ".part")
    intestazione = None
    righe = 0
    try:
        with open(part, "w", encoding="utf-8", newline="") as fo:
            for k in range(1, n_blocchi + 1):
                linee = (cartella / f"{k:05d}.csv").read_text(encoding="utf-8").splitlines()
                if not linee:
                    continue
                if intestazione is None:
                    intestazione = linee[0]
                    fo.write(intestazione + "\n")
                elif linee[0] != intestazione:
                    raise RuntimeError(f"intestazione CSV diversa nel blocco {k}")
                for riga in linee[1:]:
                    if riga:
                        fo.write(riga + "\n")
                        righe += 1
        if intestazione is None:
            raise RuntimeError("nessun dato restituito da ISTAT per nessun blocco")
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    shutil.rmtree(cartella, ignore_errors=True)
    if log:
        log.info("istat_blocchi_done", righe=righe, blocchi=n_blocchi, riusati=riusati,
                 secondi=round(time.time() - t0), path=str(out))
    return out
