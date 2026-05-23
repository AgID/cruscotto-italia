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
SPARQL_ENDPOINT = "https://dati.beniculturali.it/sparql"
# Note: il server MiC accetta sia HTTP che HTTPS sulle stesse risorse, ma:
# - alcune reti corporate/cloud (es. Aruba egress) bloccano HTTP outbound -> usa HTTPS
# - alcune sandbox CDN/proxy danno 503 su HTTPS (es. Cloudflare worker fetch) -> usa HTTP
# Default HTTPS perche' lo scenario di prod e' Aruba/VM AgID dove HTTP e' bloccato.

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
                headers={"User-Agent": USER_AGENT},
                # NB: NO header Accept. Aggiungerlo provoca 406 Not Acceptable
                # sul vhost HTTPS del MiC (test 23/05/2026 da VM AgID).
                # Il content-type del response e' negoziato via query param
                # 'format=application/json' (gia' settato sotto in params).
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
# Helper ArCo: parsing label indirizzo + estrazione WKT POINT
# =========================================================================

# Sigle province italiane vigenti (107 totali, ISTAT 2024)
VALID_SIGLE_PROVINCIA = {
    "AG","AL","AN","AO","AP","AQ","AR","AT","AV","BA","BG","BI","BL","BN","BO","BR",
    "BS","BT","BZ","CA","CB","CE","CH","CL","CN","CO","CR","CS","CT","CZ","EN","FC",
    "FE","FG","FI","FM","FR","GE","GO","GR","IM","IS","KR","LC","LE","LI","LO","LT",
    "LU","MB","MC","ME","MI","MN","MO","MS","MT","NA","NO","NU","OR","PA","PC","PD",
    "PE","PG","PI","PN","PO","PR","PT","PU","PV","PZ","RA","RC","RE","RG","RI","RM",
    "RN","RO","SA","SI","SO","SP","SR","SS","SU","SV","TA","TE","TN","TO","TP","TR",
    "TS","TV","UD","VA","VB","VC","VE","VI","VR","VT","VV",
}


def parse_address_label(lab: str | None) -> tuple[str | None, str | None]:
    """Parsa la stringa rdfs:label di CulturalPropertyAddress ArCo.

    Format empirici verificati (sample 5000+ record, 23/05/2026):
      "ITALIA, Veneto, PD, Padova, PADOVA, Via Melchiorre Cesarotti 8"
      "Italia, Campania, NA, Napoli, 80138 Via Santa Maria di Costantinopoli 40"
      "Europa, ITALIA, Piemonte, TO, Avigliana"
      "Europa - Italia - Lombardia - Mi - Milano"     (separatore '-')
      "MC, Camerino, Camerino"                        (prefisso ITALIA mancante)

    Ritorna (sigla_2char_upper, comune_upper) oppure (None, None).

    Strategia:
      1. Normalizza separatori: ' - ' e ' \u2013 ' -> ', '
      2. Split su ',' e strip ogni parte
      3. Rimuovi prefisso geo (EUROPA, ITALIA - case insensitive)
      4. Scansiona prime 4 posizioni cercando una sigla 2-char in
         VALID_SIGLE_PROVINCIA (upper)
      5. Il comune e' la parte IMMEDIATAMENTE successiva alla sigla
    """
    if not lab or not isinstance(lab, str):
        return (None, None)
    s = lab.replace(" - ", ", ").replace(" \u2013 ", ", ")
    parts = [p.strip() for p in s.split(",")]
    # rimuovi prefisso geo
    while parts and parts[0].upper() in ("EUROPA", "ITALIA"):
        parts.pop(0)
    # scansiona prime 4 posizioni per trovare la sigla
    for i, p in enumerate(parts[:4]):
        if len(p) == 2 and p.upper() in VALID_SIGLE_PROVINCIA:
            if i + 1 < len(parts) and parts[i + 1]:
                comune = parts[i + 1].strip()
                # alcuni record ripetono il comune in maiuscolo subito dopo
                # (es. "PD, Padova, PADOVA, Via...") -> prendiamo il primo
                return (p.upper(), comune.upper())
            return (p.upper(), None)
    return (None, None)


