"""Motore deterministico per intenti strutturati. Nessun LLM qui dentro:
prende un intento gia' estratto e lo esegue, o lo rifiuta se illegale."""
import json, os, re, re, unicodedata
from cens_vars import match_cens, CENS as _CENS_FULL, CATEGORIE_DISPONIBILI

# ---------- GRAMMATICA: sezione -> operazioni ammesse + campi validi ----------
GRAMMAR = {
    "carburanti": {
        "operazioni": {"ordina", "elenca", "conta", "cerca_nome", "prezzo_medio"},
        "campi": {"benzina_self", "benzina_serv", "gasolio_self", "gasolio_serv", "hvo", "gpl", "metano"},
        "campo_default": "benzina_self",
    },
    "beni_culturali": {
        "operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": {"chiesa", "palazzo", "castello", "archeologia", "museo", "monumento", "archivio", "biblioteca", "infrastruttura", "parco_giardino", "altro"},
        "campo_default": None,
    },
    "redditi": {
        "operazioni": {"serie_storica", "valore"},
        "campi": {"reddito_medio", "reddito_totale", "contribuenti", "imposta_netta_media",
                  "pensione", "dipendente", "autonomo", "fabbricati"},
        "campo_default": "reddito_medio",
    },
    "scuole": {"operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": {"infanzia", "primaria", "sec1", "sec2", "altro"}, "campo_default": None},
    "terzo_settore": {"dir": "runts", "operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": {"APS", "EF", "ETS", "IS", "ODV"}, "campo_default": None},
    "ricarica_ev": {"dir": "pun", "operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": set(), "campo_default": None},
    "immobili_pa": {"operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": {"altro", "fabbricati_amministrativi_uffici", "fabbricati_magazzini", "fabbricati_pertinenze",
                  "fabbricati_produttivi", "fabbricati_residenziali", "fabbricati_sociali_culturali",
                  "fabbricati_sociali_sanitari", "fabbricati_sociali_scolastici", "fabbricati_sociali_sportivi",
                  "terreni_agricoli", "terreni_urbani"}, "campo_default": None},
    "civici": {"dir": "anncsu", "operazioni": {"conta", "cerca_civico", "sezione_censimento", "particella"},
        "campi": {"civici", "strade"}, "campo_default": "civici"},
    "opere": {"operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": set(), "campo_default": None},
    "pnrr": {"operazioni": {"conta", "elenca", "cerca_nome", "somma"},
        "campi": set(), "campo_default": None},
    "farmacie": {"dir": "sanita_mds", "operazioni": {"conta", "elenca", "cerca_nome"},
        "campi": {"farmacia", "parafarmacia", "ospedale"}, "campo_default": "farmacia"},
    "aria": {"operazioni": {"valore", "serie_storica", "cerca_nome", "elenca"},
        "campi": {"pm10", "pm25", "no2"}, "campo_default": "pm10"},
    "veicoli": {"operazioni": {"valore", "serie_storica"},
        "campi": {"parco", "iscrizioni"}, "campo_default": None},
    "incidenti": {"dir": "veicoli", "operazioni": {"valore", "serie_storica"},
        "campi": set(), "campo_default": None},
    "demografia_dettaglio": {"dir": "demografia", "operazioni": {"valore"},
        "campi": set(), "campo_default": None},
    "imprese": {"dir": "asia", "operazioni": {"valore", "serie_storica"},
        "campi": {"unita_locali", "addetti"}, "campo_default": "unita_locali"},
    "turismo": {"operazioni": {"valore"}, "campi": set(), "campo_default": None},
    "pendolarismo": {"operazioni": {"valore", "elenca"}, "campi": set(), "campo_default": None},
    "siope": {"operazioni": {"valore", "cerca_voce", "elenca"}, "campi": set(), "campo_default": None},
    "anac": {"operazioni": {"valore", "elenca"}, "campi": set(), "campo_default": None},
    "banda_larga": {"dir": "agcom_bbmap", "operazioni": {"valore"}, "campi": set(), "campo_default": None},
    "territorio": {"operazioni": {"valore"},
        "campi": {"rifiuti", "rischio_idrogeologico", "suolo"}, "campo_default": None},
    "sismica": {"dir": "classificazione_sismica", "operazioni": {"valore"},
        "campi": set(), "campo_default": None},
    "profilo": {"operazioni": {"valore"},
        "campi": {"cittadinanza", "famiglie", "istruzione", "lavoro", "mobilita"}, "campo_default": None},
    "anagrafica": {"operazioni": {"valore"}, "campi": set(), "campo_default": None},
    "censimento": {"operazioni": {"valore", "ranking_sezioni"}, "campi": {"stranieri","stranieri_ue","stranieri_extra_ue","popolazione","maschi","femmine","occupati","famiglie","abitazioni","abitazioni_occupate","abitazioni_vuote","laureati","diplomati","pop_9plus"}, "campo_default": None},
}

def _load_dash_section(istat, key):
    """Carica la sezione da filesystem: shard per-fonte, fallback shard dashboard unificato."""
    dirn = GRAMMAR.get(key, {}).get("dir", key)
    path = f"/var/www/cruscotto-italia/data/{dirn}/{istat}.json"
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        p2 = f"/var/www/cruscotto-italia/data/dashboard/{istat}.json"
        if os.path.exists(p2):
            with open(p2) as f:
                return json.load(f).get(dirn)
    except Exception:
        pass
    return None

_SINONIMI_CAMPO = {
    "carburanti": {"diesel": "gasolio_self", "gasolio": "gasolio_self", "benzina": "benzina_self"},
}
def valida_intento(intento):
    """Ritorna (True, None) se legale, (False, motivo) altrimenti."""
    sez = intento.get("sezione")
    syn = _SINONIMI_CAMPO.get(sez, {})
    if intento.get("campo") in syn:
        intento["campo"] = syn[intento["campo"]]
    if sez not in GRAMMAR:
        return False, f"sezione '{sez}' non riconosciuta"
    g = GRAMMAR[sez]
    op = intento.get("operazione")
    if op not in g["operazioni"]:
        return False, f"operazione '{op}' non ammessa per {sez} (ammesse: {g['operazioni']})"
    campo = intento.get("campo")
    if campo and campo not in g["campi"]:
        return False, f"campo '{campo}' non valido per {sez} (validi: {g['campi']})"
    if op == "ordina" and not campo:
        return False, f"operazione 'ordina' richiede un campo (validi: {g['campi']})"
    if op == "cerca_nome" and not str(intento.get("nome") or "").strip():
        return False, "operazione 'cerca_nome' richiede il parametro 'nome'"
    if op == "cerca_voce" and not str(intento.get("nome") or "").strip():
        return False, "operazione 'cerca_voce' richiede il parametro 'nome'"
    if op == "cerca_civico" and not str(intento.get("odonimo") or "").strip():
        return False, "operazione 'cerca_civico' richiede il parametro 'odonimo'"
    if op in ("sezione_censimento", "particella") and (
            not str(intento.get("odonimo") or "").strip() or not str(intento.get("civico") or "").strip()):
        return False, f"operazione '{op}' richiede i parametri 'odonimo' e 'civico'"
    if not intento.get("istat"):
        return False, "comune non risolto a codice ISTAT"
    return True, None

_CARB_BENCH = None
def _carb_benchmark(istat):
    global _CARB_BENCH
    if _CARB_BENCH is None:
        try:
            _CARB_BENCH = json.load(open("/var/www/cruscotto-italia/data/carburanti/_nazionale.json"))
        except Exception:
            _CARB_BENCH = {}
    naz = _CARB_BENCH.get("nazionale") or {}
    regdict = _CARB_BENCH.get("regionale") or {}
    reg = None
    if istat:
        try:
            anag = _load_dash_section(istat, "anagrafica") or {}
            rnome = anag.get("regione")
            if rnome:
                low = {k.lower(): (k, v) for k, v in regdict.items()}
                hit = low.get(str(rnome).lower())
                if hit:
                    reg = (hit[0], hit[1])
        except Exception:
            reg = None
    return naz, reg

