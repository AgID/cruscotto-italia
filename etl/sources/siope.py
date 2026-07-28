#!/usr/bin/env python3
"""ETL SIOPE — fonte primaria siope.it (RGS-MEF, banca dati gestita da Banca d'Italia).

Sostituisce l'ingestione via BDAP (v0.2.0). Motivi della migrazione:
  - BDAP genera i dump on-demand: cap ~400 s lato server, 503 ricorrenti,
    resource_id hardcoded per regione+anno, nessuna API di query.
  - BDAP aveva il 2025 troncato a marzo per 1.717 comuni (Lombardia + FVG)
    e assente per 84 comuni di Trento. Su siope.it i 12 mesi ci sono tutti.
  - siope.it espone Last-Modified e Accept-Ranges: la cache e invalidabile
    correttamente (il bug della cache senza TTL aveva congelato la produzione).

Formato fonte:
  SIOPE_USCITE.<anno>.zip / SIOPE_ENTRATE.<anno>.zip -> 1 CSV nazionale,
  senza header, separatore ',', 5 campi:
      "codice_ente","anno","mese","codice_gestionale","importo"
  Importi in CENTESIMI e MENSILI PURI (non cumulati): il totale anno e la
  somma dei mesi, non l'ultimo valore.

  SIOPE_ANAGRAFICHE.zip -> ANAG_ENTI_SIOPE (codice_ente -> comune, CF,
  popolazione) + ANAG_CODGEST_USCITE/ENTRATE (descrizioni per comparto).

Risoluzione del comune: il join primario e per CODICE FISCALE contro
lookup/comuni-index.json. Il campo prov+com dichiarato da SIOPE usa una
codifica provinciale non ISTAT per tutta la Sardegna (prefissi 112-119,
province soppresse) ed e quindi inaffidabile come chiave primaria: usarlo
produceva 377 shard con nomi ISTAT inesistenti.

Compatibilita shard: la chiave `voci` resta le USCITE con `mensili` CUMULATI,
identica alla v0.2.0, per non rompere frontend e chatbot. Le ENTRATE sono
additive sotto `entrate`, con `saldo_cassa` = incassi - pagamenti.

Licenza dato: CC BY 4.0 (stesso titolare e stesso regime di BDAP; accesso
libero per art. 8 c.3 DL 66/2014 e DM MEF 47989/2014).
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
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from etl.lib import manifest

log = structlog.get_logger()

ETL_VERSION = "0.3.0"
SOURCE_LABEL = ("MEF - Ragioneria Generale dello Stato, SIOPE "
                "(banca dati gestita da Banca d'Italia, www.siope.it); "
                "codifica D.M. 9/6/2016")
LICENZA = "CC BY 4.0"

# URL finale gia risolto: l'entry point /Siope/documenti/... risponde 302 verso
# http:// (porta 80 filtrata) e manderebbe in stallo il download.
BASE_URL = "https://www.siope.it/documenti/siope2/open/last"
ANAGRAFICHE_ZIP = "SIOPE_ANAGRAFICHE.zip"

# Cache persistente: /tmp viene ripulito e ha gia causato la perdita della
# cache ANAC. Override con --cache-dir o env SIOPE_CACHE_DIR.
CACHE_DIR = Path(os.environ.get("SIOPE_CACHE_DIR", "/var/cache/cruscotto-etl/siope"))
CACHE_DIR_FALLBACK = Path("/tmp/cruscotto-siope-cache")

DEFAULT_OUTDIR = Path("/var/www/cruscotto-italia/data/siope")
LOOKUP_INDEX = Path("/var/www/cruscotto-italia/data/lookup/comuni-index.json")

HTTP_CONNECT_TIMEOUT = int(os.environ.get("SIOPE_CONNECT_TIMEOUT", "30"))
HTTP_READ_TIMEOUT = int(os.environ.get("SIOPE_READ_TIMEOUT", "600"))
CHUNK = 1 << 20  # 1 MB

HEADERS = {
    "User-Agent": "cruscotto-italia-etl (+https://cruscotto-italia.dati.gov.it)",
    "Accept": "*/*",
}

# Comparto degli enti comunali in ANAG_CODGEST_*: da ANAG_SOTTOCOMPARTI,
# COMUNE -> COMUNI -> comparto PRO ("Province - Comuni - Citta metropolitane -
# Unioni di Comuni"). Le descrizioni degli altri comparti sono in larga parte
# identiche (codifica armonizzata) ma PRO e l'unico semanticamente corretto.
COMPARTO_COMUNI = "PRO"

# Titoli: non esistono nell'anagrafica dei codici gestionali (nessun codice a
# zeri). BDAP li derivava dalla prima cifra del codice gestionale; verificato
# su Matera 2026, 0 voci non conformi su 95. Le stringhe USCITE sono quelle
# gia presenti negli shard di produzione, per continuita testuale.
TITOLI_USCITE = {
    "0": "PAGAMENTI DA REGOLARIZZARE",
    "1": "Spese correnti",
    "2": "Spese in conto capitale",
    "3": "Spese per incremento attivita finanziarie",
    "4": "Rimborso Prestiti",
    "5": "Chiusura Anticipazioni ricevute da istituto tesoriere/cassiere",
    "7": "Uscite per conto terzi e partite di giro",
}
TITOLI_ENTRATE = {
    "0": "INCASSI DA REGOLARIZZARE",
    "1": "Entrate correnti di natura tributaria, contributiva e perequativa",
    "2": "Trasferimenti correnti",
    "3": "Entrate extratributarie",
    "4": "Entrate in conto capitale",
    "5": "Entrate da riduzione di attivita finanziarie",
    "6": "Accensione prestiti",
    "7": "Anticipazioni da istituto tesoriere/cassiere",
    "9": "Entrate per conto terzi e partite di giro",
}

# Registro correzioni: codice_ente SIOPE il cui comune dichiarato e errato e
# non correggibile per codice fiscale. Auto-sanante: se la fonte corregge,
# la voce diventa un no-op registrato nel log.
ENTE_ISTAT_FIX: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Normalizzazione testo
# ---------------------------------------------------------------------------

_ACC = {"a": "\u00e0", "e": "\u00e8", "i": "\u00ec", "o": "\u00f2", "u": "\u00f9",
        "A": "\u00c0", "E": "\u00c8", "I": "\u00cc", "O": "\u00d2", "U": "\u00d9"}


def norm_desc(s: str) -> str:
    """Normalizza le descrizioni di siope.it, che sono ASCII pure.

    La fonte ha traslitterato perdendo i caratteri tipografici:
      - vocale + apostrofo a fine parola = accento  (attivita' -> attivita con accento)
      - '?' tra due lettere = apostrofo curvo perso (dell?art -> dell'art)
      - '?' prima di chiusura = ellissi persa       (ecc?) -> ecc)
    Verificata su 495 codici comuni con gli shard BDAP: 445 identiche senza
    normalizzazione, 481 con.
    """
    if not s:
        return ""
    s = s.replace("\ufeff", "").strip()
    s = re.sub(r"(?<=[A-Za-z])\?(?=[A-Za-z])", "'", s)
    s = re.sub(r"\s*,?\s*\?(?=[)\],.]|$)", "", s)
    s = re.sub(r"([aeiouAEIOU])'(?=$|[\s,.;:)\]/])", lambda m: _ACC.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def norm_nome(s: str) -> str:
    """Chiave di confronto fra denominazioni (accenti, punteggiatura, prefissi)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.upper()
    for pre in ("COMUNE DI ", "COMUNE ", "CITTA DI ", "CITTA' DI "):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return re.sub(r"[^A-Z]", "", s)


def cg_to_code(cg: str, prefisso: str) -> str | None:
    """Converte il codice gestionale NPC puntato nella notazione degli shard.

    siope.it: '1.01.01.01.002'  ->  BDAP/shard: 'U1010101002'
    Verificato su Matera: 95/95 codici combaciano.
    """
    d = (cg or "").replace(".", "").strip()
    return (prefisso + d) if d.isdigit() else None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


def remote_last_modified(sess: requests.Session, url: str) -> str | None:
    """HEAD non e supportato dalla fonte (risponde text/html): si usa un GET
    con Range di 1 byte, che restituisce 206 con gli header completi."""
    try:
        r = sess.get(url, headers={"Range": "bytes=0-0"},
                     timeout=(HTTP_CONNECT_TIMEOUT, 60), stream=True)
        r.close()
        return r.headers.get("Last-Modified")
    except requests.RequestException as e:
        log.warning("last_modified_ko", url=url, err=type(e).__name__, msg=str(e)[:300])
        return None


def fetch_zip(sess: requests.Session, nome: str, cache_dir: Path,
              no_cache: bool = False) -> Path:
    """Scarica uno zip con validazione della cache per Last-Modified."""
    url = f"{BASE_URL}/{nome}"
    dest = cache_dir / nome
    meta_p = cache_dir / (nome + ".meta.json")
    lm = remote_last_modified(sess, url)

    if not no_cache and dest.exists() and meta_p.exists() and lm:
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("last_modified") == lm and dest.stat().st_size == meta.get("size"):
                log.info("cache_hit", file=nome, last_modified=lm,
                         mb=round(dest.stat().st_size / 1e6, 1))
                return dest
            log.info("cache_stale", file=nome, cache=meta.get("last_modified"), remoto=lm)
        except Exception as e:
            log.warning("cache_meta_illeggibile", file=nome, err=type(e).__name__)

    log.info("download_start", file=nome, url=url, last_modified=lm)
    tmp = dest.with_suffix(dest.suffix + ".part")
    got = 0
    with sess.get(url, timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT), stream=True) as r:
        r.raise_for_status()
        tot = int(r.headers.get("Content-Length") or 0)
        lm = r.headers.get("Last-Modified") or lm
        soglia = 10 << 20
        prossima = soglia
        with open(tmp, "wb") as fh:
            for blocco in r.iter_content(CHUNK):
                if not blocco:
                    continue
                fh.write(blocco)
                got += len(blocco)
                if got >= prossima:
                    pct = f"{got / tot * 100:.0f}%" if tot else "?"
                    log.info("download_progress", file=nome,
                             mb=round(got / 1e6, 1), pct=pct)
                    prossima += soglia
    if got < 1024:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{nome}: download troppo piccolo ({got} byte)")
    with open(tmp, "rb") as fh:
        if fh.read(2) != b"PK":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{nome}: non e uno ZIP (magic diverso da PK)")
    os.replace(tmp, dest)
    meta_p.write_text(json.dumps({"last_modified": lm, "size": got,
                                  "scaricato": datetime.now(timezone.utc).isoformat()}),
                      encoding="utf-8")
    log.info("download_done", file=nome, mb=round(got / 1e6, 1), last_modified=lm)
    return dest


