---
name: cruscotto-italia-workflow
version: 2.9.1
description: Cruscotto Italia MCP (7896 comuni). Tool: comune_kpi (~55 KPI ~620 token); comune_dashboard (27 sezioni); search_comune; comune_opere_dettaglio; anncsu_civico_search; censimento_sezione_search. Sezioni: anagrafica, demografia POSAS, censimento profilo, turismo, PNRR, ISPRA suolo/idro/rifiuti/aria, DPC sismica, BDAP-MOP, SIOPE cassa (pagamenti+incassi+saldo), ANAC, MIUR scuole/plessi, ACI veicoli, MEF redditi/patrimonio, ANNCSU civici, MdS farmacie/ospedali, GSE ricarica EV, AGCOM FTTH, MIMIT carburanti, RUNTS, ISTAT ASIA, pendolarismo 2021, Basi Territoriali 2021, vars 2023 (127), MiC ArCo+Cultural-ON, meteo ItaliaMeteo, morfologia CNR-IRPI HR-DTM 5m (kpi_summary: morfologia_cnr con elev/slope/aspect/solar). Catasto AGE: fogli/particelle REST /data/catasto_full/. Trigger: ISTAT, BDAP, SIOPE, incassi, saldo cassa, ISPRA, sismica, PNRR, ANAC, MEF, ANNCSU, AGCOM, MIMIT, RUNTS, FTTH, pendolarismo, beni culturali, MiC, catasto, meteo, ItaliaMeteo, morfologia, DTM, rilievo, pendenza, geomorfologia, CNR-IRPI.
---

# Cruscotto Italia workflow

Reference + workflow patterns for the Cruscotto Italia MCP connector.

- **Server**: `cruscotto-italia-mcp.agid.workers.dev` (Cloudflare Worker)
- **Datasets integrati**: **28** (anagrafica, demografia, censimento profilo annuale, turismo, PNRR, territorio ISPRA, classificazione sismica DPC, opere BDAP-MOP, SIOPE cassa multi-anno (pagamenti + incassi + saldo), scuole MIUR, aria ISPRA SNPA, parco veicoli ISTAT, incidenti ISTAT, iscrizioni ACI, redditi MEF, patrimonio immobili PA MEF DE, civici ANNCSU, sanità territoriale Ministero Salute, punti di ricarica EVSE PUN GSE/MASE, banda larga AGCOM BBmap, distributori carburanti MIMIT, enti Terzo Settore RUNTS Min. Lavoro, imprese e addetti ISTAT ASIA UL, matrice pendolarismo lavoro 2021 Censimento permanente, **Basi Territoriali 2021 e Variabili censuarie 2023 con 127 variabili per sezione di censimento**, **beni culturali immobili tutelati ArCo MiC con ~113k record nazionali: chiese, palazzi, castelli, archeologia, ville, monumenti, soprintendenze**, meteo ItaliaMeteo ICON-2I, **morfologia del territorio CNR-IRPI HR-DTM 5m one-shot**)
- **Istituzioni fonte**: **18** (ANAC, BDAP, Italia Domani, ISTAT, ISPRA, MIUR, ACI, MEF Dipartimento Finanze, MEF Dipartimento Economia, Agenzia Entrate, Ministero della Salute, GSE / MASE, AGCOM, MIMIT, Ministero del Lavoro e Politiche Sociali, **Ministero della Cultura (MiC) — ICCD/ArCo**, Dipartimento della Protezione Civile, **CNR-IRPI**)
- **Comuni coperti**: ~7.918

Il connettore espone **6 tool MCP**. La scelta del tool giusto dipende dalla query:

- **Query puntuali e confronti tra comuni** → `comune_kpi` (~620 token, leggero)
- **Vista dettagliata singolo comune** → `comune_dashboard` (~250K token, completo)
- Risoluzione nome → `search_comune`
- Dettaglio opere BDAP → `comune_opere_dettaglio`
- Query civici → `anncsu_civico_search`

## Tool inventory (6 tool MCP)

I tool sono **deferred**: vanno caricati con `tool_search` prima di invocarli.

