# -*- coding: utf-8 -*-
"""
semantica.py - Dizionario semantico del chatbot Cruscotto (cornice a monte).

Fonte di verita' unica per la verbalizzazione: ambito, qualificatore, granularita,
stato, dimensioni e caveat di ogni sezione. Sostituisce i _vextra sparsi in app_v2.py.

TASK 1 (motore + 3 voci pilota): immobili_pa, opere, turismo.
Le altre sezioni si aggiungono come VOCI a questo dizionario, senza nuovo codice.

Determinismo: ogni guida nasce da un campo del dizionario; niente euristiche sparse.
I metadati testuali (_meta, contesto prompt) NON contengono cifre, cosi' check_numerico
non riceve numeri spuri da imporre.
"""

NL = chr(10)


# --------------------------------------------------------------------------- #
# 1. DIZIONARIO
# --------------------------------------------------------------------------- #
# Schema (cfr. 01_SPEC_DIZIONARIO_SEMANTICO.md, par. 2). Tutti i campi sono
# opzionali tranne label/fonte. I template di guida (it/en) sono campi del
# dizionario: costruisci_extra li formatta, non li riscrive.

SEMANTICA = {

    # ------------------------------------------------------------------- #
    "immobili_pa": {
        "label": "patrimonio immobiliare pubblico",
        "label_en": "public real-estate assets",
        "fonte": "MEF - censimento patrimonio della PA",
        "ambito": "SOLO beni di proprieta' della PA, non il totale degli immobili presenti nel comune",
        "ambito_en": "ONLY properties owned by the public administration, not the total properties in the municipality",
        "qualificatore": "immobili PUBBLICI / di proprieta' pubblica",
        "qualificatore_en": "PUBLIC / public-owned properties",
        "granularita": "comunale",
        # dimensioni interrogabili e loro stato
        "dimensioni": {
            "categoria":  {"stato": "per_campo"},
            "uso_terzi":  {
                "stato": "solo_aggregato_totale",
                "trigger": ["terzi", "concess", "locat", "affitt"],
                "kpi": "pct_uso_terzi",            # campo nello shard kpi
                "pct_out": "pct_uso_terzi",        # chiave nel dato sostituito
                "tema": "uso_terzi",
                "stima_key": "stima_immobili_uso_terzi",
                "etichetta_it": "Gli immobili dati in USO A TERZI",
                "etichetta_en": "The properties given in USE TO THIRD PARTIES",
            },
            "vincolo":    {
                "stato": "solo_aggregato_totale",
                "trigger": ["vincol", "tutelat"],
                "kpi": "pct_vincolo_qualsiasi",
                "pct_out": "pct_vincolo",
                "tema": "vincolo",
                "stima_key": "stima_immobili_vincolo",
                "etichetta_it": "Gli immobili con VINCOLO",
                "etichetta_en": "The properties under PROTECTION/constraint",
            },
        },
        # template di guida (formattati da costruisci_extra)
        "guida": {
            "base_it": ("Questi dati riguardano il PATRIMONIO IMMOBILIARE PUBBLICO "
                        "(beni di proprieta' della PA, censimento MEF), NON il totale degli "
                        "immobili presenti nel comune: precisa sempre che si tratta di immobili "
                        "PUBBLICI / di proprieta' pubblica."),
            "base_en": ("These figures refer to the PUBLIC real-estate assets (properties owned "
                        "by the public administration, MEF census), NOT the total properties in the "
                        "municipality: always make clear they are PUBLIC / public-owned properties."),
            # %s = etichetta dimensione, pct, n_totale, stima
            "spec_it": (" %s sono un dato AGGREGATO (%s%% su %s totali, circa %s immobili), "
                        "NON suddiviso per categoria: indica che la ripartizione per categoria "
                        "non e' disponibile."),
            "spec_en": (" %s are an AGGREGATE figure (%s%% of %s total, about %s properties), "
                        "NOT broken down by category: state that the breakdown by category is "
                        "not available."),
        },
        "freschezza": "annuale, latenza ~2 anni",
        "copertura": None,
        "licenza": None,
        "full": None,
    },

    # ------------------------------------------------------------------- #
    "opere": {
        "label": "opere pubbliche",
        "label_en": "public works",
        "fonte": "MEF - BDAP-MOP",
        "ambito": "progetti di opere pubbliche ATTIVE/in corso (non quelle gia' completate)",
        "ambito_en": "active/ongoing public-works projects (not already-completed ones)",
        "qualificatore": None,
        "granularita": "comunale",
        "stato": {
            "campo": "stato",
            "attivo_match": "ATTIV",       # str(stato).upper().startswith(attivo_match)
            "trigger_conclusi": ["completat", "conclus", "concluse", "terminat", "finit",
                                  "ultimat", "chiuse", "realizzat", "consegnat", "collaudat"],
            "verbi_vietati": ["realizzate", "completate"],
            "verbi_vietati_en": ["built", "completed"],
            "nota": "il set contiene solo opere ATTIVE/in corso; NON dire 'realizzate/completate'",
        },
        "guida": {
            # %s = n_attivi, n_conclusi, n_conclusi
            "conclusi_it": ("La domanda chiede quante opere sono CONCLUSE/completate. Nel dato "
                            "BDAP-MOP risultano %s opere ATTIVE (in corso) e %s concluse. Riporta "
                            "il numero di CONCLUSE (%s); se e' 0, spiega che questo insieme contiene "
                            "solo opere ATTIVE/in corso, non quelle gia' completate, quindi NON dire "
                            "che il totale e' completato."),
            "conclusi_en": ("The question asks how many works are COMPLETED/concluded. From BDAP-MOP "
                            "there are %s ACTIVE (ongoing) works and %s concluded. Report the number "
                            "CONCLUDED (%s); if it is 0, explain that this dataset only lists "
                            "ACTIVE/ongoing works, not already-completed ones, so do NOT say the total "
                            "is completed."),
            # %s = n_totale, n_attivi, coda_conclusi
            "attivi_it": ("Riporta %s progetti di opere pubbliche (fonte BDAP-MOP), di cui %s "
                          "ATTIVI/in corso%s. NON usare i verbi 'realizzate' o 'completate': sono "
                          "opere avviate e in gran parte ancora in corso, ciascuna con una propria "
                          "percentuale di avanzamento."),
            "attivi_en": ("Report %s public-works projects (source BDAP-MOP), of which %s "
                          "ACTIVE/ongoing%s. Do NOT use the verbs 'built' or 'completed': these works "
                          "have been started and are largely still in progress, each with its own "
                          "completion percentage."),
            "coda_conclusi_it": " e %s conclusi",
            "coda_conclusi_en": " and %s concluded",
        },
        "freschezza": "periodica (BDAP-MOP)",
        "copertura": None,
        "licenza": None,
        "full": None,
    },

    # ------------------------------------------------------------------- #
    "turismo": {
        "label": "turismo",
        "label_en": "tourism",
        "fonte": "ISTAT",
        "ambito": None,
        "qualificatore": None,
        "granularita": "mista",
        "granularita_campi": {
            "flussi_provincia": "provinciale",
            "capacita_comune": "comunale",
        },
        # dimensioni testuali (flussi vs capacita): trigger + guida
        "dimensioni": {
            "flussi": {
                "trigger": ["arriv", "presenz", "fluss", "pernott", "turisti", "visitatori", "movimento"],
                "guida_it": ("La domanda riguarda i FLUSSI turistici (arrivi/presenze): usa il campo "
                             "flussi_provincia e PRECISA CHIARAMENTE che e' un dato PROVINCIALE (NUTS3), "
                             "non comunale, perche' ISTAT non pubblica i flussi per singolo comune. "
                             "NON elencare la capacita ricettiva comunale."),
                "guida_en": ("The question is about tourist FLOWS (arrivals/overnight stays): use the "
                             "field flussi_provincia and STATE CLEARLY it is a PROVINCIAL figure (NUTS3), "
                             "not municipal, because ISTAT does not publish municipal flows. Do NOT list "
                             "the municipal accommodation capacity."),
            },
            "capacita": {
                "trigger": ["letto", "letti", "struttur", "esercizi", "albergh", "camere",
                            "capacita", "ricettiv", "posti"],
                "guida_it": ("La domanda riguarda la CAPACITA ricettiva comunale: riporta i dati dal "
                             "campo capacita_comune (totale_strutture, totale_letti e, se pertinente, "
                             "totale_camere). NON riportare i flussi provinciali."),
                "guida_en": ("The question is about the municipal accommodation CAPACITY: report the "
                             "figures from capacita_comune (totale_strutture, totale_letti, and "
                             "totale_camere if relevant). Do NOT report the provincial flows."),
            },
        },
        "freschezza": None,
        "copertura": None,
        "licenza": None,
        "full": None,
    },

    # ------------------------------------------------------------------- #
    # TASK 2 - sezioni "a contesto" (solo canale CONTESTO, nessun _DISPATCH)
    # ------------------------------------------------------------------- #
    "beni_culturali": {
        "label": "beni culturali catalogati",
        "label_en": "catalogued cultural heritage",
        "fonte": "MiC - ArCo (ICCD) + Cultural-ON",
        "ambito": "SOLO i beni catalogati nel catalogo nazionale del Ministero della Cultura, non tutti i beni culturali realmente presenti nel comune",
        "ambito_en": "ONLY the items recorded in the national catalogue of the Ministry of Culture, not all the cultural heritage actually present in the municipality",
        "qualificatore": "beni catalogati/censiti nel catalogo MiC",
        "qualificatore_en": "items catalogued/recorded in the MiC catalogue",
        "granularita": "comunale",
        "dimensioni": {
            "sito_web":  {"trigger": ["sito web", "sito internet", "url", "website", "indirizzo web", "pagina web"]},
            "telefono":  {"trigger": ["telefono", "numero di telefono", "recapito telefonico", "tel.", "chiamare"]},
            "email":     {"trigger": ["email", "e-mail", "mail", "posta elettronica"]},
            "indirizzo": {"trigger": ["indirizzo", "dove si trova", "ubicazione", "in che via"]},
            "contatti":  {"trigger": ["contatti", "recapiti", "come contattare", "come contatto"]},
        },
        "copertura": {
            "pct": 68.7,
            "nota_assenza": "il catalogo MiC non e' esaustivo e copre solo una parte dei comuni: se una categoria (es. biblioteche, musei) non risulta, dillo come 'non risultano ... CATALOGATE/CENSITE nel catalogo MiC', NON come 'il comune non ha ...'",
            "nota_assenza_en": "the MiC catalogue is not exhaustive and covers only part of the municipalities: if a category (e.g. libraries, museums) is missing, say it as 'no ... are CATALOGUED/RECORDED in the MiC catalogue', NOT as 'the municipality has no ...'",
            "nota_assenza_diretta": "Nel catalogo del Ministero della Cultura non risultano beni catalogati per questo comune; il catalogo non e' esaustivo.",
            "nota_assenza_diretta_en": "No items are catalogued for this municipality in the Ministry of Culture catalogue; the catalogue is not exhaustive.",
        },
        "freschezza": None,
        "licenza": "CC BY 4.0 (Cultural-ON) / ArCo",
        "full": {"endpoint": "/data/beni_culturali_full/<istat>.json"},
    },

    "aria": {
        "label": "qualita' dell'aria",
        "label_en": "air quality",
        "fonte": "ISPRA - SNPA",
        "ambito": "rilevazioni delle centraline di monitoraggio della qualità dell'aria presenti nel comune",
        "ambito_en": "measurements from the air-quality monitoring stations located in the municipality",
        "qualificatore": None,
        "granularita": "comunale (per stazione)",
        "copertura": {
            "nota_assenza": "solo una minoranza di comuni ospita una centralina di monitoraggio della qualità dell'aria: l'assenza di dati significa 'nessuna centralina nel comune', NON che l'aria non sia mai stata misurata",
            "nota_assenza_en": "only a minority of municipalities host an air-quality monitoring station: missing data means 'no monitoring station in the municipality', NOT that the air has never been measured",
        },
        "freschezza": None,
        "licenza": "ISPRA",
        "full": None,
    },

    "redditi": {
        "label": "redditi e fisco (IRPEF)",
        "label_en": "income and taxation (IRPEF)",
        "fonte": "MEF - Dipartimento delle Finanze",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "se il comune non compare, e' assente dal dataset MEF delle dichiarazioni IRPEF comunali; non significa che non vi siano redditi",
            "nota_assenza_en": "if the municipality is missing, it is absent from the MEF dataset of municipal IRPEF returns; it does not mean there is no income",
        },
        "freschezza": "annuale, latenza ~2 anni",
        "licenza": "MEF",
        "full": None,
    },

    "farmacie": {            # sezione chatbot 'farmacie' -> shard dati 'sanita_mds'
        "label": "sanita' territoriale (farmacie, parafarmacie, ospedali)",
        "label_en": "local healthcare (pharmacies, parapharmacies, hospitals)",
        "fonte": "Ministero della Salute - Open Data",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "se non risultano farmacie/parafarmacie/ospedali sono 'non censiti negli Open Data del Ministero della Salute', non necessariamente assenti sul territorio",
            "nota_assenza_en": "if no pharmacies/parapharmacies/hospitals appear, they are 'not recorded in the Ministry of Health Open Data', not necessarily absent from the area",
        },
        "freschezza": None,
        "licenza": "IODL 2.0",
        "full": None,
    },

    "imprese": {             # sezione chatbot 'imprese' -> shard dati 'asia'
        "label": "imprese e addetti (unita' locali)",
        "label_en": "businesses and employees (local units)",
        "fonte": "ISTAT - ASIA UL",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "ASIA copre tutti i comuni, ma nei micro-comuni di alta montagna puo' non risultare alcuna impresa attiva registrata",
            "nota_assenza_en": "ASIA covers all municipalities, but in tiny mountain municipalities no active registered business may appear",
        },
        "freschezza": "annuale, latenza ~2 anni",
        "licenza": "CC BY 3.0 IT",
        "full": None,
    },

    "carburanti": {
        "label": "prezzi dei carburanti",
        "label_en": "fuel prices",
        "fonte": "MIMIT - Osservatorio prezzi carburanti",
        "ambito": None,
        "qualificatore": "prezzi rilevati negli ultimi giorni (snapshot recente), non una media storica",
        "qualificatore_en": "prices collected over the last few days (recent snapshot), not a historical average",
        "granularita": "comunale (per impianto)",
        "copertura": None,
        "freschezza": "giornaliera",
        "licenza": None,
        "full": None,
    },

    "ricarica_ev": {         # sezione chatbot 'ricarica_ev' -> shard dati 'pun'
        "label": "punti di ricarica per veicoli elettrici",
        "label_en": "electric-vehicle charging points",
        "fonte": "GSE/MASE - Piattaforma Unica Nazionale (PUN)",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale (per punto di ricarica)",
        "copertura": {
            "nota_assenza": "copertura parziale: l'assenza significa 'nessun punto di ricarica censito nella PUN per il comune', non un divieto ne' uno zero certo",
            "nota_assenza_en": "partial coverage: absence means 'no charging point recorded in the PUN for the municipality', neither a ban nor a certain zero",
        },
        "freschezza": None,
        "licenza": None,
        "full": None,
    },

    "scuole": {
        "label": "scuole",
        "label_en": "schools",
        "fonte": "MIM (gia' MIUR) - Anagrafe scuole",
        "ambito": None,
        "qualificatore": "scuole statali e paritarie censite nell'anagrafe MIM",
        "qualificatore_en": "state and state-recognised schools recorded in the MIM register",
        "granularita": "comunale (per plesso/istituto)",
        "copertura": None,
        "freschezza": "annuale (anno scolastico)",
        "licenza": None,
        "full": None,
    },

    "terzo_settore": {       # sezione chatbot 'terzo_settore' -> shard dati 'runts'
        "label": "enti del Terzo Settore",
        "label_en": "third-sector entities",
        "fonte": "Ministero del Lavoro - RUNTS",
        "ambito": "SOLO gli enti iscritti al Registro Unico Nazionale del Terzo Settore, non tutte le associazioni del comune",
        "ambito_en": "ONLY the entities registered in the National Single Register of the Third Sector (RUNTS), not all associations in the municipality",
        "qualificatore": "enti iscritti al RUNTS",
        "qualificatore_en": "entities registered in the RUNTS",
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "il RUNTS ha valore di pubblicita' legale ma non e' esaustivo del volontariato: se non risultano enti, sono 'non iscritti al RUNTS', non necessariamente inesistenti",
            "nota_assenza_en": "the RUNTS has legal-publicity value but is not exhaustive of all voluntary activity: if no entities appear, they are 'not registered in the RUNTS', not necessarily non-existent",
        },
        "freschezza": None,
        "licenza": "CC BY 4.0",
        "full": None,
    },

    "siope": {
        "label": "incassi e pagamenti (SIOPE)",
        "label_en": "receipts and payments (SIOPE)",
        "fonte": "MEF - SIOPE (Banca d'Italia)",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "il dato puo' essere parziale: l'ultimo periodo potrebbe non essere ancora consolidato",
            "nota_assenza_en": "data may be partial: the most recent period may not be consolidated yet",
        },
        "freschezza": "mensile",
        "licenza": None,
        "full": None,
    },

    "demografia_dettaglio": {   # chiave canonica 'demografia_dettaglio' -> shard dir 'demografia'
        "label": "demografia",
        "label_en": "demographics",
        "fonte": "ISTAT",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": None,
        "freschezza": "annuale",
        "licenza": None,
        "full": None,
    },

    "pnrr": {
        "label": "progetti PNRR",
        "label_en": "PNRR (NRRP) projects",
        "fonte": "Italia Domani / ReGiS",
        "ambito": None,
        "qualificatore": "progetti finanziati dal PNRR, con stati diversi (concluso/in corso)",
        "qualificatore_en": "PNRR-funded projects, in different states (completed/ongoing)",
        "granularita": "comunale (per progetto)",
        "copertura": None,
        "freschezza": "periodica (ReGiS)",
        "licenza": None,
        "full": None,
    },

    "veicoli": {
        "label": "i veicoli del comune: parco circolante (stock) e immatricolazioni dell'anno (flusso)",
        "label_en": "municipal vehicles: circulating fleet (stock) and yearly new registrations (flow)",
        "fonte": "ISTAT + ACI",
        "qualificatore": None,
        "qualificatore_en": None,
        "granularita": "comunale",
    },

    # ------------------------------------------------------------------- #
    # TASK 3 - sezioni BLOCCO "a contesto" (solo canale CONTESTO, nessun _DISPATCH)
    # _meta SENZA cifre: anni/soglie arrivano all'LLM dai dati del motore.
    # ------------------------------------------------------------------- #
    "censimento": {
        "label": "censimento permanente",
        "label_en": "permanent census",
        "fonte": "ISTAT - Basi Territoriali + variabili censuarie",
        "ambito": "fotografia all'ultimo censimento permanente (rilevazione decennale), NON l'anno corrente",
        "ambito_en": "snapshot of the latest permanent census (decennial round), NOT the current year",
        "qualificatore": None,
        "granularita": "comunale; dettaglio per sezione di censimento disponibile",
        "copertura": None,
        "freschezza": "decennale",
        "licenza": "CC BY 3.0 IT",
        "full": {"endpoint": "/data/censimento_full/<istat>.geojson"},
    },

    "pendolarismo": {
        "label": "pendolarismo per lavoro/studio",
        "label_en": "commuting for work/study",
        "fonte": "ISTAT - Censimento permanente (matrice spostamenti)",
        "ambito": "spostamenti casa-lavoro/studio rilevati all'ultimo censimento permanente",
        "ambito_en": "home-to-work/study movements recorded at the latest permanent census",
        "qualificatore": "numero di PENDOLARI (da non confondere col numero di comuni di origine o destinazione)",
        "qualificatore_en": "number of COMMUTERS (not to be confused with the number of origin or destination municipalities)",
        "granularita": "comunale (flussi verso e da altri comuni)",
        "copertura": None,
        "freschezza": "decennale",
        "licenza": "CC BY 3.0 IT",
        "full": None,
    },

    "banda_larga": {            # chiave 'banda_larga' -> shard dir 'agcom_bbmap'
        "label": "copertura banda larga / FTTH",
        "label_en": "broadband / FTTH coverage",
        "fonte": "AGCOM - Broadband Map",
        "ambito": "copertura stimata delle famiglie; l'indice DESI (confidenza) e la soglia minima di velocita' sono due metriche diverse, da non confondere",
        "ambito_en": "estimated household coverage; the DESI index (confidence) and the minimum speed threshold are two different metrics, not to be confused",
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": {
            "nota_assenza": "AGCOM copre di norma tutti i comuni: se mancano i dati e' un buco di pubblicazione della mappa, non assenza di rete",
            "nota_assenza_en": "AGCOM normally covers every municipality: missing data is a gap in the map publication, not an absence of network",
        },
        "freschezza": "trimestrale",
        "licenza": None,
        "full": None,
    },

    "territorio": {
        "label": "ambiente e territorio (suolo, rischio idrogeologico, rifiuti)",
        "label_en": "environment and territory (land use, hydrogeological risk, waste)",
        "fonte": "ISPRA - Consumo di suolo / IdroGEO / Catasto Rifiuti",
        "ambito": "indicatori per tema con anni di riferimento diversi (es. consumo di suolo con serie storica pluriennale)",
        "ambito_en": "indicators by theme with different reference years (e.g. land consumption with a multi-year time series)",
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": None,
        "freschezza": None,
        "licenza": "CC BY 4.0",
        "full": None,
    },

    "sismica": {
        "label": "classificazione sismica del comune (zona 1-4)",
        "label_en": "municipal seismic classification (zone 1-4)",
        "fonte": "Dipartimento della Protezione Civile - Classificazione sismica (ex OPCM 3519/2006)",
        "ambito": "zona sismica amministrativa del comune (1 alta pericolosita', 4 bassa), con eventuale sottozona regionale; classificazione amministrativa, distinta dalla pericolosita' sismica di base (MPS04 INGV)",
        "ambito_en": "administrative seismic zone of the municipality (1 high hazard, 4 low), with optional regional sub-zone; administrative classification, distinct from base seismic hazard (MPS04 INGV)",
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": None,
        "freschezza": "annuale",
        "licenza": "CC BY 4.0",
        "caveat": ["zone: 1 alta pericolosita', 2, 3, 4 bassa pericolosita' (ex OPCM 3519/2006)",
                   "classificazione amministrativa per comune, NON la pericolosita' sismica di base (MPS04 INGV)"],
        "full": None,
    },

    "profilo": {
        "label": "profilo socio-demografico",
        "label_en": "socio-demographic profile",
        "fonte": "ISTAT - Censimento permanente (variabili aggregate)",
        "ambito": "indicatori socio-demografici aggregati (cittadinanza, famiglie, istruzione, lavoro, mobilita')",
        "ambito_en": "aggregate socio-demographic indicators (citizenship, families, education, employment, mobility)",
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": None,
        "freschezza": None,
        "licenza": "CC BY 3.0 IT",
        "full": None,
    },

    "anac": {
        "label": "contratti pubblici",
        "label_en": "public contracts",
        "fonte": "ANAC",
        "ambito": "dati AGGREGATI (numero contratti, importo totale, settori CPV), NON il singolo contratto",
        "ambito_en": "AGGREGATE data (number of contracts, total amount, CPV sectors), NOT the single contract",
        "qualificatore": None,
        "granularita": "comunale (stazione appaltante)",
        "copertura": None,
        "freschezza": None,
        "licenza": None,
        "full": None,
    },

    "anagrafica": {
        "label": "anagrafica del comune",
        "label_en": "municipality registry data",
        "fonte": "ISTAT / IPA",
        "ambito": "dati identificativi e di inquadramento del comune (denominazione, provincia, regione, codici)",
        "ambito_en": "identifying and reference data of the municipality (name, province, region, codes)",
        "qualificatore": None,
        "granularita": "comunale",
        "copertura": None,
        "freschezza": None,
        "licenza": None,
        "full": None,
    },

    # ------------------------------------------------------------------- #
    # TASK 3 - voce-con-dimensioni nel MOTORE (modello turismo): incidenti.
    # esegui_incidenti ritorna l'intero ultimo_anno (no filtro) -> qui si restringe.
    # ------------------------------------------------------------------- #
    "incidenti": {
        "label": "incidenti stradali",
        "label_en": "road accidents",
        "fonte": "ISTAT - ACI",
        "ambito": None,
        "qualificatore": None,
        "granularita": "comunale",
        "dimensioni": {
            "mortali": {
                "trigger": ["mort", "decess", "vittim"],
                "guida_it": ("La domanda riguarda i MORTI/le vittime degli incidenti: riporta SOLO "
                             "il numero di morti (campo 'morti') per l'anno indicato; NON elencare il "
                             "totale degli incidenti ne' i feriti."),
                "guida_en": ("The question is about people KILLED in road accidents: report ONLY the "
                             "number of deaths (field 'morti') for the given year; do NOT list the total "
                             "accidents or the injured."),
            },
            "feriti": {
                "trigger": ["ferit"],
                "guida_it": ("La domanda riguarda i FERITI: riporta SOLO il numero di feriti (campo "
                             "'feriti') per l'anno indicato; NON elencare il totale degli incidenti "
                             "ne' i morti."),
                "guida_en": ("The question is about the INJURED: report ONLY the number of injured "
                             "(field 'feriti') for the given year; do NOT list the total accidents or "
                             "the deaths."),
            },
        },
        "copertura": None,
        "freschezza": None,
        "licenza": None,
        "full": None,
    },

}