def esegui_carburanti(sez_data, intento):
    punti = sez_data.get("punti", [])
    op = intento["operazione"]
    if op == "conta":
        campo = intento.get("campo")
        if campo:
            n = len([p for p in punti if (p.get("prezzi") or {}).get(campo) is not None])
            return {"n_distributori": n, "carburante": campo, "n_totale_impianti": len(punti)}
        return {"n_distributori": len(punti)}
    if op == "cerca_nome":
        toks = [t for t in (intento.get("nome") or "").lower().split() if t]
        def _m(p):
            blob = " ".join(str(p.get(k) or "") for k in ("brand", "indirizzo", "name")).lower()
            return bool(toks) and all(t in blob for t in toks)
        hit = [_clean_rec(p) for p in punti if _m(p)]
        return {"operazione": "cerca_nome", "termine": intento.get("nome"),
                "n_risultati": len(hit), "risultati": hit[:20]}
    if op == "prezzo_medio":
        _kpi = sez_data.get("kpi", {}) or {}
        pm = _kpi.get("prezzo_medio", {}) or {}
        pmin = _kpi.get("prezzo_min", {}) or {}
        naz, reg = _carb_benchmark(intento.get("istat"))
        c = intento.get("campo")
        if c:
            if pm.get(c) is None:
                return {"errore": f"prezzo medio per '{c}' non disponibile", "campi_disponibili": sorted(pm.keys())}
            r = {"campo": c, "prezzo_medio": pm.get(c), "unita": "EUR/litro"}
            if pmin.get(c) is not None:
                r["prezzo_min_comune"] = pmin.get(c)
            if naz.get(c) is not None:
                r["media_italia"] = naz.get(c)
            if reg and reg[1].get(c) is not None:
                r["media_regione"] = {"regione": reg[0], "valore": reg[1].get(c)}
            return r
        out = {"operazione": "prezzo_medio", "prezzi_medi": pm, "prezzi_min": pmin, "unita": "EUR/litro"}
        if naz:
            out["media_italia"] = naz
        if reg:
            out["media_regione"] = {"regione": reg[0], "prezzi": reg[1]}
        return out
    campo = intento.get("campo") or GRAMMAR["carburanti"]["campo_default"]
    direzione = intento.get("direzione", "asc")
    off = max(int(intento.get("offset") or 0), 0)
    limite = min(int(intento.get("limite") or 10), 50)
    validi = [p for p in punti if p.get("prezzi", {}).get(campo)]
    validi.sort(key=lambda p: p["prezzi"][campo], reverse=(direzione == "desc"))
    page = validi[off:off + limite]
    risultati = [{
        "nome": p.get("name"), "brand": p.get("brand"),
        "indirizzo": p.get("indirizzo"), "lat": p.get("lat"), "lon": p.get("lon"),
        "prezzo": p["prezzi"][campo], "campo": campo,
    } for p in page]
    _LABEL_CARB = {"benzina_self": "benzina (self-service)", "benzina_serv": "benzina (servito)",
                   "gasolio_self": "gasolio/diesel (self-service)", "gasolio_serv": "gasolio/diesel (servito)",
                   "hvo": "HVO"}
    out = {"operazione": op, "carburante": _LABEL_CARB.get(campo, campo),
           "ordine": "dal piu' economico" if direzione == "asc" else "dal piu' caro",
           "n_mostrati": len(risultati), "risultati": risultati}
    if len(validi) > 1:  # nel singolo "piu' economico" il totale e' rumore
        out["n_totale_con_prezzo"] = len(validi)
    if off or off + len(page) < len(validi):
        out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(validi))
    resto = len(validi) - (off + len(page))
    if resto > 0:
        out["_altri_disponibili"] = "ci sono altri %d distributori: chiedi 'altri' per i successivi" % resto
    return out

# dispatcher sezione -> esecutore
ESECUTORI = {"carburanti": esegui_carburanti}

# ---- anno di riferimento del dato (per fonte + tempo verbale) ----
_FONTE_LIVE = {"carburanti", "civici", "beni_culturali", "terzo_settore", "ricarica_ev",
               "banda_larga", "anagrafica", "anac", "opere", "pnrr", "scuole", "sismica"}
_ANNO_KEYS = ("_anno_rilevazione", "anno_rilevazione", "_latest_year", "_anno_dati",
              "_anno_dati_parco", "rd_ultimo_anno", "ultimo_anno", "anno", "anno_fiscale")
def _af_int(v):
    import re
    if isinstance(v, bool): return None
    if isinstance(v, dict): v = v.get("anno")
    if isinstance(v, int) and 1900 <= v <= 2100: return v
    if isinstance(v, float) and 1900 <= v <= 2100: return int(v)
    if isinstance(v, str):
        a = [int(x) for x in re.findall(r"(?:19|20)\d{2}", v)]
        return max(a) if a else None
    return None
def _af_walk(o, d=0):
    out = []
    if d > 4: return out
    if isinstance(o, dict):
        for k, v in o.items():
            if k in _ANNO_KEYS:
                a = _af_int(v)
                if a: out.append(a)
            if isinstance(v, (dict, list)): out += _af_walk(v, d + 1)
    elif isinstance(o, list):
        for it in o[:3]: out += _af_walk(it, d + 1)
    return out
def _estrai_anno_fonte(sezione, sez_data, dati, intento):
    """Anno di riferimento del dato citato. None = fonte corrente/live (verbo al presente)."""
    if (intento or {}).get("operazione") == "serie_storica": return None
    if sezione in _FONTE_LIVE: return None
    if sezione in ("redditi", "siope"): return _af_int((dati or {}).get("anno"))
    if sezione == "profilo":
        c = (intento or {}).get("campo")
        if c and isinstance((dati or {}).get(c), dict): return _af_int(dati[c].get("anno"))
        anni = _af_walk(dati or {}); return max(anni) if anni else None
    if sezione in ("farmacie", "sanita"):
        if (intento or {}).get("campo") == "ospedale":
            f = ((sez_data or {}).get("_fonti") or {}).get("ospedali") or {}
            return _af_int(f.get("anno_dati")) or _af_int(((sez_data or {}).get("ospedali") or {}).get("anno_dati"))
        return None
    anni = _af_walk(dati or {})
    if not anni: anni = _af_walk(sez_data or {})
    return max(anni) if anni else None


def esegui(sez_data, intento):
    dati = ESECUTORI[intento["sezione"]](sez_data, intento)
    if isinstance(dati, dict) and "_anno_fonte" not in dati:
        try:
            _a = _estrai_anno_fonte(intento.get("sezione"), sez_data, dati, intento)
            if _a:
                dati["_anno_fonte"] = _a
        except Exception:
            pass
    return dati


# ---------- BENI CULTURALI ----------
_beni_full_cache = {}
def _load_beni_full(istat):
    if istat in _beni_full_cache: return _beni_full_cache[istat]
    luoghi = []
    path = f"/var/www/cruscotto-italia/data/beni_culturali_full/{istat}.json"
    try:
        if os.path.exists(path):
            with open(path) as f:
                luoghi = json.load(f).get("luoghi", [])
    except Exception:
        luoghi = []
    if not luoghi:
        # solo ~813/7896 comuni hanno il _full (arricchimento con descrizioni): per
        # tutti gli altri usa i luoghi dello shard base (stessa struttura, con foto
        # via campo image, senza 'descrizione'/'soprintendenza'). Allinea il chatbot
        # al frontend comune.html, che legge sempre il base.
        base_path = f"/var/www/cruscotto-italia/data/beni_culturali/{istat}.json"
        try:
            if os.path.exists(base_path):
                with open(base_path) as f:
                    luoghi = json.load(f).get("luoghi", [])
        except Exception:
            luoghi = []
    if len(_beni_full_cache) >= 3: _beni_full_cache.pop(next(iter(_beni_full_cache)))
    _beni_full_cache[istat] = luoghi
    return luoghi

_PERIODO_RE = re.compile(r"(secol|\ba\.?\s*c\b|\bd\.?\s*c\b|periodizz|\bet[aà]\b|\b1[0-9]{3}\b|[0-9]{4}|\b[ivxlcdm]{2,}\b)", re.I)
def _tipo_da_denom(denom):
    # Deriva il sotto-tipo dalla denom quando manca tipo_raw (shard base): tipo tra parentesi
    # finali "Nome (tipo, qual)"; se le parentesi sono un periodo -> primo segmento "tipo, ..., Nome";
    # denom minimale -> coincide col tipo. Normalizza trattino/slash in spazio.
    s = (denom or "").strip()
    if not s:
        return ""
    m = re.search(r"\(([^()]*)\)\s*$", s)
    if m:
        inner = m.group(1).strip()
        before = s[:m.start()].strip().rstrip(",").strip()
        cand = before.split(",")[0] if (_PERIODO_RE.search(inner) and before) else inner.split(",")[0]
    else:
        cand = s.split(",")[0]
    cand = cand.strip().lower().replace("-", " ").replace("/", " ")
    return " ".join(cand.split())
def _tipo_di(b):
    # tipo_raw se presente (shard full), altrimenti derivato dalla denom (shard base)
    t = (b.get("tipo_raw") or "").strip().lower()
    return t if t else _tipo_da_denom(b.get("denom") or "")
