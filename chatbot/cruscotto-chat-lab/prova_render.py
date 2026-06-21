import json
from intent_engine import _load_dash_section, esegui

# ---- formattazione ----
def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return f"{int(f):,}".replace(",", ".")
    return f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _fmt_importo(v):
    return _fmt_num(v) + " €"

def _fmt_prezzo(v):
    return (str(v).replace(".", ",")) + " €"

_EUR = {"costo_prev", "finanziamento_totale", "importo", "importo_totale"}
_M2 = {"sup"}
_BOOL = {"vincolo", "uso_terzi"}

def _fmt_val(chiave, v):
    if v is None or v == "" or v == "None":
        return None
    if chiave == "prezzo":
        return _fmt_prezzo(v)
    if chiave in _EUR:
        return _fmt_importo(v)
    if chiave in _M2:
        return _fmt_num(v) + " m²"
    if chiave in _BOOL:
        return "sì" if v else "no"
    if isinstance(v, float):
        return _fmt_num(v)
    return str(v)

# (chiave, label_opzionale)
_ELENCO_CFG = {
    "carburanti":    ("risultati", "Distributori", [("nome",), ("brand",), ("indirizzo",), ("prezzo",)]),
    "beni_culturali":("elenco", "Beni culturali", [("nome",), ("categoria",), ("indirizzo",)]),
    "scuole":        ("elenco", "Scuole", [("denominazione",), ("tipologia",), ("indirizzo",)]),
    "terzo_settore": ("elenco", "Enti del Terzo Settore", [("denom",), ("sez",), ("data_iscr",)]),
    "ricarica_ev":   ("elenco", "Colonnine di ricarica", [("indirizzo",), ("potenza_categoria",), ("stato",)]),
    "immobili_pa":   ("elenco", "Immobili PA", [("tipo",), ("cat",), ("sup",), ("vincolo", "vincolo"), ("uso_terzi", "uso terzi")]),
    "opere":         ("elenco", "Opere pubbliche", [("descrizione",), ("stato",), ("settore",), ("costo_prev",)]),
    "pnrr":          ("elenco", "Progetti PNRR", [("titolo",), ("missione_descrizione",), ("stato_avanzamento",), ("finanziamento_totale",)]),
    "farmacie":      ("elenco", "Farmacie/presidi", [("nome",), ("tipo",), ("indirizzo",), ("cap",)]),
    "aria":          ("stazioni", "Centraline qualità aria", [("nome",), ("tipo_zona",), ("tipo_stazione",)]),
}

def render_elenco(sezione, comune_nome, dati):
    cfg = _ELENCO_CFG.get(sezione)
    if not cfg:
        return None
    key, titolo, campi = cfg
    lista = dati.get(key) or dati.get("risultati") or dati.get("elenco") or dati.get("stazioni") or []
    if not lista:
        return None
    n_tot = dati.get("n_totale") or dati.get("n_stazioni") or dati.get("n_totale_con_prezzo") or len(lista)
    cat = dati.get("categoria") or dati.get("carburante")
    testa = f"{titolo} a {comune_nome}" + (f" — {cat}" if cat else "")
    testa += f" ({len(lista)}" + (f" di {n_tot}" if n_tot and n_tot != len(lista) else "") + "):"
    righe = [testa]
    for el in lista:
        parti = []
        for c in campi:
            chiave, lab = c[0], (c[1] if len(c) > 1 else None)
            val = _fmt_val(chiave, el.get(chiave))
            if val is None:
                continue
            parti.append(f"{lab}: {val}" if lab else val)
        if parti:
            righe.append("- " + " · ".join(parti))
    if dati.get("_troncato"):
        righe.append(f"(mostrati {len(lista)} di {n_tot})")
    return "\n".join(righe)

# ---- prova su Lecce ----
ISTAT = "075035"
for sez in _ELENCO_CFG:
    sd = _load_dash_section(ISTAT, sez)
    if sd is None:
        print(f"\n===== {sez}: dati non disponibili =====")
        continue
    it = {"comune": "Lecce", "sezione": sez, "operazione": "elenca", "campo": None, "istat": ISTAT,
          "direzione": None, "limite": None, "nome": None, "anno": None, "odonimo": None, "civico": None, "sezione_censimento": None}
    dati = esegui(sd, it)
    print(f"\n===== {sez} =====")
    print(render_elenco(sez, "Lecce", dati))
