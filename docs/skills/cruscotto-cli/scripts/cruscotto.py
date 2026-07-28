#!/usr/bin/env python3
"""Cruscotto Italia - CLI di interrogazione shard (stdlib only).

Trasporto: HTTPS pubblico oppure filesystem locale.
  export CRUSCOTTO_BASE=https://cruscotto-italia.dati.gov.it/data   (default)
  export CRUSCOTTO_BASE=/var/www/cruscotto-italia/data              (locale, sulla VM)
"""
import argparse, json, os, re, sys, time, hashlib, unicodedata
import ssl
import urllib.request, urllib.error

BASE  = os.environ.get("CRUSCOTTO_BASE", "https://cruscotto-italia.dati.gov.it/data")
CACHE = os.environ.get("CRUSCOTTO_CACHE", "/tmp/cruscotto-cache")
TTL   = int(os.environ.get("CRUSCOTTO_TTL", "86400"))
CAP   = int(os.environ.get("CRUSCOTTO_CAP", "20000"))
WINDOW = int(os.environ.get("CRUSCOTTO_WINDOW", "600"))
MAXCOM = int(os.environ.get("CRUSCOTTO_MAX_COMUNI", "12"))
MININT = float(os.environ.get("CRUSCOTTO_MIN_INTERVAL", "0.5"))

HEAVY = {"anncsu", "opere", "siope", "immobili_pa", "runts", "scuole"}


def _den_registry():
    """Registro esplicito dei denominatori leciti per le variabili censuarie.

    Ogni voce e' verificata sui dati: la somma delle parti coincide con il totale.
    Nessuna generalizzazione automatica: fuori da questo registro il rapporto
    e' rifiutato, perche' dividere variabili non comparabili produce
    percentuali plausibili ma sbagliate.
    """
    g = [
        (["P2", "P3"], "P1"),
        (["P" + str(i) for i in range(14, 30)], "P1"),
        (["P" + str(i) for i in range(30, 46)], "P2"),
        (["P" + str(i) for i in range(67, 83)], "P3"),
        (["P83"], "P1"), (["P84"], "P2"), (["P85"], "P3"),
        (["P" + str(i) for i in range(86, 91)], "P83"),
        (["P" + str(i) for i in range(91, 96)], "P84"),
        (["P" + str(i) for i in range(96, 101)], "P85"),
        (["ST1"], "P1"),
        (["ST2", "ST2_B", "ST3", "ST4", "ST5", "ST16", "ST19",
          "ST22", "ST23", "ST24"], "ST1"),
        (["ST17", "ST18"], "ST16"), (["ST20", "ST21"], "ST19"),
        (["ST25", "ST26", "ST27"], "ST2"), (["ST28", "ST29", "ST30"], "ST2_B"),
        (["ST31"], "ST23"), (["ST32"], "ST26"), (["ST33"], "ST29"),
        (["IT10"], "IT2"), (["IT11"], "IT5"), (["IT12"], "IT8"),
        (["PF" + str(i) for i in range(3, 9)], "PF1"),
        (["A2", "A3"], "A8"),
    ]
    d = {}
    for vs, den in g:
        for v in vs:
            d.setdefault(v, []).append(den)
    return d


_DEN_OK = _den_registry()
_REF  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")


def _err(msg, code=2):
    print("ERRORE: " + msg, file=sys.stderr)
    sys.exit(code)


_SSL_HELP = (
    "verifica del certificato TLS fallita. Non e' un problema del server: "
    "l'installazione di Python in uso non trova i certificati delle autorita'. "
    "Soluzioni, in ordine: (1) su macOS con Python da python.org, eseguire "
    "'Install Certificates.command' nella cartella /Applications/Python 3.x/; "
    "(2) installare certifi con 'pip3 install certifi', questo script lo usa "
    "automaticamente se presente; (3) dietro proxy aziendale, indicare il CA "
    "bundle con 'export SSL_CERT_FILE=/percorso/ca.pem'. "
    "Non disattivare la verifica del certificato: i dati sono pubblici ma il "
    "canale va comunque autenticato. In alternativa, se hai gli shard su disco, "
    "usa 'export CRUSCOTTO_BASE=/percorso/ai/dati'."
)


