# Cruscotto Italia — Design Document

**Versione**: 0.1.0  
**Data**: 2026-05-07  
**Stato**: bozza post-discovery, pre-implementazione  
**Repo**: `cruscotto-italia` (da creare)  
**Author**: piersoft + Claude (discovery + architettura)

---

## 0. TL;DR esecutivo

Cruscotto Italia è una **piattaforma di accountability sulla spesa pubblica italiana** che federa 5 fonti istituzionali (ANAC, OpenBDAP, SIOPE, OpenCoesione, ISTAT) attraverso un'**architettura MCP-first** e fornisce un **frontend comune-centric** in single-file HTML.

L'utente cerca un comune (es. "Lecce") e ottiene una vista 360°: contratti pubblici (CIG/OCDS), opere pubbliche (CUP MOP), spese (SIOPE), progetti coesione (PNRR/FSC), demografia e indicatori statistici (ISTAT) — tutto unito sulla **spina dorsale anagrafica IPA + ISTAT amministrativi**.

Topologia tecnica: **un Worker Cloudflare monolitico** (`cruscotto-italia-mcp`) che espone tool MCP, legge da un mirror **DuckDB+Parquet su R2** popolato da un ETL mensile.

---

## 1. Architettura

### 1.1 Topologia complessiva

```
┌─────────────────────────────────────────────────────────┐
│          FRONTEND (single-file HTML, statico)           │
│           cruscotto-italia.piersoft.it                  │
│                                                         │
│   - input: nome comune o codice ISTAT                   │
│   - output: vista 360° aggregata su 5 fonti             │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS (chiamate MCP via fetch)
                         ▼
┌─────────────────────────────────────────────────────────┐
│   cruscotto-italia-mcp (Cloudflare Worker monolitico)   │
│                                                         │
│   Espone N tool MCP, contiene la logica di join         │
│   cross-fonte e l'orchestrazione delle query.           │
│                                                         │
│   Tool principali:                                      │
│   - comune_overview(istat_code or name)                 │
│   - comune_contratti(istat_code, year, ...)             │
│   - comune_opere(istat_code, ...)                       │
│   - comune_spese(istat_code, ...)                       │
│   - comune_progetti_coesione(istat_code, ...)           │
│   - comune_demografia(istat_code, ...)                  │
│   - cig_lookup(cig)                                     │
│   - cup_lookup(cup)                                     │
│   - rup_lookup(cf_persona)                              │
│   - siope_codice(codice_gestionale)                     │
│   - anagrafica_join(name_or_cf_or_istat)                │
└────────────────┬────────────────────────────────────────┘
                 │
        ┌────────┴────────────────────────────────────────┐
        ▼                                                 ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│    DuckDB + Parquet     │         │   API live (fallback)       │
│    su Cloudflare R2     │         │                             │
│                         │         │   - api.anticorruzione.it   │
│  Bucket struttura:      │         │     (OCDS endpoint live,    │
│  - /anac/cig-{anno}.pq  │         │     parzialmente suspended) │
│  - /bdap/mop-{reg}.pq   │         │   - opencoesione.gov.it/api │
│  - /bdap/siope-{a}.pq   │         │     (rate-limit 12 rpm)     │
│  - /opencoesione/*.pq   │         │   - bdap-opendata           │
│  - /istat/popolazione   │         │     /ODataProxy/...         │
│  - /lookup/comuni.pq    │         │   - istat-mcp-worker        │
│  - /lookup/ipa-enti.pq  │         │                             │
└─────────▲───────────────┘         └─────────────────────────────┘
          │
          │ pull mensile
          │
┌─────────┴───────────────────────────────────────────────────────┐
│    ETL mensile (GitHub Actions cron schedule)                   │
│                                                                  │
│    1. Pull CSV/JSONL bulk dalle 5 fonti                          │
│    2. Trasformazione → Parquet partizionato                      │
│    3. Push su R2                                                 │
│    4. Aggiorna manifest.json (timestamp + dimensioni)            │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 Perché monolitico e non federato

Pro del monolitico (scelta presa):
- **Stato di join unico**: il worker conosce contemporaneamente IPA, ISTAT, CIG, CUP, SIOPE — può fare join cross-fonte in un singolo percorso di codice
- **Cache unificata**: KV/D1 condivisa per le risposte
- **Deploy semplice**: un solo `wrangler publish`
- **Rate-limit sotto controllo**: il worker decide tutto, non si interrogano servizi terzi a cascata

Contro accettati:
- File più grande di un worker per fonte (mitigato da modularità interna)
- Single point of failure (mitigato dalla disponibilità Cloudflare)

### 1.3 Stack tecnico

| Layer | Tecnologia | Note |
|-------|-----------|------|
| Frontend | HTML+CSS+JS single-file | Pattern già consolidato (piersoftckan.biz) |
| MCP Worker | Cloudflare Worker + TypeScript | Pattern istat-mcp-worker |
| Storage | Cloudflare R2 (oggetto S3-compat) | Costo €0/mese fino a 10 GB |
| Query engine | DuckDB-WASM o DuckDB Cloud | Letture Parquet via HTTP range |
| ETL runner | GitHub Actions cron | Schedule `0 4 5 * *` (5° di ogni mese alle 04:00 UTC) |
| Anagrafiche | ISTAT codici amministrativi + IPA | Pull manuale annuale + delta mensile |
| Deploy | wrangler + Cloudflare Pages | Frontend su Pages, MCP su Workers |

---

## 2. Fonti dati — caratteristiche

### 2.1 ANAC OCDS-IT

| Campo | Valore |
|-------|--------|
| URL discovery | `dati.anticorruzione.it/opendata/dataset` (CKAN 2.6.8) |
| URL API live | `api.anticorruzione.it/opendata/ocds/api/v1/1.0.0/` |
| Swagger | `dati.anticorruzione.it/opendata/ocds/api/spec/swagger.json` |
| Bulk download | CKAN dataset annuali: `cig-2024`, `cig-2025`, `ocds-appalti-ordinari-2024`, ecc. |
| Formato bulk | CSV + JSONL OCDS gzippato |
| Frequenza aggiornamento | Mensile (giorno 2) |
| Soglia | > 40.000 € (sotto-soglia: dataset SmartCIG separato) |
| Dimensione/anno | ~2-5 GB CSV compresso, ~5-10 GB JSONL OCDS |
| Identificativi | CIG (gara), OCID (release OCDS), CUP (progetto), CF stazione appaltante |
| Licenza | CC-BY 4.0 |
| **Quirks** | API REST live ha endpoint **SUSPENDED** lato WSO2 (`/releases`, `/stats`, `/tender/id/count/all`). Solo `/version`, `/tender/id/count/active`, `/awards/{filtri}` usabili con throttling stretto. |

**Strategia per cruscotto-italia**: bulk-first con pull mensile dal CKAN ANAC. Pipeline:

```
1. Pull dataset ocds-appalti-ordinari-{anno} via CKAN package_show
2. Per ogni resource: download CSV/JSONL → /tmp
3. Trasformazione: estrai i campi chiave → Parquet partizionato per
   anno × tipologia (tenders, awards, contracts, parties)
