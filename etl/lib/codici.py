"""Codici ISTAT comunali vigenti: normalizzazione dei codici non piu' in uso.

Tabella di riferimento: lookup/variazioni_istat.json, generata SOLO da
scripts/etl/genera_variazioni_istat.py (mai a mano). Contiene, per ogni codice
del vecchio universo che non e' piu' vigente:

    {"nuovo": "112001", "tipo": "ricodifica" | "fusione" | "incorporazione",
     "decorrenza": "YYYY-MM-DD", "denominazione": ..., "denominazione_nuovo": ...,
     "nota": ...}

Politica decisa l'08/10/2026 (migrazione ai codici ISTAT vigenti):
- ricodifica (stesso comune, codice nuovo): i dati del vecchio codice
  diventano del codice nuovo -> codice_vigente() restituisce il nuovo;
- fusione/incorporazione (comune soppresso): i dati del soppresso NON vengono
  attribuiti al comune vigente (nessuna somma fra comuni: medie, percentuali,
  mediane sarebbero sbagliate) -> codice_vigente() restituisce None e l'ETL
  non scrive lo shard;
- i vecchi link (frontend, MCP, CICO) si reindirizzano con
  codice_destinazione(), che per i soppressi restituisce il comune vigente.

A differenza di local_lookup, qui la tabella mancante o malformata SOLLEVA:
un ETL che normalizza i codici non deve proseguire in silenzio col vecchio
universo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from etl.lib import local_lookup

NOME_FILE = "variazioni_istat.json"
TIPI_RICODIFICA = {"ricodifica"}
TIPI_SOPPRESSIONE = {"fusione", "incorporazione"}
_RE_ISTAT = re.compile(r"^\d{6}$")

_cache: dict[str, dict] | None = None
_cache_path: Path | None = None
_predecessori: dict[str, list[str]] = {}


class TabellaVariazioniError(RuntimeError):
    """Tabella delle variazioni assente o incoerente."""


def percorso() -> Path:
    return local_lookup.get_lookup_dir() / NOME_FILE


def normalizza(cod) -> str:
    """'90003', 90003, '090003' -> '090003'. Solleva se non e' un codice comunale."""
    s = str(cod).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit() or len(s) > 6 or int(s) == 0:
        raise ValueError(f"codice ISTAT comunale non valido: {cod!r}")
    s = s.zfill(6)
    if not _RE_ISTAT.match(s):
        raise ValueError(f"codice ISTAT comunale non valido: {cod!r}")
    return s


def _valida(payload: dict, path: Path) -> dict[str, dict]:
    var = payload.get("variazioni") if isinstance(payload, dict) else None
    if not isinstance(var, dict) or not var:
        raise TabellaVariazioniError(f"{path}: chiave 'variazioni' assente o vuota")
    problemi = []
    for cod, v in var.items():
        if not _RE_ISTAT.match(cod):
            problemi.append(f"chiave non valida {cod!r}")
            continue
        nuovo, tipo = v.get("nuovo"), v.get("tipo")
        if not isinstance(nuovo, str) or not _RE_ISTAT.match(nuovo):
            problemi.append(f"{cod}: 'nuovo' non valido {nuovo!r}")
        if tipo not in TIPI_RICODIFICA | TIPI_SOPPRESSIONE:
            problemi.append(f"{cod}: tipo sconosciuto {tipo!r}")
        if nuovo in var:
            problemi.append(f"{cod}: la destinazione {nuovo} e' a sua volta non vigente")
    conteggi = payload.get("_conteggi")
    if isinstance(conteggi, dict) and sum(conteggi.values()) != len(var):
        problemi.append(f"_conteggi {conteggi} non coerente con {len(var)} variazioni")
    if problemi:
        raise TabellaVariazioniError(f"{path}: " + "; ".join(problemi[:10]))
    return var