| Tool | Scopo | Quando usarlo | Output size |
|---|---|---|---|
| `mcp_info` | Metadati server, freshness per source | Verifica freshness dataset | piccolo |
| `search_comune` | Nome → codice ISTAT 6-digit | Quando hai solo il nome del comune | piccolo |
| `comune_kpi` | **55 KPI sintetici** in 24 gruppi tematici. Importi anche pro-capite | **PRIMO TOOL** per query puntuali ("popolazione di Bari"), confronti tra N comuni ("Verona vs Bari"), ranking ("top 5 per FTTH") | ~620 token |
| `comune_dashboard` | **Vista completa 26 sezioni** con array dettagliati: top CPV, settori BDAP, missioni PNRR, piramide età, time series SIOPE pagamenti e incassi, mappe punti, top settori ATECO ASIA, sezioni di censimento 2021 | Vista approfondita singolo comune. Solo quando servono i dettagli che `comune_kpi` non ha | ~250K token |
| `anncsu_civico_search` | Query puntuali su civici con filtri server-side. Filtri: `odonimo` (substring), `civico` (esatto). Default limit 50, max 500 | Verifica esistenza/coordinate di un civico specifico | piccolo |
| `censimento_sezione_search` | **Ranking/lookup sulle 127 variabili censuarie raw** del Censimento Permanente 2023 a livello di **singola sezione di censimento** (sub-comunale). Modalità lookup (`sez_id`) o ranking (`var_name` ± `denominator_var` per percentuali). Esclude automaticamente le sezioni `no_vars` (33% delle sezioni nazionali sono aree non residenziali) | Quando serve dato granulare sub-comunale: "sezione di Roma con più stranieri Extra-UE in %", "vars censuarie della sezione X" | piccolo-medio |

**Regola pratica decision tree**:

```
User chiede dato singolo comune?
├─ Domanda puntuale o confronto tra comuni? → comune_kpi
├─ Dettaglio (mappe, top liste, time series)? → comune_dashboard
├─ Civico/strada specifica? → anncsu_civico_search
└─ Sezione di censimento specifica o ranking sub-comunale? → censimento_sezione_search
```

## Schema `comune_kpi` (output ~2.5KB)

Risposta strutturata in 25 gruppi tematici. Ogni gruppo contiene 2-7 campi scalari. Campi mancanti sono `null` espliciti (schema stabile).