def esegui_beni_culturali(sez_data, intento):
    kpi = sez_data.get("kpi", {})
    op, campo = intento["operazione"], intento.get("campo")
    if op == "conta":
        if campo:
            return {"categoria": campo, "n": kpi.get("mix_categoria", {}).get(campo, 0)}
        return {"n_totale": kpi.get("n_totale", 0), "per_categoria": kpi.get("mix_categoria", {})}
    if op == "elenca":
        if not campo:  # senza categoria: elenco dei primi N (tutte le categorie)
            luoghi = _load_beni_full(intento["istat"])
            off = max(int(intento.get("offset") or 0), 0)
            lim = min(int(intento.get("limite") or 10), 50)
            page = luoghi[off:off + lim]
            elenco = [{"nome": b.get("denom"), "categoria": b.get("categoria"),
                       "indirizzo": b.get("indirizzo")} for b in page]
            cats = sorted([k for k, v in (kpi.get("mix_categoria") or {}).items() if v and k != "altro"])
            out = {"categoria": None, "n_totale": len(luoghi), "elenco": elenco}
            if off or off + len(page) < len(luoghi):
                out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(luoghi))
            resto = len(luoghi) - (off + len(page))
            if resto > 0:
                out["_altri_disponibili"] = "ci sono altri %d beni: chiedi 'altri' per i successivi" % resto
            if cats:
                out["categorie_per_filtrare"] = cats
            return out
        sel = [b for b in _load_beni_full(intento["istat"]) if b.get("categoria") == campo]
        off = max(int(intento.get("offset") or 0), 0)
        lim = min(int(intento.get("limite") or 10), 50)
        page = sel[off:off + lim]
        elenco = [{"nome": b.get("denom"), "indirizzo": b.get("indirizzo"),
                   "lat": b.get("lat"), "lon": b.get("lon")} for b in page]
        out = {"categoria": campo, "n_totale": len(sel), "elenco": elenco}
        if off or off + len(page) < len(sel):
            out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(sel))
        resto = len(sel) - (off + len(page))
        if resto > 0:
            out["_altri_disponibili"] = "ci sono altri %d beni: chiedi 'altri' per i successivi" % resto
        return out
    if op == "cerca_nome":
        nome = str(intento["nome"]); nl = nome.lower()
        parole = [w for w in nl.split() if len(w) >= 4]
        def _mw(p, denom):  # match parola con stemming leggero (acquedotto~acquedotti, torre~torri)
            return p in denom or (len(p) >= 5 and p[:-1] in denom)
        def _foto(b):
            u = (b.get("image") or "").strip()
            if not u or u == "None" or not u.lower().startswith("http") or u.lower().endswith(".pdf"):
                return None
            if u.startswith("http://") and ".beniculturali.it" in u.lower():
                u = "https://" + u[7:]
            return u
        def _scheda(b):
            sopr = b.get("soprintendenza")
            out = {"denominazione": b.get("denom"), "categoria": b.get("categoria"),
                    "indirizzo": b.get("indirizzo"), "lat": b.get("lat"), "lon": b.get("lon"),
                    "soprintendenza": sopr if sopr not in (None, "None") else None,
                    "descrizione": (b.get("descrizione") or "")[:1500],
                    "foto": _foto(b)}
            # contatti opzionali (record Cultural-ON): inclusi solo se valorizzati
            for _src, _dst in (("telefono", "telefono"), ("email", "email"), ("website", "sito_web")):
                _v = b.get(_src)
                if _v and str(_v).strip() not in ("", "None"):
                    out[_dst] = str(_v).strip()
            return out
        def _ricchezza(b):
            return (1 if (b.get("descrizione") or "").strip() else 0) + (1 if _foto(b) else 0)
        luoghi = _load_beni_full(intento["istat"])
        # filtro per SOTTO-TIPO (tipo_raw): cappella/convento/monastero/torre/... sono VALORI
        # di tipo_raw (vocabolario pulito ~38), non nomi. Se il termine cercato e' un tipo_raw
        # del comune -> elenco COMPLETO filtrato per tipo_raw, MAI match testuale sulla denom
        # (evita falsi positivi tipo 'Torrini' per 'torre'). Allinea il chatbot al Cruscotto.
        _tipi = {t for t in (_tipo_di(b) for b in luoghi) if t}
        def _sing(s): return s[:-1] if len(s) >= 5 else s   # torre~torri, cappella~cappelle
        _tm = next((t for t in _tipi if nl == t or _sing(nl) == _sing(t)), None)
        if _tm:
            sel = [b for b in luoghi if _tipo_di(b) == _tm]
            _q = (intento.get("_q") or "").lower()
            if re.search(r"quant|numero", _q) and not re.search(r"qual|elenc|lista|mostr|dammi|vedere|fammi", _q):
                return {"sotto_tipo": _tm, "n": len(sel)}
            off = max(int(intento.get("offset") or 0), 0)
            lim = min(int(intento.get("limite") or 50), 50)
            page = sel[off:off + lim]
            elenco = [{"nome": b.get("denom"), "categoria": b.get("categoria"),
                       "indirizzo": b.get("indirizzo"), "lat": b.get("lat"), "lon": b.get("lon")} for b in page]
            out = {"sotto_tipo": _tm, "n_totale": len(sel), "elenco": elenco}
            if off or off + len(page) < len(sel):
                out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(sel))
            resto = len(sel) - (off + len(page))
            if resto > 0:
                out["_altri_disponibili"] = "ci sono altri %d: chiedi 'altri' per i successivi" % resto
            return out
        # passata 1: match pieno (frase intera o tutte le parole) -> raccoglie tutti i candidati
        cand = []
        for b in luoghi:
            denom = (b.get("denom") or "").lower()
            if nl in denom or (parole and all(_mw(p, denom) for p in parole)):
                cand.append(b)
        # se nessun candidato ha descrizione, passata "parola rara": recupera record affini ricchi
        # (stesso luogo, denominazione diversa: es. Madonna dell Idris vs Santa Maria in Idris)
        if parole and not any((b.get("descrizione") or "").strip() for b in cand):
            freq = {p: sum(1 for b in luoghi if _mw(p, (b.get("denom") or "").lower())) for p in parole}
            rara = min(parole, key=lambda p: freq.get(p, 0))
            ids = {b.get("id") for b in cand}
            for b in luoghi:
                if b.get("id") in ids: continue
                if _mw(rara, (b.get("denom") or "").lower()):
                    cand.append(b); ids.add(b.get("id"))
        if cand:
            cand.sort(key=_ricchezza, reverse=True)
            ris = [_scheda(b) for b in cand[:5]]
            return {"trovato": True, "nome_cercato": nome, "n_trovati": len(ris), "risultati": ris}
        # passata 2 (fallback): una parola alla volta, dalla piu' lunga (piu' specifica)
        ris = []
        for p in sorted(parole, key=len, reverse=True):
            sub = [b for b in luoghi if _mw(p, (b.get("denom") or "").lower())]
            if sub:
                sub.sort(key=_ricchezza, reverse=True)
                ris = [_scheda(b) for b in sub[:5]]
                break
        return {"trovato": bool(ris), "nome_cercato": nome, "n_trovati": len(ris), "match_parziale": bool(ris), "risultati": ris,
                "_nota": "Nessun bene corrisponde esattamente al nome cercato; i risultati corrispondono solo in parte." if ris else None}
    return {"errore": f"operazione {op} non implementata per beni_culturali"}

ESECUTORI["beni_culturali"] = esegui_beni_culturali


# ---------- REDDITI (MEF) ----------
_CAMPI_REDDITI = {
    "reddito_medio": lambda a: a.get("reddito_complessivo", {}).get("medio"),
    "reddito_totale": lambda a: a.get("reddito_complessivo", {}).get("tot"),
    "contribuenti": lambda a: a.get("contribuenti"),
    "imposta_netta_media": lambda a: a.get("imposta_netta", {}).get("medio"),
}
def esegui_redditi(sez_data, intento):
    campo = intento.get("campo") or GRAMMAR["redditi"]["campo_default"]
    anni = sez_data.get("anni", {})
    disp = sorted(sez_data.get("anni_disponibili", []))
    if not disp:
        return {"errore": "nessun anno disponibile"}
    _TIPOL = {"pensione": "reddito da pensione", "dipendente": "reddito da lavoro dipendente",
              "autonomo": "reddito da lavoro autonomo", "fabbricati": "reddito da fabbricati"}
    if campo in _TIPOL:
        def _bloc(y): return ((anni.get(str(y), {}) or {}).get("tipologie", {}) or {}).get(campo) or {}
        _nota = "percettori = contribuenti che dichiarano questo tipo di reddito (dato fiscale MEF), NON il numero anagrafico di persone; una persona puo' avere piu' tipi di reddito."
        if intento["operazione"] == "serie_storica":
            return {"tipologia": _TIPOL[campo],
                    "percettori_per_anno": {str(y): _bloc(y).get("freq") for y in disp},
                    "reddito_medio_per_anno": {str(y): _bloc(y).get("medio") for y in disp},
                    "anni_disponibili": disp, "fonte": sez_data.get("fonte"), "_nota": _nota}
        anno = intento.get("anno")
        try: anno = int(anno) if anno is not None else None
        except Exception: anno = None
        if anno is not None and anno not in disp:
            return {"errore": f"anno {anno} non disponibile", "anni_disponibili": disp}
        if anno is None: anno = disp[-1]
        b = _bloc(anno)
        return {"tipologia": _TIPOL[campo], "anno": anno, "percettori": b.get("freq"),
                "reddito_medio_eur": b.get("medio"), "reddito_totale_eur": b.get("tot"),
                "anni_disponibili": disp, "fonte": sez_data.get("fonte"), "_nota": _nota}
    f = _CAMPI_REDDITI[campo]
    if intento["operazione"] == "serie_storica":
        return {"campo": campo, "serie": {str(y): f(anni.get(str(y), {})) for y in disp},
                "anni_disponibili": disp, "fonte": sez_data.get("fonte")}
    # valore puntuale: anno richiesto o ultimo disponibile
    anno = intento.get("anno")
    try:
        anno = int(anno) if anno is not None else None
    except Exception:
        anno = None
    if anno is not None and anno not in disp:
        return {"errore": f"anno {anno} non disponibile", "anni_disponibili": disp}
    if anno is None:
        anno = disp[-1]
    return {"campo": campo, "anno": anno, "valore": f(anni.get(str(anno), {})),
            "anni_disponibili": disp, "fonte": sez_data.get("fonte")}

ESECUTORI["redditi"] = esegui_redditi


# ---------- ESECUTORE GENERICO LISTE (Lotto A) ----------
def _path(d, path, default=None):
    for k in path:
        if not isinstance(d, dict): return default
        d = d.get(k)
    return d if d is not None else default

LISTE_CONF = {
    "scuole":        {"lista": ["scuole"],   "nome": "denominazione", "cat_item": "macro_ordine",
                      "mix": ["kpi", "per_ordine"],    "tot": ["kpi", "n_scuole"],
                      "item": ["denominazione", "indirizzo", "macro_ordine", "tipologia"]},
    "terzo_settore": {"lista": ["enti"],     "nome": "denom",         "cat_item": "sez",
                      "mix": ["kpi", "mix_sezione"],   "tot": ["kpi", "n_totale"],
                      "item": ["denom", "sez", "data_iscr"]},
    "ricarica_ev":   {"lista": ["punti"],    "nome": None,            "cat_item": None,
                      "mix": ["kpi", "mix_potenza"],   "tot": ["kpi", "n_totale"],
                      "item": ["indirizzo", "potenza_categoria", "stato"]},
    "immobili_pa":   {"lista": ["punti"],    "nome": None,            "cat_item": "cat",
                      "mix": ["kpi", "mix_categoria"], "tot": ["kpi", "n_totale"],
                      "item": ["cat", "tipo", "sup", "vincolo", "uso_terzi"]},
    "opere":         {"lista": ["progetti"], "nome": "descrizione",   "cat_item": None,
                      "mix": None,                     "tot": ["n_progetti"],
                      "item": ["descrizione", "stato", "settore", "costo_prev"]},
    "pnrr":          {"lista": ["progetti"], "nome": "titolo",        "cat_item": None,
                      "mix": None,                     "tot": ["kpi", "n_progetti"],
                      "item": ["titolo", "missione_descrizione", "stato_avanzamento", "finanziamento_totale"]},
}

