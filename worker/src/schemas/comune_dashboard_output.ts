/**
 * outputSchema di comune_dashboard.
 *
 * Esposto in tools/list SOLO a livello di sezione (tipo + descrizione): il
 * dettaglio a due livelli portava tools/list da 31 a 56 KB, pagati da ogni
 * client LLM a ogni conversazione. Il dettaglio delle chiavi immediate di
 * ogni sezione e' in comuneDashboardSectionsDetail, usato dal test
 * output_schema.test.ts per validare le fixture reali
 * (tests/worker/fixtures/dash_*.json: Lecce, Morterone, Ponte San Pietro,
 * liste tagliate al primo elemento).
 */

export const comuneDashboardOutputSchema: Record<string, unknown> = {
  "type": "object",
  "description": "Shard dashboard completo del comune: ~28 sezioni tematiche (una per fonte), ciascuna un oggetto o null se la sorgente e' assente per il comune (vedi _missing). Schema esposto al solo livello di sezione per contenere il peso di tools/list; la struttura interna delle sezioni e' documentata nella skill cruscotto-italia-workflow.",
  "required": [
    "_generated_at",
    "_etl_version",
    "anagrafica"
  ],
  "properties": {
    "_generated_at": {
      "type": "string"
    },
    "_etl_version": {
      "type": "string"
    },
    "_missing": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Sorgenti assenti per questo comune"
    },
    "anagrafica": {
      "type": "object",
      "description": "Identificativi e categoria IPA del comune",
      "additionalProperties": true
    },
    "demografia": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT POSAS: popolazione per sesso, fasce eta', piramide, serie storica 5 annate, dinamica D7B (nati, morti, saldi)",
      "additionalProperties": true
    },
    "profilo": {
      "type": [
        "object",
        "null"
      ],
      "description": "Censimento permanente: cittadinanza, famiglie, istruzione, lavoro, mobilita'",
      "additionalProperties": true
    },
    "turismo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT capacita' ricettiva comunale e flussi provinciali",
      "additionalProperties": true
    },
    "pnrr": {
      "type": [
        "object",
        "null"
      ],
      "description": "Italia Domani: KPI, ripartizione per missione, elenco progetti con CUP",
      "additionalProperties": true
    },
    "territorio": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISPRA suolo/rifiuti/rischio idrogeologico + classificazione sismica DPC + geo",
      "additionalProperties": true
    },
    "opere": {
      "type": [
        "object",
        "null"
      ],
      "description": "BDAP-MOP: elenco opere pubbliche del comune",
      "additionalProperties": true
    },
    "siope": {
      "type": [
        "object",
        "null"
      ],
      "description": "SIOPE cassa: per anno, pagamenti/incassi mensili per codice gestionale, saldo",
      "additionalProperties": true
    },
    "scuole": {
      "type": [
        "object",
        "null"
      ],
      "description": "MIUR: KPI e anagrafe plessi",
      "additionalProperties": true
    },
    "aria": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISPRA SNPA: stazioni, ultimo anno, trend decennale",
      "additionalProperties": true
    },
    "veicoli": {
      "type": [
        "object",
        "null"
      ],
      "description": "ACI: parco veicolare per classe ambientale, nuove iscrizioni, incidenti",
      "additionalProperties": true
    },
    "redditi": {
      "type": [
        "object",
        "null"
      ],
      "description": "MEF IRPEF: per anno fiscale, trend",
      "additionalProperties": true
    },
    "immobili_pa": {
      "type": [
        "object",
        "null"
      ],
      "description": "MEF Patrimonio PA: KPI e punti georeferenziati",
      "additionalProperties": true
    },
    "anncsu": {
      "type": [
        "object",
        "null"
      ],
      "description": "ANNCSU: KPI civici e campione di punti (dataset completo via /data/anncsu_full/)",
      "additionalProperties": true
    },
    "sanita_mds": {
      "type": [
        "object",
        "null"
      ],
      "description": "Ministero della Salute: farmacie, parafarmacie, ospedali con posti letto",
      "additionalProperties": true
    },
    "pun": {
      "type": [
        "object",
        "null"
      ],
      "description": "GSE/MASE PUN: punti di ricarica EV",
      "additionalProperties": true
    },
    "agcom_bbmap": {
      "type": [
        "object",
        "null"
      ],
      "description": "AGCOM Broadband Map: copertura FTTH/FWA per tecnologia",
      "additionalProperties": true
    },
    "carburanti": {
      "type": [
        "object",
        "null"
      ],
      "description": "MIMIT: impianti e prezzi",
      "additionalProperties": true
    },
    "runts": {
      "type": [
        "object",
        "null"
      ],
      "description": "RUNTS: KPI e elenco enti del terzo settore",
      "additionalProperties": true
    },
    "asia": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT ASIA: unita' locali e addetti, dettaglio ATECO, serie storica",
      "additionalProperties": true
    },
    "censimento": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT Basi Territoriali 2021 + variabili 2023: KPI comune e distribuzioni (geometrie via /data/censimento_full/)",
      "additionalProperties": true
    },
    "beni_culturali": {
      "type": [
        "object",
        "null"
      ],
      "description": "MiC ArCo + Cultural-ON: KPI e luoghi (cap 30, lista completa via /data/beni_culturali_full/)",
      "additionalProperties": true
    },
    "meteo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ItaliaMeteo ICON-2I: previsione piu' recente",
      "additionalProperties": true
    },
    "morfologia": {
      "type": [
        "object",
        "null"
      ],
      "description": "CNR-IRPI HR-DTM 5m: statistiche di quota/pendenza/esposizione e bounds",
      "additionalProperties": true
    },
    "pendolarismo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT matrice pendolarismo 2021: KPI",
      "additionalProperties": true
    },
    "anac": {
      "type": [
        "object",
        "null"
      ],
      "description": "ANAC OCDS: aggiudicazioni aggregate, CPV, top CPV",
      "additionalProperties": true
    },
    "bdap_kpi": {
      "type": [
        "object",
        "null"
      ],
      "description": "BDAP-MOP: totali, per stato, top settori",
      "additionalProperties": true
    },
    "kpi_summary": {
      "type": [
        "object",
        "null"
      ],
      "description": "Sintesi KPI: stessa struttura della risposta di comune_kpi (vedi il suo outputSchema)"
    }
  },
  "additionalProperties": true
};