```json
{
  "_generated_at": "2026-05-15T08:00:04+00:00",
  "_etl_version": "0.1.0",
  "_missing": [],
  "anagrafica": {"istat", "nome", "provincia_sigla", "regione", "codice_fiscale", "codice_catastale"},
  "demografia": {"popolazione", "maschi", "femmine", "eta_media", "indice_vecchiaia", "indice_dipendenza", "riferimento"},
  "istruzione_profilo": {"anno", "pct_terziario", "pct_diploma_oltre"},
  "lavoro_profilo": {"anno", "tasso_occupazione", "tasso_disoccupazione", "tasso_attivita"},
  "redditi_mef": {"anno_fiscale", "n_contribuenti", "reddito_medio_eur", "imposta_netta_media_eur"},
  "scuole_miur": {"n_scuole", "anno_scolastico", "scuole_per_1000_ab", "_nota"},
  // n_scuole conta i PLESSI (punti di erogazione MIUR), non le istituzioni
  // autonome: un istituto comprensivo compare con tutti i suoi plessi.
  // Le sedi di dirigenza sono in dashboard scuole.kpi.n_sedi_direttivo.
  "contratti_anac": {"n_aggiudicazioni", "importo_totale_eur", "importo_per_abitante_eur", "ultima_aggiudicazione"},
  "opere_bdap": {"n_progetti", "importo_totale_eur", "importo_per_abitante_eur"},
  "pnrr": {"n_progetti", "n_concluso", "n_in_corso", "importo_assegnato_eur", "importo_per_abitante_eur"},
  "siope": {"anno", "totale_uscite_eur", "uscite_per_abitante_eur", "totale_incassi_eur", "incassi_per_abitante_eur", "saldo_cassa_eur", "mesi_disponibili", "parziale"},
  "patrimonio_pa": {"n_immobili", "n_fabbricati", "n_terreni", "superficie_totale_mq"},
  "ambiente": {"superficie_kmq", "consumo_suolo_pct", "raccolta_differenziata_pct", "rifiuti_kg_per_abitante"},
  "aria_ispra": {"ha_stazione", "anno", "pm10_media", "pm25_media", "no2_media"},
  "turismo": {"anno", "totale_strutture", "totale_letti", "indice_turisticita_per_100ab"},
  "veicoli_aci": {"anno", "totale_veicoli", "autovetture", "tasso_motorizzazione_per_1000_ab", "pct_inquinanti"},
  "banda_larga_agcom": {"famiglie_residenti", "copertura_ftth_pct", "copertura_ftth_20m_pct", "data_rilevazione"},
  "ricarica_ev_pun": {"n_totale", "n_attivi", "pct_attivi", "potenza_totale_kw", "punti_per_1000_ab"},
  "carburanti_mimit": {"n_impianti", "n_pompe_bianche", "prezzo_medio_benzina_self", "prezzo_medio_gasolio_self", "impianti_per_1000_ab"},
  "civici_anncsu": {"n_strade", "n_civici", "pct_geo_ref", "snapshot_date"},
  "terzo_settore_runts": {"n_enti_totali", "n_5x1000", "pct_5x1000", "enti_per_1000_ab", "snapshot_date"},
  "imprese_asia": {"anno", "ul_totali", "addetti_totali", "addetti_per_ul", "ul_yoy_pct", "ul_per_1000_ab"},
  "sanita_mds": {"n_farmacie", "n_parafarmacie", "n_ospedali", "posti_letto_ospedalieri", "farmacie_per_1000_ab"},
  "censimento": {"n_sezioni", "pop_totale", "famiglie_totali", "abitazioni_totali", "stranieri_totali", "occupati_15_64", "area_kmq", "densita_ab_per_kmq"},
  "beni_culturali_mic": {"n_beni_immobili", "n_visitabili", "n_con_coordinate", "n_senza_coordinate", "pct_con_foto", "pct_con_descrizione", "beni_per_1000_ab", "snapshot_date"},
  "morfologia": {"elev_min", "elev_max", "elev_mean", "slope_mean", "slope_gt15_pct", "aspect_dom", "aspect_dom_pct", "tri_mean", "geo_versanti", "geo_creste", "geo_impluvi", "geo_pianori", "solar_mean"}  // null se comune non ancora processato
}
```

**Importi finanziari**: sempre in **euro interi**. Versione **pro-capite o per-1000-abitanti** disponibile dove ha senso comparabile (ANAC, BDAP, PNRR, SIOPE, scuole, ricarica EV, carburanti, RUNTS, farmacie, imprese ASIA).

**Trend YoY**: non inclusi in `comune_kpi`, eccetto `imprese_asia.ul_yoy_pct` (variazione UL anno precedente). Per altre serie storiche usa `comune_dashboard.redditi.anni[*]`, `.siope.per_anno[*]`, o `.asia.serie_storica`.

**Classificazione sismica** (zona 1-4, DPC): non in `comune_kpi`. Sta in `comune_dashboard.territorio.classificazione_sismica` → `zona_principale` (int 1-4) e `zona_sismica` (con eventuale sottozona, es. "2A"/"3S"). Per "che zona sismica / classificazione sismica ha X" chiama direttamente `comune_dashboard`.

## Workflow patterns

### Pattern 1: query puntuale singolo comune

User: *"Qual è il reddito medio a Lecce?"*

```
1. search_comune("Lecce") → istat="075035"
2. comune_kpi(istat_code="075035") → leggi redditi_mef.reddito_medio_eur
```

### Pattern 2: confronto N comuni

User: *"Compara Verona, Bari e Lecce su PNRR e banda larga"*

```
1. search_comune("Verona") → "023091"
2. search_comune("Bari") → "072006"
3. search_comune("Lecce") → "075035"
4. comune_kpi("023091"), comune_kpi("072006"), comune_kpi("075035")
5. Estrai pnrr.importo_per_abitante_eur e banda_larga_agcom.copertura_ftth_pct
   da ciascuna risposta. Componi tabella.
```

**Vantaggio kpi vs dashboard**: 3 chiamate × ~620 token = ~1.860 token totali invece di ~750.000 con `comune_dashboard`.

