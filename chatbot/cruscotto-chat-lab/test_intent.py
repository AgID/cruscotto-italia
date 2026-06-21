"""Batteria di test frase -> intento atteso per intent_extract.py.
Uso: python3 test_intent.py [modello]   (default: qwen3:32b)"""
import asyncio, json, sys
from intent_extract import estrai_intento
from intent_engine import valida_intento, _SINONIMI_CAMPO as SINONIMI_CAMPO_TEST

OLLAMA = "http://172.18.0.8:11434"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3:32b"

ISTAT = {"lecce":"075035","bari":"072006","roma":"058091","napoli":"063049",
         "matera":"077014","milano":"015146","torino":"001272","brindisi":"074001",
         "foggia":"071024","morterone":"097052","taranto":"073027"}

def L(frase, comune, op, campo=None, dir=None, lim=None, nome=None, anno=None, odonimo=None, civico=None, comune2=None, sez=None):
    return {"frase":frase,"classe":"legale","atteso":{"comune":comune,"operazione":op,"campo":campo,"direzione":dir,"limite":lim,"nome":nome,"anno":anno,"odonimo":odonimo,"civico":civico,"comune2":comune2,"sezione":sez}}
def C(frase, comune, op, campo=None, dir=None, lim=None, nome=None, anno=None, odonimo=None, civico=None, comune2=None, sez=None):
    d = L(frase, comune, op, campo, dir, lim, nome, anno, odonimo, civico, comune2, sez); d["classe"]="colloquiale"; return d
def F(frase):
    return {"frase":frase,"classe":"fuori_grammatica","atteso":"RIFIUTO"}