# --------------------------------------------------------------------------- #
# 2. HELPER DATA-SIDE (usati dalla pipeline in app_v2.py)
# --------------------------------------------------------------------------- #

def immobili_trigger_words():
    """Parole che attivano il pre-step (operazione -> conta) e la sostituzione del dato
    per le dimensioni aggregate (uso_terzi/vincolo)."""
    dims = SEMANTICA["immobili_pa"]["dimensioni"]
    out = []
    for d in dims.values():
        if d.get("stato") == "solo_aggregato_totale":
            out += d.get("trigger", [])
    return out


def immobili_dimensione_aggregata(domanda):
    """Restituisce la config della dimensione aggregata (uso_terzi/vincolo) che la domanda
    tocca, o None. Deterministico, ordine = ordine del dizionario."""
    dl = (domanda or "").lower()
    for d in SEMANTICA["immobili_pa"]["dimensioni"].values():
        if d.get("stato") != "solo_aggregato_totale":
            continue
        if any(w in dl for w in d.get("trigger", [])):
            return d
    return None


def opere_attivo_match():
    return SEMANTICA["opere"]["stato"]["attivo_match"]


# --------------------------------------------------------------------------- #
# 3. _meta NEI DATI + CONTESTO NEL PROMPT
# --------------------------------------------------------------------------- #

