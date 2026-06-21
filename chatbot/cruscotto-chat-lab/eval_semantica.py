# -*- coding: utf-8 -*-
"""
eval_semantica.py — Eval OFFLINE del dizionario semantico (TASK 4).

Rete anti-regressione che NON richiede Ollama: importa semantica.py e verifica
la logica deterministica (coerenza chiavi, _meta senza cifre, firing delle
dimensioni IT/EN, nota_assenza_diretta). La verbalizzazione LLM resta da
verificare a parte (live, sulla VM).

Uso: dalla cartella del chatbot ->  python3 eval_semantica.py
Exit code 1 se ci sono FAIL "hard" (regressioni vere).
"""
import re
import sys

import semantica as S

FAIL = []
INFO = []
CIFRA = re.compile(r"\d")


def hard(cond, msg):
    if cond:
        print("PASS  ", msg)
    else:
        FAIL.append(msg)
        print("FAIL  ", msg)


def info(msg):
    INFO.append(msg)
    print("INFO  ", msg)


# --------------------------------------------------------------------------- #
# chiavi GRAMMAR: import diretto, con fallback a parse statico se intent_engine
# non e' importabile nel sandbox (dipendenze runtime/I-O).
# --------------------------------------------------------------------------- #
def grammar_keys():
    try:
        import intent_engine as ie
        return set(ie.GRAMMAR.keys())
    except Exception as e:
        print("(intent_engine non importabile: %s -> parse statico di GRAMMAR)"
              % type(e).__name__)
        txt = open("intent_engine.py", encoding="utf-8").read()
        m = re.search(r"GRAMMAR\s*=\s*\{(.*?)\n\}", txt, re.S)
        body = m.group(1) if m else txt
        return set(re.findall(r'^\s{4}"([a-z_]+)":', body, re.M))


GK = grammar_keys()

# --------------------------------------------------------------------------- #
# TEST 1 — coerenza SEMANTICA <-> GRAMMAR (il self-check, eseguito offline)
# --------------------------------------------------------------------------- #
print("\n--- TEST 1: coerenza chiavi ---")
morte, scoperte = S.verifica_coerenza(GK)
hard(not morte, "nessuna chiave morta in SEMANTICA (trovate: %s)" % (morte or "—"))
if scoperte:
    info("sezioni GRAMMAR senza voce semantica: %s" % sorted(scoperte))

# --------------------------------------------------------------------------- #
# TEST 2 — _meta SENZA cifre, su tutte le voci, IT + EN (vincolo §3.1)
# --------------------------------------------------------------------------- #
print("\n--- TEST 2: _meta senza cifre (24 voci x IT/EN) ---")
viol = []
for sez in S.SEMANTICA:
    for lang in ("it", "en"):
        meta = S.meta_dati(sez, lang) or {}
        for k, v in meta.items():
            if isinstance(v, str) and CIFRA.search(v):
                viol.append("%s/%s/%s=%r" % (sez, lang, k, v))
hard(not viol, "nessuna cifra nei campi _meta (violazioni: %s)" % (viol or "—"))

# --------------------------------------------------------------------------- #
# TEST 3 — firing delle dimensioni (_DISPATCH). IT = hard, EN = info (degrado)
# --------------------------------------------------------------------------- #
print("\n--- TEST 3: dimensioni motore (IT hard / EN info) ---")
casi = [
    ("turismo", {"sezione": "turismo", "operazione": "valore"},
     {"capacita_comune": {"totale_letti": 100, "totale_strutture": 5}},
     "quanti posti letto", "how many beds", "turismo/capacita"),
    ("turismo", {"sezione": "turismo", "operazione": "valore"},
     {"capacita_comune": {"totale_letti": 100}, "flussi_provincia": {"arrivi": 1}},
     "arrivi e presenze turistiche", "arrivals and overnight stays", "turismo/flussi"),
    ("opere", {"sezione": "opere", "operazione": "conta"},
     {"n_totale": 135, "n_attivi": 135, "n_conclusi": 0},
     "quante opere pubbliche", "how many public works", "opere/attivi"),
    ("opere", {"sezione": "opere", "operazione": "conta"},
     {"n_totale": 135, "n_attivi": 135, "n_conclusi": 0},
     "quante opere completate", "how many completed works", "opere/conclusi"),
    ("immobili_pa", {"sezione": "immobili_pa", "operazione": "conta"},
     {"tema": "uso_terzi", "pct_uso_terzi": 0.2, "n_totale": 5097,
      "stima_immobili_uso_terzi": 10},
     "quanti immobili dati a terzi", "how many properties to third parties",
     "immobili/uso_terzi"),
    ("incidenti", {"sezione": "incidenti", "operazione": "valore"},
     {"morti": 4, "feriti": 663, "incidenti": 509, "anno": 2024},
     "quanti morti negli incidenti", "how many killed in road accidents",
     "incidenti/mortali"),
    ("incidenti", {"sezione": "incidenti", "operazione": "valore"},
     {"morti": 4, "feriti": 663, "incidenti": 509, "anno": 2024},
     "quanti feriti negli incidenti", "how many injured in road accidents",
     "incidenti/feriti"),
]
for sez, intt, dati, dit, den, lab in casi:
    git = S.costruisci_extra(sez, intt, dict(dati), dit, "it") or ""
    hard(bool(git.strip()), "dimensione IT %s scatta" % lab)
    gen = S.costruisci_extra(sez, intt, dict(dati), den, "en") or ""
    if gen.strip():
        print("PASS  ", "dimensione EN %s scatta" % lab)
    else:
        info("dimensione EN %s NON scatta (trigger solo IT — degrado noto)" % lab)

# --------------------------------------------------------------------------- #
# TEST 4 — nota_assenza_diretta: dichiarativa e senza cifre (filone D)
# --------------------------------------------------------------------------- #
print("\n--- TEST 4: nota_assenza_diretta ---")
trovata = 0
for sez in S.SEMANTICA:
    for lang in ("it", "en"):
        nad = S.nota_assenza_diretta(sez, lang)
        if nad:
            trovata += 1
            hard(not CIFRA.search(nad),
                 "nota_assenza_diretta %s/%s senza cifre" % (sez, lang))
info("voci con nota_assenza_diretta: %d (es. beni_culturali)" % trovata)

# --------------------------------------------------------------------------- #
print("\n=== ESITO: %d FAIL, %d INFO ===" % (len(FAIL), len(INFO)))
if FAIL:
    print("FAIL:")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