def parse_wkt_point(wkt: str | None) -> tuple[float | None, float | None]:
    """Estrae (lat, lon) da WKT POINT serializzato.

    ATTENZIONE: WKT standard ISO usa POINT(LON LAT) - longitudine prima!
    Esempi reali ArCo (verificati 23/05/2026):
      "POINT(12.099827 45.425196)"   -> lat=45.425196 lon=12.099827
      "POINT(9.187683 45.471943)"    -> Pinacoteca di Brera, Milano

    Restituisce sempre (lat, lon) nell'ordine "geografico naturale".
    Ritorna (None, None) se WKT non parseabile o e' un POLYGON/altro.
    """
    if not wkt or not isinstance(wkt, str):
        return (None, None)
    s = wkt.strip().upper()
    if not s.startswith("POINT"):
        return (None, None)
    # estrai contenuto tra parentesi
    try:
        start = s.index("(")
        end = s.rindex(")")
    except ValueError:
        return (None, None)
    inside = s[start + 1: end].strip()
    coords = inside.split()
    if len(coords) < 2:
        return (None, None)
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return (None, None)
    # sanity check: Italia lat 35-47, lon 6-19
    if not (30.0 <= lat <= 50.0) or not (5.0 <= lon <= 22.0):
        return (None, None)
    return (lat, lon)


def _test_arco_helpers() -> int:
    """Unit test isolati per i 2 helper ArCo. Lancia con --test-helpers.

    Nessuna chiamata HTTP, solo asserzioni su input noti.
    Stampa esito e ritorna 0 se tutti OK, 1 se qualcosa fallisce.
    """
    failed = 0

    # --- parse_address_label ---
    cases_addr = [
        # (input, atteso (sigla, comune) )
        ("ITALIA, Veneto, PD, Padova, PADOVA, Via Cesarotti 8", ("PD", "PADOVA")),
        ("Italia, Campania, NA, Napoli, 80138 Via X 40",       ("NA", "NAPOLI")),
        ("Europa, ITALIA, Piemonte, TO, Avigliana",            ("TO", "AVIGLIANA")),
        ("Europa - Italia - Lombardia - Mi - Milano",          ("MI", "MILANO")),
        ("MC, Camerino, Camerino",                             ("MC", "CAMERINO")),
        ("ITALIA, Puglia, BT, Andria",                         ("BT", "ANDRIA")),
        ("ITALIA, Sicilia, RG, Pozzallo",                      ("RG", "POZZALLO")),
        ("",                                                    (None, None)),
        (None,                                                  (None, None)),
        ("XX nonvalida",                                        (None, None)),
    ]
    for lab, expected in cases_addr:
        got = parse_address_label(lab)
        ok = got == expected
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] parse_address_label({lab!r}) -> {got} (atteso {expected})")
        if not ok:
            failed += 1

    print()
    # --- parse_wkt_point ---
    cases_wkt = [
        # (input, atteso (lat, lon))
        ("POINT(12.099827 45.425196)", (45.425196, 12.099827)),
        ("POINT(9.187683 45.471943)",  (45.471943, 9.187683)),  # Brera
        ("POINT(14.32 41.07)",         (41.07, 14.32)),         # Caserta
        ("Point(11.879031 45.395654)", (45.395654, 11.879031)), # case-insensitive
        ("POLYGON((1 2, 3 4, 5 6))",  (None, None)),
        ("",                          (None, None)),
        (None,                        (None, None)),
        ("POINT(0 0)",                (None, None)),            # fuori Italia
        ("POINT(180 90)",             (None, None)),            # fuori Italia
        ("garbage",                   (None, None)),
    ]
    for wkt, expected in cases_wkt:
        got = parse_wkt_point(wkt)
        ok = got == expected
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] parse_wkt_point({wkt!r}) -> {got} (atteso {expected})")
        if not ok:
            failed += 1

    print()
    if failed:
        print(f"FAILED: {failed} test")
    else:
        print("All tests PASSED")
    return 0 if not failed else 1


# (Il blocco if __name__ e' alla fine del file, dopo lo Step 2)



# =========================================================================
# FASE 2 - SPARQL: query builders ArCo Beni Immobili
# =========================================================================
#
# Strategia (lezioni dal precedente lavoro su Cultural-ON):
# - HTTPS (HTTP bloccato da egress Aruba/AgID)
# - Niente header Accept (provoca 406)
# - Paginazione 2000/page (5000 va in timeout server)
# - Split in Q1A core (campi REQUIRED) + Q1B dettagli (OPTIONAL pesanti)
# - Stesso filtro REQUIRED su tutte le query per paginazione coerente
# =========================================================================

ARCO_IMMOBILE_CLASS = "https://w3id.org/arco/ontology/arco/ImmovableCulturalProperty"