### Pattern 3: vista approfondita singolo comune

User: *"Mostrami i top 5 contratti di Bari per importo"*

```
1. search_comune("Bari") → "072006"
2. comune_dashboard(istat_code="072006") → leggi anac.cpv (tutte le categorie merceologiche) o anac.top_cpv (prime 5)
```

Qui serve `comune_dashboard` perché `comune_kpi` non ha array (solo conteggi aggregati).

### Pattern 4: civico specifico

User: *"Quale è la quota altimetrica di Via dei Mille civico 5 a Lecce?"*

```
1. search_comune("Lecce") → "075035"
2. anncsu_civico_search(istat_code="075035", odonimo="MILLE", civico="5")
```

### Pattern 5: opere pubbliche dettaglio

User: *"Quali sono le opere PNRR in corso a Matera con CUP?"*

```
1. search_comune("Matera") → "077014"
2. comune_opere_dettaglio(istat_code="077014") → filtra per stato="ATTIVO"
```

### Pattern 6: tessuto economico e imprese

User: *"Quanti addetti e in quali settori ha Lecce?"*

```
1. search_comune("Lecce") → "075035"
2. comune_kpi("075035") → leggi imprese_asia.ul_totali, addetti_totali,
   addetti_per_ul (dimensione media UL)
3. Se servono top settori ATECO o classi dimensionali:
   comune_dashboard("075035") → leggi asia.kpi.top_settori_ul,
   asia.kpi.mix_classe_addetti, asia.serie_storica
```

User: *"Confronto tessuto economico Milano vs Bari"*

```
1. comune_kpi("015146"), comune_kpi("072006")
2. Estrai imprese_asia.{ul_totali, addetti_per_ul, ul_per_1000_ab}
   da ciascuna. Componi tabella.
```

### Pattern 7: censimento sezioni 2023 (Basi Territoriali 2021)

Due livelli di lettura:

**A. Aggregati comune-level** → usa `comune_kpi` o `comune_dashboard.censimento`. Il dato è in larga parte ridondante col `profilo` (Censimento permanente annuale): popolazione totale, M/F, famiglie, abitazioni occupate/vuote, stranieri UE/extra-UE, occupati 15-64. Va bene per il *cittadino medio* che chiede "quanti abitanti ha Lecce?".

**B. Dato sub-comunale (la vera ricchezza del BT 2021)** → usa il tool dedicato `censimento_sezione_search`. Restituisce le **127 variabili censuarie raw** per singola sezione di censimento (granularità inferiore al comune). Due modalità:

- **lookup** con `sez_id`: 1 sezione, tutte le 127 vars
- **ranking** con `var_name` (+ opzionale `denominator_var` per percentuali): top N sezioni ordinate

User: *"Dammi le variabili censuarie complete della sezione 750350001012"*

```
1. censimento_sezione_search(istat_code="075035", sez_id="750350001012")
2. Output: lat/lon centroide + area_kmq + dict {P1: 683, P2: 332, ..., NA1: 201}
   (le 127 vars raw).
```

User: *"Quale sezione di Lecce ha la più alta percentuale di stranieri Extra-UE?"*

```
1. search_comune("Lecce") → "075035"
2. censimento_sezione_search(istat_code="075035",
     var_name="ST19", denominator_var="ST1",
     top=5, min_pop=50)
   # ST19/ST1*100 = % extra-UE su totale stranieri.
   # min_pop=50 esclude sezioni con < 50 abitanti (rumore statistico).
3. Output: top 5 sezioni con coord centroide + 127 vars + _computed (la %).
```

User: *"Le 10 sezioni più popolose di Roma"*

```
1. censimento_sezione_search(istat_code="058091", var_name="P1", top=10)
   # P1 = popolazione totale. Senza denominator_var → ranking sul valore assoluto.
```

User: *"Sezioni di Milano col tasso più alto di abitazioni vuote (almeno 50 abitazioni)"*

```
1. censimento_sezione_search(istat_code="015146",
     var_name="A3", denominator_var="A8",
     top=10, min_pop=0)
   # A3 = abitazioni vuote, A8 = totali. min_pop solo su P1 (pop residente),
   # se vuoi escludere zone con poche abitazioni puoi affidarti al
   # denominator_var nullo (A8=0) che esclude automaticamente.
```

