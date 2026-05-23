"""ETL Cultural-ON / DBUnico 2.0 - Luoghi della Cultura statali e non statali.

Fonte istituzionale: Ministero della Cultura (MiC) - Direzione Generale Organizzazione.
Pubblicazione: dataset catalogato su dati.gov.it (dataset id f43f148b-01fb-406d-8337-b92f8dbb6543).
Ontologia: Cultural-ON (Cultural ONtology) sviluppata da ISTC-CNR, 2016.
Banca dati: DBUnico 2.0 - 59.472 luoghi (musei, biblioteche, archivi, aree archeologiche,
            monumenti, ville, chiese e altri istituti).
Aggiornamento: continuo (endpoint SPARQL pubblico).
Licenza: CC BY 4.0 (https://w3id.org/italia/controlled-vocabulary/licences/A21_CCBY40).

URL endpoint SPARQL (HTTP plain, evita 503 frequenti del HTTPS):
  http://dati.beniculturali.it/sparql

URL dump alternativo (NON usato qui: contiene solo i 6.721 record con
institutionalCISName@it, coverage ~40% comuni vs 83% via SPARQL):
  https://dati.beniculturali.it/dataset/dataset-luoghi.json

STRATEGIA SCELTA: SPARQL (vs dump JSON-LD)
==========================================
Verifica 23/05/2026: il dump JSON-LD pubblicato dal MiC contiene solo
6.721 luoghi con denominazione formale italiana (filtro implicito
institutionalCISName@it). Via SPARQL si raggiungono ~33-50k luoghi e
coverage 83,2% comuni italiani (6566/7895), invece del 40% del dump.
Trade-off: WAF signature-based del MiC blocca pattern complessi
(GROUP BY, COUNT distinct multipli), quindi le query sono semplici e
aggregazione fatta lato Python.

LIMITAZIONE NOTA: Cultural-ON modella i "contenitori" culturali. Manca
nei dati l'ente titolare/proprietario (l'inverso di cis:isHeldBy non
e' valorizzato nel grafo SPARQL pubblico). Per l'aggancio agli edifici
tutelati e ai beni mobili esposti esiste ArCo (ICCD), ma e' un altro
dominio (beni culturali != contenitori) e non viene integrato qui.

Schema query SPARQL (2 round paginati):
  Q1 ANAGRAFICA per ogni luogo:
    ?s a cis:CulturalInstituteOrSite ;
       cis:institutionalCISName ?name ;
       cis:hasSite [ cis:siteAddress [
         clvapit:hasCity [ rdfs:label ?comune ] ;
         clvapit:hasProvince [ rdfs:label ?provincia ] ;
         clvapit:postCode ?cap ;
         clvapit:fullAddress ?indirizzo ] ] ;
       dc:type ?cat .
    OPTIONAL ?s geo:lat ?lat ; geo:long ?lon ; foaf:depiction ?img ;
             l0:description ?desc ; l0:identifier ?id .

  Q2 CONTATTI per ogni luogo (anche su luoghi senza institutionalCISName,
     ma intersecato in Python con Q1 prima del shardatura):
    ?s smapit:hasOnlineContactPoint ?cp .
    OPTIONAL ?cp smapit:hasTelephone [ ... 031 ... ] ; ... 033 ... ;
                 smapit:hasEmail [ smapit:emailAddress ?email ] ;
                 smapit:hasWebSite [ smapit:URL ?web ] .

Schema output shard cultural_on/<istat>.json (riassunto):
{
  "_etl_version": "0.1.0",
  "_source": "MiC DBUnico 2.0 (Cultural-ON)",
  "_source_url": "http://dati.beniculturali.it/sparql",
  "_snapshot_date": "YYYY-MM-DD",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_totale": 12,
    "mix_categoria": {"museo": 7, "biblioteca": 2, "archivio": 1, "monumento": 2},
    "pct_georef": 91.7,
    "pct_con_foto": 75.0,
    "pct_con_contatti": 83.3
  },
  "luoghi": [   # cap LUOGHI_CAP_BASE=30; lista compatta
    {"id": "DBUnico.106788", "denom": "Pinacoteca di Brera",
     "categorie": ["museo"],
     "lat": 45.471943, "lon": 9.187683,
     "indirizzo": "Via Brera, 28", "cap": "20121",
     "image": "https://..."}
  ]
}

Schema output shard cultural_on_full/<istat>.json (solo se n_luoghi > LUOGHI_CAP_BASE):
  Stesso schema base + campi per ogni luogo:
    descrizione, telefono, email, website, prenotazione.

Comuni vuoti (n_totale=0): si genera comunque shard con luoghi=[] per
consistenza UX (la sezione non sara' 'null' nel dashboard).

Pattern di esecuzione:
  1) fetch_anagrafica_sparql() -> list[dict] luoghi paginato 5k/page
     skip se cache locale del giorno esiste e --skip-download
  2) fetch_contatti_sparql() -> list[dict] contatti paginato 5k/page
  3) merge_anagrafica_contatti() -> list[dict] luoghi arricchiti
  4) load_lookups() -> nome_to_istat, canonical_istat (da comuni-bundle.json)
  5) group_by_istat() -> dict[istat] = list[luogo]
  6) build_shards() -> 7896 file cultural_on/<istat>.json + opzionali cultural_on_full/
  7) write_local() -> persist su DATA_DIR/cultural_on/ (+ DATA_DIR/cultural_on_full/)

Usage:
  python -m etl.sources.cultural_on --outdir=/var/www/cruscotto-italia/data/cultural_on
  python -m etl.sources.cultural_on --skip-download --full-shards
"""