def _query_arco_core(limit: int, offset: int) -> str:
    """Query CORE: nome + indirizzo (label da parsare) + tipologia (label risolta).

    Restituisce ?s ?name ?typeLabel ?addrLabel.

    Nota: hasCulturalPropertyType punta a una risorsa, NON a un literal.
    Per ottenere lo slug umano (chiesa, palazzo, ...) facciamo il join
    inline con rdfs:label della risorsa tipo. FILTER lang='it' per
    prendere solo la label italiana e non duplicare con quella inglese.

    REQUIRED: nome + indirizzo (label). Tipologia OPTIONAL: qualche bene
    non ha tipo assegnato (es. ICCD non ancora processato).

    Tempo atteso per LIMIT 2000: ~5-8 secondi (un OPTIONAL piccolo).
    """
    return f"""PREFIX arco: <https://w3id.org/arco/ontology/arco/>
PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX arco_deno: <https://w3id.org/arco/ontology/denotative-description/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?name ?typeLabel ?addrLabel WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s rdfs:label ?name .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  ?addr rdfs:label ?addrLabel .
  OPTIONAL {{
    ?s arco_deno:hasCulturalPropertyType ?type .
    ?type rdfs:label ?typeLabel .
    FILTER(lang(?typeLabel) = "it")
  }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_coord(limit: int, offset: int) -> str:
    """Query COORD: solo WKT serialization. Filtro REQUIRED address per
    paginazione coerente con CORE. 1 OPTIONAL solo -> no cartesiano.
    """
    return f"""PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX clvapit: <https://w3id.org/italia/onto/CLV/>
SELECT ?s ?wkt WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{
    ?s clvapit:hasGeometry ?geo .
    ?geo clvapit:serialization ?wkt .
  }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_descrizione(limit: int, offset: int) -> str:
    """Query DESCRIZIONE: solo dc:description."""
    return f"""PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
SELECT ?s ?descrizione WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{ ?s dc:description ?descrizione }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_image(limit: int, offset: int) -> str:
    """Query IMAGE: solo foaf:depiction."""
    return f"""PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT ?s ?image WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{ ?s foaf:depiction ?image }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_tutela(limit: int, offset: int) -> str:
    """Query TUTELA: solo MibacScopeOfProtection.label."""
    return f"""PREFIX arco: <https://w3id.org/arco/ontology/arco/>
PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?tutelaLabel WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{
    ?s arco:hasMibacScopeOfProtection ?tutela .
    ?tutela rdfs:label ?tutelaLabel .
  }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_soprintendenza(limit: int, offset: int) -> str:
    """Query SOPRINTENDENZA: solo HeritageProtectionAgency.label."""
    return f"""PREFIX arco: <https://w3id.org/arco/ontology/arco/>
PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?soprintendenzaLabel WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{
    ?s arco:hasHeritageProtectionAgency ?soprintendenza .
    ?soprintendenza rdfs:label ?soprintendenzaLabel .
  }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_cis_link(limit: int, offset: int) -> str:
    """Query LINK Cultural-ON: associazione bene immobile -> CulturalInstituteOrSite.

    Pochi beni immobili sono anche luoghi di Cultural-ON (es. Palazzo di Brera
    = bene immobile + Pinacoteca di Brera = CIS Cultural-ON). Questo link
    permette il merge per arricchire i beni "visitabili" con orari/contatti.
    """
    return f"""PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
SELECT ?s ?cis WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalInstituteOrSite ?cis .
}} LIMIT {limit} OFFSET {offset}"""