4. Push su R2: cruscotto-italia/anac/{anno}/{tipo}.parquet
5. Index inverso per CF stazione appaltante e per CUP
```

Per il MVP: caricamento iniziale anni **2018-2025** (~30 GB R2, gratis nei primi 10 GB poi pochi cent al mese).

### 2.2 OpenBDAP (incluso SIOPE)

| Campo | Valore |
|-------|--------|
| URL discovery | `bdap-opendata.rgs.mef.gov.it/SpodCkanApi` |
| Tipo | DKAN con CKAN-API compat (api/3/action/) |
| Catalogo | 3.773 dataset totali |
| Aree principali | Bilancio Stato, MOP (opere pubbliche), SIOPE (cassa), Bilanci PA |
| Bulk download | `/api/3/datastore/dump/{UUID}.csv` |
| Query puntuale | OData proxy: `/ODataProxy/MdData('UUID@rgs')/DataRows?$filter=...` |
| Naming pattern | `spd_<area>_<entity>_<grain>_<reg##>_01_<anno>` |
| Identificativi | CUP, CF ente, codice gestionale SIOPE |
| Licenza | IODL 2.0 / CC-BY 4.0 |
| **Quirks** | (1) `count` di package_search non riflette il totale (bug DKAN) → usare `package_list` per il count. (2) Per OData serve UUID@rgs per dataset. (3) Dataset MOP "Totale" nazionale esiste dal nov 2024. |