import argparse
import json
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import structlog

from etl.lib import local_lookup, manifest

log = structlog.get_logger(__name__)


# =========================================================================
# Costanti
# =========================================================================

DUMP_URL_DOC = "https://www.dati.gov.it/view-dataset/dataset?id=f43f148b-01fb-406d-8337-b92f8dbb6543"
SPARQL_ENDPOINT = "http://dati.beniculturali.it/sparql"  # HTTP plain: HTTPS spesso da' 503

# Persistenza URL:
# - L'endpoint SPARQL del MiC e' pubblico e attivo dal 2016, riconosciuto
#   dalle linee guida AgID per i LOD culturali. URL stabile.
# - Il dump JSON-LD nazionale e' un fallback potenziale (vedi docstring):
#   contiene solo i ~6.721 record con institutionalCISName@it, coverage
#   ~40% vs 83% via SPARQL.

CACHE_DIR = Path("/tmp/cruscotto_cultural_on")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ETL_VERSION = "0.1.0"
SOURCE_LABEL = "MiC DBUnico 2.0 (Cultural-ON)"

# Cap luoghi[] nello shard base (riassunto). Comuni con > LUOGHI_CAP_BASE
# generano anche shard FULL su cultural_on_full/<istat>.json con TUTTI i
# luoghi e tutti i campi (descrizioni, contatti, prenotazioni).
LUOGHI_CAP_BASE = 30

# Paginazione SPARQL: il MiC accetta query senza GROUP BY ma con LIMIT
# fino a ~10000 in teoria. Empiricamente 5000/page va in timeout sul server.
# 2000/page sta sotto i 5 secondi per pagina con la query anagrafica
# completa (15 OPTIONAL).
SPARQL_PAGE_SIZE = 2000
SPARQL_SLEEP_BETWEEN_PAGES = 1.5
SPARQL_TIMEOUT = 90
SPARQL_MAX_RETRIES = 3