def zip_member(zp: Path, prefisso: str) -> str:
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if os.path.basename(n).upper().startswith(prefisso.upper()):
                return n
    raise RuntimeError(f"{zp.name}: nessun membro con prefisso {prefisso}")


def zip_rows(zp: Path, prefisso: str):
    """Itera le righe di un CSV dentro lo zip, in streaming."""
    nome = zip_member(zp, prefisso)
    with zipfile.ZipFile(zp) as z, z.open(nome) as fh:
        yield from csv.reader(io.TextIOWrapper(fh, encoding="latin-1", newline=""))


# ---------------------------------------------------------------------------
# Anagrafiche
# ---------------------------------------------------------------------------

def carica_comuni_ufficiali() -> tuple[dict, dict, dict]:
    """comuni-index.json -> (per_istat, per_cf, per_nome)."""
    righe = json.loads(LOOKUP_INDEX.read_text(encoding="utf-8"))
    per_istat = {r["i"]: r for r in righe}
    per_cf: dict[str, list[str]] = defaultdict(list)
    per_nome: dict[str, list[str]] = defaultdict(list)
    for r in righe:
        if r.get("cf"):
            per_cf[r["cf"].strip()].append(r["i"])
        for parte in [r["n"]] + str(r["n"]).split("/"):
            k = norm_nome(parte)
            if k and r["i"] not in per_nome[k]:
                per_nome[k].append(r["i"])
    return per_istat, per_cf, per_nome


