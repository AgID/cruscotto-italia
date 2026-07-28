# -*- coding: utf-8 -*-
"""Mappatura naturale->codice delle 127 variabili censuarie (Variabili censuarie 2023).
Dizionario label da frontend comune.html (CENSIMENTO_FAMILIES). Matcher deterministico:
token-chiave della label (tolti marcatori aggregato) sottoinsieme della domanda; vince
il match piu' specifico; sesso e sinonimi normalizzati. Nessun ripiego: se nessun match,
ritorna None (la salvaguardia in _valore_sezione dichiara 'non disponibile')."""
import re, unicodedata

CENS = CENS = {"P1": "Popolazione residente totale","P2": "Popolazione residente maschi","P3": "Popolazione residente femmine","P14": "< 5 anni","P15": "5–9","P16": "10–14","P17": "15–19","P18": "20–24","P19": "25–29","P20": "30–34","P21": "35–39","P22": "40–44","P23": "45–49","P24": "50–54","P25": "55–59","P26": "60–64","P27": "65–69","P28": "70–74","P29": "≥ 75 anni","P30": "< 5 anni M","P31": "5–9 M","P32": "10–14 M","P33": "15–19 M","P34": "20–24 M","P35": "25–29 M","P36": "30–34 M","P37": "35–39 M","P38": "40–44 M","P39": "45–49 M","P40": "50–54 M","P41": "55–59 M","P42": "60–64 M","P43": "65–69 M","P44": "70–74 M","P45": "≥ 75 anni M","P67": "< 5 anni F","P68": "5–9 F","P69": "10–14 F","P70": "15–19 F","P71": "20–24 F","P72": "25–29 F","P73": "30–34 F","P74": "35–39 F","P75": "40–44 F","P76": "45–49 F","P77": "50–54 F","P78": "55–59 F","P79": "60–64 F","P80": "65–69 F","P81": "70–74 F","P82": "≥ 75 anni F","P83": "Pop. 9+ totale","P84": "Pop. 9+ maschi","P85": "Pop. 9+ femmine","P86": "Nessun titolo","P87": "Licenza elementare","P88": "Licenza media","P89": "Diploma scuola superiore","P90": "Titolo terziario (laurea o sup.)","P91": "Nessun titolo (M)","P92": "Elementare (M)","P93": "Media (M)","P94": "Diploma (M)","P95": "Terziario (M)","P96": "Nessun titolo (F)","P97": "Elementare (F)","P98": "Media (F)","P99": "Diploma (F)","P100": "Terziario (F)","P101": "Occupati 15–64 totali","P102": "Occupati 15–64 maschi","P103": "Occupati 15–64 femmine","IT1": "Italiani 0–14","IT2": "Italiani 15–64","IT3": "Italiani 65+","IT4": "Italiani 0–14 (M)","IT5": "Italiani 15–64 (M)","IT6": "Italiani 65+ (M)","IT7": "Italiani 0–14 (F)","IT8": "Italiani 15–64 (F)","IT9": "Italiani 65+ (F)","IT10": "Occupati italiani 15–64 totali","IT11": "Occupati italiani 15–64 (M)","IT12": "Occupati italiani 15–64 (F)","ST1": "Stranieri totali","ST2": "Stranieri maschi","ST2_B": "Stranieri femmine","ST3": "Stranieri 0–29","ST4": "Stranieri 30–54","ST5": "Stranieri 55+","ST16": "Stranieri UE totali","ST17": "Stranieri UE (M)","ST18": "Stranieri UE (F)","ST19": "Stranieri Extra-UE totali","ST20": "Stranieri Extra-UE (M)","ST21": "Stranieri Extra-UE (F)","ST22": "Stranieri 0–14 totali","ST23": "Stranieri 15–64 totali","ST24": "Stranieri 65+ totali","ST25": "Stranieri 0–14 (M)","ST26": "Stranieri 15–64 (M)","ST27": "Stranieri 65+ (M)","ST28": "Stranieri 0–14 (F)","ST29": "Stranieri 15–64 (F)","ST30": "Stranieri 65+ (F)","ST31": "Occupati stranieri 15–64 totali","ST32": "Occupati stranieri 15–64 (M)","ST33": "Occupati stranieri 15–64 (F)","PF1": "Famiglie totali","PF3": "Famiglie 1 componente","PF4": "Famiglie 2 componenti","PF5": "Famiglie 3 componenti","PF6": "Famiglie 4 componenti","PF7": "Famiglie 5 componenti","PF8": "Famiglie 6+ componenti","PF9": "Famiglie coabitanti","A2": "Abitazioni occupate da residenti","A3": "Abitazioni vuote o solo non-residenti","A5": "Altro tipo di alloggi occupati","A8": "Abitazioni totali","NA1": "Automobili di proprietà","EM1": "Italiani dalla nascita nati in Italia","EM2": "Italiani dalla nascita nati all'estero","EM3": "Italiani acquisiti nati in Italia","EM4": "Italiani acquisiti nati all'estero","EM5": "Stranieri e apolidi nati in Italia","EM6": "Stranieri e apolidi nati all'estero"}


