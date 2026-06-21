"""Estrazione dell'intento dalla domanda naturale. UNICO punto LLM 'intelligente'.
Il modello produce SOLO un intento JSON conforme alla grammatica, mai la risposta."""
import json, re, httpx

INTENT_PROMPT = """Sei un parser che converte domande sui dati di un comune in un intento JSON.
NON rispondere alla domanda, NON dare numeri: produci SOLO l'intento.
SEZIONI supportate:
- "carburanti": distributori, benzinai, prezzi benzina/gasolio/diesel/hvo
- "beni_culturali": chiese, palazzi, castelli, musei, monumenti, siti archeologici
- "redditi": redditi IRPEF, reddito medio, contribuenti, imposte
- "scuole": scuole e istituti scolastici
- "farmacie": farmacie, parafarmacie, ospedali e posti letto
- "terzo_settore": associazioni, volontariato, enti del terzo settore
- "ricarica_ev": colonnine di ricarica per auto elettriche
- "immobili_pa": immobili e patrimonio della pubblica amministrazione (in inglese: municipal/public buildings, public/municipal real estate, council-owned property/buildings). NB: gli edifici di PROPRIETA' del comune o della PA vanno QUI, NON in beni_culturali (che riguarda solo beni storico-artistici: chiese, palazzi storici, castelli, musei, monumenti)
- "civici": numeri civici e strade, coordinate di un indirizzo, sezione di censimento o particella catastale di un indirizzo
- "opere": opere pubbliche e cantieri
- "pnrr": progetti PNRR
- "aria": qualita' dell'aria, smog, polveri sottili
- "veicoli": parco veicoli, automobili circolanti, composizione per classe Euro/ambientale, tasso di motorizzazione, immatricolazioni
- "incidenti": incidenti stradali, morti e feriti sulle strade
- "demografia_dettaglio": popolazione, abitanti, eta' media, maschi/femmine, indici demografici
- "imprese": imprese, unita' locali, addetti
- "turismo": turismo, strutture ricettive, posti letto turistici, arrivi e presenze
- "pendolarismo": pendolari, spostamenti casa-lavoro o casa-studio
- "siope": bilancio comunale, spese e incassi del comune
- "anac": appalti, gare, contratti pubblici
- "banda_larga": fibra ottica, FTTH, copertura internet e banda larga
- "territorio": consumo di suolo, rischio idrogeologico (frane, alluvioni), rifiuti e raccolta differenziata
- "sismica": classificazione sismica, zona sismica, rischio sismico, terremoti, pericolosita' sismica del comune. NB: "rischio idrogeologico/frane/alluvioni" -> territorio; "rischio sismico/zona sismica/terremoti" -> sismica
- "profilo": istruzione e laureati, occupazione e lavoro, famiglie, cittadinanza e stranieri, mobilita'
- "anagrafica": codici del comune (catastale, fiscale, IPA), provincia e regione di appartenenza
- "censimento": dati generali del censimento (popolazione, famiglie, abitazioni, sezioni censuarie)
OPERAZIONI E CAMPI per sezione (usa SOLO queste combinazioni):
- carburanti: ordina(campo+direzione: asc=economico, desc=caro), elenca, conta, cerca_nome(nome=brand OPPURE indirizzo di UN distributore specifico, per i suoi prezzi/coordinate; passa SOLO il brand es. "Eni" o SOLO la via es. "via Dante", mai concatenati), prezzo_medio(campo: prezzo MEDIO comunale di un carburante; campo null = tutti i prezzi medi). Campi: benzina_self, benzina_serv, gasolio_self, gasolio_serv, hvo, gpl, metano. "benzina"->benzina_self, "gasolio"/"diesel"->gasolio_self, "servito"->_serv, "gpl"->gpl, "metano"->metano. GPL e metano sono carburanti validi a tutti gli effetti: "dove costa meno/piu' economico il gpl/metano"->ordina (campo gpl/metano, asc/desc); "prezzo medio del gpl/metano"->prezzo_medio. Non tutti gli impianti li erogano, ma molti si'. "Distributore/benzinaio piu' economico/caro" senza carburante -> benzina_self. NOTA: i carburanti hanno SOLO prezzi correnti, NON serie storiche/andamenti: "serie storica/andamento del prezzo del carburante" -> operazione non_supportata (sezione carburanti).
- beni_culturali: conta(campo=categoria o null), elenca(campo=categoria o null), cerca_nome(nome=bene specifico). Categorie: chiesa, palazzo, castello, archeologia, museo, monumento, archivio, biblioteca, infrastruttura, parco_giardino, altro. Aggettivi generici (storici, antichi) NON cambiano la categoria. Oggetto NON in categorie (anfiteatro, torre, acquedotto, fontana, ponte...) -> SEMPRE cerca_nome con quel termine, ANCHE in forma di conteggio o esistenza (quanti, ci sono, ce ne sono): la regola conta/elenca vale SOLO per le categorie elencate, mai per oggetti fuori categoria. REGOLA GENERALE beni_culturali: SOLO le 11 categorie-macro sopra elencate usano conta/elenca (campo=categoria). QUALSIASI altro termine, anche se e' un tipo o sotto-tipo affine a una categoria (es. cappella, convento, monastero, torre, masseria, mulino, fontana, faro, casale, villa, ponte, cimitero), va SEMPRE a cerca_nome con quel termine al SINGOLARE, anche in forma di conteggio o esistenza (quanti, ci sono). Non mappare MAI un sotto-tipo alla sua categoria-genitore: cappella NON e' 'chiesa', torre NON e' 'castello', masseria NON e' 'palazzo'. Descrizione, dettagli, informazioni, scheda, "tutti i dati", foto/immagine/fotografia di un bene specifico -> cerca_nome(nome=bene). "foto"/"immagine"/"descrizione"/"dettagli" NON sono operazioni separate ne' non_supportata (la scheda cerca_nome include indirizzo, descrizione e foto): non creare un secondo intento per la foto.
- redditi: valore(campo, anno opzionale), serie_storica(campo). Campi: reddito_medio, reddito_totale, contribuenti, imposta_netta_media, e per TIPOLOGIA di reddito: pensione, dipendente, autonomo, fabbricati. "reddito" da solo -> reddito_medio. PENSIONATI/PENSIONE: "quanti pensionati", "reddito dei pensionati", "redditi da pensione" -> redditi campo=pensione (NON profilo ne' lavoro: il numero anagrafico di pensionati non esiste, esiste il numero di percettori di reddito da pensione). "lavoratori dipendenti" -> dipendente; "lavoratori autonomi / partite IVA" -> autonomo; "redditi da fabbricati/immobili" -> fabbricati.
- scuole: conta(campo o null), elenca(campo o null), cerca_nome. Campi: infanzia, primaria, sec1, sec2, altro. "medie"->sec1, "superiori"->sec2, "elementari"->primaria, "asili/materne"->infanzia.
- farmacie: conta(campo), elenca(campo), cerca_nome(nome=farmacia od ospedale specifico; metti campo=ospedale se e' un ospedale). Campi: farmacia, parafarmacia, ospedale. Numero di ospedali/reparti/posti letto -> conta con campo ospedale. "dov'e'/dettagli/informazioni di una farmacia o un ospedale citato per nome" -> cerca_nome(nome=...). MAI "valore" per questa sezione.
- terzo_settore: conta(campo o null), elenca(campo o null), cerca_nome. Campi: ODV, APS, ETS, IS, EF. "volontariato"->ODV, "promozione sociale"->APS, "imprese sociali"->IS.
- ricarica_ev: conta, elenca (senza campo).
- immobili_pa: conta(campo o null), elenca(campo o null). Campi: fabbricati_residenziali, fabbricati_amministrativi_uffici, fabbricati_magazzini, fabbricati_pertinenze, fabbricati_produttivi, fabbricati_sociali_culturali, fabbricati_sociali_sanitari, fabbricati_sociali_scolastici, fabbricati_sociali_sportivi, terreni_agricoli, terreni_urbani, altro.
- civici: conta(campo: civici|strade), cerca_civico(odonimo, civico opzionale: per coordinate/posizione di un indirizzo), sezione_censimento(odonimo+civico), particella(odonimo+civico: particella catastale o foglio di un indirizzo).
- opere: conta, elenca, cerca_nome (opere pubbliche).
- pnrr: conta, elenca, cerca_nome, somma (progetti PNRR). somma = finanziamento/investimento PNRR totale del comune (somma dei finanziamenti di tutti i progetti); usala per "investimento/finanziamento PNRR totale", "quanto vale il PNRR del comune", "totale fondi PNRR", "investimento PNRR per abitante".
REGOLA CONTA vs ELENCA (carburanti, beni_culturali, scuole, farmacie, terzo_settore, ricarica_ev, immobili_pa, opere, pnrr): un termine di ELENCAZIONE (elenco, elenca, elencami, elencale, lista, "dammi/mostrami la lista", "dammi/mostrami l'elenco", "quali sono", "fammi vedere tutti/tutte") -> operazione=elenca. Un termine di CONTEGGIO (quante, quanti, "numero di", "quante/quanti ce ne sono", "quante ne ha") -> operazione=conta. Questa regola ha PRIORITA' sull'inerzia degli esempi: "elenco delle chiese" e' SEMPRE elenca, MAI conta. Il campo/categoria (es. chiesa, museo) NON cambia.
REGOLA LIMITE ORDINA (carburanti): superlativo SINGOLARE ("il/qual e' il piu' economico/caro", "dove costa meno") -> ordina con limite 1. VERBO di ordinamento o PLURALE ("ordina/ordinami/classifica dal piu' economico", "elenco/lista dei distributori piu' economici/cari", "i piu' economici/cari") -> ordina con limite null (LISTA ordinata, NON un solo risultato).
REGOLA cerca_nome: per beni_culturali, scuole, terzo_settore, opere, pnrr, farmacie, ricarica_ev (colonnine, per indirizzo/luogo) e immobili_pa (per indirizzo), se la domanda cita un ELEMENTO SPECIFICO per nome proprio, oppure chiede "dettagli/informazioni/scheda/dov'e'/tutti i dati" di un singolo elemento, usa cerca_nome(nome=quel nome). NON usare non_supportata ne' conta/elenca in questi casi.
- aria: valore(campo, anno opzionale), serie_storica(campo), cerca_nome(nome=nome o codice EU di UNA stazione/centralina di monitoraggio specifica: ne restituisce dati, serie storica e coordinate), elenca (lista di TUTTE le stazioni/centraline del comune con tipo e coordinate, senza serie: per "quali/quante centraline/stazioni ci sono"). Campi: pm10, pm25, no2. Domanda generica sull'aria -> valore pm10. Stazione NOMINATA (per nome o codice) -> cerca_nome; SENZA nome di stazione -> dato aggregato del comune: usa serie_storica se la domanda chiede serie storica, andamento, evoluzione, variazione, negli anni o nel tempo; usa valore per un singolo dato o un anno specifico.
- veicoli: valore(campo: parco | iscrizioni), serie_storica. campo=parco (DEFAULT, vale anche se null) = PARCO CIRCOLANTE/stock di veicoli (categorie, classi Euro/ambientali, tasso di motorizzazione). campo=iscrizioni = IMMATRICOLAZIONI dell'anno e ALIMENTAZIONE delle nuove auto. REGOLA VEICOLI: "immatricolazioni", "immatricolati", "iscrizioni", "nuove auto/vetture immatricolate", oppure una qualsiasi ALIMENTAZIONE (elettrico/elettriche, ibrido/ibride, benzina, gasolio/diesel, gas/gpl/metano) -> campo=iscrizioni; "parco", "circolante", "quante auto/veicoli ci sono", "classi/classe Euro", "composizione per Euro", "tasso di motorizzazione", "veicoli per 1000 abitanti" -> campo=parco. La composizione del PARCO per classe Euro resta campo=parco; l'alimentazione (elettrico, ibrido, benzina, gasolio, gas) NON esiste nel parco, sta SOLO nelle immatricolazioni. ANDAMENTO/SERIE STORICA (sia del parco sia delle immatricolazioni): "andamento", "evoluzione", "negli anni", "nel tempo", "serie storica", "trend" -> operazione=serie_storica (campo=iscrizioni se riguarda le immatricolazioni, parco se riguarda il parco).
- incidenti: valore, serie_storica (senza campo).
- demografia_dettaglio: valore (senza campo). Dato ANAGRAFICO CORRENTE residenti ISTAT POSAS (stima al 1 gennaio recente): usalo per "quanti abitanti/popolazione" generico/attuale/oggi o anno >= 2025, e per gli indicatori demografici correnti del comune (età media, indice di vecchiaia, indice di dipendenza, fasce di età, over 65, under 14): questi indici stanno QUI in demografia_dettaglio, NON in profilo o censimento. POPOLAZIONE STORICA, instrada cosi: "censimento" da solo, "censimento permanente", "ultimo censimento", o anni 2022-2024 -> sezione "profilo" campo "cittadinanza" (contiene pop_totale_n del censimento permanente 2024); "censimento 2021", "basi territoriali", "sezioni di censimento", "abitazioni", o anni <= 2021 -> sezione "censimento" (2021). NON usare demografia per anni passati.
- imprese: valore (senza campo) OPPURE serie_storica(campo). Il "valore" e' il quadro ASIA completo: unita locali totali, addetti, addetti per UL, variazione annua, distribuzione per CLASSE DIMENSIONALE (micro 1-9, piccole 10-49, medie 50-249, grandi 250+ addetti) e top settori ATECO per unita locali e per addetti. Campi (solo serie_storica): unita_locali, addetti. REGOLA IMPRESE (PRIORITARIA): qualsiasi domanda su DIMENSIONE o CLASSI delle imprese ("quante/quali imprese grandi|medie|piccole|micro", "imprese con piu'/meno di N addetti o dipendenti", "imprese sopra/sotto N dipendenti", "imprese per dimensione/classe dimensionale") e sui SETTORI ("in quali settori", "che/quale settore", "settore prevalente o principale", "codice ATECO", "settori con piu' imprese/addetti") usa SEMPRE operazione=valore con campo=null. Per imprese NON usare mai conta, elenca ne' non_supportata: il dettaglio dimensionale e settoriale e' gia' incluso nel valore. Se la domanda cita una SOGLIA di addetti o dipendenti (es. 'oltre 50 addetti', 'piu di 249', 'almeno 10 dipendenti'), metti quel numero nel campo soglia_addetti (operazione resta valore).
- turismo, banda_larga, anagrafica: valore (senza campo).
- pendolarismo: valore (quanti pendolari, saldo, totali in entrata/uscita); elenca quando si chiede di ELENCARE i comuni di destinazione o di origine: "elenca/quali/dove si spostano/verso dove/destinazioni dei pendolari" -> elenca; "da dove arrivano/da dove provengono/comuni di origine o provenienza dei pendolari" -> elenca.
- anac: valore (quanti contratti, importo totale degli appalti); elenca quando si chiede di ELENCARE le categorie merceologiche/CPV/settori di spesa DEGLI APPALTI o CONTRATTI: "elenca/quali categorie CPV", "categorie di spesa dei contratti/appalti", "settori merceologici degli appalti" -> anac/elenca. ATTENZIONE: "categorie/settori di spesa" riferiti ad APPALTI/CONTRATTI/CPV sono anac, MAI siope.
- censimento: valore (dati generali, senza campo); OPPURE ranking_sezioni quando l'utente chiede QUALE SEZIONE censuaria ha piu'/meno di una variabile (campo: stranieri|stranieri_ue|stranieri_extra_ue|popolazione|maschi|femmine|occupati|famiglie|abitazioni|abitazioni_occupate|abitazioni_vuote; direzione desc=piu'/asc=meno; limite opzionale). NOTA: "e dove si trova"/"e dove" riferito alla sezione fa parte dello STESSO intento ranking_sezioni (il ranking include gia' la localita'): NON creare un secondo intento. VALORE DI UNA SINGOLA SEZIONE PER NUMERO: se l'utente cita una sezione censuaria per NUMERO (es. "nella sezione 36", "sezione 12") e chiede una variabile, usa sezione=censimento, operazione=valore, sezione_censimento=<numero>, campo=<variabile> (campi: popolazione|maschi|femmine|stranieri|stranieri_ue|stranieri_extra_ue|occupati|famiglie|abitazioni|abitazioni_occupate|abitazioni_vuote|laureati|diplomati|pop_9plus). I "laureati/diplomati" DENTRO una sezione vanno SU censimento (NON profilo): profilo e' solo per il dato dell'intero comune. sezione_censimento (in civici) si usa SOLO se c'e' un indirizzo esplicito (via + civico). RIFERIMENTO A UN CIVICO: se una variabile censuaria (laureati, diplomati, stranieri, popolazione, ecc.) e' riferita a un INDIRIZZO/CIVICO (es. "laureati nella sezione di via Lupo Protospata 53", oppure "in quella sezione censuaria" subito dopo aver citato un civico), crea l'intento censimento con operazione=valore, campo=<variabile>, odonimo=<via>, civico=<numero> e sezione_censimento=null (RIPETI odonimo e civico anche se sono gia' presenti in un altro intento della stessa domanda): la sezione viene ricavata dal civico dal sistema. NON inventare MAI un numero di sezione quando l'utente non lo ha indicato esplicitamente.
- siope: valore(anno opzionale = totale spese del comune); cerca_voce(nome = voce/categoria di spesa, anno opzionale) quando si chiede una SPECIFICA voce di spesa ("per la carta", "per il personale", "per i rifiuti", "per l'illuminazione", "per la mensa"...). "quanto spende per X" con X voce specifica -> cerca_voce con nome=X. elenca(limite opzionale) quando si chiede di ELENCARE o CLASSIFICARE le spese senza una voce specifica: "elenca/le principali/le prime N/le maggiori spese o voci di spesa", "classifica delle spese", "spese piu' alte" -> operazione=elenca (metti N in limite se indicato).
- territorio: valore(campo: rifiuti|rischio_idrogeologico|suolo o null). "raccolta differenziata"->rifiuti, "frane/alluvioni"->rischio_idrogeologico.
- sismica: valore (senza campo). Zona sismica / classificazione sismica / rischio sismico del comune (zone 1-4, ex OPCM 3519/06).
- profilo: valore(campo: cittadinanza|famiglie|istruzione|lavoro|mobilita o null). "laureati/diplomati"->istruzione, "stranieri"->cittadinanza, "occupati/disoccupati"->lavoro, "popolazione/abitanti del censimento permanente o del 2024"->cittadinanza (il blocco cittadinanza contiene pop_totale_n e anno 2024).
CONFRONTI: se la domanda confronta DUE O PIU' comuni, metti il primo in "comune" e gli altri in "comune2" separati da virgola, con la normale operazione della sezione (NON e' un caso speciale). Massimo 5 comuni. I comuni del confronto vanno indicati ESPLICITAMENTE per nome nella domanda: "comune" e "comune2" devono essere nomi di comuni reali presenti nel testo. Se la domanda chiede "tutti i comuni di una provincia/regione", "i comuni della provincia/regione di X", "tutta la provincia/regione di X" o un qualsiasi insieme territoriale NON elencato per nome -> "operazione":"non_supportata" con comune, comune2 e campo a null. NON inventare, dedurre o allucinare MAI un nome di comune che non sia scritto nella domanda.
REGOLA DI CONFINE: se la domanda chiede qualcosa FUORI da queste combinazioni (giudizi come "il piu' bello", previsioni sul futuro, carburanti non gestiti come idrogeno o GNL, altri argomenti), usa "operazione":"non_supportata" e lascia null gli altri campi. NON scegliere mai l'operazione o il campo "piu' vicino".
Se la domanda contiene PIU' richieste distinte (es. "quanti musei a Lecce E POI quante farmacie", "il reddito di Bari E le scuole di Lecce"), produci un intento per ciascuna. Massimo 3.
Rispondi SEMPRE con un oggetto JSON {"intenti": [ ... ]}, dove la lista contiene uno o piu' intenti; ogni intento ha questi campi:
{"comune": "<nome comune>", "comune2": "<altri comuni del confronto separati da virgola, o null>", "sezione": "<sezione>", "operazione": "<operazione>", "campo": "<campo o null>", "direzione": "<asc|desc o null>", "limite": <numero o null>, "nome": "<nome o null>", "anno": <anno o null>, "odonimo": "<via/piazza o null>", "civico": "<numero civico o null>", "sezione_censimento": "<numero della sezione censuaria citata, o null>", "soglia_addetti": <numero intero o null: SOLO imprese, la soglia di addetti citata, es. 50 in 'oltre 50 addetti'>}
Esempi:
"distributore di benzina piu economico a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"ordina","campo":"benzina_self","direzione":"asc","limite":1,"nome":null,"anno":null,"odonimo":null,"civico":null}
"ordina i distributori dal piu' economico a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"ordina","campo":"benzina_self","direzione":"asc","limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenco dei distributori piu' economici a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"ordina","campo":"benzina_self","direzione":"asc","limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"dove costa meno il diesel a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"ordina","campo":"gasolio_self","direzione":"asc","limite":1,"nome":null,"anno":null,"odonimo":null,"civico":null}
"prezzi del distributore in via Dante a Matera" -> {"comune":"Matera","sezione":"carburanti","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"via Dante","anno":null,"odonimo":null,"civico":null}
"i prezzi dell'Eni a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Eni","anno":null,"odonimo":null,"civico":null}
"quante chiese ci sono a Lecce" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"conta","campo":"chiesa","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenco delle chiese a Lecce" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"elenca","campo":"chiesa","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elencami i musei di Matera" -> {"comune":"Matera","sezione":"beni_culturali","operazione":"elenca","campo":"museo","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"dov'e' il Castello Carlo V a Lecce" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Castello Carlo V","anno":null,"odonimo":null,"civico":null}
"a Matera tutti i dati e la foto della Chiesa di Santa Maria in Idris" -> {"comune":"Matera","sezione":"beni_culturali","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Chiesa di Santa Maria in Idris","anno":null,"odonimo":null,"civico":null}
"ci sono torri costiere a Lecce?" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"torre","anno":null,"odonimo":null,"civico":null}
"elencami le cappelle a Lecce" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"cappella","anno":null,"odonimo":null,"civico":null}
"quanti contribuenti aveva Matera nel 2022" -> {"comune":"Matera","sezione":"redditi","operazione":"valore","campo":"contribuenti","direzione":null,"limite":null,"nome":null,"anno":2022,"odonimo":null,"civico":null}
"quanti pensionati a Lecce e che reddito hanno" -> {"comune":"Lecce","sezione":"redditi","operazione":"valore","campo":"pensione","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"a Lecce quante imprese hanno piu' di 50 addetti" -> {"comune":"Lecce","sezione":"imprese","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null,"soglia_addetti":50}
"quante aziende con piu' di 249 addetti a Lecce" -> {"comune":"Lecce","sezione":"imprese","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null,"soglia_addetti":249}
"quante imprese grandi ci sono a Bari" -> {"comune":"Bari","sezione":"imprese","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"in quali settori lavorano gli addetti a Lecce" -> {"comune":"Lecce","sezione":"imprese","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante scuole primarie ci sono a Bari" -> {"comune":"Bari","sezione":"scuole","operazione":"conta","campo":"primaria","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quali ospedali ci sono a Lecce" -> {"comune":"Lecce","sezione":"farmacie","operazione":"elenca","campo":"ospedale","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante associazioni di volontariato a Matera" -> {"comune":"Matera","sezione":"terzo_settore","operazione":"conta","campo":"ODV","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante colonnine di ricarica a Milano" -> {"comune":"Milano","sezione":"ricarica_ev","operazione":"conta","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"dammi le coordinate di via Trinchese 12 a Lecce" -> {"comune":"Lecce","sezione":"civici","operazione":"cerca_civico","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":"via Trinchese","civico":"12"}
"in che sezione di censimento si trova via Roma 5 a Matera" -> {"comune":"Matera","sezione":"civici","operazione":"sezione_censimento","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":"via Roma","civico":"5"}
"a Palermo quale sezione censuaria ha piu' stranieri" -> {"comune":"Palermo","comune2":null,"sezione":"censimento","operazione":"ranking_sezioni","campo":"stranieri","direzione":"desc","limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"le 3 sezioni con meno abitazioni occupate a Lecce" -> {"comune":"Lecce","comune2":null,"sezione":"censimento","operazione":"ranking_sezioni","campo":"abitazioni_occupate","direzione":"asc","limite":3,"nome":null,"anno":null,"odonimo":null,"civico":null}
"qual e' la particella catastale di piazza Duomo 1 a Lecce" -> {"comune":"Lecce","sezione":"civici","operazione":"particella","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":"piazza Duomo","civico":"1"}
"com'e' la qualita' dell'aria a Torino" -> {"comune":"Torino","sezione":"aria","operazione":"valore","campo":"pm10","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"i dati della stazione Arenula a Roma" -> {"comune":"Roma","sezione":"aria","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Arenula","anno":null,"odonimo":null,"civico":null}
"serie storica della centralina Garigliano a Lecce" -> {"comune":"Lecce","sezione":"aria","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Garigliano","anno":null,"odonimo":null,"civico":null}
"serie storica del pm2.5 a Milano" -> {"comune":"Milano","sezione":"aria","operazione":"serie_storica","campo":"pm25","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quali centraline dell'aria ci sono a Lecce" -> {"comune":"Lecce","sezione":"aria","operazione":"elenca","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti incidenti stradali ci sono stati a Roma" -> {"comune":"Roma","sezione":"incidenti","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti abitanti ha Lecce" -> {"comune":"Lecce","sezione":"demografia_dettaglio","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"indice di vecchiaia di Lecce" -> {"comune":"Lecce","sezione":"demografia_dettaglio","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti abitanti aveva Lecce nel 2021" -> {"comune":"Lecce","sezione":"censimento","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"popolazione di Lecce al censimento" -> {"comune":"Lecce","sezione":"profilo","operazione":"valore","campo":"cittadinanza","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti abitanti aveva Lecce nel 2024" -> {"comune":"Lecce","sezione":"profilo","operazione":"valore","campo":"cittadinanza","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"popolazione di Lecce al censimento 2021" -> {"comune":"Lecce","sezione":"censimento","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"andamento degli addetti delle imprese a Milano" -> {"comune":"Milano","sezione":"imprese","operazione":"serie_storica","campo":"addetti","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"confronta i prezzi della benzina tra Lecce e Bari" -> {"comune":"Lecce","comune2":"Bari","sezione":"carburanti","operazione":"ordina","campo":"benzina_self","direzione":"asc","limite":1,"nome":null,"anno":null,"odonimo":null,"civico":null}
"chi ha piu' scuole tra Bari e Taranto" -> {"comune":"Bari","comune2":"Taranto","sezione":"scuole","operazione":"conta","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"confronta i redditi medi di Roma, Milano, Bari e Torino" -> {"comune":"Roma","comune2":"Milano, Bari, Torino","sezione":"redditi","operazione":"valore","campo":"reddito_medio","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"confronta i redditi di tutti i comuni della provincia di Enna" -> {"comune":null,"comune2":null,"sezione":"redditi","operazione":"non_supportata","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"composizione del parco veicoli di Palermo per classe Euro" -> {"comune":"Palermo","sezione":"veicoli","operazione":"valore","campo":"parco","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante immatricolazioni a Lecce" -> {"comune":"Lecce","sezione":"veicoli","operazione":"valore","campo":"iscrizioni","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante auto elettriche e ibride immatricolate a Milano" -> {"comune":"Milano","sezione":"veicoli","operazione":"valore","campo":"iscrizioni","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"andamento delle immatricolazioni a Lecce" -> {"comune":"Lecce","sezione":"veicoli","operazione":"serie_storica","campo":"iscrizioni","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti posti letto turistici ha Lecce" -> {"comune":"Lecce","sezione":"turismo","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quante strutture ricettive a Lecce" -> {"comune":"Lecce","sezione":"turismo","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti contratti pubblici a Matera" -> {"comune":"Matera","sezione":"anac","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"importo totale degli appalti a Matera" -> {"comune":"Matera","sezione":"anac","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanto ha speso il comune di Bari" -> {"comune":"Bari","sezione":"siope","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"il finanziamento PNRR totale di Matera" -> {"comune":"Matera","sezione":"pnrr","operazione":"somma","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanto spende il comune di Matera per la carta" -> {"comune":"Matera","sezione":"siope","operazione":"cerca_voce","campo":null,"direzione":null,"limite":null,"nome":"carta","anno":null,"odonimo":null,"civico":null}
"elenca le principali spese di Lecce" -> {"comune":"Lecce","sezione":"siope","operazione":"elenca","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"le prime 10 spese di Bari" -> {"comune":"Bari","sezione":"siope","operazione":"elenca","campo":null,"direzione":null,"limite":10,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenca le destinazioni dei pendolari di Lecce" -> {"comune":"Lecce","sezione":"pendolarismo","operazione":"elenca","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"da dove arrivano i pendolari a Matera" -> {"comune":"Matera","sezione":"pendolarismo","operazione":"elenca","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenca le categorie di spesa CPV degli appalti a Lecce" -> {"comune":"Lecce","sezione":"anac","operazione":"elenca","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"dove si trova la farmacia Coniglio a Matera" -> {"comune":"Matera","sezione":"farmacie","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Coniglio","anno":null,"odonimo":null,"civico":null}
"dammi le coordinate della colonnina di Piazzale Vittime delle Foibe a Galatina" -> {"comune":"Galatina","sezione":"ricarica_ev","operazione":"cerca_nome","campo":null,"direzione":null,"limite":null,"nome":"Piazzale Vittime delle Foibe","anno":null,"odonimo":null,"civico":null}
"percentuale di raccolta differenziata a Lecce" -> {"comune":"Lecce","sezione":"territorio","operazione":"valore","campo":"rifiuti","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"che classificazione sismica ha Lecce" -> {"comune":"Lecce","sezione":"sismica","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti laureati ci sono a Bari" -> {"comune":"Bari","sezione":"profilo","operazione":"valore","campo":"istruzione","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"nella sezione 36 di Matera quanti laureati" -> {"comune":"Matera","comune2":null,"sezione":"censimento","operazione":"valore","campo":"laureati","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null,"sezione_censimento":"36"}
"quanti abitanti nella sezione 12 di Lecce" -> {"comune":"Lecce","comune2":null,"sezione":"censimento","operazione":"valore","campo":"popolazione","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null,"sezione_censimento":"12"}
"qual e' il codice catastale di Lecce" -> {"comune":"Lecce","sezione":"anagrafica","operazione":"valore","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"qual e' il museo piu' bello di Lecce" -> {"comune":"Lecce","sezione":"beni_culturali","operazione":"non_supportata","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"prezzo medio della benzina a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"prezzo_medio","campo":"benzina_self","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"prezzo medio del gpl a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"prezzo_medio","campo":"gpl","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanto costa in media il metano a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"prezzo_medio","campo":"metano","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"GPL piu' economico a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"ordina","campo":"gpl","direzione":"asc","limite":1,"nome":null,"anno":null,"odonimo":null,"civico":null}
"dove costa meno il metano a Bari" -> {"comune":"Bari","sezione":"carburanti","operazione":"ordina","campo":"metano","direzione":"asc","limite":1,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenco distributori GPL a Lecce" -> {"comune":"Lecce","sezione":"carburanti","operazione":"elenca","campo":"gpl","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"elenca i distributori di metano a Matera" -> {"comune":"Matera","sezione":"carburanti","operazione":"elenca","campo":"metano","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}
"quanti incidenti ci saranno l'anno prossimo a Roma" -> {"intenti":[{"comune":"Roma","comune2":null,"sezione":"incidenti","operazione":"non_supportata","campo":null,"direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}]}
"quanti musei ha Lecce e quante farmacie" -> {"intenti":[{"comune":"Lecce","comune2":null,"sezione":"beni_culturali","operazione":"conta","campo":"museo","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null},{"comune":"Lecce","comune2":null,"sezione":"farmacie","operazione":"conta","campo":"farmacia","direzione":null,"limite":null,"nome":null,"anno":null,"odonimo":null,"civico":null}]}
RICORDA: anche per una sola richiesta usa SEMPRE il formato {"intenti":[{...}]}.
Domanda: """

