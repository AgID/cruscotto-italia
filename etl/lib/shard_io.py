"""Helper condiviso per la scrittura fail-safe degli shard per-comune.

Molti ETL costruiscono lo shard di un comune componendo piu' sezioni, ognuna
da una fonte diversa (es. veicoli = parco ISTAT + incidenti ISTAT + iscrizioni
ACI). Quando una fonte non e' raggiungibile in un dato giro, la sezione manca
nel nuovo shard; se lo shard viene sovrascritto cosi' com'e', il dato valido
precedente viene CANCELLATO.

Questo modulo centralizza la regola fail-safe: prima di sovrascrivere uno shard,
per ogni chiave "protetta" assente o vuota nel nuovo dato ma presente nel file
esistente, si riporta il dato vecchio marcandolo come non aggiornato in questo
giro (_stale_<chiave> = data ultimo aggiornamento valido). Gli aggiornamenti
validi non vengono mai bloccati: solo le sezioni mancanti vengono preservate.

Uso tipico in un ETL:

    from etl.lib.shard_io import write_shard_preserving

    for istat, shard in shards.items():
        write_shard_preserving(
            outdir / f"{istat}.json",
            shard,
            protected_keys=["parco_veicoli", "incidenti", "iscrizioni"],
            meta_map={"parco_veicoli": "_anno_dati_parco",
                      "incidenti": "_anno_dati_incidenti",
                      "iscrizioni": "_anno_dati_iscrizioni"},
        )
"""
from __future__ import annotations

import json
from pathlib import Path


def preserva_sezioni_esistenti(
    nuovo: dict,
    vecchio: dict,
    protected_keys: list[str],
    meta_map: dict[str, str] | None = None,
) -> int:
    """Riporta nel dict `nuovo` le sezioni protette assenti/vuote ma presenti nel
    dict `vecchio` (fail-safe: un fetch fallito non cancella dati validi).

    Args:
        nuovo: shard appena costruito (verra' mutato in-place).
        vecchio: shard esistente letto da disco.
        protected_keys: chiavi-sezione che non devono mai essere cancellate.
        meta_map: mappa opzionale chiave-sezione -> chiave-metadato "anno dato"
                  da preservare insieme alla sezione (es. _anno_dati_iscrizioni).

    Returns:
        Numero di sezioni preservate dal vecchio shard.
    """
    meta_map = meta_map or {}
    n = 0
    for k in protected_keys:
        if not nuovo.get(k) and vecchio.get(k):
            nuovo[k] = vecchio[k]
            meta_k = meta_map.get(k)
            if meta_k and not nuovo.get(meta_k) and vecchio.get(meta_k):
                nuovo[meta_k] = vecchio[meta_k]
            # marca il dato come non aggiornato in questo giro
            nuovo[f"_stale_{k}"] = vecchio.get("_generated_at") or True
            n += 1
    return n


def write_shard_preserving(
    path: Path,
    nuovo: dict,
    protected_keys: list[str] | None = None,
    meta_map: dict[str, str] | None = None,
    *,
    indent: int | None = 2,
    separators: tuple[str, str] | None = None,
) -> int:
    """Scrive lo shard `nuovo` su `path` preservando le sezioni protette dal file
    esistente se assenti nel nuovo (vedi preserva_sezioni_esistenti).

    Ritorna il numero di sezioni preservate (0 se il file non esisteva o se non
    c'era nulla da preservare).
    """
    n_preservate = 0
    if protected_keys and path.exists():
        try:
            vecchio = json.loads(path.read_text(encoding="utf-8"))
            n_preservate = preserva_sezioni_esistenti(
                nuovo, vecchio, protected_keys, meta_map
            )
        except Exception:
            # file corrotto/illeggibile: non blocchiamo la scrittura del nuovo
            n_preservate = 0
    path.write_text(
        json.dumps(nuovo, ensure_ascii=False, indent=indent, separators=separators),
        encoding="utf-8",
    )
    return n_preservate