Codici variabili più comuni (catalogo non esaustivo):

| Code | Significato |
|---|---|
| `P1` | popolazione totale |
| `P2` / `P3` | maschi / femmine |
| `P14-P29` | 16 fasce età 5-anni (totale) |
| `P27-P29` | 65-69 / 70-74 / 75+ |
| `P83` | popolazione 9+ (denom. per titolo studio) |
| `P86-P90` | titolo studio: nessuno / elem. / media / diploma / terziario |
| `P101-P103` | occupati 15-64 (totale / maschi / femmine) |
| `IT1-IT12` | italiani per fascia età |
| `ST1` | stranieri totali |
| `ST16` / `ST19` | stranieri UE / Extra-UE |
| `ST3-ST5` | stranieri per fascia età (0-29, 30-54, 55+) |
| `PF1` | famiglie totali |
| `PF3-PF8` | famiglie con 1 / 2 / 3 / 4 / 5 / 6+ componenti |
| `A2` / `A3` / `A8` | abitazioni occupate / vuote / totali |
| `A5` | altri alloggi occupati |
| `PF9` | famiglie coabitanti |
| `NA1` | automobili di proprietà |
| `EM1-EM6` | paese di nascita e cittadinanza: italiani dalla nascita / acquisiti / stranieri e apolidi, nati in Italia o all'estero |

Indicatori derivati comuni (rapporti su 2 vars):

| Indicatore | var_name | denominator_var | Note |
|---|---|---|---|
| % stranieri | `ST1` | `P1` | sul totale popolazione |
| % stranieri Extra-UE | `ST19` | `ST1` | sul totale stranieri |
| % laureati 9+ | `P90` | `P83` | titolo terziario su pop 9+ |
| % famiglie unipersonali | `PF3` | `PF1` | famiglie 1 componente / tot |
| % abitazioni vuote | `A3` | `A8` | sul tot abitazioni |
| % donne occupate | `P103` | `P101` | sul tot occupati |
| Indice di vecchiaia | (manuale, P27+P28+P29 / P14+P15+P16) | — | il tool non supporta somme |

**Limite**: il tool ammette solo rapporto fra 2 vars singole. Per somme o medie complesse il consumer deve calcolarle lato client (esempio: indice di vecchiaia richiede 3+3 vars sommate, va calcolato in 2 step usando `lookup` di sezioni candidate)..

### Pattern 8: beni culturali e patrimonio tutelato

User: *"Quanti monumenti tutelati ci sono a Matera?"* / *"Top 10 comuni per beni culturali"* / *"Chiese e palazzi tutelati a Lecce"*

→ `comune_kpi` per la sintesi numerica:

```json
{
  "beni_culturali_mic": {
    "n_beni_immobili": 250,        // totale beni immobili ArCo tutelati MiC
    "n_visitabili": 12,            // sottoinsieme con link a Cultural-ON (orari, contatti)
    "n_con_coordinate": 198,
    "n_senza_coordinate": 52,      // utile per messaggio UI "mappa: 198 di 250"
    "pct_con_foto": 88.5,
    "pct_con_descrizione": 28.3,
    "beni_per_1000_ab": 4.16,
    "snapshot_date": "2026-05-23"
  }
}
```

→ Per la **lista dettagliata** (denominazione, categoria, lat/lon, indirizzo, foto, descrizione, soprintendenza, link Cultural-ON quando visitabile) usa `comune_dashboard.beni_culturali.luoghi[]`. Lo shard base contiene fino a 30 beni rappresentativi; per comuni grandi (Roma, Firenze, Milano) lo shard FULL su `/data/beni_culturali_full/<istat>.json` ha la lista completa.

**Categorie normalizzate** (campo `categoria`): chiesa, palazzo, castello, archeologia, museo, monumento, infrastruttura, parco_giardino, altro. **Tipo grezzo** ArCo (`tipo_raw`): centinaia di slug granulari (basilica, abbazia, rocca, terme, mausoleo, ecc.).

**Coverage**: ~71% dei comuni italiani (5.657 / 7.895) hanno almeno un bene immobile tutelato MiC. I comuni senza beni hanno `n_totale: 0` e `luoghi: []` (no 404).