_LBL_STOP = {"totali","totale","residente","residenti","anni","scuola","superiore",
             "o","sup","laurea","solo","non-residenti","da","di","e","oltre","pop",
             "titolo","licenza",
             # aggiunte 2023: connettivi delle nuove label (EM*, A5, NA1).
             # NB "apolidi" e stopword perche' e sempre accoppiato a "stranieri":
             # senza questo, "stranieri nati in italia" non raggiungeva EM5 e
             # ripiegava su ST1 (Stranieri totali), altra grandezza.
             "dalla","tipo","altro","in","all","apolidi"}
_SYN = [
    (r"\blaureati?\b", "terziario"), (r"\blaurea\b", "terziario"),
    (r"\bdiplomati?\b", "diploma"),
    (r"\blicenza media\b|\bmedie\b|\bscuola media\b", "media"),
    (r"\blicenza elementare\b|\belementari?\b", "elementare"),
    (r"\banalfabeti?\b|\bsenza titolo\b", "nessun titolo"),
    (r"\bextra-?comunitari?\b|\bextra ?ue\b|\bextraue\b", "extra-ue"),
    (r"\bcomunitari?\b", "ue"),
    (r"\buomini\b", "maschi"), (r"\bdonne\b", "femmine"),
    (r"\bbambini\b|\bminori\b", "0-14"),
    (r"\banziani\b|\bover ?65\b", "65+"),
    (r"\blavoratori?\b", "occupati"),
    (r"\babitanti\b|\bresidenti\b|\bcittadini\b", "popolazione"),
]

# ALIAS ESPLICITI (registro chiuso, nessuna euristica generica).
# Servono per le variabili la cui label completa non compare mai nel parlato:
# nessuno chiede "automobili di proprieta", si chiede "quante auto".
# Ogni voce e una lista di set di token: match se un set e sottoinsieme
# della domanda normalizzata.
_ALIAS = {
    "NA1": [{"automobili"}, {"auto"}, {"autovetture"}, {"macchine"}],
    "PF9": [{"coabitanti"}, {"coabitazione"}],
    "A5":  [{"alloggi"}],
    "EM1": [{"italiani", "nati", "italia"}],
    "EM2": [{"italiani", "nati", "estero"}],
    "EM3": [{"acquisiti", "italia"}, {"acquisita", "italia"}],
    "EM4": [{"acquisiti", "estero"}, {"acquisita", "estero"}],
    "EM5": [{"stranieri", "nati", "italia"}],
    "EM6": [{"stranieri", "nati", "estero"}],
    # occupati: la label pretende il token "15-64", che nel parlato non c e mai.
    "P101": [{"occupati"}],
    "P102": [{"occupati", "maschi"}],
    "P103": [{"occupate"}, {"occupati", "femmine"}],
}
# Alias GENERICI aggiunti in coda alle liste EM: se la domanda cita solo
# "estero" o "nati", i tre codici concorrono alla pari e match_cens ritorna
# {"ambiguo": [...]} invece di None, cosi il chiamante puo chiedere quale.
# NB vanno IN CODA: il primo alias che matcha vince, quindi quelli specifici
# ({"italiani","nati","estero"}) devono restare in testa.
for _c in ("EM2", "EM4", "EM6"):
    _ALIAS[_c].append({"estero"})
for _c in ("EM1", "EM3", "EM5"):
    _ALIAS[_c].append({"nati", "italia"})