def costruisci_mappa_enti(anag_zip: Path) -> tuple[dict, dict]:
    """ANAG_ENTI_SIOPE -> {codice_ente: {istat, denominazione, popolazione, dal}}.

    Cascata: codice fiscale -> prov+com (solo se la denominazione e coerente)
    -> nome univoco. Il campo prov+com non e affidabile come chiave primaria.
    """
    per_istat, per_cf, per_nome = carica_comuni_ufficiali()
    mappa: dict[str, dict] = {}
    strat: dict[str, int] = defaultdict(int)
    irrisolti: list[tuple] = []

    for row in zip_rows(anag_zip, "ANAG_ENTI_SIOPE"):
        if len(row) != 9:
            continue
        cod, dal, al, cf, den, com, prov, pop, tipo = (x.strip() for x in row)
        if al != "9999-12-31" or tipo.upper() != "COMUNE":
            continue

        istat = None
        if cod in ENTE_ISTAT_FIX:
            istat = ENTE_ISTAT_FIX[cod]
            strat["1_registro"] += 1
        else:
            cand = per_cf.get(cf)
            if cand and len(cand) == 1:
                istat = cand[0]
                strat["2_codice_fiscale"] += 1
            else:
                dic = prov + com
                if dic in per_istat and norm_nome(den) and (
                    norm_nome(per_istat[dic]["n"]).startswith(norm_nome(den))
                    or norm_nome(den).startswith(norm_nome(per_istat[dic]["n"]))
                ):
                    istat = dic
                    strat["3_prov_com"] += 1
                else:
                    n = per_nome.get(norm_nome(den)) or []
                    if len(n) == 1:
                        istat = n[0]
                        strat["4_nome"] += 1

        if not istat:
            strat["5_IRRISOLTO"] += 1
            irrisolti.append((cod, den, cf, prov + com))
            continue

        try:
            popn = float(pop)
        except ValueError:
            popn = None
        mappa[cod] = {"istat": istat, "denominazione": den,
                      "popolazione": popn, "dal": dal}

    per_comune = defaultdict(list)
    for cod, v in mappa.items():
        per_comune[v["istat"]].append(cod)
    fusioni = {k: v for k, v in per_comune.items() if len(v) > 1}

    log.info("enti_risolti", enti=len(mappa), comuni=len(per_comune),
             strategie=dict(strat), fusioni=len(fusioni))
    for r in irrisolti[:10]:
        log.warning("ente_irrisolto", codice_ente=r[0], denominazione=r[1],
                    cf=r[2], prov_com=r[3])
    return mappa, dict(per_comune)


