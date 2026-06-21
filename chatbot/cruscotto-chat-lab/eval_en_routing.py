#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Eval ROUTING EN — cruscotto-chat-lab.
Misura quanto il parser intento (estrai_intento, prompt IT-only) regge su domande EN.
NON tocca le guide _vextra/motore: valuta SOLO sezione/operazione/campo prodotti dall'LLM.

USO (sulla VM AgID, dentro /home/ubuntu/cruscotto-chat-lab, stesso venv del servizio):
    python3 eval_en_routing.py
Variabili (default = quelle del servizio):
    OLLAMA_URL (default http://172.18.0.8:11434)
    CHAT_MODEL (default qwen3:32b)

Confronto: per ogni caso si controllano SOLO i campi elencati in `expect`.
Sentinella "__NONNULL__" = il campo deve essere valorizzato (non None/"").
Il comune NON viene mai verificato (irrilevante per il routing di sezione/operazione).
"""
import os, re, asyncio, json
from intent_extract import estrai_intento

OLLAMA = os.environ.get("OLLAMA_URL", "http://172.18.0.8:11434")
MODEL  = os.environ.get("CHAT_MODEL", "qwen3:32b")
NONNULL = "__NONNULL__"

# (domanda EN, expect{campo:valore-atteso}, nota sul rischio testato)
CASES = [
    # --- carburanti: diesel->gasolio_self, LPG->gpl, cheapest->ordina asc ---
    ("cheapest diesel in Lecce",
        {"sezione":"carburanti","operazione":"ordina","campo":"gasolio_self","direzione":"asc"}, "diesel->gasolio_self + cheapest->ordina asc"),
    ("average LPG price in Lecce",
        {"sezione":"carburanti","operazione":"prezzo_medio","campo":"gpl"}, "LPG->gpl + average->prezzo_medio"),
    ("list the petrol stations in Matera",
        {"sezione":"carburanti","operazione":"elenca"}, "petrol stations->carburanti elenca"),

    # --- beni_culturali: churches->chiesa, museums->museo ---
    ("how many churches in Lecce",
        {"sezione":"beni_culturali","operazione":"conta","campo":"chiesa"}, "churches->chiesa"),
    ("list the museums of Matera",
        {"sezione":"beni_culturali","operazione":"elenca","campo":"museo"}, "museums->museo"),

    # --- redditi: average income->reddito_medio, pensions->pensione ---
    ("average income in Bari",
        {"sezione":"redditi","operazione":"valore","campo":"reddito_medio"}, "average income->reddito_medio"),
    ("income from pensions in Lecce",
        {"sezione":"redditi","operazione":"valore","campo":"pensione"}, "pensions->pensione (NON profilo)"),

    # --- scuole: high->sec2, middle->sec1, primary->primaria ---
    ("how many high schools in Bari",
        {"sezione":"scuole","operazione":"conta","campo":"sec2"}, "high schools->sec2"),
    ("list the middle schools in Lecce",
        {"sezione":"scuole","operazione":"elenca","campo":"sec1"}, "middle schools->sec1"),

    # --- farmacie: hospitals->ospedale ---
    ("how many hospitals in Lecce",
        {"sezione":"farmacie","campo":"ospedale"}, "hospitals->farmacie/ospedale"),
    ("list the pharmacies in Matera",
        {"sezione":"farmacie","operazione":"elenca","campo":"farmacia"}, "pharmacies->farmacia"),

    # --- terzo_settore: volunteering->ODV ---
    ("how many volunteering associations in Matera",
        {"sezione":"terzo_settore","operazione":"conta","campo":"ODV"}, "volunteering->ODV"),

    # --- ricarica_ev ---
    ("how many EV charging points in Milan",
        {"sezione":"ricarica_ev","operazione":"conta"}, "EV charging->ricarica_ev"),

    # --- immobili_pa ---
    ("how many municipal buildings in Lecce",
        {"sezione":"immobili_pa"}, "municipal buildings->immobili_pa"),

    # --- civici ---
    ("coordinates of via Trinchese 12 in Lecce",
        {"sezione":"civici","operazione":"cerca_civico","odonimo":NONNULL,"civico":NONNULL}, "coordinates of address->cerca_civico"),
    ("cadastral parcel of piazza Duomo 1 in Lecce",
        {"sezione":"civici","operazione":"particella"}, "cadastral parcel->particella"),

    # --- opere ---
    ("list the public works in Matera",
        {"sezione":"opere","operazione":"elenca"}, "public works->opere"),

    # --- pnrr ---
    ("total PNRR funding of Matera",
        {"sezione":"pnrr","operazione":"somma"}, "total PNRR funding->somma"),

    # --- aria ---
    ("air quality in Turin",
        {"sezione":"aria","operazione":"valore","campo":"pm10"}, "air quality->aria/pm10"),

    # --- veicoli ---
    ("vehicle fleet of Palermo by Euro class",
        {"sezione":"veicoli","operazione":"valore"}, "vehicle fleet->veicoli"),

    # --- incidenti ---
    ("how many road accidents in Rome",
        {"sezione":"incidenti","operazione":"valore"}, "road accidents->incidenti"),

    # --- demografia_dettaglio (popolazione corrente + indici) ---
    ("how many inhabitants in Lecce",
        {"sezione":"demografia_dettaglio","operazione":"valore"}, "inhabitants (current)->demografia"),
    ("ageing index of Lecce",
        {"sezione":"demografia_dettaglio","operazione":"valore"}, "ageing index->demografia (NON profilo/censimento)"),

    # --- imprese (soglia + settori) ---
    ("how many companies with more than 50 employees in Lecce",
        {"sezione":"imprese","operazione":"valore","soglia_addetti":50}, "more than 50 employees->soglia_addetti=50"),
    ("which sectors do companies operate in Lecce",
        {"sezione":"imprese","operazione":"valore"}, "sectors->imprese valore"),

    # --- turismo (capacita) ---
    ("how many tourist beds in Lecce",
        {"sezione":"turismo","operazione":"valore"}, "tourist beds->turismo"),

    # --- pendolarismo ---
    ("how many commuters in Lecce",
        {"sezione":"pendolarismo","operazione":"valore"}, "commuters->pendolarismo"),

    # --- siope (totale + voce) ---
    ("how much does Bari spend",
        {"sezione":"siope","operazione":"valore"}, "municipal spending->siope valore"),
    ("how much does Matera spend on paper",
        {"sezione":"siope","operazione":"cerca_voce","nome":NONNULL}, "spend on X->cerca_voce (nome valorizzato)"),

    # --- anac ---
    ("how many public contracts in Matera",
        {"sezione":"anac","operazione":"valore"}, "public contracts->anac"),

    # --- banda_larga ---
    ("FTTH coverage in Lecce",
        {"sezione":"banda_larga","operazione":"valore"}, "FTTH coverage->banda_larga"),

    # --- territorio (rifiuti / idrogeologico) ---
    ("recycling rate in Lecce",
        {"sezione":"territorio","operazione":"valore","campo":"rifiuti"}, "recycling rate->territorio/rifiuti"),
    ("landslide risk in Lecce",
        {"sezione":"territorio","operazione":"valore","campo":"rischio_idrogeologico"}, "landslide risk->rischio_idrogeologico"),

    # --- profilo (istruzione / cittadinanza) ---
    ("how many graduates in Bari",
        {"sezione":"profilo","operazione":"valore","campo":"istruzione"}, "graduates->profilo/istruzione"),
    ("how many foreigners in Lecce",
        {"sezione":"profilo","operazione":"valore","campo":"cittadinanza"}, "foreigners->profilo/cittadinanza"),

    # --- anagrafica ---
    ("cadastral code of Lecce",
        {"sezione":"anagrafica","operazione":"valore"}, "cadastral code->anagrafica"),

    # --- censimento (tre-vie popolazione: 2021 vs 2024 vs corrente) ---
    ("population of Lecce at the 2021 census",
        {"sezione":"censimento","operazione":"valore"}, "census 2021->censimento"),
    ("population of Lecce at the 2024 census",
        {"sezione":"profilo","operazione":"valore","campo":"cittadinanza"}, "census 2024->profilo/cittadinanza"),
    ("which census section has the most foreigners in Palermo",
        {"sezione":"censimento","operazione":"ranking_sezioni","campo":"stranieri","direzione":"desc"}, "census section ranking->ranking_sezioni"),
]

def _check(intento, expect):
    miss = []
    for k, v in expect.items():
        got = intento.get(k)
        if v == NONNULL:
            if got in (None, "", "null"):
                miss.append("%s=<vuoto> (atteso valorizzato)" % k)
        elif got != v:
            miss.append("%s=%r (atteso %r)" % (k, got, v))
    return miss

async def run():
    per_sez = {}   # sezione-attesa -> [pass, tot]
    fails = []
    print("== EVAL ROUTING EN  (model=%s) ==" % MODEL)
    print("%-3s %-62s %s" % ("#", "domanda EN", "esito"))
    for i, (q, expect, nota) in enumerate(CASES, 1):
        sez_att = expect.get("sezione", "?")
        per_sez.setdefault(sez_att, [0, 0])
        per_sez[sez_att][1] += 1
        try:
            intenti, raw = await estrai_intento(q, OLLAMA, MODEL)
            intento = (intenti or [{}])[0]
            miss = _check(intento, expect)
        except Exception as e:
            miss = ["ECCEZIONE: %s" % e]; intento = {}
        if not miss:
            per_sez[sez_att][0] += 1
            print("%-3d %-62s PASS" % (i, q[:62]))
        else:
            fails.append((i, q, nota, miss, intento))
            print("%-3d %-62s FAIL" % (i, q[:62]))

    npass = sum(p for p, _ in per_sez.values())
    ntot = sum(t for _, t in per_sez.values())
    print("\n== RIEPILOGO PER SEZIONE ==")
    for sez in sorted(per_sez):
        p, t = per_sez[sez]
        flag = "" if p == t else "  <-- degrado"
        print("  %-22s %d/%d%s" % (sez, p, t, flag))

    print("\n== DETTAGLIO FAIL ==")
    if not fails:
        print("  nessuno")
    for i, q, nota, miss, intento in fails:
        print("  #%d  %s" % (i, q))
        print("      rischio: %s" % nota)
        print("      mismatch: %s" % "; ".join(miss))
        print("      intento prodotto: %s" % json.dumps({k: intento.get(k) for k in ("sezione","operazione","campo","direzione","nome","soglia_addetti","odonimo","civico")}, ensure_ascii=False))

    print("\n== TOTALE: %d/%d casi PASS  (%.0f%%) ==" % (npass, ntot, 100.0*npass/ntot if ntot else 0))

if __name__ == "__main__":
    asyncio.run(run())