/** Chiavi immediate di ogni sezione con tipo: validazione di secondo livello nei test. */
export const comuneDashboardSectionsDetail: Record<string, Record<string, unknown>> = {
  "anagrafica": {
    "codice_catastale": {
      "type": [
        "string",
        "null"
      ]
    },
    "codice_fiscale": {
      "type": [
        "string",
        "null"
      ]
    },
    "codice_ipa": {
      "type": [
        "string",
        "null"
      ]
    },
    "denominazione": {
      "type": [
        "string",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "nome_categoria": {
      "type": [
        "string",
        "null"
      ]
    },
    "provincia": {
      "type": [
        "string",
        "null"
      ]
    },
    "regione": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "demografia": {
    "_anno_dati_dinamica": {
      "type": [
        "number",
        "null"
      ]
    },
    "_anno_riferimento": {
      "type": [
        "number",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_riferimento": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_stima": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "comune": {
      "type": [
        "string",
        "null"
      ]
    },
    "dinamica": {
      "type": [
        "object",
        "null"
      ]
    },
    "eta_media": {
      "type": [
        "number",
        "null"
      ]
    },
    "fasce_eta": {
      "type": [
        "object",
        "null"
      ]
    },
    "femmine": {
      "type": [
        "number",
        "null"
      ]
    },
    "indice_dipendenza": {
      "type": [
        "number",
        "null"
      ]
    },
    "indice_vecchiaia": {
      "type": [
        "number",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "maschi": {
      "type": [
        "number",
        "null"
      ]
    },
    "pct_femmine": {
      "type": [
        "number",
        "null"
      ]
    },
    "pct_maschi": {
      "type": [
        "number",
        "null"
      ]
    },
    "piramide": {
      "type": [
        "array",
        "null"
      ]
    },
    "popolazione_totale": {
      "type": [
        "number",
        "null"
      ]
    },
    "serie_storica": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "profilo": {
    "cittadinanza": {
      "type": [
        "object",
        "null"
      ]
    },
    "codice_istat": {
      "type": [
        "string",
        "null"
      ]
    },
    "famiglie": {
      "type": [
        "object",
        "null"
      ]
    },
    "fonte": {
      "type": [
        "string",
        "null"
      ]
    },
    "fonte_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "istruzione": {
      "type": [
        "object",
        "null"
      ]
    },
    "lavoro": {
      "type": [
        "object",
        "null"
      ]
    },
    "mobilita": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "turismo": {
    "capacita_comune": {
      "type": [
        "object",
        "null"
      ]
    },
    "codice_istat": {
      "type": [
        "string",
        "null"
      ]
    },
    "flussi_provincia": {
      "type": [
        "object",
        "null"
      ]
    },
    "fonte": {
      "type": [
        "string",
        "null"
      ]
    },
    "fonte_url": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "pnrr": {
    "codice_istat": {
      "type": [
        "string",
        "null"
      ]
    },
    "data_estrazione": {
      "type": [
        "string",
        "null"
      ]
    },
    "fonte": {
      "type": [
        "string",
        "null"
      ]
    },
    "fonte_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "per_missione": {
      "type": [
        "array",
        "null"
      ]
    },
    "progetti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "territorio": {
    "_extracted_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "classificazione_sismica": {
      "type": [
        "object",
        "null"
      ]
    },
    "denominazione": {
      "type": [
        "string",
        "null"
      ]
    },
    "geo": {
      "type": [
        "object",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "provincia": {
      "type": [
        "string",
        "null"
      ]
    },
    "regione": {
      "type": [
        "string",
        "null"
      ]
    },
    "rifiuti": {
      "type": [
        "object",
        "null"
      ]
    },
    "rischio_idrogeologico": {
      "type": [
        "object",
        "null"
      ]
    },
    "suolo": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "opere": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_filter": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "codice_fiscale": {
      "type": [
        "string",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "n_progetti": {
      "type": [
        "number",
        "null"
      ]
    },
    "progetti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "siope": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_licenza": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "anni_disponibili": {
      "type": [
        "array",
        "null"
      ]
    },
    "anno_default": {
      "type": [
        "number",
        "null"
      ]
    },
    "per_anno": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "scuole": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "anno_scolastico": {
      "type": [
        "string",
        "null"
      ]
    },
    "data_estrazione": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "scuole": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "aria": {
    "_aggiornamento_atteso": {
      "type": [
        "string",
        "null"
      ]
    },
    "_anno_dati": {
      "type": [
        "number",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "comune": {
      "type": [
        "string",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "n_stazioni": {
      "type": [
        "number",
        "null"
      ]
    },
    "provincia": {
      "type": [
        "string",
        "null"
      ]
    },
    "regione": {
      "type": [
        "string",
        "null"
      ]
    },
    "stazioni": {
      "type": [
        "array",
        "null"
      ]
    },
    "stazioni_dettaglio": {
      "type": [
        "array",
        "null"
      ]
    },
    "trend_decennale": {
      "type": [
        "object",
        "null"
      ]
    },
    "ultimo_anno": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "veicoli": {
    "_aggiornamento_atteso": {
      "type": [
        "string",
        "null"
      ]
    },
    "_anno_dati_incidenti": {
      "type": [
        "number",
        "null"
      ]
    },
    "_anno_dati_iscrizioni": {
      "type": [
        "number",
        "null"
      ]
    },
    "_anno_dati_parco": {
      "type": [
        "number",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "denominazione": {
      "type": [
        "string",
        "null"
      ]
    },
    "incidenti": {
      "type": [
        "object",
        "null"
      ]
    },
    "iscrizioni": {
      "type": [
        "object",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "parco_veicoli": {
      "type": [
        "object",
        "null"
      ]
    },
    "popolazione": {
      "type": [
        "number",
        "null"
      ]
    }
  },
  "redditi": {
    "anni": {
      "type": [
        "object",
        "null"
      ]
    },
    "anni_disponibili": {
      "type": [
        "array",
        "null"
      ]
    },
    "cod_catastale": {
      "type": [
        "string",
        "null"
      ]
    },
    "comune": {
      "type": [
        "string",
        "null"
      ]
    },
    "fonte": {
      "type": [
        "string",
        "null"
      ]
    },
    "istat_comune": {
      "type": [
        "string",
        "null"
      ]
    },
    "last_update": {
      "type": [
        "string",
        "null"
      ]
    },
    "licenza": {
      "type": [
        "string",
        "null"
      ]
    },
    "regione": {
      "type": [
        "string",
        "null"
      ]
    },
    "sigla_provincia": {
      "type": [
        "string",
        "null"
      ]
    },
    "trend": {
      "type": [
        "array",
        "null"
      ]
    },
    "url_fonte": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "immobili_pa": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "anno_rilevazione": {
      "type": [
        "number",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "punti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "anncsu": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_snapshot_date": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "punti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "sanita_mds": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_fonti": {
      "type": [
        "object",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "comune": {
      "type": [
        "string",
        "null"
      ]
    },
    "farmacie": {
      "type": [
        "object",
        "null"
      ]
    },
    "istat_code": {
      "type": "string"
    },
    "ospedali": {
      "type": [
        "object",
        "null"
      ]
    },
    "parafarmacie": {
      "type": [
        "null",
        "object"
      ]
    },
    "provincia": {
      "type": [
        "string",
        "null"
      ]
    },
    "regione": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "pun": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "punti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "agcom_bbmap": {
    "_data_period": {
      "type": [
        "string",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "anagrafica_locale": {
      "type": [
        "object",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "mappa_ufficiale": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "carburanti": {
    "_data_last_modified": {
      "type": [
        "string",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "punti": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "runts": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_snapshot_date": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "enti": {
      "type": [
        "array",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "asia": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_latest_year": {
      "type": [
        "number",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "_years_available": {
      "type": [
        "array",
        "null"
      ]
    },
    "ateco_dettaglio": {
      "type": [
        "object",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "serie_storica": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "censimento": {
    "_anno_rilevazione": {
      "type": [
        "number",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_has_asc": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "_has_full": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "distribuzioni_comune": {
      "type": [
        "object",
        "null"
      ]
    },
    "kpi_comune": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "beni_culturali": {
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_full_shard_available": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_luoghi_cap": {
      "type": [
        "number",
        "null"
      ]
    },
    "_luoghi_total": {
      "type": [
        "number",
        "null"
      ]
    },
    "_luoghi_truncated": {
      "type": [
        "boolean",
        "null"
      ]
    },
    "_snapshot_date": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    },
    "luoghi": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "meteo": {
    "fonte": {
      "type": [
        "string",
        "null"
      ]
    },
    "licenza": {
      "type": [
        "string",
        "null"
      ]
    },
    "neve_cm": {
      "type": [
        "number",
        "null"
      ]
    },
    "nuvolosita_pct": {
      "type": [
        "number",
        "null"
      ]
    },
    "prec_24h_mm": {
      "type": [
        "number",
        "null"
      ]
    },
    "raffica_max24h_kmh": {
      "type": [
        "number",
        "null"
      ]
    },
    "run_utc": {
      "type": [
        "string",
        "null"
      ]
    },
    "t2m_c": {
      "type": [
        "number",
        "null"
      ]
    },
    "t2m_max24h_c": {
      "type": [
        "number",
        "null"
      ]
    },
    "t2m_min24h_c": {
      "type": [
        "number",
        "null"
      ]
    },
    "umidita_pct": {
      "type": [
        "number",
        "null"
      ]
    },
    "valid_time_utc": {
      "type": [
        "string",
        "null"
      ]
    },
    "vento_dir_deg": {
      "type": [
        "number",
        "null"
      ]
    },
    "vento_kmh": {
      "type": [
        "number",
        "null"
      ]
    },
    "ww": {
      "type": [
        "number",
        "null"
      ]
    },
    "ww_desc": {
      "type": [
        "string",
        "null"
      ]
    }
  },
  "morfologia": {
    "bounds": {
      "type": [
        "object",
        "null"
      ]
    },
    "stats": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "pendolarismo": {
    "_anno_rilevazione": {
      "type": [
        "number",
        "null"
      ]
    },
    "_etl_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "_generated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "_license": {
      "type": [
        "string",
        "null"
      ]
    },
    "_motivo_spostamento": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source": {
      "type": [
        "string",
        "null"
      ]
    },
    "_source_url": {
      "type": [
        "string",
        "null"
      ]
    },
    "kpi": {
      "type": [
        "object",
        "null"
      ]
    }
  },
  "anac": {
    "buyer_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "count": {
      "type": [
        "number",
        "null"
      ]
    },
    "cpv": {
      "type": [
        "array",
        "null"
      ]
    },
    "distinct_cpv": {
      "type": [
        "number",
        "null"
      ]
    },
    "first_award_date": {
      "type": [
        "string",
        "null"
      ]
    },
    "importo_totale": {
      "type": [
        "number",
        "null"
      ]
    },
    "last_award_date": {
      "type": [
        "string",
        "null"
      ]
    },
    "top_cpv": {
      "type": [
        "array",
        "null"
      ]
    }
  },
  "bdap_kpi": {
    "nome_titolare": {
      "type": [
        "string",
        "null"
      ]
    },
    "per_stato": {
      "type": [
        "object",
        "null"
      ]
    },
    "top_settori": {
      "type": [
        "array",
        "null"
      ]
    },
    "totale": {
      "type": [
        "object",
        "null"
      ]
    }
  }
};