def carica_descrizioni(anag_zip: Path, prefisso_file: str, anno: int) -> dict[str, str]:
    """ANAG_CODGEST_* -> {codice NPC: descrizione normalizzata}.

    Cascata a tre livelli, perche la validita temporale dichiarata non copre
    tutti i codici realmente usati (alcuni risultano scaduti nel 2018 ma
    compaiono ancora nei movimenti, altri partono dal 2026 e sono usati prima):
      1. comparto PRO valido per l anno richiesto
      2. comparto PRO, versione piu recente a prescindere dalla validita
      3. qualunque altro comparto (la codifica e armonizzata: le descrizioni
         coincidono, cambia solo il perimetro di enti a cui si applica)
    """
    pro_valide: dict[str, tuple[str, str]] = {}
    pro_tutte: dict[str, tuple[str, str]] = {}
    altri: dict[str, tuple[str, str]] = {}

    for row in zip_rows(anag_zip, prefisso_file):
        if len(row) != 5:
            continue
        cod, comparto, desc, dal, al = (x.strip() for x in row)
        d = norm_desc(desc)
        if not d:
            continue
        if comparto == COMPARTO_COMUNI:
            prec = pro_tutte.get(cod)
            if prec is None or dal > prec[0]:
                pro_tutte[cod] = (dal, d)
            if dal[:4] <= str(anno) <= al[:4]:
                prec = pro_valide.get(cod)
                if prec is None or dal > prec[0]:
                    pro_valide[cod] = (dal, d)
        else:
            prec = altri.get(cod)
            if prec is None or dal > prec[0]:
                altri[cod] = (dal, d)

    out = {k: v[1] for k, v in pro_tutte.items()}
    out.update({k: v[1] for k, v in altri.items() if k not in out})
    out.update({k: v[1] for k, v in pro_valide.items()})
    log.info("descrizioni_caricate", file=prefisso_file, anno=anno,
             pro_valide=len(pro_valide), pro_totali=len(pro_tutte),
             da_altri_comparti=len([k for k in altri if k not in pro_tutte]),
             totale=len(out))
    return out