_TECH_KEYS = {"md5", "etag", "hash", "shard", "_resource_id", "sede_scolastica"}
def _clean_rec(x):
    return {k: v for k, v in x.items() if not (k.startswith("_") or k in _TECH_KEYS)}
def _cum2delta(mensili):
    out = {}; prev = 0.0
    for m in sorted(mensili or {}):
        try: v = float(mensili[m])
        except Exception: v = 0.0
        out[m] = round(max(0.0, v - prev), 2); prev = v
    return out
_DATEFINE_KEYS = ("data_fine", "data_fine_prevista", "data_fine_effettiva")
def _enrich_rec(x, totale=None, imp_keys=None):
    r = _clean_rec(x)
    if isinstance(r.get("mensili"), dict):
        r["mensili_delta"] = _cum2delta(r["mensili"]); r.pop("mensili", None); r.pop("mensili", None)
    for k in _DATEFINE_KEYS:
        if isinstance(r.get(k), str) and r[k].startswith("9999"):
            r[k] = "In corso"
    if totale and totale > 0 and imp_keys:
        for ck in imp_keys:
            v = x.get(ck)
            if v not in (None, ""):
                try: r["pct_sul_totale"] = round(float(v) / totale * 100, 2); break
                except Exception: pass
    return r

_PCT_TRIGGER = ("percentual", "quota", "incid", " peso", "%", "che parte", "su quanti", "su quante")

def _norm_q(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"[\/,;.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _quota_categorie(mix, tot, domanda):
    """% on-demand deterministica: somma delle categorie del mix nominate nella domanda
    diviso n_totale. Nessun ripiego: None se manca il trigger %, se nessuna categoria
    e' riconosciuta o se il totale e' nullo. Generico per ogni sezione a mix."""
    if not (isinstance(tot, (int, float)) and tot):
        return None
    if not isinstance(mix, dict) or not mix:
        return None
    dl = _norm_q(domanda)
    if not any(t in dl for t in _PCT_TRIGGER):
        return None
    work = " " + dl + " "
    trovate = []
    for k in sorted(mix.keys(), key=lambda x: -len(str(x))):
        kn = _norm_q(k)
        if kn and re.search(r"\b" + re.escape(kn) + r"\b", work):
            trovate.append(k)
            work = re.sub(r"\b" + re.escape(kn) + r"\b", " ", work)
    if not trovate:
        return None
    trovate.sort(key=lambda k: dl.find(_norm_q(k)))
    num = sum(v for k, v in mix.items() if k in trovate and isinstance(v, (int, float)))
    return {"categorie": trovate, "numeratore": num, "n_totale": tot,
            "pct": round(num / tot * 100, 1)}

def esegui_lista(sezione, sez_data, intento):
    conf = LISTE_CONF[sezione]
    op, campo = intento["operazione"], intento.get("campo")
    if sezione == "opere":
        _qf = str(intento.get("_q") or "").lower()
        if re.search(r"font[ei].{0,12}finanz|mix.{0,6}finanz|per fonte|finanziament[oi].{0,12}per|tipo.{0,8}finanz|chi finanzi", _qf):
            FONTI = [("finanz_statali", "Statali"), ("finanz_europei", "Europei"), ("finanz_enti_terr", "Enti territoriali"), ("finanz_privati", "Privati"), ("finanz_altri", "Altri")]
            _bk = _load_dash_section(intento.get("istat"), "bdap_kpi") or {}
            _tot = _bk.get("totale") or {}
            voci = [{"fonte": lbl, "importo": round(_tot.get(fk) or 0)} for fk, lbl in FONTI]
            voci.sort(key=lambda v: v["importo"], reverse=True)
            return {"n_totale": len(voci), "elenco": voci, "_forza_elenco": True,
                    "_cfg_override": ["elenco", "Fonti di finanziamento opere", [["fonte"], ["importo"]]]}
    if sezione == "immobili_pa":
        _qi = str(intento.get("_q") or "").lower()
        _vuole_conteggio = (op == "conta") or bool(re.search(r"numero|quant", _qi))
        _vuole_elenco = bool(re.search(r"elenc|lista|quali|mostr|vedere", _qi))
        _QUALIF_IMM = r"residenz|agricol|urban|pertinenz|social|scolast|sportiv|cultural|sanitar|amministrativ|uffici|magazzin|produttiv|natura|categor"
        _kimm = sez_data.get("kpi") or {}
        if _vuole_conteggio and not _vuole_elenco and not re.search(_QUALIF_IMM, _qi):
            if re.search(r"fabbricat|edific", _qi):
                return {"categoria": "fabbricati", "n": _kimm.get("n_fabbricati", 0)}
            if re.search(r"terren", _qi):
                return {"categoria": "terreni", "n": _kimm.get("n_terreni", 0)}
    if op == "conta":
        if campo and conf.get("mix"):
            mix = _path(sez_data, conf["mix"], {}) or {}
            return {"categoria": campo, "n": mix.get(campo, 0)}
        out = {"n_totale": _path(sez_data, conf["tot"])}
        if conf.get("mix"):
            mix = _path(sez_data, conf["mix"], {}) or {}
            out["per_categoria"] = mix
            _q = _quota_categorie(mix, out["n_totale"], intento.get("_q"))
            if _q:
                out["quota_richiesta"] = _q
        if isinstance(sez_data, dict) and isinstance(sez_data.get("kpi"), dict):
            _pre = {k: v for k, v in sez_data["kpi"].items() if k.startswith("pct_")}
            if _pre:
                out["pct_precalcolate"] = _pre
        return out
    lista = _path(sez_data, conf["lista"], []) or []
    if op == "somma":
        imp_keys = conf.get("importo") or []
        def _imp(x):
            for k in imp_keys:
                v = x.get(k)
                if v not in (None, ""):
                    try: return float(v)
                    except Exception: pass
            return 0.0
        totale = round(sum(_imp(x) for x in lista), 2) if imp_keys else None
        return {"n_progetti": len(lista), "totale_finanziamento": totale}
    if op == "elenca":
        sel = [x for x in lista if x.get(conf["cat_item"]) == campo] if (campo and conf.get("cat_item")) else lista
        off = max(int(intento.get("offset") or 0), 0)
        lim = min(int(intento.get("limite") or 10), 50)
        page = sel[off:off + lim]
        elenco = [{k: x.get(k) for k in conf["item"]} for x in page]
        out = {"n_totale": len(sel), "elenco": elenco}
        if campo: out["categoria"] = campo
        if off or off + len(page) < len(sel):
            out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(sel))
        resto = len(sel) - (off + len(page))
        if resto > 0:
            out["_altri_disponibili"] = "ci sono altri %d risultati: chiedi 'altri' per i successivi" % resto
        return out
    if op == "cerca_nome":
        nome = str(intento["nome"]); nl = nome.lower()
        search_keys = conf.get("search") or [conf["nome"]]
        imp_keys = conf.get("importo") or []
        def _hay(x): return " ".join(str(x.get(k) or "") for k in search_keys).lower()
        def _imp(x):
            for k in imp_keys:
                v = x.get(k)
                if v not in (None, ""):
                    try: return float(v)
                    except Exception: pass
            return 0.0
        totale = sum(_imp(x) for x in lista) if imp_keys else None
        parole = [w for w in nl.split() if len(w) >= 4]
        def _mw(p, t): return p in t or (len(p) >= 5 and p[:-1] in t)
        sel = [x for x in lista if nl in _hay(x)
               or (parole and all(_mw(p, _hay(x)) for p in parole))]
        parziale = False
        if not sel:
            for p in sorted(parole, key=len, reverse=True):
                sel = [x for x in lista if _mw(p, _hay(x))]
                if sel: parziale = True; break
        if imp_keys:
            sel.sort(key=_imp, reverse=True)
        else:
            sel.sort(key=lambda x: sum(1 for v in x.values() if v not in (None, "", "None")), reverse=True)
        ris = [_enrich_rec(x, totale, imp_keys) for x in sel[:5]]
        return {"trovato": bool(ris), "nome_cercato": nome, "n_trovati": len(ris),
                "match_parziale": parziale, "risultati": ris}
    return {"errore": f"operazione {op} non implementata per {sezione}"}

def esegui_civici(sez_data, intento):
    kpi = sez_data.get("kpi", {})
    campo = intento.get("campo") or "civici"
    chiave = {"civici": "n_civici", "strade": "n_strade"}[campo]
    return {"categoria": campo, "n": kpi.get(chiave), "pct_georeferenziati": kpi.get("pct_geo_ref")}