def meta_dati(sezione, lang="it"):
    """Campo _meta minimo (label/qualificatore/granularita) da iniettare in `dati`.
    SOLO testo, nessuna cifra: check_numerico lo ignora (non introduce numeri ammessi spuri).
    None se la sezione non e' nel dizionario."""
    S = SEMANTICA.get(sezione)
    if not S:
        return None
    if lang == "en":
        m = {"label": S.get("label_en") or S.get("label"),
             "granularita": S.get("granularita")}
        if S.get("qualificatore_en") or S.get("qualificatore"):
            m["qualificatore"] = S.get("qualificatore_en") or S.get("qualificatore")
    else:
        m = {"label": S.get("label"), "granularita": S.get("granularita")}
        if S.get("qualificatore"):
            m["qualificatore"] = S.get("qualificatore")
    amb = (S.get("ambito_en") if lang == "en" else S.get("ambito"))
    if amb:
        m["ambito"] = amb
    cop = S.get("copertura") or {}
    na = (cop.get("nota_assenza_en") if lang == "en" else cop.get("nota_assenza"))
    if na:
        m["nota_assenza"] = na
    return m


def contesto_prompt(meta, lang="it"):
    """Blocco 'CONTESTO SEMANTICO' da anteporre al prompt, costruito dal _meta nei dati.
    Solo testo, nessuna cifra. Stringa vuota se meta assente."""
    if not isinstance(meta, dict) or not meta.get("label"):
        return ""
    if lang == "en":
        parts = ["CONTEXT (do NOT cite as source): you are describing %s." % meta["label"]]
        if meta.get("granularita"):
            parts.append("Granularity: %s." % meta["granularita"])
        if meta.get("qualificatore"):
            parts.append("Always make clear they are %s." % meta["qualificatore"])
        if meta.get("ambito"):
            parts.append("Scope: %s." % meta["ambito"])
        if meta.get("nota_assenza"):
            parts.append("If an item is not in the data, do NOT say it does not exist in the municipality: %s" % meta["nota_assenza"])
        return NL + "SEMANTIC CONTEXT: " + " ".join(parts)
    parts = ["CONTESTO (non citarlo come fonte): stai descrivendo %s." % meta["label"]]
    if meta.get("granularita"):
        parts.append("Granularita': %s." % meta["granularita"])
    if meta.get("qualificatore"):
        parts.append("Precisa sempre che sono %s." % meta["qualificatore"])
    if meta.get("ambito"):
        parts.append("Ambito: %s." % meta["ambito"])
    if meta.get("nota_assenza"):
        parts.append("Se un elemento non risulta nei dati, NON dire che non esiste nel comune: %s" % meta["nota_assenza"])
    return NL + "CONTESTO SEMANTICO: " + " ".join(parts)