## Caveat per sezione

- **anac**: copre solo i comuni che sono *stazioni appaltanti* nel periodo monitorato. Comuni piccoli senza aggiudicazioni → `anac: null` o counter zero. La data `ultima_aggiudicazione` indica la freschezza reale del dato.
- **aria_ispra**: solo 604 comuni hanno stazione di rilevazione. Se `ha_stazione: false`, le medie PM10/PM2.5/NO2 sono null.
- **immobili_pa**: snapshot 2022 (MEF DE Censimento Beni Immobili). Dato non aggiornato annualmente.
- **anncsu**: snapshot Agenzia Entrate. Coverage geografica varia per comune.
- **runts**: include 6 sezioni ETS (ODV/APS/EF/IS/SMS/ETS). `pct_5x1000` indica enti accreditati al 5x1000.
- **pun**: punti di ricarica EV dichiarati al GSE. `pct_attivi` < 100% normale (impianti in collaudo).
- **banda_larga_agcom**: copertura DESI = % famiglie raggiunte da almeno una linea FTTH. `copertura_ftth_20m_pct` = % raggiunte a velocità ≥ 1 Gbps.
- **imprese_asia / .asia**: dati ISTAT ASIA UL. **Latency upstream ~2 anni**: nel 2026 `latest_year=2023` (ISTAT pubblica con Q4 anno succ.). **Caveat conta UL non imprese**: un'impresa con sedi multiple compare in più comuni; il dato è coerente con la logica operativa territoriale (dove sono i lavoratori), non con la logica societaria. Per comuni molto piccoli (UL totali <10) il `ul_yoy_pct` è poco significativo (rumore su numeri piccoli). Serie storica: 2018-2023 (6 anni). ATECO classificazione NACE Rev.2 a 2 cifre (88 divisioni economiche). Classi addetti: W0_9 (micro 1-9), W10_49 (piccole), W50_249 (medie), W_GE250 (grandi). Licenza CC BY 3.0 IT.
- **censimento (Basi Territoriali 2021)**: 756.376 sezioni nazionali, 7904 comuni coperti (100% incluso TN/BZ). **Distinto da `profilo`**: `profilo` è il Censimento permanente *annuale* aggregato comune-level (rilascio 2024); `censimento` è *Basi Territoriali 2021 + Variabili censuarie 2023* — dato biennale a livello di **sezione di censimento** (poligoni georeferenziati) con 127 variabili per sezione. **35% sezioni 'no_vars'**: aree non residenziali (parchi nazionali, zone industriali, infrastrutture, aree militari) non rilevate dal Censimento permanente perché disabitate — è atteso, non bug. Sezione `censimento` nel dashboard contiene solo aggregati comune-level (~3-5 KB); per le 127 variabili raw e le geometrie usa `/data/censimento_full/<istat>.geojson` (30 KB - 3 MB, EPSG:4326). Ultimo aggiornamento ISTAT 09/06/2026. Licenza CC BY 3.0 IT.
- **beni_culturali_mic**: dati MiC ArCo `ImmovableCulturalProperty` (~113.820 record nazionali). **Coverage 71% comuni** (5657/7895): comuni senza beni tutelati MiC hanno `n_totale: 0` e `luoghi: []` (no 404, shard sempre presente per consistenza UX). **n_con_coordinate / n_senza_coordinate**: il 77% dei beni ha geometria WKT POINT — usa questi due valori per banner UI "la mappa mostra N di M beni" quando metti il dato in mappa Leaflet. **n_visitabili**: subset con link a Cultural-ON (`hasCulturalInstituteOrSite` → CIS DBUnico 2.0): tipicamente musei, siti archeologici, biblioteche con orari/contatti. **Categorie normalizzate**: 9 macro (chiesa, palazzo, castello, archeologia, museo, monumento, infrastruttura, parco_giardino, altro) mappate da centinaia di slug ArCo tramite controlled vocabulary CulturalPropertyType (es. "basilica" → chiesa). Aggiornamento mensile via cron VM (1° del mese 05:00). Fonte: SPARQL endpoint `dati.beniculturali.it/sparql`. Licenza CC BY 4.0.

