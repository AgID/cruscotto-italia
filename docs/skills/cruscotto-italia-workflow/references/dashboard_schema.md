# Dashboard schema reference (v1.3)

Key map completo della response `comune_dashboard`. Da usare per estrarre campi specifici. Le chiavi top-level sono elencate nell'ordine restituito dall'API. I nomi tra `backtick` sono chiavi JSON letterali.

Server di riferimento: `cruscotto-italia-mcp.agid.workers.dev` (19 dataset, 15 istituzioni, ~7.918 comuni).

> **Nota**: per query puntuali e confronti tra comuni preferisci `comune_kpi` (~620 token, 55 KPI sintetici). Usa `comune_dashboard` SOLO quando servono array dettagliati (top liste, mappe, time series) o per la vista approfondita single-comune. Lo schema di `comune_kpi` è documentato in `SKILL.md`.

## Metadata

- `_etl_version` — versione pipeline ETL
- `_generated_at` — timestamp ISO 8601 di questa response
- `_missing` — array di sezioni non disponibili per il comune (es. `["aria"]`)

Sempre controllare `_missing` prima di dichiarare una sezione mancante: potrebbe semplicemente non essere stata generata per quel comune.

## `anagrafica`

Identità di base. Chiavi: `istat_code`, `denominazione`, `provincia`, `regione`, `codice_ipa`, `codice_fiscale`, `nome_categoria`, `codice_catastale`, `kpi`.

`kpi` è un riassunto quick-glance con `contratti`, `opere`, `spese_siope`, `coesione`, `popolazione`. Spesso `null` per comuni più piccoli.

## `demografia`

Fonte: ISTAT POSAS (popolazione residente per età e sesso). Riferimento: 1 gennaio (stima anno corrente).

- `popolazione_totale`, `maschi`, `femmine`, `pct_maschi`, `pct_femmine`
- `fasce_eta` — buckets: `0_14`, `15_64`, `65_piu`, `85_piu`, ognuno con `n` e `pct`
- `eta_media`
- `indice_vecchiaia` — pop65+/pop0-14 × 100
- `indice_dipendenza` — (pop0-14 + pop65+) / pop15-64 × 100
- `piramide` — array di 101 voci (età 0–100), ognuna con `eta`, `m`, `f`, `tot`

## `profilo`

Fonte: ISTAT Censimento permanente.

- `istruzione` — livelli istruzione fascia 25–64: `terziario_pct`, `diploma_oltre_pct`, `max_media_pct`, dettaglio per titolo (`nessun_titolo`, `elementare`, `media`, `diploma`, `laurea_triennale`, `laurea_magistrale_dottorato`)
- `lavoro` — `tasso_occupazione`, `tasso_disoccupazione`, `tasso_attivita` con conteggi assoluti
- `famiglie` — `n_famiglie`, `dim_media_famiglia`
- `mobilita` — pendolarismo. **Ferma al 2019** con `_warning`. Chiavi: `pendolari_totale_n`, `fuori_comune_n`, `fuori_comune_pct`, `per_lavoro_n`, `per_studio_n`
- `cittadinanza` — `italiani_n`, `stranieri_n`, `stranieri_pct`

## `turismo`

Due sotto-sezioni a scopo geografico diverso:

- `capacita_comune` — capacità ricettiva comunale. Chiavi: `totale_strutture`, `totale_letti`, `totale_camere`, `indice_turisticita_per_100ab`. Breakdown in `alberghi` (per stelle 1–5 + residence) ed `extra_alberghiero` (bnb, case in affitto, camping, agriturismi, ostelli, case per ferie, rifugi montagna).
- `flussi_provincia` — arrivi e presenze, **solo provinciale (NUTS3)**, con `_warning`. Chiavi: `arrivi_totali`, `arrivi_italiani`, `arrivi_stranieri`, `presenze_totali`, `presenze_italiane`, `presenze_straniere`, `permanenza_media`, `stranieri_pct`.

## `pnrr`

Fonte: Italia Domani / Sistema ReGiS. Solo progetti dove il comune è Soggetto Attuatore.

- `kpi` — `n_progetti`, `totale_finanziamento_pnrr`, `totale_finanziamento_globale`, conteggi per stato (`n_concluso`, `n_in_corso`, `n_altro`), `n_missioni_distinte`, `missioni_principali`
- `per_missione` — aggregato per missione (M1–M7) con descrizione e totali
- `progetti` — array. Ogni progetto: `cup`, `titolo`, `missione`, `componente`, `submisura`, `submisura_descrizione`, `finanziamento_pnrr`, `finanziamento_totale`, `stato_avanzamento` (Concluso / In Corso), `fase_iter`, date (prevista / effettiva), `soggetto_attuatore`, `settore`, `natura`
- `data_estrazione` — ultimo refresh

## `territorio`

Fonti: ISPRA SNPA (suolo), ISPRA IdroGEO (rischio), ISPRA Catasto Rifiuti, Dipartimento Protezione Civile (classificazione sismica).

- `kpi` — headline: `ar_kmq`, `suolo_consumato_2024_pct`, `incremento_2024_ha`, `popolazione_frane_p3p4_pct`, `rd_pct_ultimo_anno`, `kg_per_abitante_ultimo_anno`
- `suolo`
  - `stock_2024` — `ha` e `pct`
  - `serie_storica` — array di intervalli (2006-2012, 2012-2015, ..., 2023-2024) con `netto_ha` e `ripristino_ha`
- `rischio_idrogeologico`
  - `alluvioni` — per classe pericolosità (P1, P2, P3): area kmq e pct, popolazione esposta, famiglie, edifici, imprese, beni culturali
  - `frane` — stessa struttura, più P4 e AA (Aree di Attenzione); aggregato `p3p4`
  - `_demografia_idrogeo` — denominatori (popolazione 2011 e 2021, famiglie, edifici, imprese)
- `rifiuti`
  - `ultimo_anno` — anno più recente + KPI (`popolazione`, `ru_t`, `rd_t`, `rd_pct`, `kg_ab`)
  - `serie_storica` — array annuale dal ~2018
  - `_aggregato`, `_aggregato_ruolo` — per comuni in Unioni con gestione rifiuti consolidata
- `geo` — `ar_kmq`, `osmid`, `extent` (bounding box)
- `classificazione_sismica` — Fonte: DPC (CC-BY 4.0). `zona_sismica` (stringa: "1","2"/"2A"/"2B","3"/"3A"/"3B"/"3S","4"), `zona_principale` (int 1-4: 1 alta → 4 bassa). Ex OPCM 3519/2006. Copertura 7896/7896, annuale. Distinto dalla pericolosità di base MPS04 INGV.

## `opere`

Fonte: BDAP MOP. Vista sintetica.

- `_filter` — tipicamente `"only_2025"` (attivi + chiusi anno corrente)
- `n_progetti`
- `progetti` — array. Ogni progetto: `cup`, `descrizione`, `stato` (ATTIVO / CHIUSO), `settore`, `sottosettore`, `natura`, `data_inizio`, `data_fine` (`9999-12-31` = open-ended), `costo_eff`, `costo_prev`, e finanziamenti (`fin_statali`, `fin_europei`, `fin_enti_terr`, `fin_privati`, `fin_altri`)