# ---------------------------------------------------------------------------
# Parsing movimenti
# ---------------------------------------------------------------------------

def aggrega_anno(zp: Path, prefisso_membro: str, anno: int,
                 mappa_enti: dict, etichetta: str) -> dict:
    """Somma gli importi per (comune, codice gestionale, mese).

    Gli importi sono in centesimi e MENSILI PURI: il totale anno e la somma
    dei 12 mesi. Ritorna {istat: {cg: [12 float]}} piu i mesi visti.
    """
    acc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0.0] * 12))
    mesi_per_comune: dict[str, set] = defaultdict(set)
    righe = ignorate = fuori_anno = malformate = 0
    mesi_glob: dict[str, int] = defaultdict(int)

    log.info("parsing_start", tipo=etichetta, anno=anno, file=zp.name)
    for row in zip_rows(zp, prefisso_membro):
        righe += 1
        if righe % 1000000 == 0:
            log.info("parsing_progress", tipo=etichetta, anno=anno,
                     righe=righe, comuni=len(acc))
        if len(row) != 5:
            malformate += 1
            continue
        cod, a, mese, cg, imp = (x.strip() for x in row)
        if a != str(anno):
            fuori_anno += 1
            continue
        ente = mappa_enti.get(cod)
        if ente is None:
            ignorate += 1
            continue
        try:
            idx = int(mese) - 1
            val = int(imp) / 100.0
        except ValueError:
            malformate += 1
            continue
        if not 0 <= idx < 12:
            malformate += 1
            continue
        istat = ente["istat"]
        acc[istat][cg][idx] += val
        mesi_per_comune[istat].add(idx)
        mesi_glob[mese] += 1

    log.info("parsing_done", tipo=etichetta, anno=anno, righe=righe,
             comuni=len(acc), ignorate_non_comuni=ignorate,
             fuori_anno=fuori_anno, malformate=malformate,
             righe_per_mese=dict(sorted(mesi_glob.items())))
    return {"acc": acc, "mesi": mesi_per_comune}


