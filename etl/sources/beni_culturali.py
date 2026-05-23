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


# Quick CLI: python3 -m etl.sources.beni_culturali --test-helpers
if __name__ == "__main__":
    import sys
    if "--test-helpers" in sys.argv:
        sys.exit(_test_arco_helpers())



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
    """Query CORE: nome + indirizzo (label da parsare) + tipo.

    Restituisce ?s ?name ?type ?addrLabel
    REQUIRED tutti: serve indirizzo per estrarre sigla+comune.
    Senza queste 3 informazioni un record non e' utilizzabile per Cruscotto.

    Tempo atteso per LIMIT 2000: ~3-5 secondi.
    """
    return f"""PREFIX arco: <https://w3id.org/arco/ontology/arco/>
PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX arco_deno: <https://w3id.org/arco/ontology/denotative-description/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?name ?type ?addrLabel WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s rdfs:label ?name .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  ?addr rdfs:label ?addrLabel .
  OPTIONAL {{ ?s arco_deno:hasCulturalPropertyType ?type }}
}} LIMIT {limit} OFFSET {offset}"""


def _query_arco_dettagli(limit: int, offset: int) -> str:
    """Query DETTAGLI: coordinate WKT + descrizione + foto + tutela + soprintendenza.

    REQUIRED: stesso filtro CORE (hasCulturalPropertyAddress) per
    paginazione coerente.

    Restituisce ?s ?wkt ?descrizione ?image ?tutela ?soprintendenza
    """
    return f"""PREFIX arco: <https://w3id.org/arco/ontology/arco/>
PREFIX arco_loc: <https://w3id.org/arco/ontology/location/>
PREFIX clvapit: <https://w3id.org/italia/onto/CLV/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?wkt ?descrizione ?image ?tutelaLabel ?soprintendenzaLabel WHERE {{
  ?s a <{ARCO_IMMOBILE_CLASS}> .
  ?s arco_loc:hasCulturalPropertyAddress ?addr .
  OPTIONAL {{
    ?s clvapit:hasGeometry ?geo .
    ?geo clvapit:serialization ?wkt .
  }}
  OPTIONAL {{ ?s dc:description ?descrizione }}
  OPTIONAL {{ ?s foaf:depiction ?image }}
  OPTIONAL {{
    ?s arco:hasMibacScopeOfProtection ?tutela .
    ?tutela rdfs:label ?tutelaLabel .
  }}
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
                t_uri = _bind_str(b, "type")
                if t_uri:
                    # estrai slug finale (es. .../CulturalPropertyType/chiesa -> chiesa)
                    cur["tipo_raw"] = t_uri.rsplit("/", 1)[-1]
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

    # --- FASE 1B: DETTAGLI ---
    t1 = time.time()
    offset = 0
    page_num = 0
    while True:
        page_num += 1
        result = _sparql_post(_query_arco_dettagli(SPARQL_PAGE_SIZE, offset))
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            log.info("beni_culturali_dett_pagina_vuota", page=page_num,
                     offset=offset)
            break

        n_match = 0
        for b in bindings:
            s_uri = _bind_str(b, "s")
            if not s_uri:
                continue
            cur = beni.get(s_uri)
            if cur is None:
                continue  # bene senza addr label (skippato in CORE)
            n_match += 1
            if cur["lat"] is None:
                wkt = _bind_str(b, "wkt")
                if wkt:
                    lat, lon = parse_wkt_point(wkt)
                    cur["lat"] = lat
                    cur["lon"] = lon
            if cur["descrizione"] is None:
                cur["descrizione"] = _clean_text(_bind_str(b, "descrizione"))
            if cur["image"] is None:
                cur["image"] = _bind_str(b, "image")
            if cur["tutela"] is None:
                cur["tutela"] = _clean_text(_bind_str(b, "tutelaLabel"))
            if cur["soprintendenza"] is None:
                cur["soprintendenza"] = _clean_text(_bind_str(b, "soprintendenzaLabel"))

        log.info("beni_culturali_dett_pagina", page=page_num, offset=offset,
                 bindings=len(bindings), match_in_core=n_match)

        if len(bindings) < SPARQL_PAGE_SIZE:
            break
        offset += SPARQL_PAGE_SIZE
        time.sleep(SPARQL_SLEEP_BETWEEN_PAGES)

    log.info("beni_culturali_dett_done", secs=round(time.time() - t1, 1))

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