BATTERIA = [
 # --- legali base (20) ---
 L("distributore di benzina più economico a Lecce","Lecce","ordina","benzina_self","asc",1),
 L("dove costa meno la benzina a Matera","Matera","ordina","benzina_self","asc",1),
 L("il gasolio più caro a Bari","Bari","ordina","gasolio_self","desc",1),
 L("dove costa di più il diesel a Roma","Roma","ordina","gasolio_self","desc",1),
 L("benzina servita più economica a Milano","Milano","ordina","benzina_serv","asc",1),
 L("gasolio servito più caro a Torino","Torino","ordina","gasolio_serv","desc",1),
 L("il distributore con l'HVO più economico a Lecce","Lecce","ordina","hvo","asc",1),
 L("i 3 distributori di diesel più economici a Napoli","Napoli","ordina","gasolio_self","asc",3),
 L("top 5 distributori più cari di benzina a Roma","Roma","ordina","benzina_self","desc",5),
 L("quanti distributori ci sono a Matera","Matera","conta"),
 L("quanti benzinai a Lecce","Lecce","conta"),
 L("elenca i distributori di Bari","Bari","elenca"),
 L("quali distributori ci sono a Morterone","Morterone","elenca"),
 L("tutti i distributori di Lecce","Lecce","elenca"),
 L("dove conviene fare gasolio a Brindisi","Brindisi","ordina","gasolio_self","asc",1),
 L("prezzo benzina più basso a Foggia","Foggia","ordina","benzina_self","asc",1),
 L("diesel self più economico a Lecce","Lecce","ordina","gasolio_self","asc",1),
 L("benzina al servito dove costa meno a Bari","Bari","ordina","benzina_serv","asc",1),
 L("i 2 distributori più economici di hvo a Roma","Roma","ordina","hvo","asc",2),
 L("dove la benzina è più cara a Napoli","Napoli","ordina","benzina_self","desc",1),
 # --- colloquiali (8) ---
 C("dove faccio benzina spendendo meno a Lecce","Lecce","ordina","benzina_self","asc",1),
 C("il pieno di diesel più conveniente a Matera","Matera","ordina","gasolio_self","asc",1),
 C("mi dici i benzinai di Lecce?","Lecce","elenca"),
 C("a Bari dove mi costa meno il gasolio?","Bari","ordina","gasolio_self","asc",1),
 C("c'è un posto dove la benzina costa poco a Roma?","Roma","ordina","benzina_self","asc",1),
 C("quanti sono i distributori a Milano?","Milano","conta"),
 C("fammi vedere dove rifornirmi a Napoli","Napoli","elenca"),
 C("qual è il benzinaio più caro di Lecce?","Lecce","ordina","benzina_self","desc",1),
 # --- beni culturali: legali (9) ---
 L("quante chiese ci sono a Lecce","Lecce","conta","chiesa"),
 L("quanti beni culturali ha Roma","Roma","conta"),
 L("quali musei ci sono a Matera","Matera","elenca","museo"),
 L("elenca i castelli di Bari","Bari","elenca","castello"),
 L("che categorie di beni culturali ha Lecce?","Lecce","elenca"),
 C("quanti palazzi storici ci sono a Lecce","Lecce","conta","palazzo"),
 C("dov'è il Castello Carlo V a Lecce","Lecce","cerca_nome",nome="Castello Carlo V"),
 C("parlami della Basilica di Santa Croce a Lecce","Lecce","cerca_nome",nome="Basilica di Santa Croce"),
 C("quanti siti archeologici ha Roma","Roma","conta","archeologia"),
 # --- beni culturali: anti-indovinello, attesi cerca_nome (3) ---
 L("quanti anfiteatri ci sono a Lecce","Lecce","cerca_nome",nome="anfiteatro"),
 L("ci sono torri costiere a Lecce?","Lecce","cerca_nome",nome="torre"),
 L("c'è un acquedotto romano a Roma?","Roma","cerca_nome",nome="acquedotto romano"),
 # --- redditi: legali e colloquiali (6) ---
 L("qual è il reddito medio a Lecce","Lecce","valore","reddito_medio"),
 L("quanti contribuenti aveva Matera nel 2022","Matera","valore","contribuenti",anno=2022),
 L("andamento del reddito a Roma","Roma","serie_storica",(None,"reddito_medio")),
 L("serie storica dei contribuenti a Bari","Bari","serie_storica","contribuenti"),
 C("quanto guadagnano in media a Lecce?","Lecce","valore","reddito_medio"),
 C("com'è cambiato il reddito a Matera negli ultimi anni","Matera","serie_storica",(None,"reddito_medio")),
 # --- lotti A+B+D: 13 sezioni nuove (23) ---
 L("quante scuole primarie ci sono a Bari","Bari","conta","primaria"),
 L("elenca le scuole superiori di Lecce","Lecce","elenca","sec2"),
 C("quante scuole medie ci sono a Lecce","Lecce","conta","sec1"),
 L("quante farmacie ci sono a Matera","Matera","conta","farmacia"),
 L("quali ospedali ci sono a Lecce","Lecce","elenca","ospedale"),
 C("quanti posti letto negli ospedali di Bari","Bari","conta","ospedale"),
 L("quante associazioni di volontariato a Matera","Matera","conta","ODV"),
 L("quante colonnine di ricarica ci sono a Milano","Milano","conta"),
 L("quanti immobili pubblici ha Lecce","Lecce","conta"),
 L("dammi le coordinate di via Trinchese 12 a Lecce","Lecce","cerca_civico",odonimo="Trinchese",civico="12"),
 L("in che sezione di censimento si trova via Roma 5 a Matera","Matera","sezione_censimento",odonimo="Roma",civico="5"),
 L("qual è la particella catastale di piazza Duomo 1 a Lecce","Lecce","particella",odonimo="Duomo",civico="1"),
 L("quante strade ci sono a Lecce","Lecce","conta","strade"),
 L("quante opere pubbliche ci sono a Matera","Matera","conta"),
 L("quanti progetti PNRR ha Matera","Matera","conta"),
 L("il pm10 a Lecce nel 2018","Lecce","valore","pm10",anno=2018),
 L("serie storica del pm2.5 a Milano","Milano","serie_storica","pm25"),
 C("com'è l'aria a Torino?","Torino","valore","pm10"),
 L("quante auto circolano a Napoli","Napoli","valore"),
 L("quanti incidenti stradali ci sono stati a Roma","Roma","valore"),
 L("quanti abitanti ha Morterone","Morterone","valore"),
 C("qual è l'età media a Lecce?","Lecce","valore"),
 L("andamento degli addetti delle imprese a Milano","Milano","serie_storica","addetti"),
 # --- lotto E: 9 sezioni a blocchi (12) ---
 L("quanti posti letto turistici ha Lecce","Lecce","valore",sez="turismo"),
 L("quanti pendolari entrano ogni giorno a Milano","Milano","valore",sez="pendolarismo"),
 L("quanto ha speso il comune di Bari","Bari","valore",sez="siope"),
 L("quanti appalti ha aggiudicato il comune di Lecce","Lecce","valore",sez="anac"),
 L("qual è la copertura in fibra ottica a Matera","Matera","valore",sez="banda_larga"),
 L("percentuale di raccolta differenziata a Lecce","Lecce","valore","rifiuti",sez="territorio"),
 L("com'è il rischio idrogeologico a Matera","Matera","valore","rischio_idrogeologico",sez="territorio"),
 L("quanti laureati ci sono a Bari","Bari","valore","istruzione",sez="profilo"),
 C("quanti stranieri vivono a Lecce?","Lecce","valore","cittadinanza",sez="profilo"),
 L("qual è il codice catastale di Lecce","Lecce","valore",sez="anagrafica"),
 L("dammi i dati del censimento di Matera","Matera","valore",sez="censimento"),
 C("quanto consumo di suolo c'è a Lecce?","Lecce","valore","suolo",sez="territorio"),
 # --- fuori grammatica / ambigue (8): successo = RIFIUTO del validatore ---
 L("GPL più economico a Lecce","Lecce","ordina","gpl","asc",1),
 L("dove costa meno il metano a Bari","Bari","ordina","metano","asc",1),
 L("confronta i prezzi della benzina tra Lecce e Bari","Lecce","ordina","benzina_self","asc",1,comune2="Bari"),
 L("confronta i redditi medi di Roma, Milano, Bari e Torino","Roma","valore","reddito_medio",comune2="Milano, Bari, Torino"),
 C("dove costa meno la benzina, a Lecce o a Matera?","Lecce","ordina","benzina_self","asc",1,comune2="Matera"),
 F("serie storica del prezzo del gasolio a Roma"),
 F("il distributore più economico"),
 F("quanto costa la benzina?"),
 F("qual è il museo più bello di Lecce"),
 L("prezzo medio della benzina a Lecce","Lecce","prezzo_medio","benzina_self"),
 F("qual è il reddito medio della Puglia"),
 L("confronta il reddito di Lecce e Bari","Lecce","valore","reddito_medio",comune2="Bari"),
 F("qual è la scuola migliore di Lecce"),
 F("prezzo medio delle case a Milano"),
 F("quanti incidenti ci saranno l'anno prossimo a Roma"),
]