_SANITA_KPI_PUBBLICI = {
    "farmacia": ["n_totale", "n_geo_referenziate", "mix_tipologia"],
    "parafarmacia": ["n_totale", "n_geo_referenziate", "mix_tipologia"],
    "ospedale": ["n_stabilimenti", "n_reparti_totali", "posti_letto_totali", "posti_letto_ordinaria",
                 "posti_letto_day_hospital", "posti_letto_day_surgery", "mix_discipline"],
}
def esegui_sanita(sez_data, intento):
    campo = intento.get("campo") or "farmacia"
    if intento.get("operazione") == "cerca_nome":
        nome = str(intento.get("nome") or "").strip(); nl = nome.lower()
        if campo == "ospedale":
            cand = [dict(x, _kind="ospedale") for x in (sez_data.get("ospedali", {}) or {}).get("stabilimenti", []) or []]
            namef = "denominazione"
        else:
            farm = [dict(x, _kind="farmacia") for x in (sez_data.get("farmacie", {}) or {}).get("punti", []) or []]
            para = [dict(x, _kind="parafarmacia") for x in (sez_data.get("parafarmacie", {}) or {}).get("punti", []) or []]
            cand = farm + para; namef = "nome"
        def _hay(x): return str(x.get(namef) or "").lower()
        parole = [w for w in nl.split() if len(w) >= 4]
        def _mw(p, t): return p in t or (len(p) >= 5 and p[:-1] in t)
        sel = [x for x in cand if nl in _hay(x) or (parole and all(_mw(p, _hay(x)) for p in parole))]
        parziale = False
        if not sel:
            for p in sorted(parole, key=len, reverse=True):
                sel = [x for x in cand if _mw(p, _hay(x))]
                if sel: parziale = True; break
        ris = []
        for x in sel[:5]:
            r = _enrich_rec(x); r["tipo_presidio"] = x.get("_kind"); ris.append(r)
        return {"trovato": bool(ris), "nome_cercato": nome, "categoria": campo,
                "n_trovati": len(ris), "match_parziale": parziale, "risultati": ris}
    sub_key, lista_key = {"farmacia": ("farmacie", "punti"), "parafarmacia": ("parafarmacie", "punti"),
                          "ospedale": ("ospedali", "stabilimenti")}[campo]
    sub = sez_data.get(sub_key, {}) or {}
    kpi = sub.get("kpi", {})
    if intento["operazione"] == "conta":
        wl = _SANITA_KPI_PUBBLICI[campo]
        return {"categoria": campo, "kpi": {k: kpi[k] for k in wl if k in kpi}}
    off = max(int(intento.get("offset") or 0), 0)
    lim = min(int(intento.get("limite") or 10), 50)
    lista = sub.get(lista_key, []) or []
    if campo == "ospedale":
        lista = [{"nome": s.get("denominazione"), "tipo": s.get("tipo_struttura"),
                  "indirizzo": s.get("indirizzo"), "cap": s.get("cap")} for s in lista]
    page = lista[off:off + lim]
    out = {"categoria": campo, "n_totale": len(lista), "elenco": page}
    if off or off + len(page) < len(lista):
        out["_range"] = "da %d a %d di %d" % (off + 1, off + len(page), len(lista))
    resto = len(lista) - (off + len(page))
    if resto > 0:
        out["_altri_disponibili"] = "ci sono altri %d risultati: chiedi 'altri' per i successivi" % resto
    return out

LISTE_CONF["opere"].update({"search": ["descrizione", "cup"], "importo": ["costo_eff", "costo_prev"]})
LISTE_CONF["pnrr"].update({"search": ["cup", "titolo", "settore", "submisura_descrizione"], "importo": ["finanziamento_pnrr"]})
LISTE_CONF["scuole"].update({"search": ["denominazione", "denominazione_istituto", "indirizzo", "cap", "tipologia", "codice_scuola", "codice_istituto_riferimento"]})
LISTE_CONF["terzo_settore"].update({"search": ["denom", "cf"]})
LISTE_CONF["ricarica_ev"].update({"search": ["indirizzo"]})
LISTE_CONF["immobili_pa"].update({"search": ["indirizzo", "cat", "tipo"]})

for _sz in LISTE_CONF:
    ESECUTORI[_sz] = (lambda sz: lambda sd, it: esegui_lista(sz, sd, it))(_sz)
ESECUTORI["civici"] = esegui_civici
ESECUTORI["farmacie"] = esegui_sanita


# ---------- LOTTO B: valore / serie_storica ----------
def esegui_aria(sez_data, intento):
    if intento["operazione"] == "cerca_nome":
        nome = intento.get("nome") or ""
        toks = [t for t in nome.lower().split() if t]
        stazioni = sez_data.get("stazioni", []) or []
        dett = {d.get("station_eu_code"): d for d in (sez_data.get("stazioni_dettaglio") or [])}
        def _m(s):
            blob = (str(s.get("nome") or "") + " " + str(s.get("station_eu_code") or "")).lower()
            return bool(toks) and all(t in blob for t in toks)
        out = []
        for s in stazioni:
            if not _m(s):
                continue
            code = s.get("station_eu_code")
            anni = (dett.get(code) or {}).get("anni", []) or []
            serie = [{"anno": a.get("anno"),
                      "pm10": (a.get("pm10") or {}).get("media"),
                      "pm25": (a.get("pm25") or {}).get("media"),
                      "no2": (a.get("no2") or {}).get("media")} for a in anni]
            out.append({"nome": s.get("nome"), "station_eu_code": code,
                        "tipo_zona": s.get("tipo_zona"), "tipo_stazione": s.get("tipo_stazione"),
                        "lat": s.get("lat"), "lon": s.get("lon"),
                        "unita": "ug/m3 (media annua)", "serie_annuale": serie})
        return {"operazione": "cerca_nome", "termine": nome,
                "n_risultati": len(out), "stazioni": out}
    if intento["operazione"] == "elenca":
        stazioni = sez_data.get("stazioni", []) or []
        out = [{"nome": s.get("nome"), "station_eu_code": s.get("station_eu_code"),
                "tipo_zona": s.get("tipo_zona"), "tipo_stazione": s.get("tipo_stazione"),
                "lat": s.get("lat"), "lon": s.get("lon")} for s in stazioni]
        return {"operazione": "elenca", "n_stazioni": len(out), "stazioni": out}
    campo = intento.get("campo") or "pm10"
    trend = sez_data.get("trend_decennale", {}) or {}
    if intento["operazione"] == "serie_storica":
        return {"campo": campo, "anni": trend.get("anni"), "valori": trend.get(f"{campo}_media"),
                "unita": "ug/m3 (media annua)"}
    anno = intento.get("anno")
    try: anno = int(anno) if anno is not None else None
    except Exception: anno = None
    if anno is not None:
        anni = trend.get("anni", [])
        if anno not in anni:
            return {"errore": f"anno {anno} non disponibile", "anni_disponibili": anni}
        return {"campo": campo, "anno": anno, "media": trend.get(f"{campo}_media", [])[anni.index(anno)],
                "unita": "ug/m3 (media annua)"}
    ua = sez_data.get("ultimo_anno", {}) or {}
    return {"campo": campo, "anno": ua.get("anno"), **(ua.get(campo) or {}), "unita": "ug/m3"}

def _isc_anni(serie):
    if isinstance(serie, dict):
        an = serie.get("anni")
        if isinstance(an, list): return an
        return sorted(int(k) for k in serie.keys() if str(k).isdigit())
    if isinstance(serie, list):
        return [r.get("anno") for r in serie if isinstance(r, dict) and r.get("anno") is not None]
    return []

def _isc_anno(serie, anno):
    if isinstance(serie, dict):
        an = serie.get("anni")
        if isinstance(an, list) and anno in an:
            i = an.index(anno); rec = {"anno": anno}
            for k, v in serie.items():
                if k == "anni": continue
                if isinstance(v, list) and i < len(v): rec[k] = v[i]
            return rec
        if str(anno) in serie and isinstance(serie[str(anno)], dict):
            r = dict(serie[str(anno)]); r.setdefault("anno", anno); return r
    if isinstance(serie, list):
        for r in serie:
            if isinstance(r, dict) and r.get("anno") == anno:
                return dict(r)
    return None

def esegui_veicoli(sez_data, intento):
    import re
    isc = sez_data.get("iscrizioni", {}) or {}
    _q = str(intento.get("_q") or "").lower()
    _vuole_isc = (intento.get("campo") == "iscrizioni") or bool(re.search(
        r"immatricol|iscrizion|nuove\s+(auto|vetture|immatric)|aliment|elettric|ibrid|benzina|gasolio|diesel|\bgas\b|gpl|metano", _q))
    if intento["operazione"] == "serie_storica":
        return {"iscrizioni_serie": isc.get("serie_storica")}
    if _vuole_isc:
        ua = isc.get("ultimo_anno") or {}
        anno = intento.get("anno")
        try: anno = int(anno) if anno is not None else None
        except Exception: anno = None
        if anno is not None and anno != ua.get("anno"):
            rec = _isc_anno(isc.get("serie_storica"), anno)
            if rec is not None:
                return {"iscrizioni_anno": rec}
            return {"errore": "immatricolazioni non disponibili per il %s" % anno,
                    "anni_disponibili": _isc_anni(isc.get("serie_storica")),
                    "ultimo_anno_disponibile": ua.get("anno")}
        return {"iscrizioni_ultimo_anno": ua}
    return {"parco_veicoli": sez_data.get("parco_veicoli")}

def esegui_incidenti(sez_data, intento):
    inc = sez_data.get("incidenti", {}) or {}
    if intento["operazione"] == "serie_storica":
        return {"serie": inc.get("serie_storica")}
    anno = intento.get("anno")
    try: anno = int(anno) if anno is not None else None
    except Exception: anno = None
    if anno is not None:
        ss = inc.get("serie_storica") or {}
        anni = ss.get("anni") or []
        if anno in anni:
            i = anni.index(anno)
            g = lambda k: (ss.get(k)[i] if isinstance(ss.get(k), list) and i < len(ss.get(k)) else None)
            return {"anno": anno, "incidenti": g("incidenti"), "morti": g("morti"), "feriti": g("feriti")}
        return {"errore": f"dato incidenti non disponibile per il {anno}", "anni_disponibili": anni, "ultimo_anno_disponibile": (inc.get("ultimo_anno") or {}).get("anno")}
    return dict(inc.get("ultimo_anno") or {})