# --------------------------------------------------------------------------- #
# 4. COSTRUISCI_EXTRA (sostituisce i _vextra delle 3 voci pilota)
# --------------------------------------------------------------------------- #

def _extra_immobili(S, intento, dati, domanda, lang):
    # fire su tema (dato gia' sostituito a valle) o su operazione conta
    if not (isinstance(dati, dict) and (dati.get("tema") or intento.get("operazione") == "conta")):
        return ""
    g = S["guida"]
    base = g["base_en"] if lang == "en" else g["base_it"]
    tema = dati.get("tema")
    spec = ""
    if tema:
        dim = SEMANTICA["immobili_pa"]["dimensioni"].get(tema)
        if dim:
            etich = dim["etichetta_en"] if lang == "en" else dim["etichetta_it"]
            pct = dati.get(dim["pct_out"])
            stima = dati.get(dim["stima_key"])
            tpl = g["spec_en"] if lang == "en" else g["spec_it"]
            spec = tpl % (etich, pct, dati.get("n_totale"), stima)
    return NL + base + spec


def _extra_opere(S, intento, dati, domanda, lang):
    if not (intento.get("operazione") == "conta" and isinstance(dati, dict)
            and dati.get("n_totale") is not None):
        return ""
    st = S["stato"]; g = S["guida"]
    dl = (domanda or "").lower()
    concl = any(w in dl for w in st["trigger_conclusi"])
    nt = dati.get("n_totale"); na = dati.get("n_attivi"); nc = dati.get("n_conclusi")
    if concl:
        tpl = g["conclusi_en"] if lang == "en" else g["conclusi_it"]
        return NL + tpl % (na, nc, nc)
    coda = ""
    if nc:
        coda = (g["coda_conclusi_en"] if lang == "en" else g["coda_conclusi_it"]) % nc
    tpl = g["attivi_en"] if lang == "en" else g["attivi_it"]
    return NL + tpl % (nt, na, coda)