def _norm(v):
    if v in (None,"null","","None"): return None
    return v

def confronta(intento, atteso):
    """Ritorna lista campi sbagliati (vuota = OK)."""
    err = []
    if (intento.get("comune") or "").strip().lower() != atteso["comune"].lower(): err.append("comune")
    if intento.get("operazione") != atteso["operazione"]: err.append("operazione")
    if ((_norm(intento.get("campo")) not in atteso["campo"]) if isinstance(atteso["campo"],(list,tuple)) else (_norm(intento.get("campo")) != atteso["campo"])): err.append("campo")
    if _norm(intento.get("direzione")) != atteso["direzione"]: err.append("direzione")
    lim = _norm(intento.get("limite"))
    try: lim = int(lim) if lim is not None else None
    except Exception: pass
    if lim is None and intento.get("operazione") == "ordina": lim = 1  # default motore
    if lim != atteso["limite"]: err.append("limite")
    if atteso.get("anno") is not None:
        try: est_anno = int(_norm(intento.get("anno")) or 0)
        except Exception: est_anno = 0
        if est_anno != atteso["anno"]: err.append("anno")
    if atteso.get("sezione") is not None and intento.get("sezione") != atteso["sezione"]:
        err.append("sezione")
    if atteso.get("comune2") is not None:
        est = {x.strip().lower() for x in str(_norm(intento.get("comune2")) or "").split(",") if x.strip()}
        att = {x.strip().lower() for x in atteso["comune2"].split(",")}
        if est != att: err.append("comune2")
    if atteso.get("odonimo") is not None:
        est = str(_norm(intento.get("odonimo")) or "").lower()
        if atteso["odonimo"].lower() not in est: err.append("odonimo")
    if atteso.get("civico") is not None:
        if str(_norm(intento.get("civico")) or "") != str(atteso["civico"]): err.append("civico")
    if atteso.get("nome") is not None:
        est = str(_norm(intento.get("nome")) or "").strip().lower()
        att = atteso["nome"].strip().lower()
        # match flessibile sul nome: uguale, contenuto o stessa radice (anfiteatro~anfiteatri)
        if not (est == att or att in est or est in att or (len(est)>=5 and len(att)>=5 and est[:5]==att[:5])):
            err.append("nome")
    return err