def costruisci_voci(cg_map: dict[str, list], anno: int, descrizioni: dict,
                    prefisso: str, titoli: dict) -> tuple[list, float]:
    """Da {cg: [12 mensili puri]} alle voci dello shard.

    `mensili` e CUMULATO e `importo_cumulato` e il totale d'anno della voce:
    stessa semantica della v0.2.0, per non rompere frontend e chatbot.
    """
    voci = []
    for cg, mens in cg_map.items():
        code = cg_to_code(cg, prefisso)
        if not code:
            continue
        cum = 0.0
        mensili: dict[str, float] = {}
        ultimo = ""
        for i, v in enumerate(mens):
            if v == 0.0 and cum == 0.0:
                continue
            cum += v
            k = f"{anno}/{i + 1:02d}"
            mensili[k] = round(cum, 2)
            ultimo = k
        if not mensili:
            continue
        voci.append({
            "codice_gestionale": code,
            "desc_gestionale": descrizioni.get(cg, ""),
            "codice_titolo": prefisso + code[1] + "0" * 9,
            "desc_titolo": titoli.get(code[1], ""),
            "importo_cumulato": round(cum, 2),
            "ultimo_mese": ultimo,
            "mensili": mensili,
        })
    voci.sort(key=lambda v: v["importo_cumulato"], reverse=True)
    return voci, round(sum(v["importo_cumulato"] for v in voci), 2)


# ---------------------------------------------------------------------------
# Shard
# ---------------------------------------------------------------------------

def blocco_anno(istat: str, anno: int, codici: list[str], mappa_enti: dict,
                uscite: dict, entrate: dict | None,
                desc_u: dict, desc_e: dict) -> dict | None:
    """Costruisce il blocco di un anno per un comune, sommando i codice_ente
    che vi afferiscono (piu di uno in caso di fusione)."""
    cg_u: dict[str, list] = defaultdict(lambda: [0.0] * 12)
    cg_e: dict[str, list] = defaultdict(lambda: [0.0] * 12)
    mesi: set[int] = set()

    acc_u, mesi_u = uscite["acc"], uscite["mesi"]
    for cg, m in acc_u.get(istat, {}).items():
        for i, v in enumerate(m):
            cg_u[cg][i] += v
    mesi |= mesi_u.get(istat, set())

    if entrate is not None:
        for cg, m in entrate["acc"].get(istat, {}).items():
            for i, v in enumerate(m):
                cg_e[cg][i] += v
        mesi |= entrate["mesi"].get(istat, set())

    if not cg_u and not cg_e:
        return None

    voci_u, tot_u = costruisci_voci(cg_u, anno, desc_u, "U", TITOLI_USCITE)
    voci_e, tot_e = costruisci_voci(cg_e, anno, desc_e, "E", TITOLI_ENTRATE)

    mesi_lista = [f"{anno}/{i + 1:02d}" for i in sorted(mesi)]
    # L'ente piu recente e quello corretto in caso di fusione.
    ente = sorted((mappa_enti[c] for c in codici), key=lambda x: x["dal"])[-1]

    blocco = {
        "anno": anno,
        "parziale": len(mesi_lista) < 12,
        "ente_siope": ente["denominazione"],
        "popolazione": ente["popolazione"],
        "mesi_disponibili": mesi_lista,
        "ultimo_mese": mesi_lista[-1] if mesi_lista else "",
        "n_voci": len(voci_u),
        "totale_anno": tot_u,
        "voci": voci_u,
    }
    if entrate is not None:
        blocco["entrate"] = {
            "n_voci": len(voci_e),
            "totale_anno": tot_e,
            "voci": voci_e,
        }
        blocco["saldo_cassa"] = round(tot_e - tot_u, 2)
    return blocco


def costruisci_shards(anni: list[int], dati: dict, mappa_enti: dict,
                      per_comune: dict, descrizioni: dict) -> dict[str, dict]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    shards: dict[str, dict] = {}
    for istat, codici in sorted(per_comune.items()):
        per_anno: dict[str, dict] = {}
        for anno in anni:
            b = blocco_anno(istat, anno, codici, mappa_enti,
                            dati[anno]["uscite"], dati[anno].get("entrate"),
                            descrizioni[anno]["u"], descrizioni[anno]["e"])
            if b:
                per_anno[str(anno)] = b
        if not per_anno:
            continue
        shards[istat] = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_LABEL,
            "_licenza": LICENZA,
            "_generated_at": now,
            "per_anno": per_anno,
        }
    return shards