def _extra_turismo(S, intento, dati, domanda, lang):
    if not (isinstance(dati, dict) and isinstance(dati.get("capacita_comune"), dict)):
        return ""
    dims = S["dimensioni"]
    dl = (domanda or "").lower()
    flussi = any(w in dl for w in dims["flussi"]["trigger"])
    capac = any(w in dl for w in dims["capacita"]["trigger"])
    if flussi and not capac:
        d = dims["flussi"]
        return NL + (d["guida_en"] if lang == "en" else d["guida_it"])
    if capac:
        d = dims["capacita"]
        return NL + (d["guida_en"] if lang == "en" else d["guida_it"])
    return ""


def _extra_incidenti(S, intento, dati, domanda, lang):
    # dato grezzo = ultimo_anno (anno/incidenti/morti/feriti); nessun filtro nel motore
    # esecutore -> qui si restringe la verbalizzazione alla dimensione richiesta.
    if not isinstance(dati, dict):
        return ""
    dims = S.get("dimensioni") or {}
    dl = (domanda or "").lower()
    mort = any(t in dl for t in dims["mortali"]["trigger"])
    feri = any(t in dl for t in dims["feriti"]["trigger"])
    if mort and not feri:
        d = dims["mortali"]
        return NL + (d["guida_en"] if lang == "en" else d["guida_it"])
    if feri and not mort:
        d = dims["feriti"]
        return NL + (d["guida_en"] if lang == "en" else d["guida_it"])
    return ""