# =========================================================================
# Whitelist dc:type - 1 sola colonna (categoria luogo)
#
# Cultural-ON usa dc:type come literal (es. "Museo, Galleria e/o raccolta").
# 27 valori distinti verificati su SPARQL endpoint 23/05/2026.
#
# La dimensione "ente titolare/proprietario" NON e' modellata nel grafo
# Cultural-ON: cis:holdsRoleInTime/roapit:TimeIndexedRole non sono
# popolati per i CulturalInstituteOrSite, e dc:type contiene
# occasionalmente stringhe tipo "MiC"/"Comune"/"Regione" ma sono presenti
# in <10 record su 6721. Non e' una dimensione affidabile per KPI:
# preferiamo non modellarla affatto, mantenendoci coerenti con la
# semantica "Cultural-ON modella i contenitori culturali".
#
# Le stringhe in dc:type che indicano ente (MiC, Fondazione, Regione,
# Comune, Istituto Centrale, Soprintendenza ...) NON sono mappate qui
# sotto: vengono ignorate (luogo finisce in 0 categorie -> diventa "altro").
# =========================================================================

# Tipologia LUOGO (categoria fisica) -> nome breve normalizzato
CATEGORIA_LUOGO: dict[str, str] = {
    "Museo, Galleria e/o raccolta":                          "museo",
    "Biblioteca":                                            "biblioteca",
    "Biblioteca Statale":                                    "biblioteca",
    "Archivio":                                              "archivio",
    "Archivio di Stato":                                     "archivio",
    "Area Archeologica":                                     "area_archeologica",
    "Parco Archeologico":                                    "area_archeologica",
    "Monumento":                                             "monumento",
    "Monumento Funerario":                                   "monumento",
    "Monumento di Archeologia Industriale":                  "monumento",
    "Architettura Civile":                                   "architettura",
    "Architettura Fortificata":                              "architettura",
    "Villa o Palazzo di interesse storico o artistico":      "architettura",
    "Parco o Giardino di interesse storico o artistico":     "parco_giardino",
    "Chiesa o edificio di culto":                            "chiesa",
    "Altro":                                                 "altro",
    "I tesori della Cultura":                                "altro",
}

# Ordine di priorita' per la categoria primaria (icona mappa, etc.)
CATEGORIA_PRIORITA = [
    "museo", "biblioteca", "archivio", "area_archeologica",
    "monumento", "architettura", "parco_giardino", "chiesa", "altro",
]


# =========================================================================
# Helpers
# =========================================================================

def normalize(s: str) -> str:
    """Normalizza un nome comune per il match nome->ISTAT.

    Stessa logica di runts.normalize() / pnrr_progetti.normalize():
      1. Split su '/' (per nomi bilingui bundle tipo "Bolzano/Bozen")
      2. NFD-strip degli accenti
      3. Apostrofi (dritti e tipografici) rimossi
      4. Trattini sostituiti con spazio
      5. Uppercase + collapse di spazi multipli
    """
    if not s:
        return ""
    s = s.split("/")[0]
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("\u2019", "")
    s = s.replace("-", " ")
    return " ".join(s.upper().split())


# =========================================================================
# FASE 1 - SPARQL: query builders, retry, paginazione
# =========================================================================

USER_AGENT = (
    "Mozilla/5.0 (compatible; CruscottoItalia/1.0; "
    "+https://cruscotto-italia.dati.gov.it/) cultural_on-etl"
)


def _sparql_post(query: str, timeout: int = SPARQL_TIMEOUT) -> dict:
    """Esegue una query SPARQL e ritorna il JSON parsato.

    Strategia anti-WAF MiC:
      - HTTP plain (non HTTPS che spesso ritorna 503)
      - GET con data-urlencode (POST funziona ma il WAF MiC e' piu' tollerante
        con GET su queste query semplici)
      - Retry esponenziale su 503/timeout: 3 tentativi con backoff 5/10/20s
      - User-Agent identificabile per la collaborazione con il MiC
    """
    last_err = None
    for attempt in range(SPARQL_MAX_RETRIES):
        if attempt > 0:
            backoff = 5 * (2 ** (attempt - 1))
            log.warning("cultural_on_sparql_retry", attempt=attempt, backoff_s=backoff,
                        last_err=str(last_err)[:200] if last_err else None)
            time.sleep(backoff)
        try:
            r = requests.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "application/json"},
                headers={"User-Agent": USER_AGENT,
                         "Accept": "application/sparql-results+json"},
                timeout=timeout,
            )
            # WAF MiC ritorna HTML 200 con "Web Application Firewall" se blocca
            if r.status_code == 200 and "Web Application Firewall" in r.text[:2000]:
                raise RuntimeError("SPARQL bloccato dal WAF MiC (signature). "
                                   "Verifica pattern query (no GROUP BY / ORDER BY complessi).")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError, RuntimeError) as e:
            last_err = e
            continue
    raise RuntimeError(f"SPARQL fallito dopo {SPARQL_MAX_RETRIES} tentativi: {last_err}")


