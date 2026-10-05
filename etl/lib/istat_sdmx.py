"""Download SDMX ISTAT a blocchi di comuni.

Perche' esiste (05/10/2026): le richieste nazionali in un colpo solo
(tutti i comuni, es. incidenti 41_983 2020-2024 o capacita ricettiva
TUR_1) andavano in ReadTimeout dopo 5 minuti di silenzio, mentre la stessa
sorgente rispondeva in ~14 s per un singolo comune. ASIA scaricava gia' a
blocchi ed era l'unico ETL ISTAT senza problemi.

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


def chiave_con_codici(chiave: str, codici: list[str]) -> str:
    """Inserisce i codici nella posizione REF_AREA (seconda) della chiave."""
    parti = chiave.split(".")
    if len(parti) < 2 or parti[1] != "":
        raise ValueError(f"chiave senza REF_AREA vuota in seconda posizione: {chiave!r}")
    parti[1] = "+".join(codici)
    return ".".join(parti)


def scarica_a_blocchi(base_dataflow: str, chiave: str, anno_inizio: int, anno_fine: int,
                      codici: list[str], out: Path, *, blocco: int = 100,
                      tentativi: int = 3, pausa: float = 2.0, timeout: int = 300,
                      user_agent: str = "cruscotto-italia-etl", log=None) -> Path:
    """Scarica il dataflow per tutti i codici, a blocchi, in un unico CSV."""
    headers = {"Accept": ACCEPT_CSV, "User-Agent": user_agent}
    part = out.with_suffix(out.suffix + ".part")
    intestazione = None
    righe = 0
    n_blocchi = (len(codici) + blocco - 1) // blocco
    t0 = time.time()
    try:
        with open(part, "w", encoding="utf-8", newline="") as fo:
            for i in range(0, len(codici), blocco):
                gruppo = codici[i:i + blocco]
                url = (f"{base_dataflow}/{chiave_con_codici(chiave, gruppo)}"
                       f"?startPeriod={anno_inizio}&endPeriod={anno_fine}")
                testo = None
                for n in range(1, tentativi + 1):
                    try:
                        r = requests.get(url, headers=headers, timeout=timeout)
                        if r.status_code == 404:      # nessun dato per il blocco
                            testo = ""
                            break
                        r.raise_for_status()
                        testo = r.content.decode("utf-8-sig")
                        break
                    except requests.RequestException as e:
                        if log:
                            log.warning("istat_blocco_retry", blocco=i // blocco + 1,
                                        tentativo=n, error=str(e)[:200])
                        if n == tentativi:
                            raise
                        time.sleep(30 * n)
                linee = testo.splitlines()
                if linee:
                    if intestazione is None:
                        intestazione = linee[0]
                        fo.write(intestazione + "\n")
                    elif linee[0] != intestazione:
                        raise RuntimeError("intestazione CSV diversa tra blocchi")
                    for riga in linee[1:]:
                        if riga:
                            fo.write(riga + "\n")
                            righe += 1
                k = i // blocco + 1
                if log and (k % 10 == 0 or k == n_blocchi):
                    log.info("istat_blocchi_progress", blocchi=f"{k}/{n_blocchi}",
                             righe=righe, secondi=round(time.time() - t0))
                if k < n_blocchi and pausa > 0:
                    time.sleep(pausa)
        if intestazione is None:
            raise RuntimeError("nessun dato restituito da ISTAT per nessun blocco")
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if log:
        log.info("istat_blocchi_done", righe=righe, blocchi=n_blocchi,
                 secondi=round(time.time() - t0), path=str(out))
    return out