_BC_CONTATTI = {
    "sito_web":  ("il sito web", "the website"),
    "telefono":  ("il telefono", "the phone number"),
    "email":     ("l'email", "the email"),
    "indirizzo": ("l'indirizzo", "the address"),
}
def _extra_beni_culturali(S, intento, dati, domanda, lang):
    if not isinstance(dati, dict) or not dati.get("risultati"):
        return ""
    dims = S.get("dimensioni") or {}
    dl = (domanda or "").lower()
    hit = [n for n, c in dims.items() if any(t in dl for t in c["trigger"])]
    if not hit:
        return ""
    if "contatti" in hit:
        hit = ["telefono", "email", "sito_web"]
    campi = [c for c in ("sito_web", "telefono", "email", "indirizzo") if c in hit]
    if not campi:
        return ""
    if lang == "en":
        et = ", ".join(_BC_CONTATTI[c][1] for c in campi)
        return NL + ("The question asks for specific field(s) of the place: report ONLY %s "
                     "of the FIRST result, concisely, WITHOUT the full description. If a "
                     "requested field is absent from the data, say it is not available." % et)
    et = ", ".join(_BC_CONTATTI[c][0] for c in campi)
    return NL + ("La domanda chiede dato/i specifico/i del luogo: riporta SOLO %s del PRIMO "
                 "risultato, in modo conciso, SENZA la descrizione completa. Se un campo "
                 "richiesto manca nei dati, dillo." % et)