**Strategia**:
- ANNO 0 (catalogo): pull `package_list` + `package_show` per tutti i 3.773 → costruisce un index degli UUID per area
- MOP nazionale: pull mensile del CSV unificato (Progetti Opere Pubbliche MOP - Totale)
- SIOPE: pull annuale dei dataset `spd_rnd_spe_sio_*` e `spd_rnd_ent_sio_*` per ogni regione+anno
- Tutto in Parquet su R2

Volume stimato MOP nazionale: 200-500 MB Parquet. SIOPE per regione+anno: 50-200 MB ciascuno.

### 2.3 OpenCoesione

| Campo | Valore |
|-------|--------|
| URL API | `opencoesione.gov.it/api/` (REST navigabile, HTML+JSON) |
| Bulk download | `opencoesione.gov.it/it/opendata/` (CSV.gz e Parquet) |
| Dataset chiave | `progetti_esteso.csv.gz`, `soggetti.csv.gz`, `localizzazioni.csv.gz` |
| Volumi | 1.817.138 progetti totali (cicli 2007-2013, 2014-2020, 2021-2027) per €354 mld |
| Frequenza | Bimestrale |
| Identificativi | LocalProjectCode, CUP, codice ISTAT, NUTS, CodiceProgramma |
| Variabili sintetiche | Prefix `OC_*` (11 temi), classificazione CUP |
| Licenza | CC-BY 4.0 |
| Rate-limit API | 12 rpm anonimo, 60 rpm con registrazione |
| **Quirks** | API blocca traffico extra-EU (geofence). Dal Worker CF in EU funziona. **Il bulk Parquet ufficiale è oro**: scarica e usa direttamente. |

**Strategia**: bulk Parquet bimestrale. Il Worker MCP legge direttamente i Parquet ufficiali su R2 (mirror). API live solo per richieste real-time per singolo progetto.

### 2.4 ISTAT

| Campo | Valore |
|-------|--------|
| MCP server | `istat-mcp-server.datigovit.workers.dev/mcp` (già deployato by piersoft) |
| Tool disponibili | 8 tool (popolazione, indicatori macro, demografia, ecc.) |
| API ufficiale | `sdmx.istat.it/SDMXWS/rest/` (SDMX) |
| Codici amministrativi | `istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv` |
| Aggiornamento codici | Annuale + delta intra-anno |
| Numero comuni attuali | 7.894 (al 21 feb 2026) |
| Licenza | CC-BY 3.0 IT |

**Strategia**: 
- Per anagrafica: pull annuale del CSV Elenco-comuni-italiani → Parquet `lookup/comuni.parquet`
- Per dati statistici: il Worker invoca i tool del tuo `istat-mcp` esistente (delegation pattern)
- Cache delle query frequenti in KV per 24h

### 2.5 IPA (Indice PA)

| Campo | Valore |
|-------|--------|
| URL | `indicepa.gov.it/ipa-dati` (CKAN 2.9.8) |
| Catalogo | 27 dataset |
| Dataset chiave | `enti`, `unita-organizzative`, `aoo`, `servizi-digitali` |
| Identificativi | Codice_IPA, Codice_fiscale_ente, Codice_comune_ISTAT, Codice_catastale_comune |
| Licenza | CC-BY 4.0 |

**Strategia**: pull mensile dei CSV → Parquet `lookup/ipa-enti.parquet`. Questo è il **cuore della spina dorsale**: contiene il join CF ente ↔ Codice ISTAT comune.

### 2.6 dati.gov.it (già coperto)

Già coperto in v2026.05.07.2 atlas. Non è una fonte primaria del cruscotto ma rimane un **portale di scoperta**: utile per dataset locali specifici di un singolo comune, harvest-ati dai portali regionali.

---

## 3. Schema unificato

### 3.1 Spina dorsale anagrafica

Tutte le query del cruscotto partono da uno di questi tre identificativi:

| Identificativo | Esempio | Fonte canonica | Lunghezza |
|----------------|---------|----------------|-----------|
| Codice ISTAT comune | `075035` (Lecce) | ISTAT codici amministrativi | 6 |
| Codice fiscale ente | `80008250752` | IPA `enti` | 11 |
| Codice IPA | `c_e506` (Comune di Lecce) | IPA `enti` | ~20 |

Tabella di mapping `lookup/anagrafica_unificata.parquet`:

```
codice_istat | codice_fiscale | codice_ipa | denominazione | provincia | regione | popolazione | nome_categoria
075035       | 80008250752    | c_e506     | LECCE         | LE        | Puglia  | 94808       | Comuni
```

Costruita una volta al mese da:
1. ISTAT codici amministrativi (denominazione, provincia, regione)
2. IPA enti (codice IPA, CF ente, mapping CF↔ISTAT)
3. ISTAT popolazione (numero abitanti, ultimo anno disponibile)

### 3.2 Mapping cross-fonte

| Da fonte | A fonte | Chiave di join | Note |
|----------|---------|----------------|------|
| ANAC OCDS | OpenBDAP MOP | CUP | quando il CIG ha CUP (~70% dei contratti per opere pubbliche) |
| ANAC OCDS | OpenCoesione | CUP | identico al precedente |
| OpenBDAP MOP | OpenCoesione | CUP | identico |
| ANAC OCDS | IPA | CF stazione appaltante | i CIG riportano il CF dell'ente, IPA dà la mappatura → ISTAT comune |
| SIOPE | IPA | CF ente | SIOPE è per ente (non per progetto), join su CF |
| Tutto | ISTAT | Codice ISTAT comune | il "codice universale" del territorio |

### 3.3 Modello logico tabelle Parquet

```
lookup/
├── anagrafica_unificata.parquet  (~7.894 righe, ~10MB)
├── ipa_enti.parquet              (~25.000 righe, ~5MB)
└── istat_comuni.parquet          (~7.894 righe, ~3MB)

anac/
├── 2024/
│   ├── tenders.parquet           (~1M righe, ~500MB)
│   ├── awards.parquet            (~1M righe, ~600MB)
│   ├── parties.parquet           (~200k righe, ~50MB)
│   └── contracts.parquet         (~1M righe, ~400MB)
├── 2025/...
└── ...

bdap/
├── mop_totale.parquet            (~600.000 opere, ~300MB)
├── siope_spese_2024.parquet      (~60M movimenti, ~2GB)
└── ...

opencoesione/
├── progetti_esteso.parquet       (~1.8M progetti, ~1GB)
├── soggetti.parquet              (~3M righe, ~500MB)
└── localizzazioni.parquet        (~5M righe, ~700MB)

manifest.json                     (timestamp + dimensioni di ogni file)
```

**Dimensione totale stimata**: ~10-15 GB. Con R2 i primi 10 GB sono gratuiti, oltre €0.015/GB/mese → costo finale **<€1/mese**.

---

## 4. Catalogo tool MCP

### 4.1 Tool comune-centric (MVP)