async def main():
    print(f"=== TEST ESTRAZIONE INTENTO — modello: {MODEL} ===\n")
    ris = {"legale":[0,0],"colloquiale":[0,0],"fuori_grammatica":[0,0]}  # [ok, tot]
    dettaglio_ko = []
    for i, t in enumerate(BATTERIA, 1):
        _out, raw = await estrai_intento(t["frase"], OLLAMA, MODEL)
        intento = (_out[0] if isinstance(_out, list) and _out else None)
        if intento:  # applica la normalizzazione sinonimi del motore (come fa il sistema prima dell'esecuzione)
            _syn = SINONIMI_CAMPO_TEST.get(intento.get("sezione"), {})
            if intento.get("campo") in _syn:
                intento["campo"] = _syn[intento["campo"]]
        cls = t["classe"]; ris[cls][1] += 1
        if t["atteso"] == "RIFIUTO":
            # rifiuto OK se: niente JSON, oppure validatore boccia
            if intento is None:
                ris[cls][0] += 1; print(f"[{i:2}] OK  (no-JSON ⇒ rifiuto)  {t['frase']}")
            else:
                if intento.get("comune"):
                    istat = ISTAT.get(str(intento["comune"]).strip().lower())
                    if istat: intento["istat"] = istat
                ok, motivo = valida_intento(intento)
                if not ok:
                    ris[cls][0] += 1; print(f"[{i:2}] OK  (rifiutato: {motivo})  {t['frase']}")
                else:
                    print(f"[{i:2}] KO! ACCETTATO un intento illegale: {json.dumps(intento,ensure_ascii=False)}  ←  {t['frase']}")
                    dettaglio_ko.append((i, t["frase"], "falso-accettato", intento))
        else:
            if intento is None:
                print(f"[{i:2}] KO  JSON malformato. Raw: {raw[:120]!r}  ←  {t['frase']}")
                dettaglio_ko.append((i, t["frase"], "json_malformato", raw[:200])); continue
            err = confronta(intento, t["atteso"])
            if not err:
                ris[cls][0] += 1; print(f"[{i:2}] OK  {t['frase']}")
            else:
                print(f"[{i:2}] KO  campi sbagliati: {err}  estratto: {json.dumps(intento,ensure_ascii=False)}  ←  {t['frase']}")
                dettaglio_ko.append((i, t["frase"], ",".join(err), intento))
    print("\n=== RIEPILOGO ===")
    tot_ok = tot = 0
    for cls,(ok,n) in ris.items():
        tot_ok += ok; tot += n
        print(f"  {cls:18}: {ok}/{n}  ({100*ok/n:.0f}%)" if n else "")
    print(f"  {'TOTALE':18}: {tot_ok}/{tot}  ({100*tot_ok/tot:.0f}%)")
    if dettaglio_ko:
        print("\n=== DETTAGLIO KO ===")
        for i, frase, tipo, dato in dettaglio_ko:
            print(f"  [{i:2}] {tipo:20} {frase}\n       → {json.dumps(dato,ensure_ascii=False) if isinstance(dato,dict) else dato}")

asyncio.run(main())