async def estrai_intento(domanda, ollama_url, model, contesto_prec="", stato=None):
    if isinstance(stato, dict) and any(stato.get(k) for k in ("comune","sezione","operazione","oggetto")) and INTENT_PROMPT.endswith("Domanda: "):
        _righe = []
        if stato.get("comune"): _righe.append("- comune: " + str(stato["comune"]))
        if stato.get("sezione"): _righe.append("- sezione: " + str(stato["sezione"]))
        if stato.get("operazione"): _righe.append("- operazione: " + str(stato["operazione"]))
        if stato.get("campo"): _righe.append("- campo/carburante/categoria: " + str(stato["campo"]))
        if stato.get("oggetto"): _righe.append("- oggetto: " + str(stato["oggetto"]))
        _ist = ("CONTESTO DEL TURNO PRECEDENTE (stato strutturato, affidabile):\n" + "\n".join(_righe)
            + "\n\nLa DOMANDA CORRENTE ha PRIORITA' ASSOLUTA. Se nomina gia' il comune e l'oggetto/sezione, "
              "estrai l'intento SOLO dalla domanda corrente. Se invece e' una CONTINUAZIONE ellittica "
              "(es. 'ordina dal piu economico', 'e i musei?', 'dividilo per mese', 'nel dettaglio', "
              "'di che tipo?', 'di che tipologia?', 'come si dividono?', 'in quali categorie?', 'quali tipi', 'quali settori', "
              "'altri', 'i prossimi', 'i successivi', 'continua', 'mostrane altri', 'vai avanti', "
              "'e a gennaio', 'dammi i dettagli'), EREDITA dal contesto i campi mancanti (comune, sezione, campo/carburante/categoria) "
              "e usa l'operazione/ordinamento indicati dalla domanda; se la domanda chiede di PROSEGUIRE un elenco (es. 'altri', 'i prossimi', 'i successivi', 'continua', 'mostrane altri', 'vai avanti'), EREDITA comune+sezione+campo e usa operazione=elenca; se la domanda chiede una TIPOLOGIA, delle CATEGORIE o un DETTAGLIO/SUDDIVISIONE del dato precedente senza indicare un'operazione (es. 'di che tipo?', 'di che tipologia?', 'come si dividono?'), usa operazione=valore ereditando comune e sezione. NON marcare non_supportata. Se la domanda "
              "introduce comune/sezione/oggetto NUOVI, usa quelli e ignora il contesto. Se la domanda e' FUORI "
              "PERIMETRO (non e' una continuazione ne' rientra nelle sezioni), marca non_supportata e NON "
              "inventare una sezione dal contesto.\n\nDomanda: ")
        _content = INTENT_PROMPT[:-len("Domanda: ")] + _ist + domanda
    elif contesto_prec and INTENT_PROMPT.endswith("Domanda: "):
        _ist = ("CONVERSAZIONE PRECEDENTE (turni utente precedenti, solo contesto):\n" + contesto_prec
            + "\n\nLa DOMANDA CORRENTE ha PRIORITA' ASSOLUTA. Se da sola nomina gia' il comune E "
              "l'oggetto della richiesta (sezione, voce o nome), IGNORA del tutto la conversazione "
              "precedente ed estrai l'intento ESCLUSIVAMENTE dalla domanda corrente. Usa la "
              "conversazione precedente SOLO quando la domanda corrente e' ellittica/incompleta, "
              "cioe' manca il comune OPPURE manca l'oggetto della richiesta (es. 'e a Lecce?', "
              "'dividilo per mese', 'nel dettaglio', 'e a gennaio'): in quel caso eredita dal "
              "contesto i campi mancanti (comune, sezione, operazione, nome) e NON marcare "
              "non_supportata. Esempio follow-up: contesto 'quanto spende Matera per la carta' + "
              "domanda 'dividilo per mese' -> sezione siope, operazione cerca_voce, nome carta."
              "\n\nDomanda: ")
        _content = INTENT_PROMPT[:-len("Domanda: ")] + _ist + domanda
    else:
        _content = INTENT_PROMPT + domanda
    payload = {"model": model, "messages": [{"role":"user","content": _content}],
               "stream": False, "think": False, "keep_alive": "60m", "options": {"temperature": 0, "num_predict": 512}}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{ollama_url}/api/chat", json=payload)
            txt = r.json().get("message", {}).get("content", "")
    except Exception:
        return None, ""
    # estraggo il primo oggetto JSON dalla risposta
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        return None, txt
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None, txt
    if isinstance(obj, dict) and isinstance(obj.get("intenti"), list):
        intenti = [x for x in obj["intenti"] if isinstance(x, dict)][:5]
        return (intenti or None), txt
    if isinstance(obj, dict):  # retrocompat: oggetto singolo -> lista di 1
        return [obj], txt
    return None, txt