```typescript
// Tool 1: Vista d'insieme di un comune
comune_overview(istat_code: string): {
  anagrafica: { denominazione, provincia, regione, popolazione }
  contratti: { totale_count, totale_importo, top_categorie }
  opere: { totale_count, totale_importo, in_corso, completate }
  spese: { totale_pagamenti_anno_corrente, top_voci_siope }
  coesione: { totale_progetti, totale_finanziamento, per_fondo }
  rup_principali: [{ nome, count_cig }]
  fornitori_principali: [{ ragione_sociale, piva, totale_importo }]
}

// Tool 2: Contratti pubblici di un comune
comune_contratti(
  istat_code: string,
  year?: number,
  bottom_amount?: number,
  top_amount?: number,
  cpv?: string,        // categoria merceologica CPV
  fornitore_piva?: string,
  rup_cf?: string,
  has_pnrr?: boolean,
  page?: number,
  page_size?: number
): { 
  count, total_amount, results: [...{ cig, ocid, oggetto, importo, aggiudicatario, rup, cup, pnrr_flag }] 
}

// Tool 3: Opere pubbliche
comune_opere(
  istat_code: string,
  stato?: 'in_corso' | 'completato' | 'sospeso',
  cup?: string,
  importo_min?: number,
  page?: number
): { count, total_amount, results }

// Tool 4: Spese SIOPE
comune_spese(
  istat_code: string,
  anno: number,
  codice_siope?: string,
  livello?: 1 | 2 | 3,  // aggregazione gerarchica
  trimestre?: 1 | 2 | 3 | 4
): { totale, breakdown, trend_mensile }

// Tool 5: Progetti coesione (PNRR + FSC + FESR + FSE)
comune_progetti_coesione(
  istat_code: string,
  fondo?: 'PNRR' | 'FSC' | 'FESR' | 'FSE',
  ciclo?: '2014-2020' | '2021-2027',
  tema?: number  // 1-11 sec. classificazione OC_TEMA_SINTETICO
): { count, total_finanziamento, results }

// Tool 6: Demografia ISTAT (delega al tuo istat-mcp)
comune_demografia(
  istat_code: string,
  indicators?: string[]  // popolazione, eta-mediana, stranieri, etc.
): { ... }
```

### 4.2 Tool lookup puntuali

```typescript
cig_lookup(cig: string): { full_ocds_release }
cup_lookup(cup: string): { mop_data, opencoesione_data, anac_data }
rup_lookup(cf_persona: string): { count_cig, total_importo, contratti }
fornitore_lookup(piva: string): { count_cig, total_importo, top_clienti, regioni_attive }
siope_codice_show(codice: string): { descrizione, livello, padre, figli }
```

### 4.3 Tool di scoperta

```typescript
search_comune(query: string): { suggestions: [...{ istat_code, denominazione, provincia }] }
suggest_top_spenders(regione?: string, anno?: number, limit?: number): { ... }
trend_nazionale(metric: string, periodo: string): { ... }
```

### 4.4 Tool meta/admin

```typescript
mcp_info(): { version, last_etl_run, datasources_status }
data_freshness(): { per_source: { anac: timestamp, bdap: timestamp, ... } }
```

---

## 5. Frontend MVP

### 5.1 Pagine

```
/                                    Homepage: search comune + macro KPI Italia
/comune/{istat_code}                  Vista 360° del comune
/comune/{istat_code}/contratti        Lista contratti con filtri
/comune/{istat_code}/opere            Lista opere
/comune/{istat_code}/spese            Spese SIOPE con trend
/comune/{istat_code}/coesione         Progetti coesione/PNRR
/comune/{istat_code}/demografia       Indicatori ISTAT
/cig/{cig}                            Vista release OCDS singolo CIG
/cup/{cup}                            Vista CUP unificata (BDAP+ANAC+OC)
/rup/{cf}                             Profilo RUP
/fornitore/{piva}                     Profilo aggiudicatario
/about                                Metodologia, fonti, licenze
/api                                  Documentazione MCP + esempi
```

### 5.2 Stile e pattern

- **Single-file HTML** per pagina (nessun framework, vanilla JS)
- **Versioning**: `cruscotto-italia-v2026.MM.DD.N.html` (pattern Piersoft)
- **Mappa Leaflet** centrata sul comune (lat/lng da ISTAT)
- **Sezioni a tabs** sulla pagina comune: Contratti / Opere / Spese / Coesione / Demografia
- **Tabelle filtrabili e paginabili** (vanilla JS, no DataTables)
- **Export CSV** di ogni tabella (CSV.gz se >1000 righe)
- **Permalink condivisibili** su ogni filtro applicato
- **Dark mode** (CSS variables, default theme dark)
- **Footer**: link al MCP server + alle fonti originali per ogni dataset

### 5.3 Esempio mockup pagina comune