- **morfologia (CNR-IRPI HR-DTM 5m)**: dato one-shot (pipeline non periodica). Shard separato `/data/morfologia/<istat>/<istat>_stats.json` — non incluso nel dashboard shard R2 ma fetchato e allegato dal Worker in risposta a `comune_dashboard`. Se il comune non è ancora stato processato, il campo `morfologia` è assente dalla risposta (non è null, è proprio assente). Progressivo: ~7.895 comuni in elaborazione. Non aggiornato periodicamente salvo nuova versione DTM CNR-IRPI. Licenza CC BY 4.0.

## Endpoint REST aggiuntivi

**Base URL di TUTTI gli endpoint REST `/data/...`**: `https://cruscotto-italia.dati.gov.it`
(è il sito Cruscotto Italia, NON l'host del connettore MCP). Esempio completo:
`/data/catasto_full/075035_ple.geojson.gz` →
`https://cruscotto-italia.dati.gov.it/data/catasto_full/075035_ple.geojson.gz`.
Questi path sono pubblici e same-origin del sito, accessibili **senza autenticazione**.
NON usare l'host del connettore MCP (`*.workers.dev`) per i path `/data/...`, e NON
usare MAI servizi esterni (geoportale/WMS/WFS Agenzia delle Entrate): il dato è già tutto qui.

Endpoint disponibili (anteporre sempre il Base URL sopra):

- `GET /data/anncsu_full/<istat>.json` — dataset completo civici per comune (per Roma 515.815, Milano ~280.000). Usalo solo se serve davvero il dato completo; per query puntuali usa `anncsu_civico_search` che evita di buttare 500k civici nel context LLM.
- `GET /data/censimento_full/<istat>.geojson` — FeatureCollection EPSG:4326 (WGS84) di tutte le sezioni di censimento del comune, con poligoni georeferenziati + 127 variabili demografiche/abitative raw per sezione (P1-P103, IT1-IT12, ST1-ST33, PF1, PF3-PF9, A2/A3/A5/A8, NA1, EM1-EM6). Dimensioni: ~30 KB (Morterone) — ~3 MB (Roma 13.000 sezioni). Usalo solo per query che richiedono il dettaglio per sezione (densità, mappe choropleth, filtri territoriali sub-comunali); per gli aggregati comune-level basta `comune_dashboard.censimento` (~3 KB).

### Catasto Agenzia delle Entrate (cartografia INSPIRE, CC-BY 4.0)

Il catasto **NON è esposto come tool MCP**: una singola particella è un poligono di decine/centinaia di coordinate, e un comune ne ha migliaia (Roma ~492.000). Restituirle via MCP saturerebbe il context. Il catasto è quindi accessibile **solo via REST GeoJSON gzippato**, e l'agente compone l'URL deterministicamente.

Endpoint:

- `GET /data/catasto_full/<istat>_map.geojson.gz` — **fogli** catastali del comune (CadastralZoning). Sempre presente (< 1 MB). Property chiave: `NATIONALCADASTRALZONINGREFERENCE` (es. `H501_012900`).
- `GET /data/catasto_full/<istat>_ple.geojson.gz` — **particelle** monolitiche (CadastralParcel), solo se ≤ 20 MB. Property chiave: `NATIONALCADASTRALREFERENCE` (es. `H501_012900.455`).
- `GET /data/catasto_full/<istat>_ple/<BELFIORE>_<FFFF00>.geojson.gz` — particelle di **un singolo foglio**. Sempre presente (è lo split per foglio, usato anche dal frontend in modalità chunking per i comuni grandi come Roma).

**Schema di composizione URL** (per query tipo "mostra/dammi foglio N particella M del comune X"):

1. Risolvi `X` → ISTAT + BELFIORE. Il BELFIORE (codice catastale comune, 4 char) è in `comune_kpi` / `comune_dashboard.anagrafica.codice_catastale`.
2. Padding foglio: `N` → `FFFF00` = numero foglio a 4 cifre zero-pad + suffisso `00`. Es. foglio 129 → `012900`.
3. URL: `/data/catasto_full/<ISTAT>_ple/<BELFIORE>_<FFFF00>.geojson.gz`. Es. Palermo (082053, G273) foglio 129 → `/data/catasto_full/082053_ple/G273_012900.geojson.gz`.
4. Scarica, decomprimi gzip, filtra le feature con `properties.NATIONALCADASTRALREFERENCE` che termina in `.<M>` (es. `.455`).
5. Mostra/estrai solo quella geometria (centroide per pin su mappa, o poligono completo).

**Copertura**: 19 regioni (esclusa Provincia Autonoma di Trento e Bolzano, catasto gestito localmente). Alcuni comuni hanno zone urbane non coperte dal dataset INSPIRE upstream di AGE (es. Napoli Vomero, Salerno centro storico — vedi `catasto_anomalies.json`); il dato è comunque fedele al 100% a quanto AGE pubblica.

### Pattern 9: foglio/particella catastale specifici

```
Utente: "mostrami il foglio 129 particella 455 di Palermo"
1. search_comune("Palermo") → ISTAT 082053
2. comune_kpi(082053) → anagrafica.codice_catastale = G273 (BELFIORE)
3. componi URL: /data/catasto_full/082053_ple/G273_012900.geojson.gz
4. fetch + gunzip + filtra NATIONALCADASTRALREFERENCE == "G273_012900.455"
5. restituisci geometria / centroide / link mappa
```

Nota: se la particella non esiste nel foglio, il filtro torna vuoto → comunicalo all'utente invece di inventare coordinate.

### Pattern 10: civico/coordinata → foglio e particella (geocoding inverso)

Per domande tipo "a quale foglio/particella appartiene Via X, civico N?" oppure
"qual è la particella alle coordinate lat,lon?": il calcolo è **interamente locale**
(civici ANNCSU + catasto sono entrambi qui). **NON interrogare MAI WMS/WFS o il
geoportale cartografico dell'Agenzia delle Entrate**: non serve e non va fatto.

```
Utente: "foglio e particella di Via Vittorio Emanuele II 29 a Lecce"
1. anncsu_civico_search → coordinate del civico (lat, lon)
2. GET /data/catasto_full/<istat>_ple.geojson.gz  (monolitico; Roma = split per foglio)
3. point-in-polygon: trova la particella che contiene il punto
4. se il match è STRADA/ACQUA (il civico è georef sul fronte strada), riporta il
   FOGLIO (certo) + le particelle edificate adiacenti dello stesso foglio (candidate)
```

Promessa corretta: **foglio certo + particelle candidate adiacenti**, non "la
particella esatta" (il civico cade quasi sempre sulla sede stradale). Algoritmo
completo, snippet point-in-polygon, parser dei due formati di
`NATIONALCADASTRALREFERENCE` ed esempio validato (Lecce civico 29 → foglio 0259,
particelle 1648/1649): vedi `references/catasto_geocoding.md`.

Accesso ai file: prova prima `bash`/python nel container; se la rete in uscita è
bloccata, **NON arrenderti** — gli endpoint `/data/` hanno CORS `*`, quindi costruisci
un widget browser che fa fetch + gunzip + point-in-polygon lato client (snippet JS
pronto nella reference). Non comporre mai a mano il codice foglio (può avere sezione
catastale, es. `0025D0`): leggi `NATIONALCADASTRALREFERENCE` dalla feature.

## Codici ISTAT di test consigliati

| Codice | Comune | Note |
|---|---|---|
| 075035 | Lecce | comune medio, ampia copertura dati, ~11.900 UL ASIA |
| 072006 | Bari | capoluogo Puglia |
| 058091 | Roma | metropoli, dati molto ricchi |
| 015146 | Milano | metropoli nord, capoluogo industriale |
| 077014 | Matera | medio sud, patrimonio UNESCO |
| 007021 | Cogne | piccolo comune montano |
| 097055 | Morterone | comune piccolissimo (33 abitanti), test edge case ASIA (3 UL) e censimento (~1-2 sezioni) |
| 082053 | Palermo | capoluogo Sicilia, test pattern catasto foglio/particella (BELFIORE G273) |
| 001272 | Torino | capoluogo Piemonte, n_civici_geo_ref=0 → catasto presente ma civici non geo-referenziati (mappa parte comunque) |