Per vista aggregata per stato/settore → `bdap_kpi`. Per dettaglio completo filtrabile → tool `comune_opere_dettaglio`.

## `siope`

Fonte: **MEF — Ragioneria Generale dello Stato, SIOPE** (banca dati gestita da
Banca d'Italia, `siope.it`). Licenza CC BY 4.0. **Multi-anno**.

Sono movimenti di **cassa**, non di competenza: il saldo di cassa **non è un
avanzo di bilancio**. Copertura 7.896 comuni, serie mensile.

- `_etl_version` (`0.3.0`), `_source`, `_licenza`, `_generated_at`
- `anni_disponibili` — array degli anni con dati (es. `[2025, 2026]`)
- `anno_default` — ultimo anno **chiuso** (12 mesi) disponibile per quel comune;
  se nessun anno è chiuso, l'ultimo parziale
- `per_anno` — dict keyed by year (stringa). Ogni anno:
  - `anno`, `parziale` (boolean — derivato da `len(mesi_disponibili) < 12`)
  - `ente_siope`, `popolazione`
  - `mesi_disponibili` — array tipo `["2025/01", "2025/02", ..., "2025/12"]`
  - `ultimo_mese`
  - **USCITE = pagamenti di cassa** (chiavi al primo livello, invariate):
    - `n_voci`, `totale_anno`
    - `voci` — array. Ogni voce: `codice_gestionale`, `desc_gestionale`,
      `codice_titolo`, `desc_titolo`, `importo_cumulato`, `ultimo_mese`,
      `mensili` (dict **cumulativo**, chiave `"YYYY/MM"`)
  - **ENTRATE = incassi di cassa** (dal 28/07/2026):
    - `entrate` — `{ n_voci, totale_anno, voci }`, stessa struttura delle uscite
  - `saldo_cassa` — `entrate.totale_anno - totale_anno`. **Può essere negativo.**

Titoli (`codice_titolo`) — prefisso `U` per le uscite, `E` per le entrate:

| Uscite | | Entrate | |
|---|---|---|---|
| `U0...` | Pagamenti da regolarizzare | `E0...` | Incassi da regolarizzare |
| `U1...` | Spese correnti | `E1...` | Entrate tributarie e perequative |
| `U2...` | Spese in conto capitale | `E2...` | Trasferimenti correnti |
| `U3...` | Incremento attività finanziarie | `E3...` | Entrate extratributarie |
| `U4...` | Rimborso prestiti | `E4...` | Entrate in conto capitale |
| `U5...` | Chiusura anticipazioni tesoriere | `E5...` | Riduzione attività finanziarie |
| `U7...` | Uscite per conto terzi e partite di giro | `E6...` | Accensione prestiti |
| | | `E7...` | Anticipazioni da tesoriere |
| | | `E9...` | Entrate per conto terzi e partite di giro |

**Attenzione ai codici "da regolarizzare"** (`U0...`, `E0...`): possono avere
importi mensili **negativi** per storni, e il totale della voce può essere
negativo.

## `scuole`

Fonte: MIUR DS0400SCUANAGRAFESTAT + DS0420SCUANAAUTSTAT.

- `anno_scolastico`, `data_estrazione`
- `kpi` — `n_scuole` (numero di PLESSI, cioe' punti di erogazione del servizio, non istituzioni scolastiche autonome), `n_sedi_direttivo` (plessi che ospitano la dirigenza: proxy del numero di istituzioni), `per_ordine` (infanzia / primaria / sec1 / sec2 / altro)
- `scuole` — array. Ogni scuola: `codice_scuola`, `codice_istituto_riferimento`, `denominazione`, `denominazione_istituto`, `indirizzo`, `cap`, `tipologia`, `macro_ordine`, `caratteristica`, `sede_direttivo`, `sede_scolastica`, `email`, `pec`, `sito_web`

## `aria`

Fonte: ISPRA SNPA. Disponibile per ~604 comuni con stazioni. **Spesso `null`** — controllare sempre prima.

Quando presente: dati monitoraggio PM10, PM2.5, NO2 con lista stazioni e misure.

## `veicoli` — nota: gli incidenti stanno qui

Fonte: ISTAT 41_993 (parco PRA) + ISTAT 41_983 (incidenti) + ACI LOD (iscrizioni).

Top-level: `_anno_dati_parco`, `_anno_dati_incidenti`, `_anno_dati_iscrizioni`, `istat_code`, `denominazione`, `popolazione`.

Tre sotto-sezioni:

- `parco_veicoli`
  - Conteggi per categoria: `autovetture`, `autobus`, `motocicli`, `motocarri`, `altri`, `autocarri`, `motrici`, `rimorchi`
  - `euro` — conteggi per classe Euro (`euro_0` ... `euro_6`) + `pct_inquinanti` (% Euro 0–3)
  - `totale`, `anno`, `tasso_motorizzazione_per_1000_ab`

- `incidenti` — **qui vivono gli incidenti, non al top level**
  - `ultimo_anno` — `anno`, `incidenti`, `morti`, `feriti`, `morti_per_10k_ab`, `feriti_per_10k_ab`
  - `serie_storica` — `anni` array + array paralleli `incidenti`, `morti`, `feriti` (tipicamente 2020–2024)

- `iscrizioni` — nuove immatricolazioni
  - `ultimo_anno` — `totale`, `benzina`, `gasolio`, `elettriche`, `ibride`, `gas_metano_gpl`, `pct_elettriche_ibride`
  - `serie_storica` — `anni` + array paralleli (`totale`, `elettriche`, `ibride`, `elettriche_ibride`)

## `redditi`

Fonte: MEF Dipartimento delle Finanze — dichiarazioni IRPEF su base comunale. Licenza: CC BY 3.0.

- `istat_comune`, `comune`, `sigla_provincia`, `regione`, `cod_catastale`
- `anni_disponibili` — tipicamente `[2020, 2021, 2022, 2023, 2024]`
- `anni` — dict keyed by year. Ogni anno:
  - `contribuenti`
  - `reddito_complessivo` — `freq`, `tot`, `medio`, `medio_per_dichiarante`, `derivato_da_fasce` (boolean — true se medio computato da bucket, non grezzo)
  - `imposta_netta` — `freq`, `tot`, `medio`
  - `addizionale_comunale`, `addizionale_regionale` — stessa forma
  - `trattamento` — bonus IRPEF (2023–2024); per 2020–2022 il bonus è in chiave `bonus` (schema legacy)
  - `tipologie` — breakdown per fonte: `dipendente`, `pensione`, `autonomo`, `fabbricati`, ognuna con `freq`, `tot`, `medio`
  - `fasce` — 8 fasce di reddito f0–f7 (`≤ 0`, `0 - 10k`, `10k - 15k`, `15k - 26k`, `26k - 55k`, `55k - 75k`, `75k - 120k`, `> 120k`), ognuna con `label`, `freq`, `tot`
- `trend` — array convenience: per anno `[anno, reddito_medio, contribuenti, imposta_media, add_comunale_media]`
- `fonte`, `licenza`, `url_fonte`, `last_update`

## `immobili_pa`

Fonte: MEF Dipartimento Economia — Censimento Beni Immobili Pubblici al 31/12/2022. Licenza: CC BY 4.0.

**Caveat**: snapshot statico 2022. Su comuni piccoli senza beni dichiarati può essere `null`.

- `_etl_version`, `_source` (`"MEF DE - Beni Immobili Pubblici 2022"`), `_generated_at`
- `anno_rilevazione` — `2022`
- `kpi`
  - `n_totale` — totale immobili dichiarati
  - `n_fabbricati`, `n_terreni`
  - `pct_geo_referenziati` — % immobili con coordinate
  - `pct_vincolo_qualsiasi` — % con vincolo qualsiasi (paesaggistico, culturale, altro)
  - `pct_vincolo_culturale` — % con vincolo culturale stretto
  - `pct_uso_terzi` — % concessi in uso a terzi
  - `superficie_totale_mq`
  - `mix_categoria` — dict keyed da 17 categorie semantiche con conteggi. Categorie possibili: `fabbricati_sociali_scolastici`, `fabbricati_sociali_sanitari`, `fabbricati_sociali_sportivi`, `fabbricati_sociali_culturali`, `fabbricati_amministrativi_uffici`, `fabbricati_amministrativi_sicurezza`, `fabbricati_residenziali`, `fabbricati_pertinenze`, `fabbricati_magazzini`, `fabbricati_parcheggi`, `fabbricati_produttivi`, `terreni_agricoli`, `terreni_boschivi`, `terreni_pascolo`, `terreni_urbani`, `terreni_parchi_pubblici`, `altro`
  - `mix_natura` — dict `{FABBRICATO: n, TERRENO: n}`
- `punti` — array (cappato ~500 con sampling stratificato per categoria). Ogni punto: `lat`, `lon`, `cat` (categoria semantica), `tipo` (tipologia testuale es. "Abitazione"), `sup` (superficie mq, può essere `None`), `vincolo` (bool, true se ≠ "Nessuno"), `uso_terzi` (bool)

I KPI sono calcolati sul totale; i `punti` sono un sample geografico per la mappa.

## `anncsu`

Fonte: ANNCSU — Archivio Nazionale Numeri Civici e Strade Urbane (Agenzia delle Entrate + ISTAT). Open Data UE 2023/138 HVD.

**Sample 1000 punti** in dashboard. Per dataset completo (Lecce 47.917, Roma 515.815 civici) usare l'endpoint REST `/data/anncsu_full/<istat>.json` — vedi sezione "Endpoint HTTP diretti" in `SKILL.md`.

- `_etl_version`, `_source` (`"ANNCSU - Agenzia delle Entrate + Istat"`), `_snapshot_date`, `_generated_at`
- `kpi`
  - `n_strade` — totale odonimi distinti
  - `n_civici`, `n_civici_geo_ref`, `pct_geo_ref`
  - `n_civici_metrici` — civici con numerazione metrica (rari)
  - `n_civici_bis` — civici con esponente (es. "23/A", "23 bis")
  - `bbox` — `{lat_min, lat_max, lon_min, lon_max}`
  - `metodi_georef` — dict conteggi per metodo (`gps`, `cartografia`, `catasto`, `ortofoto`, `altro`)
  - `top_10_strade` — array `{odo, acc}` (strade con più accessi numerati)
- `punti` — array 1000 sample. Ogni punto: `lat`, `lon`, `odo` (odonimo UPPERCASE), `civ` (numero civico stringa), `esp` (esponente/subcivico, es. "A"), `quota` (può essere `None`), `met` (metodo georef: 1=GPS, 3=catasto, 4=altra sorgente, 5=…)

**Nota**: gli odonimi sono in maiuscolo e normalizzati (i numerali romani diventano parole: "Via Vittorio Emanuele II" → `VIA VITTORIO EMANUELE SECONDO`). Per lookup di indirizzo usa l'endpoint full e filtra case-insensitive su `odo` + match esatto su `civ`.

## `sanita_mds`

Fonte: Ministero della Salute - Open Data (`https://www.dati.salute.gov.it/`). Licenza **IODL v2.0**.

Bundle di tre sotto-sezioni indipendenti — ciascuna può essere `null` se il comune non ha dati per quella tipologia. Il `cod_comune` MdS è già ISTAT 6-digit nativo, quindi nessun fuzzy match coinvolto.

### Metadati top-level

- `_etl_version`, `_source` (`"Ministero della Salute - Open Data"`), `_license` (`"IODL v2.0"`), `_generated_at`
- `_fonti` — dict con URL canonical e cadenza per ciascuna sotto-sezione:
  - `farmacie.url`, `farmacie.data_riferimento` (YYYY-MM-DD), `farmacie.aggiornamento` = `"quotidiano"`
  - `parafarmacie.url`, `parafarmacie.data_riferimento`, `parafarmacie.aggiornamento` = `"quotidiano"`
  - `ospedali.url`, `ospedali.anno_dati` = `2023`, `ospedali.aggiornamento` = `"annuale (luglio dell'anno N+1)"`
- `istat_code`, `comune`, `provincia` (sigla), `regione`

### `sanita_mds.farmacie` (`null` se nessuna farmacia attiva)

Aggiornamento quotidiano. Filtro: solo farmacie attive (`data_fine_validita` futura o vuota). Coverage nazionale: **7.258 comuni / 91.9%**, ~20.800 farmacie attive totali.

- `kpi`
  - `n_totale` — n. farmacie attive nel comune
  - `n_geo_referenziate` — sottoinsieme con `lat`/`lon` valide
  - `pct_geo_referenziate` — %
  - `mix_tipologia` — dict conteggi per tipologia: `Ordinaria`, `Dispensario`, `Succursale`, `Dispensario stagionale`, eventuali `Non specificata`
  - `n_outlier_coordinate` — conteggio punti con coord fuori bbox Italia (35-47°N, 6-19°E). Tipicamente 0; tasso macro ~0.24% segnala errori MdS isolati (es. coord di una farmacia romana in altra provincia)
- `punti` — array, **non cappato**. Ogni punto: `nome` (denominazione), `tipo` (tipologia), `indirizzo`, `cap`, `lat`, `lon`

### `sanita_mds.parafarmacie` (`null` se nessuna parafarmacia attiva)

Aggiornamento quotidiano. Coverage: **2.158 comuni / 27.3%**, ~7.200 parafarmacie attive (concentrate in centri urbani medi/grandi). Schema simile a farmacie ma senza `mix_tipologia` (sono tutte stessa categoria).

- `kpi.n_totale`, `kpi.n_geo_referenziate`, `kpi.pct_geo_referenziate`, `kpi.n_outlier_coordinate`
- `punti` — array, non cappato. Ogni punto: `nome`, `indirizzo`, `cap`, `lat`, `lon`

### `sanita_mds.ospedali` (`null` se nessuno stabilimento)

Aggiornamento annuale, **dato fermo all'anno 2023** (rilascio luglio 2025). Coverage: **736 comuni / 9.3%**, 1.272 stabilimenti totali, ~212.768 posti letto totali nazionali.

- `kpi`
  - `n_stabilimenti` — n. stabilimenti distinti nel comune (aggregati per `(codice_struttura, subcodice)`)
  - `n_reparti_totali` — somma `N. Reparti` di tutte le discipline
  - `posti_letto_totali`
  - `posti_letto_ordinaria` — degenza ordinaria
  - `posti_letto_pagamento` — degenza a pagamento (privata accreditata)
  - `posti_letto_day_hospital`
  - `posti_letto_day_surgery`
  - `mix_discipline` — dict aggregato per descrizione disciplina: `{"CARDIOLOGIA": 42, "MEDICINA GENERALE": 86, ...}` (posti letto totali per disciplina nel comune, ordinato decrescente)
- `stabilimenti` — array completo, ordinato per `totale_posti_letto` decrescente. Ogni stabilimento:
  - `codice_struttura`, `subcodice` — identificativo MdS
  - `denominazione` — es. "POLICLINICO UNIVERSITARIO A. GEMELLI"
  - `tipo_struttura` — testuale, es. "Ospedale a gestione diretta", "Casa di cura accreditata", "Ospedale Classificato"
  - `tipo_azienda_cod` — codice numerico tipo azienda MdS
  - `indirizzo`
  - `totale_posti_letto` — somma sui posti letto di tutte le discipline
  - `discipline` — array completo. Ogni disciplina: `codice`, `descrizione`, `tipo` (`ACUTI`/`RIABILITAZIONE`/`LUNGODEGENZA`), `n_reparti`, `ord` (ordinaria), `pag` (pagamento), `dh` (day hospital), `ds` (day surgery), `totale`

**Pattern lookup tipici**:
- "Quanti posti letto in totale a <comune>?" → `sanita_mds.ospedali.kpi.posti_letto_totali`
- "Quante farmacie a <comune>?" → `sanita_mds.farmacie.kpi.n_totale`
- "Quali ospedali a <comune>?" → `sanita_mds.ospedali.stabilimenti[].denominazione`
- "Posti letto di cardiologia a <comune>?" → cerca in `sanita_mds.ospedali.kpi.mix_discipline["CARDIOLOGIA"]` (aggregato) o itera `stabilimenti[].discipline[]` per dettaglio per ospedale
- "Posti letto Day Hospital totali Italia?" → non disponibile aggregato in dashboard, ma sommabile su tutti i comuni. La pagina home pubblica espone i totali nazionali nell'aggregato `sanita_mds-lookup.json`.

## `pun`

Fonte: GSE (Gestore Servizi Energetici) per conto del MASE (Ministero dell'Ambiente e della Sicurezza Energetica) — **Piattaforma Unica Nazionale (PUN)** dei punti di ricarica per veicoli elettrici. URL canonico: `https://www.piattaformaunicanazionale.it/idr`.

**Licenza**: `CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)` — pubblicazione open data di default delle Pubbliche Amministrazioni, con attribuzione a GSE / Piattaforma Unica Nazionale.

**Copertura**: 66.619 punti di ricarica (EVSE) totali in 5.185 comuni (65,7% del totale 7.918). Comuni piccoli e di montagna spesso hanno `pun: null`.

**Aggiornamento**: **quotidiano** (~03:00 UTC). Prima fonte a frequenza giornaliera nel cruscotto.

### Metadati top-level

- `_etl_version`, `_source` (`"GSE/MASE PUN - Piattaforma Unica Nazionale"`), `_source_url`, `_license` (`"CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)"`)
- `_data_last_modified` — timestamp ISO 8601 ultimo aggiornamento CSV upstream GSE
- `_generated_at` — timestamp generazione shard locale

### `pun.kpi` (sempre presente quando `pun` non è null)

- `n_totale` — totale PdR registrati nel comune
- `n_attivi` — PdR con `stato == "Attivo"`
- `n_non_attivi` — PdR con `stato == "Non Attivo"`
- `pct_attivi` — percentuale attivi (0-100)
- `n_ac` — PdR a corrente alternata
- `n_dc` — PdR a corrente continua
- `potenza_tot_kw` — somma potenza massima erogabile (kW), calcolata su tutti i PdR del comune. **Capacità nominale, non istantanea**: rappresenta la massima potenza erogabile contemporaneamente se tutti fossero attivi e usati al 100%
- `mix_potenza` — dict `{categoria: count}` con 5 categorie:
  - `"Slow"` — fino a 7 kW
  - `"Quick"` — 7-22 kW
  - `"Fast"` — 22-50 kW
  - `"HPC"` — 50-150 kW (High Power Charging)
  - `"Ultra fast"` — oltre 150 kW

### `pun.punti[]` (sample cappato a 500)

Per comuni urbani grandi (Roma 3680, Milano 3045, Napoli 1199, Torino 1144, Genova 740) i `punti` sono un sample stratificato per categoria potenza fino a 500. I KPI invece sono calcolati sul totale.

Ogni elemento di `punti[]`:

- `id_evse` — identificativo univoco EVSE assegnato da GSE (stringa)
- `lat`, `lon` — coordinate WGS84 (6 decimali ~11cm). Tutti i PdR sono georeferenziati (100% coverage geo)
- `indirizzo` — odonimo + civico (testo)
- `cap` — codice avviamento postale (testo)
- `stato` — `"Attivo"` oppure `"Non Attivo"`
- `tipo_parcheggio` — codice vocabolario **OCPI** (Open Charge Point Interface, standard europeo). Valori possibili: `"ON_STREET"` (su strada), `"PARKING_LOT"` (area parcheggio), `"UNDERGROUND_GARAGE"` (garage sotterraneo), `"PARKING_GARAGE"`, `"ROOFTOP_CARPARK"`. **`null` quando il CPO non lo dichiara** (frequente, ~57% null su Roma)
- `potenza_categoria` — `"Slow"` | `"Quick"` | `"Fast"` | `"HPC"` | `"Ultra fast"` (vedi soglie sopra)
- `potenza_w` — potenza massima erogabile in **watt** (non kW). Per kW dividere per 1000
- `corrente` — `"AC"` o `"DC"`
- `restrizioni` — stringa multi-valore con codici OCPI separati da `"- "` (es. `"EV_ONLY- PLUGGED- DISABLED"`). Valori OCPI: `EV_ONLY`, `PLUGGED`, `DISABLED`, `CUSTOMERS`, `MOTORCYCLES`, `CARSHARING`, `TAXI`. `null` se senza restrizioni
- `servizi_vicini` — stringa multi-valore OCPI: `RESTAURANT`, `CAFE`, `HOTEL`, `MALL`, `SUPERMARKET`, `RECREATION_AREA`, `NATURE`, `PARKING`. `null` se non dichiarato (frequente)
- `orario` — testuale. `"Aperto 24/7"` nel 95% dei casi. Quando diverso: formato CSV giorno-per-giorno tipo `"lunedi:06:00-18:00,martedi:06:00-18:00,..."`. `null` se non dichiarato

### Pattern lookup tipici

- "Quante colonnine elettriche a <comune>?" → `pun.kpi.n_totale` (e `n_attivi` per quelle operative)
- "Colonnine HPC/Ultra fast a <comune>?" → `pun.kpi.mix_potenza["HPC"]`, `pun.kpi.mix_potenza["Ultra fast"]`
- "Potenza totale ricarica disponibile a <comune>?" → `pun.kpi.potenza_tot_kw` (con caveat: nominale, non istantanea)
- "Punti di ricarica DC fast a <comune>?" → filtra `pun.punti[]` con `corrente == "DC"` e `potenza_categoria in ["Fast", "HPC", "Ultra fast"]`
- "Quali colonnine sono fuori servizio a <comune>?" → filtra `pun.punti[]` con `stato == "Non Attivo"`
- "Colonnine 24/7 a <comune>?" → filtra `pun.punti[]` con `orario == "Aperto 24/7"` (95% dei casi)
- "Mobilità elettrica copertura nazionale" → non in dashboard per-comune; serve aggregare 7.918 dashboard, oppure mcp_info dichiara `66.619 PdR su 5.185 comuni`

## `agcom_bbmap`

Fonte: **AGCOM Broadband Map (BBmap)** — reportistica delle consistenze dei punti geografici raggiunti dalla rete cablata ai sensi dell'art. 22 del Codice delle Comunicazioni Elettroniche. Licenza **CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)** — open data di default delle PA con attribuzione ad AGCOM. Aggiornamento **trimestrale**, dato corrente al 31/12/2025 (rilascio 10/02/2026).

Copertura **nazionale completa 7.896/7.896 comuni (100%)** — prima fonte nel cruscotto senza buchi di copertura.

### Metadati

- `_etl_version`, `_source`, `_source_url`, `_license`
- `_data_period` — data di riferimento del dato (es. `"31/12/2025"`)
- `_generated_at` — timestamp ETL

### `kpi` — copertura banda larga

Le percentuali sono in formato numerico (`97.0` significa 97%), `null` quando il dato AGCOM non è disponibile per il comune.

**Indicatori sintetici** (i più rilevanti per cittadino e LLM):

- `copertura_ftth_desi_pct` — **metrica UE ufficiale DESI** (Digital Economy and Society Index). È quello che dovresti citare di default quando l'utente chiede "copertura fibra a X".
- `copertura_ftth_20m_pct` — copertura più stringente: famiglie con FTTH **entro 20 metri** dall'edificio. Cita questo solo se l'utente chiede "fibra effettivamente a casa" o "FTTH operativa".
- `confidenza_desi_pct` — indice di confidenza AGCOM sul dato DESI. Sotto 70% = dato debole, sopra 90% = dato robusto. Citalo quando significativamente diverso.

**Famiglie**:

- `famiglie_residenti` — stima famiglie ISTAT (denominatore)
- `famiglie_ftth` — raggiunte da FTTH (qualunque distanza)
- `famiglie_ftth_20m` — raggiunte da FTTH entro 20m

**Dati tecnici** (utili per analisi avanzate):

- `celle_20m_raggiunte` — celle 20×20m con almeno una rete cablata
- `celle_20m_ftth` — di queste, quante coperte da FTTH (fibra fino a casa)
- `celle_20m_fttc` — quante coperte da FTTC (fibra al cabinet + rame ultimo miglio)
- `punti_dichiarati` — punti di terminazione dichiarati dagli operatori (può contenere duplicati: ogni operatore dichiara i propri)
- `punti_geo_distinti` — stessi punti deduplicati spazialmente con passo 5m (numero più affidabile)
- `indirizzi_postali_distinti` — indirizzi civici univoci raggiunti
- `punti_dichiarati_ftth`, `punti_geo_distinti_ftth`, `indirizzi_postali_distinti_ftth` — sottoinsiemi limitati alla sola fibra

### `anagrafica_locale`

Ridondante con la sezione `anagrafica` del dashboard ma utile per debug standalone.

- `regione`, `provincia`, `comune` (testo come da CSV AGCOM)

### `mappa_ufficiale`

- `url` — URL base del Web AppBuilder AGCOM (`https://geo.agcom.it/agcomapps/BB4/BB4_BBwired_na_app16_4/`)
- `level` — `null` di default; il frontend lo sovrascrive con un livello di zoom calcolato in base alla taglia del comune (12 per Roma/Milano, 13 capoluoghi medi, 14 comuni medi, 15 piccoli)

**Pattern: come costruire il deep-link alla mappa AGCOM zoomata sul comune.**

```
deep_link = `${agcom_bbmap.mappa_ufficiale.url}?center=${anagrafica.coordinate.lon},${anagrafica.coordinate.lat}&level=${level}`
```

Dove `level` è scelto in base a `famiglie_residenti`:
- `>= 200000` → level 12 (Roma, Milano)
- `>= 30000` → level 13 (capoluoghi medi)
- `>= 3000` → level 14 (comuni medi)
- `< 3000` → level 15 (piccoli/montani)

### Pattern lookup tipici

- "Copertura fibra a <comune>?" → `agcom_bbmap.kpi.copertura_ftth_desi_pct` (default DESI)
- "FTTH effettivo a casa a <comune>?" → `agcom_bbmap.kpi.copertura_ftth_20m_pct`
- "Quante famiglie hanno la fibra a <comune>?" → `agcom_bbmap.kpi.famiglie_ftth` e `famiglie_ftth_20m`, su denominatore `famiglie_residenti`
- "Banda larga in Italia, comuni meglio coperti" → non in dashboard per-comune; serve aggregare 7.896 dashboard
- "Mappa della rete fibra a <comune>" → costruisci deep-link AGCOM via `mappa_ufficiale.url` + center+level (vedi sopra). Non tentare di scaricare il FeatureServer ArcGIS AGCOM: ~192k segmenti per Roma, troppo voluminoso.

### Cosa NON è in `agcom_bbmap`

- **Velocità medie download/upload** — disponibili nel FeatureServer ArcGIS AGCOM ma non nel CSV comunale aggregato
- **Operatori specifici** (TIM, Fastweb, Iliad, WindTre, Open Fiber, ecc.) — non in CSV bulk per ragioni di concorrenza
- **Tecnologie wireless** (5G FWA, satellitare Starlink) — BBmap mappa solo reti cablate ex art. 22 CCE
- **Storia trimestrale** — solo l'ultimo trimestre disponibile sul portale AGCOM; storico richiederebbe scaricare tutti gli `itemId` precedenti

## `carburanti`

Fonte: **MIMIT — Osservatorio Prezzi Carburanti**, dataset "Carburanti - Prezzi praticati e anagrafica degli impianti" pubblicato ai sensi dell'art. 51 L. 99/2009. Licenza **IODL 2.0** (attribution-only, compatibile con CC BY). Aggiornamento **quotidiano** ("Prezzo alle 8 di mattina" — i gestori comunicano al ministero i prezzi praticati alle ore 8 del giorno precedente).

Copertura **~5.450/7.896 comuni (69%)**, ~23.700 impianti. I ~2.450 comuni senza distributore sono in larga parte micro-comuni montani; per quei comuni `carburanti: null`.

### Metadati

- `_etl_version`, `_source`, `_source_url`, `_license`
- `_data_last_modified` — data dello snapshot MIMIT (`YYYY-MM-DD`)
- `_generated_at` — timestamp ETL

### `kpi` — anagrafica + prezzi aggregati

**Contatori impianti**:

- `n_impianti` — totale impianti attivi nel comune
- `n_stradali` — di tipo Stradale (esclude autostrade)
- `n_autostradali` — aree di servizio autostradali (prezzi tipicamente 15-30% più alti)
- `n_pompe_bianche` — impianti non affiliati a una bandiera (senza brand)
- `pct_pompe_bianche` — % pompe bianche sul totale (0-100)

**Bandiere/brand**:

- `n_bandiere_distinte` — numero di marchi distinti nel comune
- `mix_bandiere` — dizionario `{brand: n_impianti}` con le **top 5 bandiere** + chiave `"Altre"` come aggregato delle minori. Tipiche bandiere: `Agip Eni`, `Api-Ip`, `Q8`, `Esso`, `Tamoil`, `Shell`, `Pompe Bianche`, ecc.

**Prezzi medi** (`prezzo_medio`):

- `benzina_self`, `benzina_serv` — Benzina, self-service e servito (€/litro)
- `gasolio_self`, `gasolio_serv` — Gasolio, self-service e servito
- `gpl` — GPL (€/litro)
- `metano` — Metano (€/kg secondo dichiarazione gestore, ma il CSV riporta come prezzo numerico)
- `hvo` — HVO / HVOlution (biocarburante 100% rinnovabile, in espansione)

Ogni chiave è `null` se nessun impianto del comune offre quel carburante. La media è calcolata su **tutti** gli impianti (Stradali + Autostradali); per medie filtrate ricalcola lato client da `punti[]`.

**Prezzi minimi** (`prezzo_min`):

- `benzina_self` — prezzo Benzina self più basso nel comune
- `gasolio_self` — prezzo Gasolio self più basso nel comune

Utile come hook giornalistico ("a X si trova benzina a 1.569€!") ma volatile da un giorno all'altro.

**Freshness** (`freshness_pct`): percentuale di impianti con `ultimo_aggiornamento` <=7 giorni. Sopra 90% = dato robusto. Sotto 70% = molti gestori non aggiornano il prezzo da settimane, media meno affidabile.

### `punti[]` — impianti georeferenziati (no cap)

A differenza di `pun` e `immobili_pa`, qui **tutti** gli impianti del comune sono inclusi (Roma worst case ~936, ~1MB). Sicuro per query puntuali di prossimità geografica lato client.

Schema per punto:

- `id` — `idImpianto` MIMIT (univoco nazionale)
- `name` — nome commerciale dell'impianto
- `brand` — bandiera/marchio (`"Pompe Bianche"` per i non affiliati)
- `tipo` — `"Stradale"` o `"Autostradale"`
- `lat`, `lon` — coordinate WGS84 (6 decimali, ~11cm)
- `indirizzo` — come comunicato dal gestore
- `prezzi` — dizionario con i prezzi correnti per i 7 carburanti core (chiavi presenti solo se l'impianto eroga quel carburante)
- `prezzi_extra` — dizionario opzionale con i premium proprietari: `"Benzina Shell V Power"`, `"Diesel Shell V Power"`, `"Hi-Q Diesel"`, `"Supreme Diesel"`, `"Blue Diesel"`, `"Gasolio Premium"`. Non entrano nei `prezzo_medio` per coerenza statistica.
- `ultimo_aggiornamento` — data ultimo refresh del prezzo (`YYYY-MM-DD`) dal campo `dtComu` del CSV prezzi

### Aggregati nazionali (shard separato)

L'ETL pubblica anche **`carburanti/_nazionale.json`** con medie pre-calcolate nazionali e per regione. Schema:

```json
{
  "_snapshot_date": "2026-05-12",
  "nazionale": {
    "benzina_self": 1.840, "benzina_serv": 1.965,
    "gasolio_self": 1.950, "gasolio_serv": 2.080,
    "gpl": 0.795, "metano": 1.620, "hvo": 1.985
  },
  "regionale": {
    "Lombardia": {"benzina_self": 1.825, ...},
    "Puglia": {"benzina_self": 1.851, ...},
    ...
  }
}
```

NON è incluso nello shard `dashboard/<istat>.json`: per il calcolo `delta_vs_nazionale` il frontend deve fare fetch lazy a `https://cruscotto-italia-data.r2.dev/carburanti/_nazionale.json` quando la tab si apre.

### Pattern lookup tipici

- "Prezzo benzina a <comune>?" → `carburanti.kpi.prezzo_medio.benzina_self` (default self) + cita `_data_last_modified`
- "Quanti distributori a <comune>?" → `carburanti.kpi.n_impianti`
- "Distributore più economico a <comune>?" → `carburanti.kpi.prezzo_min.benzina_self` (o gasolio_self), poi filtra `punti[]` per identificare il singolo impianto
- "Confronto prezzo <comune> vs media nazionale" → fetch `_nazionale.json`, calcola `((prezzo_medio_comune - nazionale) / nazionale) * 100`
- "Pompe bianche a <comune>?" → `carburanti.kpi.n_pompe_bianche` + `pct_pompe_bianche`
- "Dove c'è HVO a <comune>?" → filtra `carburanti.punti[]` per `"hvo" in p.prezzi`
- "Dove c'è metano a <comune>?" → filtra `carburanti.punti[]` per `"metano" in p.prezzi`

### Cosa NON è in `carburanti`

- **Storia prezzi (trend mensili/annuali)** — solo snapshot corrente, no time-series locale. MIMIT pubblica un archivio storico trimestrale separato in `https://www.mimit.gov.it/it/open-data/elenco-dataset/carburanti-archivio-prezzi` (tar.gz, non ancora integrato nel cruscotto)
- **Volumi venduti, fatturati, statistiche di mercato** — dati commerciali non pubblici
- **Servizi accessori** (autolavaggio, bar, officina) — non in CSV bulk
- **API zonale "5km dalla mia posizione"** — funzionalità lato client che usa `https://carburanti.mise.gov.it/ospzApi/search/zone` con body POST `{points:[{lat,lng}], fuelType:"2-1", radius:5}` (fuelType: `1-1`=Benzina self, `2-1`=Gasolio self, ecc.). Non integrata server-side ma il pattern è documentato per il frontend.

## `anac`

Fonte: ANAC contratti pubblici (CC BY 4.0).

- `buyer_name` — nome comune come appare in ANAC
- `count` — numero aggiudicazioni
- `importo_totale` — importo cumulato aggiudicato
- `first_award_date`, `last_award_date` — range temporale
- `distinct_cpv` — n. CPV distinti
- `top_cpv` — array top categorie: `code`, `desc`, `count`, `importo`
- `cpv` — array di TUTTE le categorie CPV (stesso schema di top_cpv: code/desc/count/importo), ordinate per importo decrescente. Per elenchi completi/filtrabili; `top_cpv` resta le prime 5.

Aggregato. Non c'è ancora dettaglio CIG-level via connettore: la query CIG-level con filtri per anno, importo, fornitore, RUP, CPV è prevista ma non ancora attiva.

## `bdap_kpi`

KPI aggregati sul dataset BDAP-MOP.

- `nome_titolare`
- `totale` — `count`, `costo_lavori_eff`, breakdown finanziamenti
- `per_stato` — split per `ATTIVO` e `CHIUSO`, stessi campi
- `top_settori` — array: `settore`, `count`, `costo`

Settori tipici: INFRASTRUTTURE AMBIENTALI E RISORSE IDRICHE, INFRASTRUTTURE DI TRASPORTO, INFRASTRUTTURE SOCIALI, INFRASTRUTTURE DEL SETTORE ENERGETICO, ALTRE INFRASTRUTTURE PUBBLICHE.

## `censimento`

Censimento permanente, variabili 2023 su Basi Territoriali 2021 — ISTAT (CC BY 3.0 IT). Distinto da `profilo`: `profilo` è aggregato comune-level annuale; `censimento` è dato biennale a livello di **sezione di censimento** (sub-comunale) con 127 variabili demografiche/abitative per ogni sezione.

La sezione `censimento` del dashboard contiene solo gli **aggregati comune-level** (~3-5 KB). Per i poligoni georeferenziati delle sezioni e le 127 variabili raw è disponibile l'endpoint REST `/data/censimento_full/<istat>.geojson` (lazy fetch, EPSG:4326).

### Campi top-level

- `_source` — "ISTAT Basi Territoriali 2021 + Variabili censuarie 2023 del Censimento permanente"
- `_license` — "CC-BY 3.0 IT"
- `_version` — "2023"
- `_has_full` — boolean, true se esiste lo shard `/data/censimento_full/<istat>.geojson`

### `kpi_comune` — aggregati comune-level

- `n_sezioni` — numero di sezioni di censimento nel comune
- `pop_totale`, `pop_maschi`, `pop_femmine`
- `famiglie_totali`
- `abitazioni_totali`, `abitazioni_occupate`, `abitazioni_vuote`
- `stranieri_totali`, `stranieri_ue`, `stranieri_extra_ue`
- `occupati_15_64`, `occupati_maschi`, `occupati_femmine`
- `area_kmq`

### `distribuzioni_comune` — distribuzioni aggregate

- `eta_per_fascia` — `{"0-14": N, "15-64": N, "65+": N}`
- `eta_5anni` — dict con 16 chiavi `"0-4"`, `"5-9"`, ..., `"75+"`
- `titolo_studio_9plus` — `{"nessuno", "elementare", "media", "diploma", "terziario"}`
- `famiglie_componenti` — `{"1", "2", "3", "4", "5", "6+"}`
- `stranieri_eta` — `{"0-29", "30-54", "55+"}`

### Sezioni "no_vars"

Il 35% delle sezioni nazionali (262.434 su 756.376) non hanno variabili censuarie: sono **aree non residenziali** rilevate solo come poligoni (parchi nazionali, zone industriali, infrastrutture, aree militari). Non è un bug ETL ma comportamento atteso del Censimento permanente che non rileva variabili dove non ci sono residenti. Nel GeoJSON queste feature hanno `properties.vars: {}`.

### Caveat

- Aggiornamento biennale: le variabili sono aggiornate sulle stesse Basi Territoriali 2021 (tornate 2021, 2023, ...). Ultimo aggiornamento ISTAT 09/06/2026. La variabile `E3` (edifici residenziali), presente nel 2021, non è più diffusa nel 2023.
- Per comuni piccoli (es. Morterone con 1-2 sezioni) il dato è meno granulare ma resta valido come aggregato comune-level.
- `n_sezioni` include anche le sezioni no_vars (la sezione esiste come poligono territoriale, anche se non residenziale).

## `beni_culturali`

Beni culturali immobili tutelati dal Ministero della Cultura (MiC) — ontologia ArCo `ImmovableCulturalProperty` di ICCD. Dataset nazionale ~113.820 beni, ~5.657 comuni coperti (71% del Paese). Fonte: SPARQL `dati.beniculturali.it/sparql`. Licenza CC BY 4.0.

### Campi top-level

- `_etl_version` — "0.1.0"
- `_source` — "MiC DBUnico 2.0 (Cultural-ON)" (etichetta storica, oggi include anche ArCo)
- `_source_url` — endpoint SPARQL
- `_snapshot_date` — data esecuzione ETL (YYYY-MM-DD)
- `_generated_at` — timestamp ISO 8601 UTC
- `_luoghi_truncated`, `_luoghi_total`, `_luoghi_cap`, `_full_shard_available` — presenti solo per comuni con più di 30 beni (vedi shard FULL)

### `kpi` — KPI sintetici per comune

- `n_totale` — totale beni immobili tutelati MiC in questo comune
- `n_visitabili` — sottoinsieme con link a Cultural-ON (orari, contatti via `hasCulturalInstituteOrSite`)
- `n_con_coordinate` — beni con geometria WKT POINT
- `n_senza_coordinate` — beni catalogati ma senza coordinate (usa per banner UI "mappa: N di M")
- `mix_categoria` — dict ordinato per priorità: `{"chiesa": N, "palazzo": N, "castello": N, ...}` (9 macro)
- `pct_con_foto` — percentuale beni con `foaf:depiction`
- `pct_con_descrizione` — percentuale beni con `dc:description`

### `luoghi[]` — array compatto (cap 30 nello shard base)

Ogni elemento ha:

- `id` — identificatore breve (ultimo segmento URI ArCo)
- `denom` — denominazione (pulita: rimosso suffisso `" - Comune (SG)"`)
- `categoria` — slug normalizzato 9-classi: chiesa, palazzo, castello, archeologia, museo, monumento, infrastruttura, parco_giardino, altro
- `lat`, `lon` — coordinate (può essere null)
- `indirizzo` — stringa indirizzo ArCo originale
- `image` — URL immagine (foaf:depiction), può essere null
- `cis_link` — URI Cultural-ON DBUnico 2.0 se bene è visitabile, else null

### Shard FULL

Per comuni con più di 30 beni esiste `/data/beni_culturali_full/<istat>.json` con tutti i beni + campi aggiuntivi per ogni elemento:

- `tipo_raw` — slug ArCo grezzo (es. "basilica", "abbazia", "rocca", "torre", centinaia di valori granulari)
- `descrizione` — testo libero MiC (campo `dc:description`, presente solo nel ~28% dei beni)
- `tutela` — vincolo MiC se valorizzato (presente solo nel ~5% dei beni immobili, raro)
- `soprintendenza` — nome agenzia di tutela (`hasHeritageProtectionAgency`)

### Caveat

- Aggiornamento mensile (cron VM 1° del mese 05:00).
- I beni immobili ArCo modellano il **patrimonio architettonico tutelato**: chiese, palazzi, castelli, edifici storici. NON copre beni mobili (quadri, sculture, manoscritti) né luoghi di Cultural-ON DBUnico 2.0 (musei, biblioteche statali e non statali, archivi) se non come elemento `cis_link`.
- Coverage geografica sbilanciata: Nord-Ovest e Nord-Est hanno più beni catalogati per ragioni storiche (catalogazione ICCD avviata negli anni '70, completata a tappe per regione).
- Comuni senza beni tutelati (29%): shard sempre presente con `n_totale: 0` e `luoghi: []` per consistenza UX (no 404 dal Worker MCP).

## `meteo_italiameteo` (in `kpi_summary`)
Previsioni ItaliaMeteo ICON-2I, griglia 2.2 km, copertura Italia.
Aggiornamento 2x/giorno (corse 00 e 12 UTC — disponibili ~03:30 e ~14:30 UTC).
Licenza CC BY 4.0 · HVD Meteorologici (Reg. UE 2023/138).
Fonte: https://meteohub.agenziaitaliameteo.it/

### Campi
- `t2m_c` — temperatura attuale a 2m (°C)
- `t2m_max24h_c` — temperatura massima prossime 24h (°C)
- `t2m_min24h_c` — temperatura minima prossime 24h (°C)
- `prec_24h_mm` — precipitazioni cumulate prossime 24h (mm)
- `umidita_pct` — umidità relativa (%)
- `vento_kmh` — velocità vento a 10m (km/h)
- `vento_dir_deg` — direzione vento (gradi, 0=Nord)
- `raffica_max24h_kmh` — raffica massima prossime 24h (km/h)
- `nuvolosita_pct` — copertura nuvolosa totale (%)
- `neve_cm` — altezza neve al suolo (cm)
- `ww` — codice meteo WMO (0=sereno, 61=pioggia, 71=neve, 95=temporale...)
- `ww_desc` — descrizione italiana del codice WMO
- `run_utc` — corsa modello (es. "2026062800" = 28/06/2026 00 UTC)
- `valid_time_utc` — timestamp previsione in formato ISO 8601 UTC

### Caveat
- Dato previsionale, non osservato: riflette il modello numerico, non stazioni a terra.
- Copertura 7895/7895 comuni (100%), inclusi comuni senza stazioni ISPRA-SNPA.
- Step corrente: il valore `t2m_c` si riferisce all'ora più vicina all'UTC corrente nella corsa.

## `morfologia_cnr` (in `kpi_summary`)
KPI sintetici morfologia da HR-DTM 5m CNR-IRPI. Presente solo se lo shard morfologia è disponibile per il comune (pipeline one-shot).
Fonte: `/data/morfologia/<istat>/<istat>_stats.json`.

### Campi
- `elev_min` — quota minima (m slm)
- `elev_max` — quota massima (m slm)
- `elev_mean` — quota media (m slm)
- `slope_mean` — pendenza media (gradi)
- `slope_gt15_pct` — % superficie con pendenza >15°
- `aspect_dom` — esposizione prevalente (N/NE/E/SE/S/SO/O/NO)
- `solar_mean` — irraggiamento solare medio annuo (kWh/m²/anno)

### Caveat
- Null per comuni non ancora processati dalla pipeline (attualmente ~7901/7904 disponibili).
- Per il dettaglio completo (geomorfologia, classi pendenza, TRI, ecc.) usare il shard separato `morfologia` via `comune_dashboard`.

## `morfologia` (shard separato, condizionale)
Morfologia del territorio da HR-DTM 5m CNR-IRPI (Zenodo DOI 10.5281/zenodo.18872933, CC BY 4.0).
Elaborazione GRASS GIS headless (r.slope.aspect, r.geomorphon, r.sun). Dato one-shot (non periodico).
Shard: `/data/morfologia/<istat>/<istat>_stats.json` + `/data/morfologia/<istat>/bounds.json`.
Presente nella risposta `comune_dashboard` solo se il comune è stato processato dalla pipeline.

### Struttura
```json
{
  "morfologia": {
    "stats": {
      "istat": "077014",
      "area_km2": 389.6,
      "elev_min": 54,
      "elev_max": 519,
      "elev_mean": 289,
      "slope_mean": 6.3,
      "slope_max": 79.4,
      "slope_gt15_pct": 6.9,
      "slope_gt35_pct": 0.7,
      "aspect_dom": "SO",
      "aspect_dom_pct": 18.5,
      "aspect_dist": {"N":11.3,"NE":11.3,"E":14.5,"SE":15.2,"S":11.5,"SO":18.5,"O":12.7,"NO":13.3},
      "tri_mean": 0.44,
      "tri_max": 24,
      "geomorph": {"Pianoro":8.4,"Cima":0.5,"Cresta":5.4,"Spalla":0.5,"Dosso":11.9,"Versante":60.2,"Conca":9.9,"Piede":0.5,"Valle":3.5,"Depressione":0.5},
      "geo_versanti": 60.2,
      "geo_creste": 24.9,
      "geo_impluvi": 24.4,
      "geo_pianori": 8.4,
      "solar_mean": 2124,
      "solar_min": 545,
      "solar_max": 2617
    },
    "bounds": {
      "west": 16.399,
      "south": 40.554,
      "east": 16.743,
      "north": 40.777,
      "cx": 16.571,
      "cy": 40.666
    }
  }
}
```

### Campi stats
- `elev_min/max/mean` — quota in m slm (DTM 5m)
- `slope_mean/max` — pendenza media/massima in gradi (r.slope.aspect Horn 3×3)
- `slope_gt15_pct` / `slope_gt35_pct` — % sup. con pendenza >15° / >35°
- `aspect_dom` — esposizione prevalente (N/NE/E/SE/S/SO/O/NO)
- `aspect_dom_pct` — % sup. con esposizione prevalente
- `aspect_dist` — distribuzione % per 8 direzioni cardinali
- `tri_mean/max` — Terrain Ruggedness Index (Riley et al. 1999)
- `geomorph` — distribuzione % 10 forme del terreno (r.geomorphon, Jasiewicz-Stepinski)
- `geo_versanti/creste/impluvi/pianori` — % raggruppamenti macro geomorfologia
- `solar_mean/min/max` — irraggiamento solare kWh/m²/anno (r.sun GRASS, 4 giorni stagionali a 20m)

### Caveat
- Dato assente se comune non ancora processato dalla pipeline one-shot.
- Elaborazione da DTM 5m: accurata per comuni con rilievo significativo; per comuni pianeggianti (es. Lecce, quota -1÷59m) i valori di pendenza e geomorfologia sono numericamente corretti ma poco discriminanti.
- `solar_mean` è una stima modellistica **clear-sky** (cielo sereno), non una misura reale. La pipeline campiona 4 giorni — equinozi (80, 264) e solstizi (172, 355) — con `r.sun` a passo orario e annualizza: `(g_80+g_172+g_264+g_355)/4*365/1000`. Non vengono passati coefficienti di torbidità o nuvolosità: GRASS applica i default (Linke 3.0, albedo 0.2). I valori risultano quindi superiori del 20–25% rispetto a PVGIS o Atlante solare ENEA, che includono la copertura nuvolosa. Non utilizzabile per il dimensionamento di impianti fotovoltaici.