def _completa_metadati(shard: dict) -> dict:
    """anni_disponibili e anno_default derivati dal contenuto reale.

    L'anno di default e l'ultimo CHIUSO (12 mesi) disponibile per quel comune;
    se non ce ne sono, l'ultimo parziale. Mai da costante hardcoded: era la
    causa per cui un anno troncato veniva presentato come completo.
    """
    anni = sorted(int(a) for a in shard["per_anno"])
    chiusi = [a for a in anni if not shard["per_anno"][str(a)].get("parziale")]
    shard["anni_disponibili"] = anni
    shard["anno_default"] = max(chiusi) if chiusi else max(anni)
    return shard


def scrivi_shards(shards: dict[str, dict], outdir: Path, anni: list[int]) -> dict:
    """Scrive gli shard preservando gli anni non processati in questo giro.

    Regola fail-safe: se un anno presente nel file esistente non e fra quelli
    elaborati ora, va conservato. La v0.2.0 riscriveva lo shard per intero da
    memoria e un fallimento parziale cancellava gli anni non caricati.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    elaborati = {str(a) for a in anni}
    scritti = preservati = 0
    for istat, shard in shards.items():
        path = outdir / f"{istat}.json"
        if path.exists():
            try:
                vecchio = json.loads(path.read_text(encoding="utf-8"))
                for a, blocco in (vecchio.get("per_anno") or {}).items():
                    if a not in elaborati:
                        shard["per_anno"][a] = blocco
                        preservati += 1
            except Exception:
                pass
        _completa_metadati(shard)
        shard["per_anno"] = {k: shard["per_anno"][k]
                             for k in sorted(shard["per_anno"])}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(shard, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, path)
        scritti += 1
    return {"scritti": scritti, "anni_preservati": preservati}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="ETL SIOPE da fonte primaria siope.it (uscite + entrate)")
    p.add_argument("--target", choices=["local"], default="local")
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument("--anni", type=str, default=None,
                   help="Anni separati da virgola (default: anno corrente e precedente)")
    p.add_argument("--no-cache", action="store_true",
                   help="Forza il re-download ignorando Last-Modified")
    p.add_argument("--no-entrate", action="store_true",
                   help="Elabora le sole uscite")
    p.add_argument("--dry-run", action="store_true",
                   help="Non scrive shard: solo download, parsing e statistiche")
    args = p.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    anno_corrente = datetime.now(timezone.utc).year
    if args.anni:
        anni = sorted({int(x) for x in args.anni.split(",") if x.strip()})
    else:
        anni = [anno_corrente - 1, anno_corrente]

    cache_dir = args.cache_dir or CACHE_DIR
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / ".w").write_text("x"); (cache_dir / ".w").unlink()
    except OSError as e:
        log.warning("cache_dir_non_scrivibile", path=str(cache_dir),
                    err=str(e), fallback=str(CACHE_DIR_FALLBACK))
        cache_dir = CACHE_DIR_FALLBACK
        cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("etl_start", versione=ETL_VERSION, anni=anni, outdir=str(args.outdir),
             cache=str(cache_dir), entrate=not args.no_entrate, dry_run=args.dry_run)
    sess = make_session()
    files_manifest = []

    try:
        anag = fetch_zip(sess, ANAGRAFICHE_ZIP, cache_dir, args.no_cache)
        mappa_enti, per_comune = costruisci_mappa_enti(anag)
        if not mappa_enti:
            raise RuntimeError("anagrafica enti vuota")

        dati: dict[int, dict] = {}
        descr: dict[int, dict] = {}
        anni_saltati: list[int] = []
        for anno in anni:
            descr[anno] = {
                "u": carica_descrizioni(anag, "ANAG_CODGEST_USCITE", anno),
                "e": carica_descrizioni(anag, "ANAG_CODGEST_ENTRATE", anno),
            }
            try:
                zu = fetch_zip(sess, f"SIOPE_USCITE.{anno}.zip", cache_dir, args.no_cache)
            except requests.HTTPError as e:
                # A gennaio il file dell'anno nuovo non esiste ancora (404): il
                # cron passa sempre anno-1 e anno corrente. Senza questa guardia
                # l'intero ETL moriva e si perdeva anche l'aggiornamento
                # dell'anno precedente, che invece c'e'.
                # Si salta solo il 404; ogni altro errore HTTP resta un guasto.
                if getattr(e.response, "status_code", None) == 404:
                    log.warning("anno_non_ancora_pubblicato", anno=anno,
                                msg="file assente sulla fonte, anno saltato")
                    anni_saltati.append(anno)
                    continue
                raise
            d = {"uscite": aggrega_anno(zu, f"USCITE_{anno}", anno, mappa_enti, "uscite")}
            files_manifest.append({"key": f"siope.it/SIOPE_USCITE.{anno}.zip",
                                   "size": zu.stat().st_size})
            if not args.no_entrate:
                try:
                    ze = fetch_zip(sess, f"SIOPE_ENTRATE.{anno}.zip", cache_dir, args.no_cache)
                    d["entrate"] = aggrega_anno(ze, f"ENTRATE_{anno}", anno,
                                                mappa_enti, "entrate")
                    files_manifest.append({"key": f"siope.it/SIOPE_ENTRATE.{anno}.zip",
                                           "size": ze.stat().st_size})
                except requests.HTTPError as e:
                    # Uscite presenti ma entrate no: si prosegue con le sole
                    # uscite invece di perdere tutto l'anno.
                    if getattr(e.response, "status_code", None) == 404:
                        log.warning("entrate_non_disponibili", anno=anno)
                    else:
                        raise
            dati[anno] = d

        anni = [a for a in anni if a in dati]
        if not anni:
            raise RuntimeError("nessun anno disponibile sulla fonte: "
                               f"saltati {anni_saltati}")
        if anni_saltati:
            log.warning("anni_saltati", anni=anni_saltati, elaborati=anni)
        shards = costruisci_shards(anni, dati, mappa_enti, per_comune, descr)
        log.info("shard_costruiti", comuni=len(shards))

        copertura = {}
        for anno in anni:
            n = sum(1 for s in shards.values() if str(anno) in s["per_anno"])
            compl = sum(1 for s in shards.values()
                        if not (s["per_anno"].get(str(anno)) or {"parziale": True})["parziale"])
            copertura[anno] = {"comuni": n, "anni_completi_12_mesi": compl}
        log.info("copertura", **{str(k): v for k, v in copertura.items()})

        if args.dry_run:
            log.info("dry_run_fine", comuni=len(shards))
            return 0

        res = scrivi_shards(shards, args.outdir, anni)
        log.info("shard_scritti", **res)
        files_manifest.append({"key": "data/siope/", "row_count": res["scritti"]})

        # Il manifest descrive la PRODUZIONE: un run su --outdir diverso e un
        # test e non deve toccarlo (altrimenti i test non sono isolabili).
        if args.outdir.resolve() == DEFAULT_OUTDIR.resolve() and not args.dry_run:
            try:
                manifest.update_source("siope", files_manifest, status="ok")
            except Exception as e:
                log.warning("manifest_skip", error=str(e))
        else:
            log.info("manifest_non_toccato", motivo="outdir non di produzione",
                     outdir=str(args.outdir))
        log.info("etl_done", comuni=len(shards), **res)
        return 0

    except Exception as e:
        log.exception("etl_failed", tipo=type(e).__name__, error=str(e))
        # Anche in errore: un --dry-run non deve marcare la produzione come
        # degradata. Il 28/07/2026 una prova con un anno inesistente ha
        # sovrascritto lo status di una fonte sana e azzerato la lista file.
        if args.outdir.resolve() == DEFAULT_OUTDIR.resolve() and not args.dry_run:
            try:
                manifest.update_source("siope", [],
                                       status=f"failed: {type(e).__name__}: {e}")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