def _bind_str(bindings: dict, key: str) -> str | None:
    """Estrae valore di una variabile dal binding SPARQL JSON o None."""
    b = bindings.get(key)
    if not b:
        return None
    v = b.get("value")
    return v if v else None


def _bind_float(bindings: dict, key: str) -> float | None:
    s = _bind_str(bindings, key)
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _clean_text(s: str | None) -> str | None:
    """Strip + normalize whitespace su literal sporchi dal MiC.

    Esempio reale: 'mailto:\\n  lupo.civitella@parcoabruzzo.it\\n  '
    diventa 'mailto:lupo.civitella@parcoabruzzo.it'.
    """
    if not s:
        return None
    # collapse whitespace consecutivi (compresi \n, \t)
    s = " ".join(s.split())
    return s if s else None


def _clean_email(s: str | None) -> str | None:
    s = _clean_text(s)
    if not s:
        return None
    if s.lower().startswith("mailto:"):
        s = s[7:].lstrip()
    if "@" not in s or "." not in s:
        return None
    return s


def _clean_url(s: str | None) -> str | None:
    """Pulisce URL: rimuove markdown [url](url), valida prefisso http(s)://."""
    s = _clean_text(s)
    if not s:
        return None
    # markdown [url](url) -> url
    if s.startswith("[") and "](" in s:
        # estrai l'URL dentro (...)
        try:
            s = s.split("](", 1)[1].rstrip(")")
        except (IndexError, ValueError):
            pass
    if not s.lower().startswith(("http://", "https://")):
        return None
    return s


def _clean_phone(s: str | None) -> str | None:
    s = _clean_text(s)
    if not s:
        return None
    # un telefono ragionevole ha almeno 4 cifre
    digits = sum(1 for c in s if c.isdigit())
    if digits < 4:
        return None
    return s


# =========================================================================
# FASE 2 - Query anagrafica paginata
# =========================================================================

# Channel SOAS per smapit:hasTelephoneType (controlled vocabulary AgID)
TEL_CHANNEL_PHONE = "https://w3id.org/italia/controlled-vocabulary/classifications-for-public-services/channel/031"
TEL_CHANNEL_FAX = "https://w3id.org/italia/controlled-vocabulary/classifications-for-public-services/channel/033"


def _query_anagrafica_core(limit: int, offset: int) -> str:
    """Query anagrafica CORE: solo i campi REQUIRED + provincia OPTIONAL.

    Restituisce ?s ?name ?type ?comune ?provincia. Tutti i campi qui sono
    quasi sempre presenti (institutionalCISName, dc:type, hasSite full).
    Tempo medio per pagina 2000: ~3 secondi.

    Un singolo ?s puo' avere PIU' righe se ha PIU' dc:type. Aggrego
    lato Python.
    """
    return f"""PREFIX cis: <http://dati.beniculturali.it/cis/>
PREFIX clvapit: <https://w3id.org/italia/onto/CLV/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
SELECT ?s ?name ?type ?comune ?provincia WHERE {{
  ?s a cis:CulturalInstituteOrSite .
  ?s cis:institutionalCISName ?name .
  FILTER(lang(?name) = "it")
  ?s dc:type ?type .
  ?s cis:hasSite ?site .
  ?site cis:siteAddress ?addr .
  ?addr clvapit:hasCity ?city .
  ?city rdfs:label ?comune .
  OPTIONAL {{ ?addr clvapit:hasProvince ?prov . ?prov rdfs:label ?provincia }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_anagrafica_dettagli(limit: int, offset: int) -> str:
    """Query anagrafica DETTAGLI: tutti i campi OPTIONAL su CIS + Address.

    Separata dalla CORE per evitare il cross-product di 15 OPTIONAL che
    portava in timeout server-side l'endpoint MiC. Tempo medio per
    pagina 2000: ~5 secondi.

    Restituisce ?s ?identifier ?indirizzo ?cap ?lat ?lon ?image ?descrizione.
    Da joinare con CORE lato Python via ?s.
    """
    return f"""PREFIX cis: <http://dati.beniculturali.it/cis/>