_DISPATCH = {
    "immobili_pa": _extra_immobili,
    "opere": _extra_opere,
    "turismo": _extra_turismo,
    "incidenti": _extra_incidenti,
    "beni_culturali": _extra_beni_culturali,
}


def costruisci_extra(sezione, intento, dati, domanda, lang="it"):
    """Genera l'istruzione `extra` per la verbalizzazione, in modo deterministico,
    dai campi di SEMANTICA[sezione]. Restituisce stringa vuota se la sezione non e'
    nel dizionario o se nessuna guida e' pertinente. Il valore inizia con NL (chr(10))
    come gli ex _vextra."""
    S = SEMANTICA.get(sezione)
    if not S:
        return ""
    fn = _DISPATCH.get(sezione)
    if not fn:
        return ""
    try:
        return fn(S, intento, dati, domanda, lang)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 5. SELF-CHECK SEMANTICA <-> GRAMMAR (fail-loud, chiamato al boot da app_v2)
# --------------------------------------------------------------------------- #

def verifica_coerenza(sezioni_grammar):
    """Confronta le chiavi di SEMANTICA con le sezioni GRAMMAR.
    Ritorna (chiavi_morte, scoperte):
      - chiavi_morte: in SEMANTICA ma NON in GRAMMAR (errore: il caveat non scattera' mai);
      - scoperte: in GRAMMAR ma senza voce semantica (informativo: nessun caveat semantico).
    Funzione pura: nessun import di intent_engine (evita cicli)."""
    gram = set(sezioni_grammar)
    morte = sorted(set(SEMANTICA) - gram)
    scoperte = sorted(gram - set(SEMANTICA))
    return morte, scoperte


def nota_assenza_diretta(sezione, lang="it"):
    """Forma DICHIARATIVA della nota di assenza, per il messaggio diretto del fallback
    (ramo senza LLM). None se la voce non la definisce: in tal caso il fallback usa la
    nota_assenza standard (gia' dichiarativa per le altre sezioni)."""
    S = SEMANTICA.get(sezione)
    if not S:
        return None
    cop = S.get("copertura") or {}
    return (cop.get("nota_assenza_diretta_en") if lang == "en"
            else cop.get("nota_assenza_diretta"))