def carica(path: Path | None = None, *, ricarica: bool = False) -> dict[str, dict]:
    """Restituisce {codice_vecchio: variazione}. Cache per processo."""
    global _cache, _cache_path
    p = Path(path) if path else percorso()
    if _cache is not None and _cache_path == p and not ricarica:
        return _cache
    if not p.exists():
        raise TabellaVariazioniError(
            f"{p} assente: generarla con scripts/etl/genera_variazioni_istat.py --out {p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TabellaVariazioniError(f"{p}: JSON non valido ({e})") from e
    _cache, _cache_path = _valida(payload, p), p
    _predecessori.clear()
    for vecchio, v in sorted(_cache.items()):
        if v["tipo"] in TIPI_RICODIFICA:
            _predecessori.setdefault(v["nuovo"], []).append(vecchio)
    return _cache


def variazione(cod) -> dict | None:
    """La voce della tabella per un codice non vigente, None se il codice e' vigente."""
    return carica().get(normalizza(cod))


def codice_vigente(cod) -> str | None:
    """Codice con cui SCRIVERE i dati di una fonte.

    - codice vigente      -> lo stesso codice (normalizzato a 6 cifre)
    - ricodifica          -> il codice nuovo
    - comune soppresso    -> None (i dati del soppresso non si attribuiscono)
    """
    c = normalizza(cod)
    v = carica().get(c)
    if v is None:
        return c
    return v["nuovo"] if v["tipo"] in TIPI_RICODIFICA else None


def codice_destinazione(cod) -> str:
    """Codice a cui REINDIRIZZARE un vecchio link: per i soppressi il comune vigente."""
    c = normalizza(cod)
    v = carica().get(c)
    return v["nuovo"] if v else c


def e_soppresso(cod) -> bool:
    v = variazione(cod)
    return bool(v) and v["tipo"] in TIPI_SOPPRESSIONE


def rimappa_chiavi(dati: dict) -> tuple[dict, dict]:
    """Rimappa un dict {codice_istat: valore} sui codici vigenti.

    Ricodifiche rinominate, soppressi scartati. Se una fonte avesse gia' sia il
    vecchio sia il nuovo codice dello stesso comune solleva ValueError: quale
    dei due valori sia giusto lo decide chi scrive l'ETL, non questa funzione.

    Restituisce (dict_rimappato, resoconto) con resoconto =
    {"ricodificati": n, "scartati_soppressi": [codici], "invariati": n}.
    """
    out: dict = {}
    origine: dict[str, str] = {}
    scartati: list[str] = []
    ricodificati = invariati = 0
    for k, val in dati.items():
        c = normalizza(k)
        dest = codice_vigente(c)
        if dest is None:
            scartati.append(c)
            continue
        if dest in out:
            raise ValueError(
                f"collisione su {dest}: presente sia come {origine[dest]} sia come {c}")
        out[dest] = val
        origine[dest] = c
        if dest != c:
            ricodificati += 1
        else:
            invariati += 1
    return out, {"ricodificati": ricodificati,
                 "scartati_soppressi": sorted(scartati),
                 "invariati": invariati}


# ---------------------------------------------------------------------------
# Lettura con inoltro (decisione 08/10/2026): gli ETL scrivono col codice usato
# dalla fonte; chi LEGGE cerca prima il codice vigente, poi i codici con cui lo
# stesso comune era ricodificato. Mai i comuni soppressi: i loro dati non si
# attribuiscono al comune vigente.
# ---------------------------------------------------------------------------

def candidati_lettura(cod) -> list[str]:
    """[codice, codici_precedenti_dello_stesso_comune...] in ordine di preferenza.

    Solo ricodifiche: per 112001 -> ['112001', '090003']; per 024129 -> ['024129'].
    """
    c = normalizza(cod)
    carica()
    return [c] + _predecessori.get(c, [])


def trova_file(modello: str, cod) -> tuple[Path, str] | None:
    """Primo file esistente per il comune: modello con {istat}, es. 'DATA/aria/{istat}.json'.

    Restituisce (path, codice_usato) oppure None se nessun candidato esiste.
    """
    for c in candidati_lettura(cod):
        p = Path(modello.format(istat=c))
        if p.exists():
            return p, c
    return None