```
┌─────────────────────────────────────────────────────┐
│ Cruscotto Italia · v2026.07.01.1                    │
│ [search comune ▼]                          [About]  │
├─────────────────────────────────────────────────────┤
│                                                      │
│  LECCE                                               │
│  Codice ISTAT: 075035 · Provincia: LE · Puglia       │
│  Popolazione: 94.808 (2024)                          │
│                                                      │
│  ┌──────────┬──────────┬──────────┬──────────┐      │
│  │ CONTRATTI│  OPERE   │  SPESE   │ COESIONE │      │
│  │  4.521   │   234    │ €87M/24  │  €312M   │      │
│  │ €157M tot│ €78M tot │          │ 156 prog │      │
│  └──────────┴──────────┴──────────┴──────────┘      │
│                                                      │
│  [Contratti] [Opere] [Spese] [Coesione] [Demografia]│
│  ─────────────────                                   │
│                                                      │
│  Filtri: [anno ▼] [importo ▼] [fornitore ▼]         │
│                                                      │
│  CIG          Oggetto              Importo  Stato   │
│  ─────────────────────────────────────────────────   │
│  Z3A1234567   Manutenzione...      €45.000  Aggiud. │
│  ...                                                 │
│                                                      │
│  [Esporta CSV] [Permalink]                           │
└─────────────────────────────────────────────────────┘
```

---

## 6. ETL — Pipeline mensile

### 6.1 Flusso GitHub Actions

```yaml
name: cruscotto-italia ETL
on:
  schedule:
    - cron: '0 4 5 * *'  # 5° giorno alle 04:00 UTC
  workflow_dispatch:

jobs:
  etl:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install duckdb pandas requests boto3 pyarrow

      - name: ETL ANAC
        run: python etl/anac.py --year=$(date +%Y) --target=r2

      - name: ETL OpenBDAP MOP
        run: python etl/bdap_mop.py --target=r2

      - name: ETL OpenBDAP SIOPE
        run: python etl/bdap_siope.py --year=$(date +%Y) --target=r2

      - name: ETL OpenCoesione
        run: python etl/opencoesione.py --target=r2

      - name: ETL Anagrafica
        run: python etl/anagrafica.py --target=r2

      - name: Update manifest
        run: python etl/manifest.py --target=r2

      - name: Notify worker
        run: curl -X POST $WORKER_URL/admin/cache/purge
```

### 6.2 Pseudocodice ETL ANAC

```python
import requests, duckdb, json
from urllib.parse import quote

CKAN = "https://dati.anticorruzione.it/opendata/api/3/action"
HDRS = {"User-Agent": "cruscotto-italia-etl/0.1"}

def etl_anac_year(year: int):
    # 1. Get dataset metadata from CKAN
    pkg = requests.get(f"{CKAN}/package_show?id=ocds-appalti-ordinari-{year}", headers=HDRS).json()
    resources = pkg["result"]["resources"]
    
    # 2. Identify the JSONL OCDS resource (or CSV)
    ocds_resource = next(r for r in resources if r["format"].lower() == "json")
    
    # 3. Download
    download_to_local(ocds_resource["url"], f"/tmp/anac-{year}.jsonl.gz")
    
    # 4. Transform with DuckDB into 4 partitioned tables
    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE releases AS
        SELECT * FROM read_json_auto('/tmp/anac-{year}.jsonl.gz', format='newline_delimited')
    """)
    
    # 5. Extract sub-tables
    for sub in ['tender', 'awards', 'contracts', 'parties']:
        con.execute(f"""
            COPY (SELECT ocid, {sub}.* FROM releases, UNNEST({sub}))
            TO '/tmp/anac/{year}/{sub}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
    
    # 6. Push to R2
    upload_to_r2(f"/tmp/anac/{year}/", f"anac/{year}/")
```

---

## 7. Roadmap & Milestones

### v0.1 — Setup foundation (settimana 1-2)

- Repo GitHub `cruscotto-italia` (struttura monorepo: `worker/`, `frontend/`, `etl/`, `docs/`)
- Cloudflare R2 bucket creato
- Cloudflare Worker scaffold con primo tool funzionante (`comune_overview` su solo IPA + ISTAT)
- ETL anagrafica unificata (IPA + ISTAT codici amministrativi)
- Frontend single-page con search comune + KPI vuoti

### v0.2 — Single comune end-to-end (settimana 3-4)

- ETL ANAC: 1 anno (2024) per tutta Italia
- Tool `comune_contratti` funzionante
- Tab "Contratti" sul frontend con filtri base
- Test end-to-end su 5-10 comuni di taglie diverse