def esegui_sismica(sez_data, intento):
    return {"zona_sismica": sez_data.get("zona_sismica"),
            "zona_principale": sez_data.get("zona_principale")}

def esegui_demografia(sez_data, intento):
    out = {k: sez_data.get(k) for k in ("popolazione_totale", "maschi", "femmine", "pct_maschi",
            "pct_femmine", "eta_media", "indice_vecchiaia", "indice_dipendenza", "fasce_eta")}
    out["data_riferimento"] = sez_data.get("_riferimento")
    return out

def esegui_imprese(sez_data, intento):
    campo = intento.get("campo") or "unita_locali"
    serie = sez_data.get("serie_storica", {}) or {}
    if intento["operazione"] == "serie_storica":
        chiave = {"unita_locali": "ul", "addetti": "addetti"}[campo]
        _vals = serie.get(chiave)
        if campo == "addetti" and isinstance(_vals, list):
            _vals = [round(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for v in _vals]
        return {"campo": campo, "anni": serie.get("anni"), "valori": _vals}
    kpi = sez_data.get("kpi", {}) or {}
    import re
    _qte = str(intento.get("_q") or "").lower()
    if re.search(r"ateco|settor", _qte) and re.search(r"prim[ei]|top|classific|graduator|maggior|ordina|per (numero di )?addett|per unit", _qte):
        crit = "ul" if (re.search(r"unit|local", _qte) and not re.search(r"addett", _qte)) else "addetti"
        src = kpi.get("top_settori_" + crit) or []
        _el = [{"label": x.get("label"), "code": x.get("code"),
                "addetti": float(round(x.get("addetti"))) if isinstance(x.get("addetti"), (int, float)) else None,
                "ul": float(x.get("ul")) if isinstance(x.get("ul"), (int, float)) else None}
               for x in src if isinstance(x, dict)]
        return {"categoria": ("per addetti" if crit == "addetti" else "per unita locali"),
                "n_totale": len(_el), "elenco": _el, "_forza_elenco": True}
    anno = sez_data.get("_latest_year")
    ad = (sez_data.get("ateco_dettaglio", {}) or {}).get(str(anno), {}) or {}
    _lab = {}
    for _ts in (kpi.get("top_settori_ul") or []) + (kpi.get("top_settori_addetti") or []):
        if isinstance(_ts, dict) and _ts.get("code"):
            _lab[str(_ts["code"])] = _ts.get("label") or ("ATECO " + str(_ts["code"]))
    _CLS = [("micro_1_9", "W0_9"), ("piccole_10_49", "W10_49"), ("medie_50_249", "W50_249"), ("grandi_250_plus", "W_GE250")]
    tot_classe = {nome: 0 for nome, _ in _CLS}
    settori = []
    for code, blocchi in ad.items():
        if not isinstance(blocchi, dict):
            continue
        tot = blocchi.get("TOTAL") or {}
        rec = {"code": code, "label": _lab.get(str(code), "ATECO " + str(code)),
               "ul": tot.get("ul"), "addetti": tot.get("addetti")}
        for nome, wk in _CLS:
            n = int((blocchi.get(wk) or {}).get("ul") or 0)
            rec[nome] = n
            tot_classe[nome] += n
        settori.append(rec)
    settori.sort(key=lambda r: r.get("ul") or 0, reverse=True)
    out = {"anno": anno, "unita_locali_totali": kpi.get("ul_totali"),
           "addetti_totali": kpi.get("addetti_totali"), "addetti_per_ul": kpi.get("addetti_per_ul"),
           "variazione_ul_pct": kpi.get("ul_yoy_pct"),
           "ul_per_classe_dimensionale": tot_classe,
           "settori_ateco": settori,
           "_nota": "I valori per classe sono CONTEGGI di unita locali (UL), non percentuali. Classi ASIA a fasce: micro 1-9, piccole 10-49, medie 50-249, grandi 250+ addetti."}
    soglia = intento.get("soglia_addetti")
    try: soglia = int(soglia) if soglia not in (None, "") else None
    except Exception: soglia = None
    if soglia is not None:
        if soglia >= 249: incl = ["grandi_250_plus"]
        elif soglia >= 49: incl = ["medie_50_249", "grandi_250_plus"]
        elif soglia >= 9: incl = ["piccole_10_49", "medie_50_249", "grandi_250_plus"]
        else: incl = ["micro_1_9", "piccole_10_49", "medie_50_249", "grandi_250_plus"]
        _LBL = {"micro_1_9": "1-9", "piccole_10_49": "10-49", "medie_50_249": "50-249", "grandi_250_plus": "250+"}
        sett_s = [{"code": r["code"], "label": r["label"], **{c: r[c] for c in incl}}
                  for r in settori if any(r[c] for c in incl)]
        import re
        _q = str(intento.get("_q") or "").lower()
        _vuole_elenco = bool(re.search(r"qual|elenc|lista|mostr|che tipo|tipolog|settor|ateco|dettagl|distribu|riparti|comparti", _q))
        _fsa = {
            "soglia_richiesta": soglia,
            "classi_incluse": [_LBL[c] for c in incl],
            "ul_totali_oltre_soglia": sum(tot_classe[c] for c in incl)}
        if _vuole_elenco:
            _fsa["settori_ateco"] = sett_s
            _fsa["_nota"] = "ul_totali_oltre_soglia = numero di unita locali con almeno la soglia di addetti (somma delle classi). Classi ASIA a fasce: piu di N approssimato alla fascia che contiene N."
        else:
            _fsa["_nota"] = "ul_totali_oltre_soglia = numero di unita locali con almeno la soglia di addetti. Riporta SOLO questo numero; NON elencare i settori."
        out["filtro_soglia_addetti"] = _fsa
        out.pop("settori_ateco", None)
    return out

ESECUTORI["aria"] = esegui_aria
ESECUTORI["veicoli"] = esegui_veicoli
ESECUTORI["incidenti"] = esegui_incidenti
ESECUTORI["sismica"] = esegui_sismica
ESECUTORI["demografia_dettaglio"] = esegui_demografia
ESECUTORI["imprese"] = esegui_imprese


# ---------- LOTTO C-D: zoom full (civici, censimento, catasto) ----------
import gzip as _gzip, math as _math

_anncsu_cache = {}
def _load_anncsu_full(istat):
    if istat in _anncsu_cache: return _anncsu_cache[istat]
    punti = []
    path = f"/var/www/cruscotto-italia/data/anncsu_full/{istat}.json"
    try:
        if os.path.exists(path):
            with open(path) as f: punti = json.load(f).get("punti", [])
    except Exception:
        punti = []
    if len(_anncsu_cache) >= 3: _anncsu_cache.pop(next(iter(_anncsu_cache)))
    _anncsu_cache[istat] = punti
    return punti

def _match_civici(istat, odonimo, civico=None, lim=10):
    ol = str(odonimo).lower()
    parole = [w for w in ol.split() if len(w) >= 3]
    ris = []
    for p in _load_anncsu_full(istat):
        odo = (p.get("odo") or "").lower()
        if not (ol in odo or (parole and all(w in odo for w in parole))): continue
        if civico is not None and str(p.get("civ")) != str(civico): continue
        ris.append(p)
        if len(ris) >= lim: break
    return ris

def _punto_civico(istat, odonimo, civico):
    """Primo civico georeferenziato che corrisponde, o None."""
    for p in _match_civici(istat, odonimo, civico, lim=10):
        if p.get("lat") is not None and p.get("lon") is not None:
            return p
    return None

def _pip(lon, lat, ring):
    n, inside, j = len(ring), False, len(ring) - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]; xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi): inside = not inside
        j = i
    return inside

_CENS_VAR = {
    "stranieri": "ST1", "stranieri_ue": "ST16", "stranieri_extra_ue": "ST19",
    "popolazione": "P1", "maschi": "P2", "femmine": "P3",
    "occupati": "P101", "famiglie": "PF1",
    "abitazioni": "A8", "abitazioni_occupate": "A2", "abitazioni_vuote": "A3",
    "laureati": "P90", "diplomati": "P89", "pop_9plus": "P83",
}
_CENS_VAR_LABEL = {
    "stranieri": "popolazione straniera", "stranieri_ue": "stranieri UE", "stranieri_extra_ue": "stranieri extra-UE",
    "popolazione": "popolazione totale", "maschi": "maschi", "femmine": "femmine",
    "occupati": "occupati (15-64)", "famiglie": "famiglie", "abitazioni": "abitazioni totali",
    "abitazioni_occupate": "abitazioni occupate", "abitazioni_vuote": "abitazioni vuote",
    "laureati": "laureati (titolo terziario)", "diplomati": "diplomati", "pop_9plus": "popolazione 9+ anni",
}
def _ranking_sezioni(istat, campo, order="desc", top=5, min_pop=10):
    var = _CENS_VAR.get(campo)
    if not var:
        return {"errore": f"variabile '{campo}' non disponibile per il ranking delle sezioni",
                "variabili_disponibili": sorted(_CENS_VAR.keys())}
    path = f"/var/www/cruscotto-italia/data/censimento_full/{istat}.geojson"
    if not os.path.exists(path):
        return {"errore": "dati sezioni di censimento non disponibili per questo comune"}
    with open(path) as f: feats = json.load(f)["features"]
    righe = []
    for ft in feats:
        pr = ft.get("properties", {}); v = pr.get("vars") or {}
        val = v.get(var); pop = v.get("P1") or 0
        if val is None or pop < min_pop:
            continue
        righe.append({"sezione": pr.get("sez"), "tipo_localita": pr.get("tipo_loc"),
                      "valore": val, "popolazione_sezione": pop, "area_mq": pr.get("area_mq")})
    righe.sort(key=lambda r: r["valore"], reverse=(order != "asc"))
    return {"variabile": _CENS_VAR_LABEL.get(campo, campo), "ordine": "decrescente" if order != "asc" else "crescente",
            "n_sezioni_totali": len(feats), "n_sezioni_valide": len(righe),
            "risultati": righe[:max(1, min(top, 20))]}