# GUARDIA ANTI-RIPIEGO. Se la domanda contiene un marcatore di famiglia ma
# nessun candidato appartiene a quella famiglia, si ritorna None invece di
# lasciar vincere una label piu' corta e piu' generica. Senza questa guardia
# il matcher rispondeva con un numero di un ALTRA grandezza, che e peggio di
# un "non disponibile" su un cruscotto istituzionale.
_EM_ALL = {"EM1", "EM2", "EM3", "EM4", "EM5", "EM6"}
_MARKER_FAMIGLIA = {
    "nati": _EM_ALL, "nascita": _EM_ALL, "estero": _EM_ALL,
    "coabitanti": {"PF9"},
    "automobili": {"NA1"}, "auto": {"NA1"}, "autovetture": {"NA1"},
    "alloggi": {"A5"},
}

def _norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("\u2013","-").replace("\u2014","-").replace("\u2212","-")
    s = s.replace("\u2265","").replace("\u2264","").replace("<"," meno ")
    s = re.sub(r"[(),.:;'’]", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _sesso_query(q):
    if re.search(r"\bmaschi\b|\bmaschio\b|\bm\b", q): return "M"
    if re.search(r"\bfemmine\b|\bfemmina\b|\bf\b", q): return "F"
    return None

def _sesso_label(lab):
    toks = lab.split()
    if "m" in toks or "maschi" in toks: return "M"
    if "f" in toks or "femmine" in toks: return "F"
    return "T"

def _key_tokens(lab):
    return set(t for t in lab.split() if t and t not in _LBL_STOP)

def match_cens(domanda, vars_presenti):
    """domanda: testo utente. vars_presenti: dict vars della sezione (chiavi=codici).
    Ritorna {'codice','label'} | {'ambiguo':[(c,l),...]} | None."""
    if not domanda:
        return None
    q = _norm(domanda)
    for pat, repl in _SYN:
        q = re.sub(pat, repl, q)
    q = re.sub(r"\s+", " ", q).strip()
    q_sex = _sesso_query(" " + q + " ")
    qset = set(q.split())
    if q_sex == "M": qset.add("m")
    if q_sex == "F": qset.add("f")
    cand = []
    for code, lab in CENS.items():
        if code not in vars_presenti:
            continue
        nl = _norm(lab)
        kt = _key_tokens(nl)
        if kt and kt <= qset:
            cand.append((code, lab, _sesso_label(nl), len(kt)))
    for code, alias_list in _ALIAS.items():
        if code not in vars_presenti or any(c[0] == code for c in cand):
            continue
        for al in alias_list:
            if al <= qset:
                nl = _norm(CENS[code])
                cand.append((code, CENS[code], _sesso_label(nl), len(al)))
                break
    for _marker, _fam in _MARKER_FAMIGLIA.items():
        if _marker in qset and not any(c[0] in _fam for c in cand):
            return None
    if not cand:
        # sesso "nudo" senza categoria esplicita -> popolazione per sesso (P2/P3)
        _CATEG = {"stranieri","straniero","straniere","italiani","italiano","famiglie",
                  "famiglia","abitazioni","abitazione","occupati","occupato","terziario",
                  "diploma","media","elementare","nessun",
                  # senza "occupate"/"occupata" la domanda "donne occupate" cadeva
                  # nel ripiego sesso-nudo e tornava P3 (popolazione femmine),
                  # cioe una grandezza diversa da quella chiesta.
                  "occupate","occupata"}
        if q_sex and not (qset & _CATEG):
            code = "P2" if q_sex == "M" else "P3"
            if code in vars_presenti:
                return {"codice": code, "label": CENS[code]}
        return None
    if q_sex:
        sx = [c for c in cand if c[2] == q_sex]
        if sx: cand = sx
    else:
        t = [c for c in cand if c[2] == "T"]
        if t: cand = t
    cand.sort(key=lambda c: (-c[3], c[0]))
    best = cand[0]
    top = [c for c in cand if c[3] == best[3] and c[2] == best[2]]
    if len(top) > 1:
        return {"ambiguo": [(c[0], c[1]) for c in top]}
    return {"codice": best[0], "label": best[1]}

CATEGORIE_DISPONIBILI = ("popolazione (totale/M/F, per fascia d'eta')",
    "stranieri (totali/UE/extra-UE, per fascia d'eta' e sesso)", "italiani per fascia d'eta'",
    "titolo di studio (nessuno/elementare/media/diploma/terziario)",
    "occupati 15-64", "famiglie per numero di componenti e famiglie coabitanti",
    "abitazioni occupate/vuote/totali e altri alloggi",
    "paese di nascita e acquisizione della cittadinanza", "automobili di proprieta")
