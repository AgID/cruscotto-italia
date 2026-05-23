"""ETL Cultural-ON / DBUnico 2.0 - Luoghi della Cultura statali e non statali.

Fonte istituzionale: Ministero della Cultura (MiC) - Direzione Generale Organizzazione.
Pubblicazione: dataset catalogato su dati.gov.it (dataset id f43f148b-01fb-406d-8337-b92f8dbb6543).
Ontologia: Cultural-ON (Cultural ONtology) sviluppata da ISTC-CNR, 2016.
Banca dati: DBUnico 2.0 - 59.472 luoghi (musei, biblioteche, archivi, aree archeologiche,
            monumenti, ville, chiese e altri istituti).
Aggiornamento: quotidiano (verificato 23/05/2026).
Licenza: CC BY 4.0 (https://w3id.org/italia/controlled-vocabulary/licences/A21_CCBY40).

URL ufficiale dump nazionale (JSON-LD):
  https://dati.beniculturali.it/dataset/dataset-luoghi.json (~88 MB)

Struttura JSON-LD:
  {
    "@graph": [
      {"@type": "cis:CulturalInstituteOrSite", "@id": ".../CulturalInstituteOrSite/N",
       "cis:institutionalCISName": {"@language": "it", "@value": "..."},
       "dc:type": "..." (literal o lista di literal, whitelist a 2 colonne),
       "cis:hasSite": {"@id": ".../Site/Sede_di_N"},
       "geo:lat": float, "geo:long": float,
       "foaf:depiction": {"@id": "..."},
       "l0:identifier": "DBUnico.N",
       "l0:description": {"@language": "it", "@value": "..."},
       "smapit:hasOnlineContactPoint": {"@id": "..."},
       "accessCondition:hasAccessCondition": [{"@id": "..."}],
       ...},
      {"@type": "cis:Site", "@id": ".../Site/Sede_di_N",
       "cis:siteAddress": {"@id": ".../Address/..."},
       ...},
      {"@type": "clvapit:Address", "@id": ".../Address/...",
       "clvapit:fullAddress": "...",
       "clvapit:postCode": "...",
       "clvapit:hasCity": {"@id": ".../City/Nome"},
       "clvapit:hasProvince": {"@id": ".../Province/Sigla"},
       ...},
      {"@type": "smapit:OnlineContactPoint", ...},
      {"@type": "smapit:Email", "smapit:emailAddress": "..."},
      {"@type": "smapit:Telephone", "smapit:telephoneNumber": "..."},
      {"@type": "smapit:WebSite", "smapit:URL": "..."},
      ...
    ],
    "@context": {...}
  }

Schema output shard cultural_on/<istat>.json (riassunto):
{
  "_etl_version": "0.1.0",
  "_source": "MiC DBUnico 2.0 (Cultural-ON)",
  "_source_url": "https://dati.beniculturali.it/dataset/dataset-luoghi.json",
  "_snapshot_date": "YYYY-MM-DD",
  "_generated_at": "ISO-8601",
  "kpi": {
    "n_totale": 12,
    "mix_categoria": {"museo": 7, "biblioteca": 2, "archivio": 1, "monumento": 2},
    "mix_ente": {"mic": 3, "comune": 5, "fondazione": 4},
    "n_statali": 3,
    "n_non_statali": 9,
    "pct_georef": 91.7
  },
  "luoghi": [   # cap LUOGHI_CAP_BASE=30; lista compatta
    {"id": "DBUnico.106788", "denom": "Pinacoteca di Brera",
     "categorie": ["museo"], "enti": ["mic"], "is_statale": true,
     "lat": 45.471943, "lon": 9.187683,
     "indirizzo": "Via Brera, 28", "cap": "20121",
     "image": "https://..."}
  ]
}

Schema output shard cultural_on_full/<istat>.json (solo se n_luoghi > LUOGHI_CAP_BASE):
  Stesso schema base + campi per ogni luogo:
    descrizione, telefono, email, website, prenotazione, biglietti_url, orari.

Comuni vuoti (n_totale=0): si genera comunque shard con luoghi=[] per
consistenza UX (la sezione non sara' 'null' nel dashboard).

Pattern di esecuzione:
  1) download_dump() -> /tmp/cruscotto_cultural_on/dataset-luoghi-YYYYMMDD.json
     skip se cache locale del giorno esiste e --skip-download
  2) parse_dump() -> stream JSON-LD, restituisce list[dict] di luoghi normalizzati
  3) load_lookups() -> nome_to_istat, canonical_istat
  4) group_by_istat() -> dict[istat] = list[luogo]
  5) build_shards() -> 7896 file cultural_on/<istat>.json + opzionali cultural_on_full/
  6) write_local() -> persist su DATA_DIR/cultural_on/ (+ DATA_DIR/cultural_on_full/)

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

DUMP_URL = "https://dati.beniculturali.it/dataset/dataset-luoghi.json"
DUMP_URL_DOC = "https://www.dati.gov.it/view-dataset/dataset?id=f43f148b-01fb-406d-8337-b92f8dbb6543"

# Persistenza URL:
# - Lo slug 'dataset-luoghi.json' e' stabile, dichiarato dal MiC nel campo
#   "URI" della scheda dati.gov.it. NON e' un UUID intermediato (l'UUID
#   nella URL dati.gov.it si riferisce al record CKAN AgID, che e' un
#   aggregatore: e' quello che puo' cambiare se AgID rigenera il catalogo).
# - L'URL del dump fisico sul server IIS del MiC e' sotto controllo MiC
#   ed e' allineato per regione: dataset-luoghi<RegioneCamelCase>.json.
# - In caso il dump nazionale fosse temporaneamente indisponibile, il
#   fallback e' iterare sulle 20 distribuzioni regionali, di cui qui sotto
#   tengo solo i nomi delle regioni (slug exact dell'archivio MiC):
DUMP_URL_REGIONI_FALLBACK = [
    "Abruzzo", "Basilicata", "Calabria", "Campania", "EmiliaRomagna",
    "FriuliVeneziaGiulia", "Lazio", "Liguria", "Lombardia", "Marche",
    "Molise", "Piemonte", "Puglia", "Sardegna", "Sicilia", "Toscana",
    "TrentinoAltoAdige", "Umbria", "ValleAosta", "Veneto",
]

CACHE_DIR = Path("/tmp/cruscotto_cultural_on")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ETL_VERSION = "0.1.0"
SOURCE_LABEL = "MiC DBUnico 2.0 (Cultural-ON)"

# Cap luoghi[] nello shard base (riassunto). Comuni con > LUOGHI_CAP_BASE
# generano anche shard FULL su cultural_on_full/<istat>.json con TUTTI i
# luoghi e tutti i campi (descrizioni, contatti, orari, prenotazioni).
# Roma stimati ~800 luoghi, Firenze ~300, Milano ~250: 30 cattura il
# riassunto utile in tab senza appesantire il payload base.
LUOGHI_CAP_BASE = 30


# =========================================================================
# Whitelist dc:type (2 colonne)
#
# Cultural-ON usa dc:type come literal (es. "Museo, Galleria e/o raccolta")
# mescolando 2 dimensioni: tipologia LUOGO + tipologia ENTE TITOLARE.
# 27 valori distinti verificati sull'endpoint SPARQL 23/05/2026.
#
# Un singolo CulturalInstituteOrSite puo' avere PIU' dc:type (es. un museo
# statale puo' essere insieme "Museo, Galleria e/o raccolta" + "MiC").
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

# Ordine di priorita' per la categoria primaria (per icona mappa, etc.)
CATEGORIA_PRIORITA = [
    "museo", "biblioteca", "archivio", "area_archeologica",
    "monumento", "architettura", "parco_giardino", "chiesa", "altro",
]

# Tipologia ENTE TITOLARE (governance) -> chiave breve + flag is_statale
ENTE_TITOLARE: dict[str, dict] = {
    "MiC":                                                                          {"key": "mic",                       "statale": True},
    "Amministrazione dello Stato":                                                  {"key": "amministrazione_stato",     "statale": True},
    "Istituto Centrale":                                                            {"key": "istituto_centrale",         "statale": True},
    "Istituto dotato di autonomia speciale":                                        {"key": "istituto_autonomia",        "statale": True},
    "Istituto dotato di autonomia speciale, di rilevante interesse nazionale":      {"key": "istituto_autonomia",        "statale": True},
    "Soprintendenza Archeologia, Belle Arti e Paesaggio":                           {"key": "soprintendenza",            "statale": True},
    "Soprintendenza Archivistica e Bibliografica":                                  {"key": "soprintendenza",            "statale": True},
    "Regione":                                                                      {"key": "regione",                   "statale": False},
    "Comune":                                                                       {"key": "comune",                    "statale": False},
    "Fondazione":                                                                   {"key": "fondazione",                "statale": False},
}


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


def _extract_lang_value(node, lang: str = "it") -> str | None:
    """Estrae valore literal da nodo JSON-LD considerando @language e @value.

    Supporta:
      - string diretto:                "foo"
      - dict singolo:                  {"@language": "it", "@value": "foo"}
      - dict senza @language:          {"@value": "foo"}
      - lista di dict:                 [{"@language": "it", "@value": "foo"},
                                        {"@language": "en", "@value": "bar"}]
    Preferisce @language=lang. Ritorna None se non trova.
    """
    if node is None:
        return None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        # singolo nodo literal
        if "@value" in node:
            if node.get("@language") in (lang, None):
                return node["@value"]
            return node["@value"]
        return None
    if isinstance(node, list):
        # preferisci lang richiesto
        for it in node:
            if isinstance(it, dict) and it.get("@language") == lang:
                return it.get("@value")
        # fallback: primo @value disponibile
        for it in node:
            if isinstance(it, dict) and "@value" in it:
                return it["@value"]
            if isinstance(it, str):
                return it
        return None
    return None


def _extract_id_ref(node) -> str | None:
    """Estrae @id da un reference JSON-LD ({@id: '...'})."""
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get("@id")
    if isinstance(node, str):
        return node
    return None


def _extract_id_refs(node) -> list[str]:
    """Come _extract_id_ref ma per liste."""
    if node is None:
        return []
    if isinstance(node, list):
        return [x for x in (_extract_id_ref(it) for it in node) if x]
    single = _extract_id_ref(node)
    return [single] if single else []


def _as_list(node) -> list:
    """Normalizza node a lista (dc:type puo' essere literal o lista)."""
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


# =========================================================================
# FASE 1 - Download dump nazionale (88 MB JSON-LD)
# =========================================================================

USER_AGENT = (
    "Mozilla/5.0 (compatible; CruscottoItalia/1.0; "
    "+https://cruscotto-italia.dati.gov.it/) cultural_on-etl"
)


def download_dump(force: bool = False) -> tuple[Path, str]:
    """Scarica il dump nazionale luoghi della cultura.

    Strategia cache:
      - file cache: /tmp/cruscotto_cultural_on/dataset-luoghi.json
      - meta cache: /tmp/cruscotto_cultural_on/dataset-luoghi.last_modified
                    (contiene il valore Last-Modified ritornato dal server IIS)
      - HEAD request iniziale per leggere Last-Modified
      - se file presente AND Last-Modified server == quello salvato -> riusa cache
      - altrimenti GET completo (~88 MB)

    Ritorna (path_file, snapshot_date_iso). snapshot_date_iso e' la data
    da Last-Modified header (YYYY-MM-DD), usata per _snapshot_date dello shard.
    """
    out = CACHE_DIR / "dataset-luoghi.json"
    meta = CACHE_DIR / "dataset-luoghi.last_modified"

    if not force:
        # Probe HEAD per Last-Modified
        try:
            r = requests.head(DUMP_URL, headers={"User-Agent": USER_AGENT},
                              timeout=30, allow_redirects=True)
            r.raise_for_status()
            server_lm = r.headers.get("Last-Modified", "")
            log.info("cultural_on_head_ok", last_modified=server_lm,
                     content_length=r.headers.get("Content-Length"))
        except requests.RequestException as e:
            log.warning("cultural_on_head_failed", err=str(e))
            server_lm = ""

        # Hit di cache se file presente e Last-Modified invariato
        if (server_lm and out.exists() and meta.exists()
                and meta.read_text().strip() == server_lm
                and out.stat().st_size > 10_000_000):
            log.info("cultural_on_cache_hit", path=str(out),
                     size=out.stat().st_size, last_modified=server_lm)
            snapshot_date = _parse_lm_to_iso(server_lm)
            return out, snapshot_date

    # Download completo
    log.info("cultural_on_download_start", url=DUMP_URL)
    t0 = time.time()
    r = requests.get(DUMP_URL, headers={"User-Agent": USER_AGENT},
                     timeout=600, stream=True)
    r.raise_for_status()

    server_lm = r.headers.get("Last-Modified", "")
    total = int(r.headers.get("Content-Length", "0"))
    written = 0
    tmp_out = out.with_suffix(".json.tmp")
    with open(tmp_out, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
                written += len(chunk)
                if written % (10 * 1024 * 1024) < 262144:
                    pct = (written * 100 / total) if total else 0
                    log.info("cultural_on_download_progress",
                             mb=written // (1024 * 1024),
                             pct=round(pct, 1))
    tmp_out.replace(out)
    if server_lm:
        meta.write_text(server_lm)

    elapsed = time.time() - t0
    log.info("cultural_on_download_ok", path=str(out),
             size=out.stat().st_size, secs=round(elapsed, 1),
             last_modified=server_lm)
    snapshot_date = _parse_lm_to_iso(server_lm)
    return out, snapshot_date


def _parse_lm_to_iso(last_modified: str) -> str:
    """Converte HTTP Last-Modified -> YYYY-MM-DD.

    Esempio header: 'Mon, 04 May 2026 00:01:57 GMT' -> '2026-05-04'.
    Fallback: data odierna in UTC.
    """
    if not last_modified:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(last_modified)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# =========================================================================
# FASE 2 - Parse JSON-LD + build index per @id
# =========================================================================

def _is_type(node: dict, target_type: str) -> bool:
    """True se @type del nodo contiene target_type (string o lista)."""
    t = node.get("@type")
    if not t:
        return False
    if isinstance(t, str):
        return target_type in t
    if isinstance(t, list):
        return any(target_type in x for x in t if isinstance(x, str))
    return False


def parse_dump(dump_path: Path) -> tuple[list[dict], dict[str, dict]]:
    """Parsa il JSON-LD e ritorna (cis_nodes, index_by_id).

    - cis_nodes: lista di nodi con @type cis:CulturalInstituteOrSite
    - index_by_id: tutti i nodi del @graph indicizzati per @id (per join
      Site -> Address -> City/Province e ContactPoint -> Email/Phone/Web)
    """
    log.info("cultural_on_parse_start", path=str(dump_path),
             size=dump_path.stat().st_size)
    t0 = time.time()
    with open(dump_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        raise RuntimeError(
            f"Dump JSON-LD inatteso: @graph non e' lista (tipo={type(graph).__name__})"
        )

    index: dict[str, dict] = {}
    cis: list[dict] = []
    type_counter: Counter[str] = Counter()
    for node in graph:
        nid = node.get("@id")
        if nid:
            index[nid] = node
        # conta tipi (debug)
        t = node.get("@type")
        if isinstance(t, str):
            type_counter[t] += 1
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    type_counter[x] += 1
        if _is_type(node, "CulturalInstituteOrSite"):
            cis.append(node)

    elapsed = time.time() - t0
    log.info("cultural_on_parse_ok",
             n_nodes=len(graph),
             n_cis=len(cis),
             secs=round(elapsed, 1),
             top_types=type_counter.most_common(8))
    return cis, index


# =========================================================================
# FASE 3 - Estrazione luogo (join via index)
# =========================================================================

def extract_luogo(cis_node: dict, index: dict[str, dict]) -> dict | None:
    """Risolve un CulturalInstituteOrSite navigando l'index per @id.

    Ritorna un dict normalizzato con tutti i campi utili oppure None se
    manca l'informazione minima (comune + nome).
    """
    # Identificativo DBUnico
    identifier = cis_node.get("l0:identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("@value")

    # Denominazione: institutionalCISName preferito, fallback rdfs:label
    denom = _extract_lang_value(cis_node.get("cis:institutionalCISName"), "it")
    if not denom:
        denom = _extract_lang_value(cis_node.get("rdfs:label"), "it")
    if not denom:
        return None

    # Descrizione
    descrizione = _extract_lang_value(cis_node.get("l0:description"), "it") or ""

    # dc:type (puo' essere literal singolo o lista) -> 2 colonne whitelist
    raw_types = _as_list(cis_node.get("dc:type"))
    categorie: list[str] = []
    enti: list[str] = []
    is_statale = False
    is_non_statale = False
    for rt in raw_types:
        # alcuni dc:type sono literal-only, non dict
        val = rt if isinstance(rt, str) else _extract_lang_value(rt, "it")
        if not val:
            continue
        if val in CATEGORIA_LUOGO:
            cat = CATEGORIA_LUOGO[val]
            if cat not in categorie:
                categorie.append(cat)
        if val in ENTE_TITOLARE:
            ent = ENTE_TITOLARE[val]["key"]
            if ent not in enti:
                enti.append(ent)
            if ENTE_TITOLARE[val]["statale"]:
                is_statale = True
            else:
                is_non_statale = True

    # Coordinate dirette sul CIS
    lat = cis_node.get("geo:lat")
    lon = cis_node.get("geo:long")
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None

    # Foto preview (foaf:depiction)
    image = _extract_id_ref(cis_node.get("foaf:depiction"))

    # Join hasSite -> Site -> Address -> City/Province
    site_id = _extract_id_ref(cis_node.get("cis:hasSite"))
    site = index.get(site_id) if site_id else None

    indirizzo = cap = comune = provincia = None
    if site:
        addr_id = _extract_id_ref(site.get("cis:siteAddress"))
        addr = index.get(addr_id) if addr_id else None
        if addr:
            indirizzo = _extract_lang_value(addr.get("clvapit:fullAddress"), "it")
            cap_raw = addr.get("clvapit:postCode")
            cap = cap_raw if isinstance(cap_raw, str) else _extract_lang_value(cap_raw, "it")

            city_id = _extract_id_ref(addr.get("clvapit:hasCity"))
            city = index.get(city_id) if city_id else None
            if city:
                comune = _extract_lang_value(city.get("rdfs:label"), "it")

            prov_id = _extract_id_ref(addr.get("clvapit:hasProvince"))
            prov = index.get(prov_id) if prov_id else None
            if prov:
                provincia = _extract_lang_value(prov.get("rdfs:label"), "it")

    if not comune:
        # senza comune non possiamo mappare a ISTAT -> scarto
        return None

    # Contatti via OnlineContactPoint
    telefono = email = website = None
    cp_ids = _extract_id_refs(cis_node.get("smapit:hasOnlineContactPoint"))
    for cp_id in cp_ids:
        cp = index.get(cp_id)
        if not cp:
            continue
        # telefono
        for tel_id in _extract_id_refs(cp.get("smapit:hasTelephone")):
            tel = index.get(tel_id)
            if tel and not telefono:
                tn = tel.get("smapit:telephoneNumber")
                telefono = tn if isinstance(tn, str) else _extract_lang_value(tn, "it")
        # email
        for em_id in _extract_id_refs(cp.get("smapit:hasEmail")):
            em = index.get(em_id)
            if em and not email:
                ea = em.get("smapit:emailAddress")
                email = ea if isinstance(ea, str) else _extract_lang_value(ea, "it")
        # website
        for ws_id in _extract_id_refs(cp.get("smapit:hasWebSite")):
            ws = index.get(ws_id)
            if ws and not website:
                u = ws.get("smapit:URL")
                website = u if isinstance(u, str) else _extract_id_ref(u) or _extract_lang_value(u, "it")

    # Prenotazione (Booking)
    prenotazione = None
    for ac_id in _extract_id_refs(cis_node.get("accessCondition:hasAccessCondition")):
        ac = index.get(ac_id)
        if ac and _is_type(ac, "Booking"):
            lab = _extract_lang_value(ac.get("rdfs:label"), "it")
            if lab and lab.lower() not in ("none", "null"):
                prenotazione = lab
                break

    return {
        "id": identifier,
        "denom": denom,
        "descrizione": descrizione,
        "categorie": categorie,
        "enti": enti,
        "is_statale": is_statale,
        "is_non_statale": is_non_statale,
        "lat": lat,
        "lon": lon,
        "indirizzo": indirizzo,
        "cap": cap,
        "comune_label": comune,
        "provincia_label": provincia,
        "telefono": telefono,
        "email": email,
        "website": website,
        "prenotazione": prenotazione,
        "image": image,
    }