def _sezione_censimento(istat, lat, lon):
    path = f"/var/www/cruscotto-italia/data/censimento_full/{istat}.geojson"
    if not os.path.exists(path): return None
    with open(path) as f: feats = json.load(f)["features"]
    for ft in feats:
        g = ft["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            if _pip(lon, lat, poly[0]) and not any(_pip(lon, lat, h) for h in poly[1:]):
                pr = ft["properties"]
                return {"sezione_id": pr.get("id"), "sezione": pr.get("sez"), "tipo_localita": pr.get("tipo_loc"),
                        "area_mq": pr.get("area_mq"), "popolazione_sezione": (pr.get("vars") or {}).get("P1")}
    return None

_SEZ_ISTRUZIONE_PCT = {"laureati": "P90", "diplomati": "P89"}
def _valore_sezione(istat, sez, campo, domanda=None):
    """Valore di una variabile censuaria per UNA singola sezione (per numero sez).
    Non ripiega mai sul dato comunale: se assente, lo dichiara."""
    path = f"/var/www/cruscotto-italia/data/censimento_full/{istat}.geojson"
    if not os.path.exists(path):
        return {"errore": "dati sezioni di censimento non disponibili per questo comune"}
    with open(path) as f:
        feats = json.load(f)["features"]
    target = None
    for ft in feats:
        if str((ft.get("properties") or {}).get("sez")) == str(sez):
            target = ft["properties"]; break
    if target is None:
        return {"errore": f"sezione {sez} non trovata in questo comune"}
    v = target.get("vars") or {}
    base = {"sezione": str(sez), "tipo_localita": target.get("tipo_loc"),
            "popolazione_sezione": v.get("P1"), "area_mq": target.get("area_mq")}
    if not v:
        return {**base, "non_disponibile": True,
                "nota": "questa sezione non ha dati censuari di dettaglio (area non residenziale)"}
    _ISTR_TOT = {"P86", "P87", "P88", "P89", "P90"}
    def _out_codice(code, label):
        val = v.get(code)
        if val is None:
            return {**base, "non_disponibile": True, "nota": "variabile '%s' assente in questa sezione" % label}
        out = {**base, "campo": label, "valore": val}
        if code in _ISTR_TOT:
            den = v.get("P83"); out["pop_9plus"] = den
            out["percentuale"] = round(val / den * 100, 1) if den else None
            out["denominatore"] = "popolazione 9+ anni"
        return out
    # 1) MATCHER-FIRST sulla domanda: copre le 119 vars e batte il campo enum
    #    quando la domanda nomina una variabile piu' specifica (no ripieghi errati).
    m = match_cens(domanda, v)
    if isinstance(m, dict) and "ambiguo" in m:
        return {**base, "ambiguo": [{"codice": c, "descrizione": l} for c, l in m["ambiguo"]],
                "nota": "richiesta ambigua: specifica quale variabile"}
    if isinstance(m, dict) and "codice" in m:
        return _out_codice(m["codice"], m["label"])
    # 2) fallback campo enum (follow-up col campo ereditato dal contesto)
    var = _CENS_VAR.get(campo) if campo else None
    if var:
        return _out_codice(var, _CENS_VAR_LABEL.get(campo, campo))
    # 2b) domanda assente (follow-up senza testo) -> default popolazione totale
    if not (domanda or "").strip():
        return _out_codice("P1", _CENS_VAR_LABEL.get("popolazione", "popolazione"))
    # 3) SALVAGUARDIA: variabile non mappabile -> nessun ripiego su una vicina
    return {**base, "non_disponibile": True,
            "nota": "variabile non riconosciuta tra quelle censuarie interrogabili per sezione",
            "categorie_disponibili": list(CATEGORIE_DISPONIBILI)}

_catasto_cache = {}
def _parse_catasto_ref(ref):
    """Estrae foglio e particella dal NATIONALCADASTRALREFERENCE. Due formati:
    con underscore senza sezione (G273_0025D0.2365 -> foglio 25, particella 2365)
    e con lettera di sezione (L049A02430Z.25 -> foglio 243, particella 25)."""
    import re as _re
    m = _re.match(r"^([A-Z]\d{3})(?:_|([A-Z]))(\d{4})(..)\.(.+)$", ref or "")
    if not m:
        return {}
    bel, sez, fog, suf, part = m.groups()
    out = {"foglio": int(fog), "particella": part, "riferimento": ref}
    if sez:
        out["sezione_catastale"] = sez
    return out

def _particella(istat, lat, lon):
    if istat in _catasto_cache:
        feats = _catasto_cache[istat]
    else:
        path = f"/var/www/cruscotto-italia/data/catasto_full/{istat}_ple.geojson.gz"
        if not os.path.exists(path): return None
        with _gzip.open(path, "rt") as f: feats = json.load(f)["features"]
        if len(_catasto_cache) >= 2: _catasto_cache.pop(next(iter(_catasto_cache)))
        _catasto_cache[istat] = feats
    hit = None
    for ft in feats:
        g = ft["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            if _pip(lon, lat, poly[0]) and not any(_pip(lon, lat, h) for h in poly[1:]):
                hit = ft["properties"]; break
        if hit: break
    near = []
    for ft in feats:
        pr = ft["properties"]
        if "STRADA" in pr.get("LABEL", "") or "ACQUA" in pr.get("LABEL", ""): continue
        g = ft["geometry"]; ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        cx = sum(q[0] for q in ring) / len(ring); cy = sum(q[1] for q in ring) / len(ring)
        d = _math.sqrt(((cx - lon) * _math.cos(_math.radians(lat))) ** 2 + (cy - lat) ** 2) * 111000
        near.append((round(d, 1), pr.get("NATIONALCADASTRALREFERENCE")))
    near.sort()
    hit_ref = hit.get("NATIONALCADASTRALREFERENCE") if hit else None
    hit_out = None
    if hit_ref:
        hit_out = {**_parse_catasto_ref(hit_ref)}
    return {"particella_contenente_il_punto": hit_out or hit,
            "particelle_edificate_vicine": [{"distanza_m": d, **_parse_catasto_ref(r)} for d, r in near[:3]]}

def _esegui_civici_v2(sez_data, intento):
    op = intento["operazione"]
    istat = intento["istat"]
    if op == "conta":
        return esegui_civici(sez_data, intento)
    odonimo, civico = intento.get("odonimo"), intento.get("civico")
    if not _load_anncsu_full(istat):
        # ~2503 comuni: la fonte ANNCSU non pubblica i civici puntuali (solo il totale).
        # Le operazioni puntuali userebbero un dettaglio assente -> evita falsi negativi
        # ("non trovato") con un esito esplicito di dato non pubblicato.
        return {"odonimo_cercato": odonimo, "civico_cercato": civico,
                "dettaglio_non_disponibile": True,
                "n_civici_totali": (sez_data.get("kpi") or {}).get("n_civici"),
                "nota": "Il dettaglio puntuale dei numeri civici (ANNCSU) non risulta pubblicato dalla fonte per questo comune; e' disponibile solo il totale dei civici."}
    if op == "cerca_civico":
        ris = _match_civici(istat, odonimo, civico)
        return {"odonimo_cercato": odonimo, "civico_cercato": civico, "n_trovati": len(ris),
                "risultati": [{"odonimo": p.get("odo"), "civico": p.get("civ"), "esponente": p.get("esp"),
                               "lat": p.get("lat"), "lon": p.get("lon")} for p in ris]}
    punto = _punto_civico(istat, odonimo, civico)
    if punto is None:
        return {"errore": f"civico '{civico}' di '{odonimo}' non trovato o non georeferenziato"}
    base = {"odonimo": punto.get("odo"), "civico": punto.get("civ"), "lat": punto.get("lat"), "lon": punto.get("lon")}
    if op == "sezione_censimento":
        sez = _sezione_censimento(istat, punto["lat"], punto["lon"])
        if sez is None: return {**base, "errore": "sezione censuaria non determinabile"}
        return {**base, **sez}
    if op == "particella":
        part = _particella(istat, punto["lat"], punto["lon"])
        if part is None: return {**base, "errore": "dati catastali non disponibili per questo comune"}
        return {**base, **part}
    return {"errore": f"operazione {op} non implementata per civici"}

ESECUTORI["civici"] = _esegui_civici_v2


# ---------- LOTTO E: sezioni a blocchi (valore) ----------
_BLOCCHI_CONF = {
    "turismo":      {"keys": ["capacita_comune", "flussi_provincia", "fonte"]},
    "pendolarismo": {"keys": ["kpi", "_anno_rilevazione", "_motivo_spostamento"]},
    "anac":         {"keys": ["buyer_name", "count", "importo_totale", "distinct_cpv",
                              "top_cpv", "first_award_date", "last_award_date"]},
    "banda_larga":  {"keys": ["kpi", "_data_period"]},
    "anagrafica":   {"keys": ["denominazione", "provincia", "regione", "codice_catastale",
                              "codice_fiscale", "codice_ipa", "kpi"]},
    "censimento":   {"keys": ["kpi_comune", "_anno_rilevazione"]},
    "territorio":   {"keys": ["kpi"], "blocchi": {"rifiuti", "rischio_idrogeologico", "suolo"}},
    "profilo":      {"keys": [], "blocchi": {"cittadinanza", "famiglie", "istruzione", "lavoro", "mobilita"}},
}

def esegui_blocchi(sezione, sez_data, intento):
    conf = _BLOCCHI_CONF[sezione]
    campo = intento.get("campo")
    if campo and campo in conf.get("blocchi", set()):
        return {campo: sez_data.get(campo)}
    out = {k: sez_data.get(k) for k in conf["keys"] if sez_data.get(k) is not None}
    if not out and conf.get("blocchi"):  # profilo senza campo: tutti i blocchi
        out = {k: sez_data.get(k) for k in conf["blocchi"]}
    return out or {"errore": f"nessun dato per {sezione}"}

def esegui_siope(sez_data, intento):
    disp = sez_data.get("anni_disponibili", []) or []
    anno = intento.get("anno")
    try: anno = int(anno) if anno is not None else None
    except Exception: anno = None
    if anno is not None and anno not in disp:
        return {"errore": f"anno {anno} non disponibile", "anni_disponibili": disp}
    if anno is None:
        anno = sez_data.get("anno_default") or (disp[-1] if disp else None)
    blocco = (sez_data.get("per_anno", {}) or {}).get(str(anno))
    if blocco is None:
        return {"errore": "dati SIOPE non disponibili", "anni_disponibili": disp}
    if intento.get("operazione") == "elenca":
        voci = blocco.get("voci") or []
        vs = sorted(voci, key=lambda v: v.get("importo_cumulato") or 0, reverse=True)
        off = max(int(intento.get("offset") or 0), 0)
        lim = min(int(intento.get("limite") or 10), 50)
        elenco = [{"voce": v.get("desc_gestionale"), "importo": v.get("importo_cumulato")} for v in vs[off:off + lim]]
        out = {"n_totale": len(voci), "anno": anno, "elenco": elenco, "_forza_elenco": True,
               "_cfg_override": ["elenco", "Spese SIOPE " + str(anno), [["voce"], ["importo"]]]}
        if elenco:
            out["_range"] = "%d-%d di %d" % (off + 1, off + len(elenco), len(voci))
        resto = len(vs) - (off + lim)
        if resto > 0:
            out["_altri_disponibili"] = "ci sono altre %d voci: chiedi 'altri' per le successive" % resto
        return out
    if intento.get("operazione") == "cerca_voce":
        nome = str(intento.get("nome") or "").strip(); nl = nome.lower()
        voci = blocco.get("voci") or []
        totale = blocco.get("totale_anno") or 0
        def _hay(v): return (str(v.get("desc_gestionale") or "") + " " + str(v.get("codice_gestionale") or "")).lower()
        parole = [w for w in nl.split() if len(w) >= 4]
        def _mw(p, t): return p in t or (len(p) >= 5 and p[:-1] in t)
        sel = [v for v in voci if nl in _hay(v) or (parole and all(_mw(p, _hay(v)) for p in parole))]
        parziale = False
        if not sel:
            for p in sorted(parole, key=len, reverse=True):
                sel = [v for v in voci if _mw(p, _hay(v))]
                if sel: parziale = True; break
        sel.sort(key=lambda v: v.get("importo_cumulato") or 0, reverse=True)
        ris = [_enrich_rec(v, totale, ["importo_cumulato"]) for v in sel[:5]]
        _out = {"trovato": bool(ris), "nome_cercato": nome, "anno": anno,
                "n_trovati": len(ris), "match_parziale": parziale, "risultati": ris}
        _mese = intento.get("mese")
        if _mese:
            _NOMI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
                     "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
            _mk = "%d/%02d" % (anno, int(_mese))
            for r in ris:
                _md = r.get("mensili_delta") or {}
                r["spesa_mese"] = _md.get(_mk)
                r["mese"] = _NOMI[int(_mese)]
            _out["mese_richiesto"] = _NOMI[int(_mese)]
        return _out
    # operazione 'valore' (totale comune): NON inviare al verbalizzatore tutte le voci con le
    # serie mensili (payload ~54KB -> l'LLM va in timeout). Sommario: totale + top voci per
    # importo (senza serie) + uscite per abitante. Per il dettaglio di una voce -> cerca_voce.
    voci = blocco.get("voci") or []
    top = sorted(voci, key=lambda v: v.get("importo_cumulato") or 0, reverse=True)[:8]
    top_pub = [{"voce": v.get("desc_gestionale"), "titolo": v.get("desc_titolo"),
                "importo_cumulato": v.get("importo_cumulato")} for v in top]
    blocco_pub = {k: v for k, v in blocco.items()
                  if k != "voci" and not (k.startswith("_") or k in ("md5", "etag"))}
    _pop, _tot = blocco.get("popolazione"), blocco.get("totale_anno")
    if isinstance(_pop, (int, float)) and _pop and isinstance(_tot, (int, float)):
        blocco_pub["uscite_per_abitante"] = round(_tot / _pop, 2)
    return {"anno": anno, "anni_disponibili": disp, **blocco_pub, "top_voci": top_pub}

_ISTAT_NAMES = None
def _nome_comune(istat):
    global _ISTAT_NAMES
    if _ISTAT_NAMES is None:
        try: _ISTAT_NAMES = json.load(open("/var/www/cruscotto-stats/istat-names.json"))
        except Exception: _ISTAT_NAMES = {}
    return _ISTAT_NAMES.get(istat, istat)

def esegui_pendolarismo(sez_data, intento):
    if intento.get("operazione") == "elenca":
        _k0 = sez_data.get("kpi") or {}
        _ql = str(intento.get("_q") or "").lower()
        _orig = bool(re.search(r"origin|provengon|arrivan|entrant|in entrata|da cui|da dove", _ql))
        _key = "top_origini" if _orig else "top_destinazioni"
        _raw = _k0.get(_key) or []
        _lim = min(int(intento.get("limite") or 10), 50)
        _el = [{"comune": _nome_comune(x.get("istat")), "pendolari": x.get("count")} for x in _raw[:_lim]]
        _tit = "Comuni di origine dei pendolari (entrata)" if _orig else "Destinazioni dei pendolari (uscita)"
        return {"n_totale": len(_raw), "elenco": _el, "_forza_elenco": True,
                "_cfg_override": ["elenco", _tit, [["comune"], ["pendolari"]]]}
    out = esegui_blocchi("pendolarismo", sez_data, intento)
    kpi = dict(out.get("kpi") or {})
    for k in ("top_destinazioni", "top_origini"):
        if isinstance(kpi.get(k), list):
            kpi[k] = [{**x, "comune": _nome_comune(x.get("istat"))} for x in kpi[k][:10]]
    out["kpi"] = kpi
    out["_nota"] = "top_destinazioni = comuni verso cui si spostano i residenti (pendolari in uscita); top_origini = comuni da cui arrivano i pendolari in entrata."
    return out

for _sz in _BLOCCHI_CONF:
    ESECUTORI[_sz] = (lambda sz: lambda sd, it: esegui_blocchi(sz, sd, it))(_sz)
def esegui_censimento(sez_data, intento):
    if intento.get("operazione") == "ranking_sezioni":
        return _ranking_sezioni(intento["istat"], intento.get("campo") or "popolazione",
                                intento.get("direzione") or "desc", intento.get("limite") or 5)
    istat = intento["istat"]
    odonimo, civico = intento.get("odonimo"), intento.get("civico")
    if odonimo and civico:
        punto = _punto_civico(istat, odonimo, civico)
        if punto is None:
            return {"errore": "civico '%s' di '%s' non trovato o non georeferenziato" % (civico, odonimo)}
        sezinfo = _sezione_censimento(istat, punto["lat"], punto["lon"])
        sez_real = (sezinfo or {}).get("sezione")
        if sez_real in (None, ""):
            return {"odonimo": punto.get("odo"), "civico": punto.get("civ"),
                    "errore": "sezione censuaria non determinabile per questo civico"}
        res = _valore_sezione(istat, sez_real, intento.get("campo"), intento.get("_q"))
        return {"odonimo": punto.get("odo"), "civico": punto.get("civ"), **res}
    sez = intento.get("sezione_censimento")
    if sez not in (None, "", []) and str(sez).strip().isdigit():
        return _valore_sezione(istat, sez, intento.get("campo"), intento.get("_q"))
    return esegui_blocchi("censimento", sez_data, intento)
ESECUTORI["censimento"] = esegui_censimento
ESECUTORI["siope"] = esegui_siope
ESECUTORI["pendolarismo"] = esegui_pendolarismo
def esegui_anac(sez_data, intento):
    if intento.get("operazione") == "elenca":
        _cpv = sez_data.get("cpv") or sez_data.get("top_cpv") or []
        _cpv = sorted(_cpv, key=lambda c: c.get("importo") or 0, reverse=True)
        _tot = sez_data.get("distinct_cpv") or len(_cpv)
        _off = max(int(intento.get("offset") or 0), 0)
        _lim = min(int(intento.get("limite") or 10), 50)
        _el = [{"categoria": c.get("desc"), "contratti": c.get("count"), "importo": c.get("importo")} for c in _cpv[_off:_off + _lim]]
        out = {"n_totale": _tot, "elenco": _el, "_forza_elenco": True,
               "_cfg_override": ["elenco", "Categorie merceologiche ANAC", [["categoria"], ["contratti"], ["importo"]]]}
        if _el:
            out["_range"] = "%d-%d di %d" % (_off + 1, _off + len(_el), len(_cpv))
        _resto = len(_cpv) - (_off + _lim)
        if _resto > 0:
            out["_altri_disponibili"] = "ci sono altre %d categorie: chiedi 'altri' per le successive" % _resto
        return out
    return esegui_blocchi("anac", sez_data, intento)
ESECUTORI["anac"] = esegui_anac
