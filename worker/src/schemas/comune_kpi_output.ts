/**
 * outputSchema di comune_kpi (MCP 2025-06-18+).
 *
 * Derivato dai campioni reali in tests/worker/fixtures/kpi_*.json (Lecce, Roma,
 * Morterone, Ponte San Pietro). Tipi volutamente larghi (number|null) e
 * additionalProperties:true ovunque: lo schema descrive, non vincola, perche'
 * l'ETL aggiunge campi nel tempo. Il test tools.test.ts verifica che ogni
 * fixture sia conforme.
 */

export const comuneKpiOutputSchema: Record<string, unknown> = {
  "type": "object",
  "description": "KPI sintetici di un comune (kpi_summary dello shard dashboard). Gruppi tematici con soli scalari; null = dato non disponibile. Nuovi campi/gruppi possono comparire con l'evoluzione dell'ETL.",
  "required": [
    "_generated_at",
    "_etl_version",
    "anagrafica",
    "demografia"
  ],
  "properties": {
    "_generated_at": {
      "type": "string",
      "description": "Istante di generazione dello shard (ISO 8601)"
    },
    "_etl_version": {
      "type": "string",
      "description": "Versione ETL che ha prodotto lo shard"
    },
    "_missing": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Sorgenti assenti per questo comune (es. 'aria', 'anac')"
    },
    "ambiente": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISPRA consumo di suolo e rifiuti urbani",
      "properties": {
        "consumo_suolo_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "raccolta_differenziata_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "rifiuti_kg_per_abitante": {
          "type": [
            "number",
            "null"
          ]
        },
        "superficie_kmq": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "anagrafica": {
      "type": "object",
      "description": "Identificativi del comune (ISTAT, catastale, CF, provincia, regione)",
      "properties": {
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
        "istat": {
          "type": [
            "string",
            "null"
          ]
        },
        "nome": {
          "type": [
            "string",
            "null"
          ]
        },
        "provincia_sigla": {
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
      "additionalProperties": true
    },
    "aria_ispra": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISPRA SNPA qualita' dell'aria (medie annue, se presente una stazione)",
      "properties": {
        "anno": {
          "type": [
            "null",
            "number"
          ]
        },
        "ha_stazione": {
          "type": [
            "boolean",
            "null"
          ]
        },
        "no2_media": {
          "type": [
            "null",
            "number"
          ]
        },
        "pm10_media": {
          "type": [
            "null",
            "number"
          ]
        },
        "pm25_media": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "banda_larga_agcom": {
      "type": [
        "object",
        "null"
      ],
      "description": "AGCOM Broadband Map copertura FTTH",
      "properties": {
        "copertura_ftth_20m_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "copertura_ftth_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "data_rilevazione": {
          "type": [
            "string",
            "null"
          ]
        },
        "famiglie_residenti": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "beni_culturali_mic": {
      "type": [
        "object",
        "null"
      ],
      "description": "MiC ArCo + Cultural-ON beni culturali immobili",
      "properties": {
        "beni_per_1000_ab": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_beni_immobili": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_con_coordinate": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_senza_coordinate": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_visitabili": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_con_descrizione": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_con_foto": {
          "type": [
            "number",
            "null"
          ]
        },
        "snapshot_date": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "carburanti_mimit": {
      "type": [
        "object",
        "null"
      ],
      "description": "MIMIT Osservatorio prezzi carburanti",
      "properties": {
        "impianti_per_1000_ab": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_impianti": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_pompe_bianche": {
          "type": [
            "null",
            "number"
          ]
        },
        "prezzo_medio_benzina_self": {
          "type": [
            "null",
            "number"
          ]
        },
        "prezzo_medio_gasolio_self": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "civici_anncsu": {
      "type": [
        "object",
        "null"
      ],
      "description": "ANNCSU civici e strade, quota georeferenziata",
      "properties": {
        "n_civici": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_strade": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_geo_ref": {
          "type": [
            "number",
            "null"
          ]
        },
        "snapshot_date": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "contratti_anac": {
      "type": [
        "object",
        "null"
      ],
      "description": "ANAC aggiudicazioni (OCDS)",
      "properties": {
        "importo_per_abitante_eur": {
          "type": [
            "null",
            "number"
          ]
        },
        "importo_totale_eur": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_aggiudicazioni": {
          "type": [
            "null",
            "number"
          ]
        },
        "ultima_aggiudicazione": {
          "type": [
            "null",
            "string"
          ]
        }
      },
      "additionalProperties": true
    },
    "demografia": {
      "type": "object",
      "description": "Popolazione ISTAT POSAS e dinamica D7B dell'ultimo anno (nati, morti, saldo naturale, indici di struttura)",
      "properties": {
        "anno_dinamica": {
          "type": [
            "number",
            "null"
          ]
        },
        "eta_media": {
          "type": [
            "number",
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
        "maschi": {
          "type": [
            "number",
            "null"
          ]
        },
        "morti": {
          "type": [
            "number",
            "null"
          ]
        },
        "nati": {
          "type": [
            "number",
            "null"
          ]
        },
        "popolazione": {
          "type": [
            "number",
            "null"
          ]
        },
        "riferimento": {
          "type": [
            "string",
            "null"
          ]
        },
        "saldo_naturale": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "imprese_asia": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT ASIA unita' locali e addetti",
      "properties": {
        "addetti_per_ul": {
          "type": [
            "number",
            "null"
          ]
        },
        "addetti_totali": {
          "type": [
            "number",
            "null"
          ]
        },
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "ul_per_1000_ab": {
          "type": [
            "number",
            "null"
          ]
        },
        "ul_totali": {
          "type": [
            "number",
            "null"
          ]
        },
        "ul_yoy_pct": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "istruzione_profilo": {
      "type": [
        "object",
        "null"
      ],
      "description": "Censimento permanente: livello di istruzione (%)",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_diploma_oltre": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_terziario": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "lavoro_profilo": {
      "type": [
        "object",
        "null"
      ],
      "description": "Censimento permanente: tassi di attivita', occupazione, disoccupazione (%)",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "tasso_attivita": {
          "type": [
            "number",
            "null"
          ]
        },
        "tasso_disoccupazione": {
          "type": [
            "number",
            "null"
          ]
        },
        "tasso_occupazione": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "meteo_italiameteo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ItaliaMeteo ICON-2I previsione piu' recente",
      "properties": {
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
      "additionalProperties": true
    },
    "morfologia_cnr": {
      "type": [
        "object",
        "null"
      ],
      "description": "CNR-IRPI HR-DTM 5m: quota, pendenza, esposizione, irraggiamento",
      "properties": {
        "aspect_dom": {
          "type": [
            "string",
            "null"
          ]
        },
        "elev_max": {
          "type": [
            "number",
            "null"
          ]
        },
        "elev_mean": {
          "type": [
            "number",
            "null"
          ]
        },
        "elev_min": {
          "type": [
            "number",
            "null"
          ]
        },
        "slope_gt15_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "slope_mean": {
          "type": [
            "number",
            "null"
          ]
        },
        "solar_mean": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "opere_bdap": {
      "type": [
        "object",
        "null"
      ],
      "description": "BDAP-MOP opere pubbliche",
      "properties": {
        "importo_per_abitante_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "importo_totale_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_progetti": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "patrimonio_pa": {
      "type": [
        "object",
        "null"
      ],
      "description": "MEF Patrimonio immobiliare PA nel territorio comunale",
      "properties": {
        "n_fabbricati": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_immobili": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_terreni": {
          "type": [
            "number",
            "null"
          ]
        },
        "superficie_totale_mq": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "pendolarismo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT matrice pendolarismo 2021",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "auto_contenimento_pct": {
          "type": [
            "number",
            "null"
          ]
        },
        "entranti_totali": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_destinazioni": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_origini": {
          "type": [
            "number",
            "null"
          ]
        },
        "saldo_netto": {
          "type": [
            "number",
            "null"
          ]
        },
        "uscenti_totali": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "pnrr": {
      "type": [
        "object",
        "null"
      ],
      "description": "Italia Domani: progetti PNRR con CUP nel comune",
      "properties": {
        "importo_assegnato_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "importo_per_abitante_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_concluso": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_in_corso": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_progetti": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "redditi_mef": {
      "type": [
        "object",
        "null"
      ],
      "description": "MEF dichiarazioni IRPEF: reddito medio, imposta netta media, contribuenti",
      "properties": {
        "anno_fiscale": {
          "type": [
            "number",
            "null"
          ]
        },
        "imposta_netta_media_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_contribuenti": {
          "type": [
            "number",
            "null"
          ]
        },
        "reddito_medio_eur": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "ricarica_ev_pun": {
      "type": [
        "object",
        "null"
      ],
      "description": "GSE/MASE PUN punti di ricarica veicoli elettrici",
      "properties": {
        "n_attivi": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_totale": {
          "type": [
            "null",
            "number"
          ]
        },
        "pct_attivi": {
          "type": [
            "null",
            "number"
          ]
        },
        "potenza_totale_kw": {
          "type": [
            "null",
            "number"
          ]
        },
        "punti_per_1000_ab": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "sanita_mds": {
      "type": [
        "object",
        "null"
      ],
      "description": "Ministero della Salute: farmacie, parafarmacie, ospedali, posti letto",
      "properties": {
        "farmacie_per_1000_ab": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_farmacie": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_ospedali": {
          "type": [
            "null",
            "number"
          ]
        },
        "n_parafarmacie": {
          "type": [
            "null",
            "number"
          ]
        },
        "posti_letto_ospedalieri": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "scuole_miur": {
      "type": [
        "object",
        "null"
      ],
      "description": "MIUR anagrafe scuole",
      "properties": {
        "_nota": {
          "type": [
            "string",
            "null"
          ]
        },
        "anno_scolastico": {
          "type": [
            "null",
            "string"
          ]
        },
        "n_scuole": {
          "type": [
            "null",
            "number"
          ]
        },
        "scuole_per_1000_ab": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "siope": {
      "type": [
        "object",
        "null"
      ],
      "description": "SIOPE cassa dell'anno corrente: incassi, uscite, saldo (parziale=true se anno non completo)",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "incassi_per_abitante_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "mesi_disponibili": {
          "type": [
            "number",
            "null"
          ]
        },
        "parziale": {
          "type": [
            "boolean",
            "null"
          ]
        },
        "saldo_cassa_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "totale_incassi_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "totale_uscite_eur": {
          "type": [
            "number",
            "null"
          ]
        },
        "uscite_per_abitante_eur": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "terzo_settore_runts": {
      "type": [
        "object",
        "null"
      ],
      "description": "RUNTS enti del terzo settore",
      "properties": {
        "enti_per_1000_ab": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_5x1000": {
          "type": [
            "number",
            "null"
          ]
        },
        "n_enti_totali": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_5x1000": {
          "type": [
            "number",
            "null"
          ]
        },
        "snapshot_date": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "additionalProperties": true
    },
    "turismo": {
      "type": [
        "object",
        "null"
      ],
      "description": "ISTAT capacita' ricettiva",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "indice_turisticita_per_100ab": {
          "type": [
            "null",
            "number"
          ]
        },
        "totale_letti": {
          "type": [
            "null",
            "number"
          ]
        },
        "totale_strutture": {
          "type": [
            "null",
            "number"
          ]
        }
      },
      "additionalProperties": true
    },
    "veicoli_aci": {
      "type": [
        "object",
        "null"
      ],
      "description": "ACI parco veicolare e classi ambientali",
      "properties": {
        "anno": {
          "type": [
            "number",
            "null"
          ]
        },
        "autovetture": {
          "type": [
            "number",
            "null"
          ]
        },
        "pct_inquinanti": {
          "type": [
            "number",
            "null"
          ]
        },
        "tasso_motorizzazione_per_1000_ab": {
          "type": [
            "number",
            "null"
          ]
        },
        "totale_veicoli": {
          "type": [
            "number",
            "null"
          ]
        }
      },
      "additionalProperties": true
    }
  },
  "additionalProperties": true
};