def fetch_arco_immobili(skip_cache: bool = False) -> list[dict]:
    """Scarica tutti i beni immobili ArCo in 2 passate (core + dettagli)
    + parsing indirizzo + parsing WKT.

    Cache: /tmp/cruscotto_cultural_on/arco_immobili.json.

    Ritorna list[dict] con chiavi per ogni bene:
      uri, denom, tipo_raw, addr_raw, sigla_provincia, comune_label,
      lat, lon, descrizione, image, tutela, soprintendenza, cis_link (URI o None)
    """
    cache_path = CACHE_DIR / "arco_immobili.json"
    today = datetime.now().strftime("%Y%m%d")
    cache_marker = CACHE_DIR / f"arco_immobili.{today}.ok"

    if not skip_cache and cache_path.exists() and cache_marker.exists():
        data = json.loads(cache_path.read_text())
        log.info("beni_culturali_cache_hit", path=str(cache_path), n=len(data))
        return data

    log.info("beni_culturali_fetch_start", endpoint=SPARQL_ENDPOINT,
             page_size=SPARQL_PAGE_SIZE)
    t0 = time.time()

    beni: dict[str, dict] = {}

    # --- FASE 1A: CORE ---
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        result = _sparql_post(_query_arco_core(SPARQL_PAGE_SIZE, offset))
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            log.info("beni_culturali_core_pagina_vuota", page=page_num,
                     offset=offset)
            break

        for b in bindings:
            s_uri = _bind_str(b, "s")
            if not s_uri:
                continue
            cur = beni.setdefault(s_uri, {
                "uri": s_uri,
                "denom": None,
                "tipo_raw": None,
                "addr_raw": None,
                "sigla_provincia": None,
                "comune_label": None,
                "lat": None,
                "lon": None,
                "descrizione": None,
                "image": None,
                "tutela": None,
                "soprintendenza": None,
                "cis_link": None,
            })
            if cur["denom"] is None:
                cur["denom"] = _clean_text(_bind_str(b, "name"))
            if cur["tipo_raw"] is None:
                # typeLabel arriva gia' come label italiana risolta
                # (es. "chiesa", "palazzo"). Vecchia versione leggeva ?type
                # come URI hash MD5 -> serviva risoluzione separata.
                t_label = _bind_str(b, "typeLabel")
                if t_label:
                    cur["tipo_raw"] = t_label.strip().lower()
            if cur["addr_raw"] is None:
                lab = _clean_text(_bind_str(b, "addrLabel"))
                if lab:
                    cur["addr_raw"] = lab
                    sigla, comune = parse_address_label(lab)
                    cur["sigla_provincia"] = sigla
                    cur["comune_label"] = comune

        log.info("beni_culturali_core_pagina", page=page_num, offset=offset,
                 bindings=len(bindings), beni_aggregati=len(beni))

        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    log.info("beni_culturali_core_done", n_beni=len(beni),
             secs=round(time.time() - t0, 1))

    # --- FASE 1B: DETTAGLI (5 query separate per evitare cartesiano O(N^5)) ---
    # La precedente query con 5 OPTIONAL su ?s generava da 3 a 10 righe per
    # ogni bene (combinazione di N geometry x M tutele x ...). Splittando in
    # query mono-OPTIONAL ogni pagina genera al massimo K righe per ?s, dove
    # K e' la cardinalita' di quel solo predicato.
    t1 = time.time()
    dettagli_specs = [
        ("coord",       _query_arco_coord,         "wkt",                "lat_lon"),
        ("descrizione", _query_arco_descrizione,   "descrizione",        "descrizione"),
        ("image",       _query_arco_image,         "image",              "image"),
        ("tutela",      _query_arco_tutela,        "tutelaLabel",        "tutela"),
        ("soprintendenza", _query_arco_soprintendenza, "soprintendenzaLabel", "soprintendenza"),
    ]
    for sub_name, query_fn, var_name, field_name in dettagli_specs:
        sub_t = time.time()
        offset = 0
        page_num = 0
        while True:
            page_num += 1
            result = _sparql_post(query_fn(SPARQL_PAGE_SIZE, offset))
            bindings = result.get("results", {}).get("bindings", [])
            if not bindings:
                break

            n_match = 0
            for b in bindings:
                s_uri = _bind_str(b, "s")
                if not s_uri:
                    continue
                cur = beni.get(s_uri)
                if cur is None:
                    continue
                n_match += 1
                raw = _bind_str(b, var_name)
                if raw is None:
                    continue
                if field_name == "lat_lon":
                    if cur["lat"] is None:
                        lat, lon = parse_wkt_point(raw)
                        if lat is not None and lon is not None:
                            cur["lat"] = lat
                            cur["lon"] = lon
                else:
                    if cur[field_name] is None:
                        cur[field_name] = _clean_text(raw)

            log.info("beni_culturali_dett_pagina", sub=sub_name,
                     page=page_num, offset=offset,
                     bindings=len(bindings), match_in_core=n_match)

            if len(bindings) < SPARQL_PAGE_SIZE:
                break
            offset += SPARQL_PAGE_SIZE
            time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

        log.info("beni_culturali_dett_sub_done", sub=sub_name,
                 pagine=page_num, secs=round(time.time() - sub_t, 1))

    log.info("beni_culturali_dett_done", secs_totali=round(time.time() - t1, 1))

    # --- FASE 1C: LINK Cultural-ON ---
    t2 = time.time()
    offset = 0
    page_num = 0
    n_links = 0
    while True:
        page_num += 1
        result = _sparql_post(_query_arco_cis_link(SPARQL_PAGE_SIZE, offset))
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            break
        for b in bindings:
            s_uri = _bind_str(b, "s")
            cis_uri = _bind_str(b, "cis")
            if s_uri and cis_uri:
                cur = beni.get(s_uri)
                if cur and cur["cis_link"] is None:
                    cur["cis_link"] = cis_uri
                    n_links += 1
        log.info("beni_culturali_link_pagina", page=page_num, offset=offset,
                 bindings=len(bindings))
        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    log.info("beni_culturali_link_done", n_links=n_links,
             secs=round(time.time() - t2, 1))

    result = list(beni.values())
    elapsed = time.time() - t0
    log.info("beni_culturali_fetch_done",
             n_beni=len(result),
             n_con_geo=sum(1 for x in result if x["lat"] and x["lon"]),
             n_con_comune=sum(1 for x in result if x["comune_label"]),
             n_con_cis_link=sum(1 for x in result if x["cis_link"]),
             secs_totali=round(elapsed, 1))

    cache_path.write_text(json.dumps(result, ensure_ascii=False))
    cache_marker.touch()
    return result