PREFIX clvapit: <https://w3id.org/italia/onto/CLV/>
PREFIX geo: <http://www.w3.org/2003/01/geo/wgs84_pos#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX l0: <https://w3id.org/italia/onto/l0/>
SELECT ?s ?identifier ?indirizzo ?cap ?lat ?lon ?image ?descrizione WHERE {{
  ?s a cis:CulturalInstituteOrSite .
  OPTIONAL {{ ?s l0:identifier ?identifier }}
  OPTIONAL {{ ?s cis:hasSite ?site . ?site cis:siteAddress ?addr .
             OPTIONAL {{ ?addr clvapit:fullAddress ?indirizzo }}
             OPTIONAL {{ ?addr clvapit:postCode ?cap }} }}
  OPTIONAL {{ ?s geo:lat ?lat }}
  OPTIONAL {{ ?s geo:long ?lon }}
  OPTIONAL {{ ?s foaf:depiction ?image }}
  OPTIONAL {{ ?s l0:description ?descrizione . FILTER(lang(?descrizione) = "it") }}
}} LIMIT {limit} OFFSET {offset}"""


def fetch_anagrafica_sparql(skip_cache: bool = False) -> list[dict]:
    """Scarica tutti i record anagrafica SPARQL in 2 passate (core + dettagli)
    + aggrega righe duplicate (un luogo con N dc:type genera N righe).

    Cache su disco: /tmp/cruscotto_cultural_on/anagrafica.json.
    Ritorna list[dict] di luoghi normalizzati con chiavi:
      uri, id, denom, descrizione, categorie[], categorie_raw[],
      comune_label, provincia_label, indirizzo, cap, lat, lon, image
    """
    cache_path = CACHE_DIR / "anagrafica.json"
    today = datetime.now().strftime("%Y%m%d")
    cache_marker = CACHE_DIR / f"anagrafica.{today}.ok"

    if not skip_cache and cache_path.exists() and cache_marker.exists():
        data = json.loads(cache_path.read_text())
        log.info("cultural_on_anagrafica_cache_hit", path=str(cache_path),
                 n=len(data))
        return data

    log.info("cultural_on_anagrafica_fetch_start", endpoint=SPARQL_ENDPOINT,
             page_size=SPARQL_PAGE_SIZE)
    t0 = time.time()

    # --- FASE 1A: CORE (denom + categoria + comune + provincia) ---
    luoghi: dict[str, dict] = {}
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        q = _query_anagrafica_core(SPARQL_PAGE_SIZE, offset)
        result = _sparql_post(q)
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            log.info("cultural_on_core_pagina_vuota", page=page_num, offset=offset)
            break

        for b in bindings:
            s_uri = _bind_str(b, "s")
            if not s_uri:
                continue
            cur = luoghi.setdefault(s_uri, {
                "uri": s_uri,
                "id": None,
                "denom": None,
                "descrizione": None,
                "categorie": [],
                "categorie_raw": [],
                "comune_label": None,
                "provincia_label": None,
                "indirizzo": None,
                "cap": None,
                "lat": None,
                "lon": None,
                "image": None,
            })
            if cur["denom"] is None:
                cur["denom"] = _clean_text(_bind_str(b, "name"))
            if cur["comune_label"] is None:
                cur["comune_label"] = _clean_text(_bind_str(b, "comune"))
            if cur["provincia_label"] is None:
                cur["provincia_label"] = _clean_text(_bind_str(b, "provincia"))
            raw_type = _bind_str(b, "type")
            if raw_type:
                if raw_type not in cur["categorie_raw"]:
                    cur["categorie_raw"].append(raw_type)
                mapped = CATEGORIA_LUOGO.get(raw_type)
                if mapped and mapped not in cur["categorie"]:
                    cur["categorie"].append(mapped)

        log.info("cultural_on_core_pagina", page=page_num, offset=offset,
                 bindings=len(bindings), luoghi_aggregati=len(luoghi))

        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    log.info("cultural_on_core_done", n_luoghi=len(luoghi),
             secs=round(time.time() - t0, 1))

    # --- FASE 1B: DETTAGLI (id, descr, indirizzo, cap, lat, lon, image) ---
    t1 = time.time()
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        q = _query_anagrafica_dettagli(SPARQL_PAGE_SIZE, offset)
        result = _sparql_post(q)
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            log.info("cultural_on_dett_pagina_vuota", page=page_num, offset=offset)
            break

        n_match = 0
        for b in bindings:
            s_uri = _bind_str(b, "s")
            if not s_uri:
                continue
            cur = luoghi.get(s_uri)
            if cur is None:
                # luogo non presente in CORE (senza institutionalCISName@it): skip
                continue
            n_match += 1
            if cur["id"] is None:
                cur["id"] = _bind_str(b, "identifier")
            if cur["descrizione"] is None:
                cur["descrizione"] = _clean_text(_bind_str(b, "descrizione"))
            if cur["indirizzo"] is None:
                cur["indirizzo"] = _clean_text(_bind_str(b, "indirizzo"))
            if cur["cap"] is None:
                cur["cap"] = _clean_text(_bind_str(b, "cap"))
            if cur["lat"] is None:
                cur["lat"] = _bind_float(b, "lat")
            if cur["lon"] is None:
                cur["lon"] = _bind_float(b, "lon")
            if cur["image"] is None:
                cur["image"] = _bind_str(b, "image")

        log.info("cultural_on_dett_pagina", page=page_num, offset=offset,
                 bindings=len(bindings), match_in_core=n_match)

        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    log.info("cultural_on_dett_done",
             secs=round(time.time() - t1, 1))

    result = list(luoghi.values())
    elapsed = time.time() - t0
    log.info("cultural_on_anagrafica_done",
             n_luoghi=len(result),
             secs_totali=round(elapsed, 1))

    cache_path.write_text(json.dumps(result, ensure_ascii=False))
    cache_marker.touch()
    return result


# =========================================================================
# FASE 3 - Query contatti paginata (telefono, email, website, prenotazioni)
# =========================================================================

def _query_contatti(limit: int, offset: int) -> str:
    """Query contatti per ogni CulturalInstituteOrSite.

    Restituisce N righe per ?s (un luogo puo' avere piu' contact point,
    o un solo contact point con piu' canali). Aggreghiamo lato Python.

    OPTIONAL su tutti i sub-pattern: vogliamo TUTTI i luoghi anche senza
    contatti, per non perdere righe se manca solo l'email.
    """
    return f"""PREFIX cis: <http://dati.beniculturali.it/cis/>
PREFIX smapit: <https://w3id.org/italia/onto/SM/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?telefono ?fax ?email ?website ?prenotazione
WHERE {{
  ?s a cis:CulturalInstituteOrSite .
  ?s smapit:hasOnlineContactPoint ?cp .
  OPTIONAL {{
    ?cp smapit:hasTelephone ?tel .
    ?tel smapit:hasTelephoneType <{TEL_CHANNEL_PHONE}> .
    ?tel smapit:telephoneNumber ?telefono .
  }}
  OPTIONAL {{
    ?cp smapit:hasTelephone ?fax_n .
    ?fax_n smapit:hasTelephoneType <{TEL_CHANNEL_FAX}> .
    ?fax_n smapit:telephoneNumber ?fax .
  }}
  OPTIONAL {{ ?cp smapit:hasEmail ?em . ?em smapit:emailAddress ?email }}
  OPTIONAL {{ ?cp smapit:hasWebSite ?ws . ?ws smapit:URL ?website }}
}} LIMIT {limit} OFFSET {offset}"""


def fetch_contatti_sparql(skip_cache: bool = False) -> dict[str, dict]:
    """Scarica i contatti SPARQL e ritorna dict uri -> dict contatti.

    Cache: /tmp/cruscotto_cultural_on/contatti.json (mappa uri -> contatti).
    """
    cache_path = CACHE_DIR / "contatti.json"
    today = datetime.now().strftime("%Y%m%d")
    cache_marker = CACHE_DIR / f"contatti.{today}.ok"

    if not skip_cache and cache_path.exists() and cache_marker.exists():
        data = json.loads(cache_path.read_text())
        log.info("cultural_on_contatti_cache_hit", path=str(cache_path),
                 n=len(data))
        return data

    log.info("cultural_on_contatti_fetch_start", endpoint=SPARQL_ENDPOINT,
             page_size=SPARQL_PAGE_SIZE)
    t0 = time.time()

    contatti: dict[str, dict] = {}
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        q = _query_contatti(SPARQL_PAGE_SIZE, offset)
        result = _sparql_post(q)
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            log.info("cultural_on_contatti_pagina_vuota", page=page_num,
                     offset=offset)
            break

        for b in bindings:
            s_uri = _bind_str(b, "s")
            if not s_uri:
                continue
            cur = contatti.setdefault(s_uri, {
                "telefono": None, "fax": None,
                "email": None, "website": None,
                "prenotazione": None,
            })
            # prima occorrenza vince (se NON gia' valorizzato)
            if cur["telefono"] is None:
                cur["telefono"] = _clean_phone(_bind_str(b, "telefono"))
            if cur["fax"] is None:
                cur["fax"] = _clean_phone(_bind_str(b, "fax"))
            if cur["email"] is None:
                cur["email"] = _clean_email(_bind_str(b, "email"))
            if cur["website"] is None:
                cur["website"] = _clean_url(_bind_str(b, "website"))

        log.info("cultural_on_contatti_pagina", page=page_num,
                 offset=offset, bindings=len(bindings),
                 luoghi_aggregati=len(contatti))

        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    elapsed = time.time() - t0
    log.info("cultural_on_contatti_done",
             n_luoghi=len(contatti),
             pagine=page_num,
             secs=round(elapsed, 1))

    cache_path.write_text(json.dumps(contatti, ensure_ascii=False))
    cache_marker.touch()
    return contatti


# =========================================================================
# FASE 4 - Merge anagrafica + contatti
# =========================================================================

def merge_anagrafica_contatti(anagrafica: list[dict],
                              contatti: dict[str, dict]) -> list[dict]:
    """Aggiunge i campi di contatto ai record anagrafica.

    I luoghi senza contact point restano con telefono/email/website=None.
    """
    merged = 0
    out = []
    for luogo in anagrafica:
        c = contatti.get(luogo["uri"], {})
        luogo_out = dict(luogo)
        luogo_out["telefono"] = c.get("telefono")
        luogo_out["fax"] = c.get("fax")
        luogo_out["email"] = c.get("email")
        luogo_out["website"] = c.get("website")
        luogo_out["prenotazione"] = c.get("prenotazione")
        if any(luogo_out.get(k) for k in ("telefono", "email", "website")):
            merged += 1
        out.append(luogo_out)
    log.info("cultural_on_merge_done",
             n_totale=len(out),
             n_con_contatti=merged,
             pct_contatti=round(merged * 100 / len(out), 1) if out else 0)
    return out