_SWEEP_HELP = (
    "limite di sicurezza raggiunto: gia' {n} comuni distinti scaricati negli "
    "ultimi {m} minuti. Questa skill serve per singoli comuni e per confronti "
    "fra pochi, non per scaricare interi territori. Il server applica un limite "
    "di richieste e blocca per un'ora gli indirizzi che lo superano. "
    "Non esistono dati aggregati per regione, provincia o area nazionale, e non "
    "vanno ricostruiti scaricando tutti i comuni di un territorio: se serve un "
    "aggregato, va detto all'utente che non e' disponibile in questa forma. "
    "Non alzare CRUSCOTTO_MAX_COMUNI per aggirare questo limite: riferisci "
    "invece il problema all'utente."
)


def _guard(path):
    """Throttle persistente e guardia anti-sweep, prima di ogni richiesta di rete.

    Lo stato sta su file, non in memoria: ogni invocazione della CLI e' un
    processo nuovo, e il caso che ha motivato questa guardia era proprio uno
    sciame di processi paralleli che saturavano il burst di nginx nello stesso
    secondo. Il lock esclusivo li mette in coda invece di sommarli.
    Inattivo in modalita' filesystem locale: li' non c'e' nessun server da
    proteggere.
    """
    if BASE.startswith("/"):
        return
    os.makedirs(CACHE, exist_ok=True)
    m = re.search(r"/(\d{6})[._]", "/" + path)
    istat = m.group(1) if m else None
    try:
        import fcntl
    except ImportError:
        fcntl = None
    f = open(os.path.join(CACHE, ".guard.json"), "a+", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            st = json.loads(f.read() or "{}")
        except Exception:
            st = {}
        now = time.time()
        com = {k: v for k, v in st.get("comuni", {}).items() if now - v < WINDOW}
        if istat and istat not in com and len(com) >= MAXCOM:
            _err(_SWEEP_HELP.format(n=len(com), m=int(WINDOW / 60)))
        attesa = st.get("last", 0) + MININT - now
        if attesa > 0:
            time.sleep(attesa)
            now = time.time()
        if istat:
            com[istat] = now
        f.seek(0)
        f.truncate()
        f.write(json.dumps({"last": now, "comuni": com}))
        f.flush()
    finally:
        if fcntl:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except Exception:
                pass
        f.close()


def _ssl_ctx():
    """Contesto TLS.

    Parte sempre dallo store di sistema, che onora anche SSL_CERT_FILE e
    SSL_CERT_DIR. Solo se lo store risulta vuoto - il caso del Python di
    python.org su macOS, che non aggancia i certificati di sistema - aggiunge
    il bundle di certifi, se installato. Mai in sostituzione: lo store di
    sistema contiene anche le eventuali CA aziendali, certifi no.
    """
    ctx = ssl.create_default_context()
    try:
        if ctx.cert_store_stats().get("x509_ca", 0) == 0:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def fetch(path):
    """Scarica (o legge) uno shard. Ritorna oggetto Python."""
    if BASE.startswith("/"):
        fp = os.path.join(BASE, path)
        if not os.path.exists(fp):
            _err("shard non trovato: " + fp)
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    url = BASE.rstrip("/") + "/" + path
    key = hashlib.sha256(url.encode()).hexdigest()[:24] + ".json"
    cf  = os.path.join(CACHE, key)
    if os.path.exists(cf) and (time.time() - os.path.getmtime(cf)) < TTL:
        with open(cf, encoding="utf-8") as f:
            return json.load(f)
    _guard(path)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cruscotto-cli/0.1.2"})
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        _err("HTTP " + str(e.code) + " su " + url)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            _err(_SSL_HELP)
        _err("rete: " + str(reason))
    except Exception as e:
        _err("rete: " + str(e))
    os.makedirs(CACHE, exist_ok=True)
    with open(cf, "w", encoding="utf-8") as f:
        f.write(raw)
    return json.loads(raw)


def out(obj, nota=None):
    """Stampa JSON con cap hard. Oltre il cap tronca e suggerisce un filtro."""
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    if len(s) > CAP:
        s = s[:CAP]
        print(s)
        print("\n--- OUTPUT TRONCATO a " + str(CAP) + " byte ---")
        print("Restringi la query: usa --top N, un filtro specifico, oppure il comando q con un path.")
        return
    print(s)
    if nota:
        print("\n# " + nota)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def shrink(o, top):
    """Tronca ricorsivamente ogni lista a 'top' elementi."""
    if isinstance(o, list):
        r = [shrink(x, top) for x in o[:top]]
        if len(o) > top:
            r.append({"_troncato": str(top) + " di " + str(len(o))})
        return r
    if isinstance(o, dict):
        return {k: shrink(v, top) for k, v in o.items()}
    return o


def dashboard(istat):
    return fetch("dashboard/" + istat + ".json")


def load_index():
    return fetch("lookup/comuni-index.json")


def resolve(token):
    """Accetta ISTAT a 6 cifre oppure nome comune. Ritorna codice ISTAT."""
    if re.fullmatch(r"\d{6}", token):
        return token
    q = norm(token)
    idx = load_index()
    esatti = [c for c in idx if norm(c["n"]) == q]
    if len(esatti) == 1:
        return esatti[0]["i"]
    cand = esatti or [c for c in idx if q in norm(c["n"])]
    if not cand:
        _err("nessun comune corrispondente a: " + token)
    if len(cand) == 1:
        return cand[0]["i"]
    righe = [c["i"] + "  " + c["n"] + " (" + c["p"] + ", " + c["r"] + ")" for c in cand[:20]]
    _err("ambiguo (" + str(len(cand)) + " risultati), usa il codice ISTAT:\n  " + "\n  ".join(righe))


# ---------------------------------------------------------------- comandi

def cmd_find(a):
    q = norm(a.nome)
    idx = load_index()
    res = [c for c in idx if q in norm(c["n"])]
    if not res:
        _err("nessun risultato per: " + a.nome)
    for c in res[:a.top]:
        print(c["i"] + "  " + c["n"] + " (" + c["p"] + ", " + c["r"] + ")")
    if len(res) > a.top:
        print("... " + str(len(res) - a.top) + " altri risultati")


def cmd_kpi(a):
    d = dashboard(resolve(a.comune))
    out({"anagrafica": d.get("anagrafica"),
         "kpi_summary": d.get("kpi_summary"),
         "_generated_at": d.get("_generated_at")})


def cmd_sezioni(a):
    d = dashboard(resolve(a.comune))
    for k, v in d.items():
        if k.startswith("_"):
            continue
        n = len(json.dumps(v, ensure_ascii=False))
        flag = "  [PESANTE: richiede --top o q]" if k in HEAVY else ""
        print(k.ljust(20) + str(n).rjust(9) + " byte" + flag)


def cmd_sez(a):
    d = dashboard(resolve(a.comune))
    if a.sezione not in d:
        _err("sezione inesistente. Disponibili: " + ", ".join(k for k in d if not k.startswith("_")))
    v = d[a.sezione]
    if a.sezione in HEAVY and not a.top:
        _err("sezione pesante (" + str(len(json.dumps(v, ensure_ascii=False))) + " byte). "
             "Usa --top N per limitare le liste, oppure il comando q con un path.")
    out(shrink(v, a.top) if a.top else v)


def cmd_q(a):
    node = dashboard(resolve(a.comune))
    for tok in a.path.split("."):
        m = re.fullmatch(r"([^\[\]]*)\[(\d+)\]", tok)
        idx = None
        if m:
            tok, idx = m.group(1), int(m.group(2))
        if tok:
            if not isinstance(node, dict) or tok not in node:
                _err("path non risolvibile al segmento: " + tok)
            node = node[tok]
        if idx is not None:
            if not isinstance(node, list) or idx >= len(node):
                _err("indice fuori range: " + str(idx))
            node = node[idx]
    out(node)


# ---------------------------------------------------------------- full

def cmd_full_beni(a, istat):
    d = fetch("beni_culturali_full/" + istat + ".json")
    if a.kpi:
        return out(d.get("kpi"))
    rec = d.get("luoghi", [])
    if a.categoria:
        rec = [x for x in rec if norm(x.get("categoria", "")) == norm(a.categoria)]
    if a.find:
        q = norm(a.find)
        rec = [x for x in rec if q in norm(x.get("denom", "")) or q in norm(x.get("indirizzo", ""))]
    if not (a.categoria or a.find):
        _err("filtro obbligatorio: --kpi, --categoria X oppure --find TESTO")
    for x in rec:
        if not a.desc:
            x.pop("descrizione", None)
        elif isinstance(x.get("descrizione"), str) and len(x["descrizione"]) > 200:
            x["descrizione"] = x["descrizione"][:200] + "..."
    out(rec[:a.top], nota=str(len(rec)) + " record corrispondenti")


def cmd_full_anncsu(a, istat):
    d = fetch("anncsu_full/" + istat + ".json")
    if a.kpi:
        return out(d.get("kpi"))
    if not a.via:
        _err("filtro obbligatorio: --kpi oppure --via NOME [--civico N]")
    q = norm(a.via)
    rec = [x for x in d.get("punti", []) if q in norm(x.get("odo", ""))]
    if a.civico:
        rec = [x for x in rec if str(x.get("civ", "")) == str(a.civico)]
    out(rec[:a.top], nota=str(len(rec)) + " civici corrispondenti")


def cmd_full_censimento(a, istat):
    d = fetch("censimento_full/" + istat + ".geojson")
    feats = [f["properties"] for f in d.get("features", [])]
    try:
        with open(os.path.join(_REF, "censimento_vars.json"), encoding="utf-8") as f:
            lab = json.load(f)
    except Exception:
        lab = {}
    if a.kpi:
        con = [p for p in feats if p.get("vars")]
        return out({"n_sezioni": len(feats), "n_sezioni_con_vars": len(con),
                    "n_sezioni_no_vars": len(feats) - len(con),
                    "pop_totale_P1": sum(p["vars"].get("P1", 0) for p in con),
                    "area_mq_totale": round(sum(p.get("area_mq") or 0 for p in feats), 1)})
    if a.sez:
        for p in feats:
            if str(p.get("sez")) == str(a.sez) or str(p.get("id")) == str(a.sez):
                v = p.pop("vars", {})
                p["vars"] = {k + " (" + lab.get(k, "?") + ")": val for k, val in v.items()}
                return out(p)
        _err("sezione non trovata: " + str(a.sez))
    if a.var:
        if a.var not in lab:
            _err("variabile sconosciuta: " + a.var)
        if a.den:
            if a.den not in lab:
                _err("denominatore sconosciuto: " + a.den)
            ok = _DEN_OK.get(a.var, [])
            if a.den not in ok and not a.force_den:
                sug = ("Denominatori leciti per " + a.var + ": " + ", ".join(ok) + "."
                       ) if ok else ("Per " + a.var + " non esiste un denominatore "
                                     "lecito fra le 127 variabili censuarie.")
                _err("rapporto non comparabile: " + a.var + " non e' un sottoinsieme di "
                     + a.den + ". " + sug
                     + " Usa --force-den solo se la comparabilita' e' verificata a parte.")
        con = [p for p in feats if p.get("vars")]
        rows, scartate = [], 0
        for p in con:
            val = p["vars"].get(a.var, 0)
            r = {"sez": p.get("sez"), "valore": val}
            if a.den:
                dv = p["vars"].get(a.den, 0)
                if dv < a.min_den:
                    scartate += 1
                    continue
                r["denominatore"] = dv
                r["pct"] = round(val / dv * 100, 2) if dv else None
            rows.append(r)
        key = (lambda r: (r["pct"] is None, -(r["pct"] or 0))) if a.den else (lambda r: -r["valore"])
        rows.sort(key=key)
        return out({"variabile": a.var + " = " + lab[a.var],
                    "denominatore": (a.den + " = " + lab.get(a.den, "?")) if a.den else None,
                    "sezioni_escluse_no_vars": len(feats) - len(con),
                    "sezioni_escluse_min_den": scartate,
                    "_denominatore_forzato": (
                        "ATTENZIONE: rapporto fuori registro, comparabilita' non garantita"
                        if (a.den and a.force_den and a.den not in _DEN_OK.get(a.var, []))
                        else None),
                    "_avviso": ("denominatori piccoli non filtrati: la percentuale puo' essere "
                                "statisticamente irrilevante, valuta --min-den") if (a.den and not a.min_den) else None,
                    "top": rows[:a.top]})
    _err("filtro obbligatorio: --kpi, --sez ID oppure --var CODICE [--den CODICE]")


def cmd_full(a):
    istat = resolve(a.comune)
    {"beni": cmd_full_beni, "anncsu": cmd_full_anncsu, "censimento": cmd_full_censimento}[a.fonte](a, istat)


def cmd_vars(a):
    with open(os.path.join(_REF, "censimento_vars.json"), encoding="utf-8") as f:
        lab = json.load(f)
    q = norm(a.cerca) if a.cerca else None
    for k, v in lab.items():
        if q is None or q in norm(v) or q in norm(k):
            print(k.ljust(8) + v)


def main():
    p = argparse.ArgumentParser(prog="cruscotto", description="CLI dati Cruscotto Italia")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("find", help="cerca comune per nome -> codice ISTAT")
    x.add_argument("nome"); x.add_argument("--top", type=int, default=15)
    x.set_defaults(fn=cmd_find)

    x = s.add_parser("kpi", help="anagrafica + kpi_summary (economico)")
    x.add_argument("comune"); x.set_defaults(fn=cmd_kpi)

    x = s.add_parser("sezioni", help="elenca le sezioni disponibili con il peso")
    x.add_argument("comune"); x.set_defaults(fn=cmd_sezioni)

    x = s.add_parser("sez", help="stampa una sezione del dashboard")
    x.add_argument("comune"); x.add_argument("sezione")
    x.add_argument("--top", type=int, default=0, help="tronca ogni lista a N elementi")
    x.set_defaults(fn=cmd_sez)

    x = s.add_parser("q", help="path dot-notation nel dashboard, es. siope.anni[0]")
    x.add_argument("comune"); x.add_argument("path"); x.set_defaults(fn=cmd_q)

    x = s.add_parser("full", help="shard estesi: beni | anncsu | censimento")
    x.add_argument("comune"); x.add_argument("fonte", choices=["beni", "anncsu", "censimento"])
    x.add_argument("--kpi", action="store_true")
    x.add_argument("--top", type=int, default=20)
    x.add_argument("--categoria"); x.add_argument("--find"); x.add_argument("--desc", action="store_true")
    x.add_argument("--via"); x.add_argument("--civico")
    x.add_argument("--sez"); x.add_argument("--var"); x.add_argument("--den")
    x.add_argument("--min-den", dest="min_den", type=int, default=0,
                   help="scarta le sezioni con denominatore < N (evita pct su basi minime)")
    x.add_argument("--force-den", dest="force_den", action="store_true",
                   help="forza un rapporto fuori registro (sconsigliato)")
    x.set_defaults(fn=cmd_full)

    x = s.add_parser("vars", help="dizionario delle 127 variabili censuarie")
    x.add_argument("cerca", nargs="?"); x.set_defaults(fn=cmd_vars)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    except KeyboardInterrupt:
        os._exit(130)