# =========================================================================
# FASE 3 - Categoria normalizzata (whitelist)
# =========================================================================
#
# Lo slug ArCo CulturalPropertyType ha decine di valori granulari
# (chiesa, palazzo, torre, fortificazione, villa, abbazia, ...). Mappiamo
# in 8 macro-categorie per KPI mix_categoria e icone mappa.
#
# Verra' raffinata sul dataset reale: al primo run sul server vedremo
# i top 30-50 slug ArCo effettivi e completiamo la whitelist.
# =========================================================================

CATEGORIA_ARCO: dict[str, str] = {
    # chiese e edifici di culto
    "chiesa":                    "chiesa",
    "cattedrale":                "chiesa",
    "basilica":                  "chiesa",
    "santuario":                 "chiesa",
    "oratorio":                  "chiesa",
    "abbazia":                   "chiesa",
    "monastero":                 "chiesa",
    "convento":                  "chiesa",
    "battistero":                "chiesa",
    "cappella":                  "chiesa",
    "edificio-di-culto":         "chiesa",

    # palazzi e ville
    "palazzo":                   "palazzo",
    "palazzo-nobiliare":         "palazzo",
    "palazzo-vescovile":         "palazzo",
    "villa":                     "palazzo",
    "casa":                      "palazzo",
    "casa-nobiliare":            "palazzo",
    "casa-padronale":            "palazzo",

    # fortificazioni
    "castello":                  "castello",
    "fortezza":                  "castello",
    "rocca":                     "castello",
    "torre":                     "castello",
    "mura":                      "castello",
    "bastione":                  "castello",
    "fortino":                   "castello",
    "fortificazione":            "castello",

    # archeologia
    "area-archeologica":         "archeologia",
    "sito-archeologico":         "archeologia",
    "necropoli":                 "archeologia",
    "tomba":                     "archeologia",
    "domus":                     "archeologia",
    "anfiteatro":                "archeologia",
    "teatro-romano":             "archeologia",
    "tempio":                    "archeologia",
    "terme":                     "archeologia",

    # monumenti e infrastrutture
    "monumento":                 "monumento",
    "fontana":                   "monumento",
    "obelisco":                  "monumento",
    "ponte":                     "infrastruttura",
    "acquedotto":                "infrastruttura",
    "mulino":                    "infrastruttura",

    # parchi e giardini
    "parco":                     "parco_giardino",
    "giardino":                  "parco_giardino",
    "parco-storico":             "parco_giardino",
    "giardino-storico":          "parco_giardino",

    # musei e istituzioni (sovrapposizione con Cultural-ON)
    "museo":                     "museo",
    "biblioteca":                "museo",
    "archivio":                  "museo",
    "galleria":                  "museo",
    "pinacoteca":                "museo",
}

CATEGORIA_PRIORITA_BENI = [
    "chiesa", "palazzo", "castello", "archeologia",
    "museo", "monumento", "infrastruttura", "parco_giardino", "altro",
]