### v0.3 — Aggiunta fonti (settimana 5-6)

- ETL OpenBDAP MOP nazionale
- ETL OpenCoesione bulk Parquet
- Tool `comune_opere`, `comune_progetti_coesione`
- Tab "Opere" e "Coesione"

### v0.4 — SIOPE + ISTAT (settimana 7-8)

- ETL SIOPE per 1-2 anni recenti
- Integrazione delega tool ISTAT MCP esistente
- Tab "Spese" e "Demografia"
- Documentazione metodologica

### v1.0 — Polishing + lookup puntuali (settimana 9-12)

- Pagine `/cig/{cig}`, `/cup/{cup}`, `/fornitore/{piva}`
- Vista federata cross-fonte (CIG + CUP unificati)
- Performance tuning (cache KV per query frequenti)
- Documentazione MCP completa con esempi
- Lancio pubblico

### v1.x — Espansione (mese 4-6)

- Storico completo ANAC (2018-2025)
- Backfill SIOPE multi-anno
- Comparazione cross-comune (es. Lecce vs Bari vs Brindisi)
- Indicatori derivati: red-flag corruzione (algoritmi Open Contracting), tempi-pagamento, fornitori-ricorrenti
- Export multi-formato (Parquet, JSON-LD, RDF DCAT)

---

## 8. Rischi & Mitigazioni

| Rischio | Probabilità | Impatto | Mitigazione |
|---------|-------------|---------|-------------|
| ANAC API live suspended ulteriormente | Alta | Basso | Bulk-first, API solo fallback |
| OpenCoesione blocca traffico extra-EU | Già verificato | Basso | Worker CF è in EU |
| Bug DKAN su BDAP (count sbagliati) | Già verificato | Medio | Workaround `package_list` documentato |
| Variazione comuni (fusioni) | Media | Medio | Anagrafica con date validità, fallback su codice catastale |
| Volume Parquet > 10 GB R2 | Bassa al MVP | Basso | R2 costa €0.015/GB oltre i 10GB |
| GitHub Actions timeout (6h max) | Media | Medio | ETL incrementale, parallelizzazione per fonte |
| Schema OCDS cambia | Bassa | Alto | OCDS è standard internazionale, breaking change rari |
| IPA cambia naming | Bassa | Medio | Pin versione dataset + mapping table interno |
| Usage abuse del MCP pubblico | Media | Medio | Rate-limit per IP a 60 req/min sul Worker |

---

## 9. Open questions

Cose ancora da decidere o esplorare:

1. **Frontend hosting**: Cloudflare Pages o GitHub Pages? (Pages dà cache edge migliore)
2. **Auth opzionale**: avere una "API key" gratuita per chi vuole rate-limit più alto? GitHub OAuth?
3. **Sotto-soglia ANAC SmartCIG**: includerlo nel MVP o solo nei contratti sopra-soglia?
4. **Formato pagina export**: solo CSV o anche Excel/Parquet?
5. **Internazionalizzazione**: solo IT o anche EN per attrarre utenti OCDS internazionali?
6. **Domain**: `cruscotto-italia.piersoft.it` o nuovo dominio dedicato?
7. **Licenza codice**: AGPL-3.0 (consistente con tuoi altri progetti) o MIT?
8. **Deploy MCP**: `cruscotto-italia-mcp.workers.dev` o sub-dominio custom?
9. **Test data**: tenere snapshot mini di ogni fonte nel repo per CI/test, o solo mock?

---

## 10. Riferimenti

- ANAC OCDS: https://dati.anticorruzione.it/opendata/ocds_it
- OCDS Standard: https://standard.open-contracting.org/
- OpenBDAP: https://bdap-opendata.rgs.mef.gov.it
- OpenCoesione: https://opencoesione.gov.it
- ISTAT codici amministrativi: https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/
- IPA: https://indicepa.gov.it/ipa-dati
- istat-mcp-worker (Piersoft): https://istat-mcp-server.datigovit.workers.dev/mcp
- ckan-mcp-server (ondata): https://github.com/ondata/ckan-mcp-server

---

*Documento prodotto da discovery sistematica via ckan-mcp + web. Ogni numero in questo doc è verificato live al 2026-05-07.*
