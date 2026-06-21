# -*- coding: utf-8 -*-
"""Mappatura naturale->codice delle 119 variabili censuarie (Basi Territoriali 2021).
Dizionario label da frontend comune.html (CENSIMENTO_FAMILIES). Matcher deterministico:
token-chiave della label (tolti marcatori aggregato) sottoinsieme della domanda; vince
il match piu' specifico; sesso e sinonimi normalizzati. Nessun ripiego: se nessun match,
ritorna None (la salvaguardia in _valore_sezione dichiara 'non disponibile')."""
import re, unicodedata

CENS = CENS = {"P1": "Popolazione residente totale","P2": "Popolazione residente maschi","P3": "Popolazione residente femmine","P14": "< 5 anni","P15": "5–9","P16": "10–14","P17": "15–19","P18": "20–24","P19": "25–29","P20": "30–34","P21": "35–39","P22": "40–44","P23": "45–49","P24": "50–54","P25": "55–59","P26": "60–64","P27": "65–69","P28": "70–74","P29": "≥ 75 anni","P30": "< 5 anni M","P31": "5–9 M","P32": "10–14 M","P33": "15–19 M","P34": "20–24 M","P35": "25–29 M","P36": "30–34 M","P37": "35–39 M","P38": "40–44 M","P39": "45–49 M","P40": "50–54 M","P41": "55–59 M","P42": "60–64 M","P43": "65–69 M","P44": "70–74 M","P45": "≥ 75 anni M","P67": "< 5 anni F","P68": "5–9 F","P69": "10–14 F","P70": "15–19 F","P71": "20–24 F","P72": "25–29 F","P73": "30–34 F","P74": "35–39 F","P75": "40–44 F","P76": "45–49 F","P77": "50–54 F","P78": "55–59 F","P79": "60–64 F","P80": "65–69 F","P81": "70–74 F","P82": "≥ 75 anni F","P83": "Pop. 9+ totale","P84": "Pop. 9+ maschi","P85": "Pop. 9+ femmine","P86": "Nessun titolo","P87": "Licenza elementare","P88": "Licenza media","P89": "Diploma scuola superiore","P90": "Titolo terziario (laurea o sup.)","P91": "Nessun titolo (M)","P92": "Elementare (M)","P93": "Media (M)","P94": "Diploma (M)","P95": "Terziario (M)","P96": "Nessun titolo (F)","P97": "Elementare (F)","P98": "Media (F)","P99": "Diploma (F)","P100": "Terziario (F)","P101": "Occupati 15–64 totali","P102": "Occupati 15–64 maschi","P103": "Occupati 15–64 femmine","IT1": "Italiani 0–14","IT2": "Italiani 15–64","IT3": "Italiani 65+","IT4": "Italiani 0–14 (M)","IT5": "Italiani 15–64 (M)","IT6": "Italiani 65+ (M)","IT7": "Italiani 0–14 (F)","IT8": "Italiani 15–64 (F)","IT9": "Italiani 65+ (F)","IT10": "Occupati italiani 15–64 totali","IT11": "Occupati italiani 15–64 (M)","IT12": "Occupati italiani 15–64 (F)","ST1": "Stranieri totali","ST2": "Stranieri maschi","ST2_B": "Stranieri femmine","ST3": "Stranieri 0–29","ST4": "Stranieri 30–54","ST5": "Stranieri 55+","ST16": "Stranieri UE totali","ST17": "Stranieri UE (M)","ST18": "Stranieri UE (F)","ST19": "Stranieri Extra-UE totali","ST20": "Stranieri Extra-UE (M)","ST21": "Stranieri Extra-UE (F)","ST22": "Stranieri 0–14 totali","ST23": "Stranieri 15–64 totali","ST24": "Stranieri 65+ totali","ST25": "Stranieri 0–14 (M)","ST26": "Stranieri 15–64 (M)","ST27": "Stranieri 65+ (M)","ST28": "Stranieri 0–14 (F)","ST29": "Stranieri 15–64 (F)","ST30": "Stranieri 65+ (F)","ST31": "Occupati stranieri 15–64 totali","ST32": "Occupati stranieri 15–64 (M)","ST33": "Occupati stranieri 15–64 (F)","PF1": "Famiglie totali","PF3": "Famiglie 1 componente","PF4": "Famiglie 2 componenti","PF5": "Famiglie 3 componenti","PF6": "Famiglie 4 componenti","PF7": "Famiglie 5 componenti","PF8": "Famiglie 6+ componenti","A2": "Abitazioni occupate da residenti","A3": "Abitazioni vuote o solo non-residenti","A8": "Abitazioni totali","E3": "Edifici residenziali"}


_LBL_STOP = {"totali","totale","residente","residenti","anni","scuola","superiore",
             "o","sup","laurea","solo","non-residenti","da","di","e","oltre","pop",
             "titolo","licenza"}
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

def _norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("\u2013","-").replace("\u2014","-").replace("\u2212","-")
    s = s.replace("\u2265","").replace("\u2264","").replace("<"," meno ")
    s = re.sub(r"[(),.:;]", " ", s)
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
    if not cand:
        # sesso "nudo" senza categoria esplicita -> popolazione per sesso (P2/P3)
        _CATEG = {"stranieri","straniero","straniere","italiani","italiano","famiglie",
                  "famiglia","abitazioni","abitazione","occupati","occupato","terziario",
                  "diploma","media","elementare","nessun","edifici"}
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
    "occupati 15-64", "famiglie per numero di componenti", "abitazioni occupate/vuote/totali")