def _categoria_for(tipo_raw: str | None) -> str:
    """Mappa lo slug ArCo grezzo alla categoria normalizzata.

    Strategia: lookup esatto sulla whitelist; se assente, marca 'altro'.
    Il primo run sul dataset reale ci permettera' di estendere la
    whitelist con gli slug ArCo non coperti (vedremo i top 20-50
    'altro' nei log post-ETL).
    """
    if not tipo_raw:
        return "altro"
    return CATEGORIA_ARCO.get(tipo_raw.lower(), "altro")


# =========================================================================
# FASE 4 - Load lookups + mapping a ISTAT
# =========================================================================

def load_lookups() -> tuple[dict[tuple[str, str], str], set[str]]:
    """Carica gazetteer comuni e costruisce mapping (sigla, COMUNE_NORM) -> istat6.

    Usa la sigla provincia (campo 'provincia' del bundle, 2 char) PIU' il
    nome del comune normalizzato come chiave. Risolve naturalmente i 7
    comuni omonimi italiani (Castro BG/LE, Calliano AT/TN, ...).

    Ritorna:
      - (sigla, COMUNE_NORM_UPPER) -> istat6
      - canonical_istat: set di tutti gli ISTAT validi (per shardatura
        anche dei comuni vuoti)
    """
    log.info("beni_culturali_lookups_loading")
    comuni = local_lookup.load_comuni_bundle()
    if comuni is None:
        raise SystemExit(
            "Local lookup 'comuni-bundle.json' assente in "
            f"{local_lookup.get_lookup_dir()}. "
            "Esegui prima 'python -m etl.sources.anagrafica'."
        )

    pair_to_istat: dict[tuple[str, str], str] = {}
    canonical_istat: set[str] = set()
    for istat, c in comuni.items():
        canonical_istat.add(istat)
        sigla = (c.get("provincia") or "").strip().upper()
        denom = c.get("denominazione", "")
        if sigla and denom:
            key = (sigla, normalize(denom))
            pair_to_istat[key] = istat

    log.info("beni_culturali_lookups_loaded",
             n_comuni=len(canonical_istat),
             n_pairs=len(pair_to_istat))
    return pair_to_istat, canonical_istat


# =========================================================================
# FASE 5 - Group by ISTAT
# =========================================================================

def group_by_istat(beni: list[dict],
                   pair_to_istat: dict[tuple[str, str], str]
                   ) -> tuple[dict[str, list[dict]], int]:
    """Raggruppa beni ArCo per ISTAT del comune.

    Match key: (sigla_provincia, normalize(comune_label)) -> istat6.
    Ritorna (dict istat -> list beni, n_unmatched).
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    unmatched: dict[tuple[str, str], int] = defaultdict(int)
    n_unmatched = 0

    for b in beni:
        sigla = b.get("sigla_provincia")
        comune = b.get("comune_label")
        if not sigla or not comune:
            n_unmatched += 1
            continue
        key = (sigla, normalize(comune))
        istat = pair_to_istat.get(key)
        if not istat:
            unmatched[key] += 1
            n_unmatched += 1
            continue
        grouped[istat].append(b)

    if unmatched:
        top = sorted(unmatched.items(), key=lambda x: -x[1])[:20]
        log.warning("beni_culturali_unmatched",
                    n_distinct=len(unmatched),
                    n_records=n_unmatched,
                    top=[(f"{s}/{c}", n) for (s, c), n in top])

    return dict(grouped), n_unmatched


# =========================================================================
# FASE 6 - Build KPI per comune
# =========================================================================

def build_kpi(beni: list[dict]) -> dict:
    """KPI aggregati per un singolo comune."""
    n = len(beni)
    if n == 0:
        return {
            "n_totale": 0,
            "mix_categoria": {},
            "pct_georef": 0.0,
            "pct_con_foto": 0.0,
            "pct_con_descrizione": 0.0,
            "pct_con_tutela": 0.0,
            "n_con_cis_link": 0,
        }

    mix: dict[str, int] = defaultdict(int)
    n_geo = n_foto = n_desc = n_tut = n_cis = 0
    for b in beni:
        cat = _categoria_for(b.get("tipo_raw"))
        mix[cat] += 1
        if b.get("lat") is not None and b.get("lon") is not None:
            n_geo += 1
        if b.get("image"):
            n_foto += 1
        if b.get("descrizione"):
            n_desc += 1
        if b.get("tutela"):
            n_tut += 1
        if b.get("cis_link"):
            n_cis += 1

    # ordina mix per priorità categoria (non per count)
    mix_sorted = {
        c: mix[c] for c in CATEGORIA_PRIORITA_BENI if c in mix
    }

    return {
        "n_totale": n,
        "mix_categoria": mix_sorted,
        "pct_georef": round(100.0 * n_geo / n, 1),
        "pct_con_foto": round(100.0 * n_foto / n, 1),
        "pct_con_descrizione": round(100.0 * n_desc / n, 1),
        "pct_con_tutela": round(100.0 * n_tut / n, 1),
        "n_con_cis_link": n_cis,
    }


# =========================================================================
# FASE 7 - Build shard BASE + FULL
# =========================================================================

def _slim_bene_base(b: dict) -> dict:
    """Versione compatta per shard base (senza descrizione, tutela, sopr.)."""
    return {
        "id": b["uri"].rsplit("/", 1)[-1],
        "denom": b.get("denom"),
        "categoria": _categoria_for(b.get("tipo_raw")),
        "lat": b.get("lat"),
        "lon": b.get("lon"),
        "indirizzo": b.get("addr_raw"),
        "image": b.get("image"),
        # cis_link presente solo se valorizzato (per UI: link Cultural-ON arricchimento)
        "cis_link": b.get("cis_link"),
    }


def _slim_bene_full(b: dict) -> dict:
    """Versione completa per shard full (tutti i campi)."""
    return {
        "id": b["uri"].rsplit("/", 1)[-1],
        "denom": b.get("denom"),
        "categoria": _categoria_for(b.get("tipo_raw")),
        "tipo_raw": b.get("tipo_raw"),
        "lat": b.get("lat"),
        "lon": b.get("lon"),
        "indirizzo": b.get("addr_raw"),
        "descrizione": b.get("descrizione"),
        "image": b.get("image"),
        "tutela": b.get("tutela"),
        "soprintendenza": b.get("soprintendenza"),
        "cis_link": b.get("cis_link"),
    }


def _sort_beni(beni: list[dict]) -> list[dict]:
    """Ordina i beni per priorita' di rilevanza UX:
      1. con foto > senza
      2. con descrizione > senza
      3. denominazione alfabetica
    """
    return sorted(
        beni,
        key=lambda b: (
            0 if b.get("image") else 1,
            0 if b.get("descrizione") else 1,
            (b.get("denom") or "").upper(),
        ),
    )


def build_shard_base(istat: str,
                     beni: list[dict],
                     snapshot_date: str,
                     generated_at: str) -> dict:
    """Shard riassunto: KPI + lista compatta (cap LUOGHI_CAP_BASE)."""
    kpi = build_kpi(beni)
    sorted_beni = _sort_beni(beni)
    truncated = len(sorted_beni) > LUOGHI_CAP_BASE
    selected = sorted_beni[:LUOGHI_CAP_BASE]

    shard = {
        "_etl_version": ETL_VERSION,
        "_source": SOURCE_LABEL,
        "_source_url": SPARQL_ENDPOINT,
        "_snapshot_date": snapshot_date,
        "_generated_at": generated_at,
        "kpi": kpi,
        "luoghi": [_slim_bene_base(b) for b in selected],
    }
    if truncated:
        shard["_luoghi_truncated"] = True
        shard["_luoghi_total"] = len(beni)
        shard["_luoghi_cap"] = LUOGHI_CAP_BASE
        shard["_full_shard_available"] = True
    return shard


def build_shard_full(istat: str,
                     beni: list[dict],
                     snapshot_date: str,
                     generated_at: str) -> dict:
    """Shard completo: tutti i beni con tutti i campi."""
    kpi = build_kpi(beni)
    sorted_beni = _sort_beni(beni)
    return {
        "_etl_version": ETL_VERSION,
        "_source": SOURCE_LABEL + " (full)",
        "_source_url": SPARQL_ENDPOINT,
        "_snapshot_date": snapshot_date,
        "_generated_at": generated_at,
        "_full": True,
        "kpi": kpi,
        "luoghi": [_slim_bene_full(b) for b in sorted_beni],
    }


def build_all_shards(grouped: dict[str, list[dict]],
                     canonical_istat: set[str],
                     snapshot_date: str,
                     out_dir: Path,
                     full_out_dir: Path | None = None) -> tuple[int, int, int]:
    """Scrive uno shard JSON per ogni ISTAT canonico.

    Comuni con 0 beni: shard con luoghi=[] per consistenza UX (la tab
    frontend mostra "Nessun bene censito in questo comune" invece di
    fallire con 404).

    Ritorna (n_base_written, n_full_written, n_empty).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if full_out_dir is not None:
        full_out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    n_base, n_full, n_empty = 0, 0, 0
    for istat in sorted(canonical_istat):
        beni = grouped.get(istat, [])
        if not beni:
            n_empty += 1

        # Shard base sempre
        shard_base = build_shard_base(istat, beni, snapshot_date, generated_at)
        (out_dir / f"{istat}.json").write_text(
            json.dumps(shard_base, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n_base += 1

        # Shard full solo se: full_out_dir attivo + comune ha piu' di LUOGHI_CAP_BASE beni
        if full_out_dir is not None and len(beni) > LUOGHI_CAP_BASE:
            shard_full = build_shard_full(istat, beni, snapshot_date, generated_at)
            (full_out_dir / f"{istat}.json").write_text(
                json.dumps(shard_full, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            n_full += 1

        if n_base % 1000 == 0:
            log.info("beni_culturali_shards_progress",
                     base_written=n_base, full_written=n_full,
                     total=len(canonical_istat))

    log.info("beni_culturali_shards_done",
             base_written=n_base, full_written=n_full,
             empty=n_empty)
    return n_base, n_full, n_empty


# =========================================================================
# FASE 8 - Main CLI
# =========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="ETL Beni Culturali (ArCo Immobili tutelati MiC)",
    )
    ap.add_argument("--target", choices=["local"], default="local",
                    help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    ap.add_argument("--outdir",
                    default="/var/www/cruscotto-italia/data/beni_culturali",
                    help="Directory shard base locali")
    ap.add_argument("--full-shards", action="store_true",
                    help="Genera anche shard FULL (beni_culturali_full/) "
                         "per i comuni con piu' di LUOGHI_CAP_BASE beni.")
    ap.add_argument("--full-outdir",
                    default="/var/www/cruscotto-italia/data/beni_culturali_full",
                    help="Directory shard FULL locali")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="Riusa cache /tmp/cruscotto_cultural_on/arco_immobili.json")
    args = ap.parse_args()

    log.info("beni_culturali_etl_start")
    t_start = time.time()

    # FASE 1-2: fetch SPARQL (con cache)
    beni = fetch_arco_immobili(skip_cache=False)
    if not beni:
        log.error("beni_culturali_no_beni_fetched")
        return 1

    # snapshot date: oggi (dataset ArCo non espone Last-Modified utile)
    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    log.info("beni_culturali_snapshot_date", date=snapshot_date)

    # FASE 4: lookups (sigla, comune) -> istat
    pair_to_istat, canonical_istat = load_lookups()

    # FASE 5: group by istat
    grouped, n_unmatched = group_by_istat(beni, pair_to_istat)
    pct_unmatched = round(100.0 * n_unmatched / len(beni), 3) if beni else 0
    log.info("beni_culturali_grouped",
             records=len(beni),
             comuni_distinti=len(grouped),
             unmatched=n_unmatched,
             pct_unmatched=pct_unmatched)
    if pct_unmatched > 5.0:
        log.warning("beni_culturali_unmatched_high",
                    pct=pct_unmatched,
                    note="estendi mapping (sigla, comune) o controlla parser indirizzo")

    # FASE 7: build shards (base + opzionale full)
    out_dir = Path(args.outdir)
    full_out_dir = Path(args.full_outdir) if args.full_shards else None
    n_base, n_full, n_empty = build_all_shards(
        grouped, canonical_istat, snapshot_date, out_dir, full_out_dir
    )

    # Manifest update best-effort
    try:
        files = [{"name": f.name,
                  "size": f.stat().st_size,
                  "key": f"beni_culturali/{f.name}"}
                 for f in sorted(out_dir.glob("*.json"))]
        manifest.update_source("beni_culturali", files, status="ok")
        log.info("beni_culturali_manifest_updated", n_files=len(files))
    except Exception as e:
        log.warning("beni_culturali_manifest_update_skipped", err=str(e))

    elapsed = time.time() - t_start
    log.info("beni_culturali_etl_done",
             elapsed_s=round(elapsed, 1),
             base_shards=n_base,
             full_shards=n_full,
             empty_shards=n_empty,
             snapshot=snapshot_date)
    return 0


# Sovrascrive l'__main__ del test runner (era prima di FASE 2) per
# supportare sia --test-helpers che il full ETL
if __name__ == "__main__":
    import sys as _sys
    if "--test-helpers" in _sys.argv:
        _sys.exit(_test_arco_helpers())
    _sys.exit(main())
