# Cruscotto Italia

🇮🇹 Italiano · [🇬🇧 English](README.en.md)

> Piattaforma istituzionale di trasparenza data-driven per i comuni italiani.
> Federa i principali dataset pubblicati dagli enti istituzionali nazionali e
> li ricompone per comune, esponendo un'interfaccia web pubblica e un endpoint
> Model Context Protocol per agenti AI.

[![Deploy Worker](https://github.com/AgID/cruscotto-italia/actions/workflows/deploy-worker.yml/badge.svg)](https://github.com/AgID/cruscotto-italia/actions/workflows/deploy-worker.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

---

## Dati federati

Cercando un comune ("Lecce") si ottiene una vista a 360° su:

- 🏗️ **Contratti pubblici** (ANAC OCDS-IT)
- 🚧 **Opere pubbliche** (BDAP-MOP — MEF/RGS)
- 💰 **Flussi di cassa** (SIOPE — MEF/RGS): pagamenti, incassi e saldo di cassa
- 🇪🇺 **Progetti PNRR** (Italia Domani — Sistema ReGiS)
- 👥 **Demografia comunale** (ISTAT POSAS)
- 🎓 **Profilo socioeconomico** (ISTAT Censimento permanente)
- 🏨 **Turismo** (ISTAT capacità ricettiva + flussi provinciali)
- 🏫 **Scuole** (MIUR — Anagrafe scuole statali)
- 👶 **Dinamica demografica** (ISTAT D7B — bilancio demografico mensile: nati, morti, saldo naturale e migratorio, fonte ANPR)
- 🌫️ **Qualità dell'aria** (ISPRA SNPA — PM10/PM2.5/NO2 per stazione: serie storiche ISPRA, Annuario dei dati ambientali, tabelle preliminari SNPA per gli anni non ancora consolidati)
- 🏞️ **Territorio** (ISPRA — consumo di suolo, IdroGEO frane e alluvioni, rifiuti urbani)
- 🌋 **Classificazione sismica** (Dipartimento Protezione Civile)
- ⛰️ **Morfologia del territorio** (CNR-IRPI — HR-DTM 5 m: quota, pendenza, esposizione, geomorfologia, irraggiamento solare)
- 🚗 **Parco veicoli e incidenti** (ISTAT 41_993 + ACI LOD)
- 💶 **Redditi e fisco** (MEF — Dichiarazioni IRPEF)
- 🏛️ **Patrimonio immobiliare PA** (MEF DE — Beni Immobili Pubblici)
- 🏠 **Civici e strade** (ANNCSU — Agenzia delle Entrate, Open Data HVD)
- 💊 **Sanità territoriale** (Ministero della Salute — farmacie, parafarmacie, posti letto ospedalieri)
- ⚡ **Punti di ricarica veicoli elettrici** (GSE/MASE — Piattaforma Unica Nazionale)
- 📶 **Banda larga** (AGCOM Broadband Map — copertura FTTH/FTTC per comune)
- ⛽ **Distributori carburante e prezzi** (MIMIT — Osservatorio Prezzi Carburanti)
- 🤝 **Enti del Terzo Settore** (Ministero del Lavoro — RUNTS, D.Lgs 117/2017: ODV, APS, EF, IS, SMS, ETS)
- 🏭 **Imprese e addetti** (ISTAT — ASIA UL, serie 2018-2023)
- 🚌 **Pendolarismo** (ISTAT Censimento permanente 2021 — matrice OD origine/destinazione lavoro)
- 🗺️ **Censimento per sezione** (ISTAT Basi Territoriali 2021 + variabili censuarie 2023 — ~756.000 sezioni di censimento con 127 variabili demografiche/abitative per sezione)
- 🏛️ **Beni culturali** (MiC — ICCD ArCo per beni immobili tutelati: chiese, palazzi, castelli, archeologia, ville, monumenti, soprintendenze; Cultural-ON DBUnico 2.0 per Luoghi della Cultura visitabili: musei, biblioteche, archivi con orari e contatti)
- 🗺️ **Cartografia catastale** (Agenzia delle Entrate — Catasto Terreni INSPIRE: particelle e fogli di mappa per 19 regioni italiane, dataset bulk semestrale CC BY 4.0)
- 🏘️ **Quotazioni immobiliari** (Agenzia delle Entrate — OMI: valori di compravendita e locazione in €/m² per zona omogenea sub-comunale, per tipologia, destinazione d'uso e stato di conservazione, con i perimetri geografici delle zone; 24.101 zone quotate in 7.885 comuni, semestrale, CC BY 4.0)
- 🌤️ **Previsioni meteorologiche** (ItaliaMeteo — ICON-2I, griglia 2,2 km: temperatura, precipitazioni, vento, neve, nuvolosità, 73 step orari 0–72h, aggiornamento bi-giornaliero, CC BY 4.0 HVD Meteorologici)

L'elenco completo, con licenze, frequenze di aggiornamento e link diretti
alle fonti istituzionali, è disponibile nella pagina pubblica `about.html`
del sito.

I dati sono inoltre esposti come **catalogo DCAT-AP_IT** (un `dcat:Dataset`
per comune, generato da `etl/sources/dcat_catalog.py`) per l'harvesting
su [dati.gov.it](https://www.dati.gov.it).

Tutto ricomposto sulla **spina dorsale anagrafica ISTAT comuni** (~7.896
comuni) integrata con `IPA` (Indice dei domicili digitali della Pubblica
Amministrazione, AgID).

---

## Architettura

```
                  ┌────────────────────────────────────────┐
                  │  cruscotto-italia.dati.gov.it          │
                  │  (frontend statico HTML/CSS/JS)        │
                  └────────────────┬───────────────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────────────┐
                  │  Cloudflare Worker (MCP server)        │
                  │  cruscotto-italia-mcp.agid.workers.dev │
                  │  - 6 tool MCP + 2 di compatibilità     │
                  │    ChatGPT (search/fetch)              │
                  │  - JSON-RPC 2.0 stateless              │
                  └────────────────┬───────────────────────┘
                                   │ HTTPS pull
                                   ▼
                  ┌────────────────────────────────────────┐
                  │  VM AgID (FastWeb)                     │
                  │  - nginx serve /var/www/.../data/*     │
                  │  - cron /etc/cron.d/cruscotto-etl      │
                  │  - 29 ETL Python (bi-daily/daily/weekly│
                  │    /monthly/annual/semestrale)         │
                  │  - fetch_aci_artifact.py (1 feb/apr/   │
                  │    lug): CSV ACI prodotti su Actions   │
                  └────────────┬───────────────────────────┘
                               │
                ┌──────────────┴──────────────────┐
                │                                 │
                ▼                                 ▼
   ┌─────────────────────────┐         ┌──────────────────────────┐
   │ Fonti istituzionali IT  │         │ GitHub Actions           │
   │ (cron VM, IP italiano)  │         │ ubuntu-latest            │
   │                         │         │ (solo CSV ACI LOD:       │
   │  ANAC · BDAP · SIOPE    │         │  lod.aci.it non e        │
   │  PNRR · MEF · ISPRA     │         │  raggiungibile dalla VM) │
   │  MIUR · ACI · ANNCSU    │         │                          │
   │  Salute · MIMIT · GSE   │         │  Output: artifact tar.gz │
   │  AGCOM · Lavoro · MiC   │         │  (retention 3 giorni),   │
   │  AdE Catasto INSPIRE    │         │  scaricato dalla VM con  │
   │  DPC · CNR-IRPI · Meteo │         │  fetch_aci_artifact.py   │
   └─────────────────────────┘         └──────────────────────────┘
```

Dettagli architetturali completi: [`DESIGN.md`](DESIGN.md) ·
[`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) · [`docs/SECURITY.md`](docs/SECURITY.md).

### Cadenze ETL

Orari in **ora italiana** (il cron della VM usa il fuso Europe/Rome), tranne il workflow ACI su GitHub Actions (UTC).

| Cadenza | Fonti | Esecuzione | Trigger |
|---|---|---|---|
| **Giornaliera** (07:45, 08:00, 10:30 e 15:30) | sanità MdS (farmacie, parafarmacie, posti letto), PUN punti ricarica, MIMIT carburanti (con ripasso pomeridiano) | cron VM AgID | automatico |
| **Giornaliera** (05:00, 11:00, 16:00, 22:00) | ItaliaMeteo ICON-2I previsioni meteo | cron VM AgID | automatico |
| **Giornaliera** (11:00 e 16:00) | dashboard rebuild (aggregato di tutte le sezioni) | cron VM AgID | automatico |
| **Giornaliera** (09:30) | freshness check: esecuzione degli ETL e periodo del dato per fonte | cron VM AgID | automatico |
| **Settimanale** (lunedì 04:00-06:00) | ANAC OCDS (affidamenti degli ultimi 12 file mensili pubblicati, deduplicati), PNRR, RUNTS, SIOPE, dashboard | cron VM AgID | automatico |
| **Mensile** (giorno 5, 04:00-08:40) | anagrafica, BDAP-MOP, SIOPE, ANNCSU, AGCOM banda larga, Cultural-ON, beni culturali, dashboard | cron VM AgID | automatico |
| **Annuale** (1 feb / 1 apr / 1 lug, 04:00-14:00) | demografia POSAS e bilancio demografico, profilo Censimento, aria, ASIA, turismo, classificazione sismica, territorio, veicoli (CSV ACI da GitHub Actions), redditi IRPEF, immobili PA, dashboard | cron VM AgID | automatico |
| **Annuale** (5 settembre, 04:00) | scuole MIUR (anno scolastico appena iniziato) | cron VM AgID | automatico |
| **Semestrale** (controllo il 1° di ogni mese, 03:00) | cartografia catastale AGE (particelle + fogli, 19 regioni): scarica solo se le dimensioni dei file AGE sono cambiate | cron VM AgID | automatico |
| **Semestrale** (sentinella giornaliera 03:20) | quotazioni OMI AGE (zone + perimetri): `omi_semestrale.sh` interroga ogni giorno l'elenco dei semestri pubblicati e avvia la raccolta solo quando ne compare uno nuovo (l'Agenzia pubblica entro il 15 marzo e il 15 ottobre, senza data fissa) | cron VM AgID | automatico |
| **Decennale** (manuale, prossimo 2031) | censimento Basi Territoriali (sezioni + 119 vars) | run manuale `python -m etl.sources.censimento` su VM | `workflow_dispatch` |
| **ACI su Actions** (1 feb / 1 apr / 1 lug, 04:00 UTC) | CSV prime iscrizioni ACI LOD, scaricati poi dalla VM con `fetch_aci_artifact.py` | GitHub Actions `ubuntu-latest` | `schedule` + `workflow_dispatch` |
| **Riserva manuale** | istat_profilo, asia, pendolarismo | GitHub Actions `ubuntu-latest` | `workflow_dispatch` |

### Perché 2 esecutori distinti

Le **fonti istituzionali italiane** sono protette da WAF (F5 Volterra,
Akamai, ecc.) che bloccano con HTTP 403 le richieste da IP cloud (Azure
GitHub Actions, AWS, GCP). Vengono quindi interrogate solo dalla VM AgID
(IP italiano).

L'unica eccezione e **ACI LOD** (`lod.aci.it`), non raggiungibile dalla
VM: il workflow `etl-aci-refresh.yml` scarica i CSV su GitHub Actions e la
VM li recupera con
[`scripts/etl/fetch_aci_artifact.py`](scripts/etl/fetch_aci_artifact.py)
prima dell'ETL veicoli.

**ISTAT esploradati** limita le richieste troppo grandi o troppo frequenti
(in passato ha bloccato IP per uso intensivo; oltre 35 codici comune per
richiesta risponde 400). Tutti gli ETL ISTAT girano quindi dalla VM e
scaricano a blocchi da 35 comuni, in sequenza e con pausa
([`etl/lib/istat_sdmx.py`](etl/lib/istat_sdmx.py)). I workflow ISTAT su
Actions (profilo, ASIA, pendolarismo) restano solo come riserva manuale:
nessun cron della VM ne scarica gli artifact.

I workflow weekly/monthly/annual presenti in `.github/workflows/` sono
quindi **smoke test documentali**: il revisore può aprirli dalla UI
Actions per leggere i comandi Python eseguiti dal cron VM, ma su
ubuntu-latest essi falliscono con 403 (WAF). Il produttore reale dei
dati è il cron `/etc/cron.d/cruscotto-etl` sulla VM AgID, mai i workflow.

---

## API MCP per agenti AI

Cruscotto Italia espone un server [Model Context Protocol](https://modelcontextprotocol.io)
per consentire l'interrogazione dei dati civici da chatbot AI compatibili
(Claude, ChatGPT con wrapper, OpenWebUI, agenti custom).

**Endpoint pubblico** (Worker AgID): `https://cruscotto-italia-mcp.agid.workers.dev/mcp`

**Tool esposti** (6 semantici + 2 di compatibilità):

- `mcp_info` — metadata del servizio, elenco fonti integrate, licenze
- `search_comune` — ricerca per nome → codice ISTAT (gestione omonimi)
- `comune_kpi` — KPI sintetici di un comune (~620 token, primo tool da
  chiamare per query puntuali e confronti)
- `comune_dashboard` — vista unificata: una sola chiamata restituisce le
  sezioni del comune (anagrafica, demografia, contratti, opere
  pubbliche BDAP-MOP con dettaglio progetti CUP, ANNCSU, sanità,
  banda larga, beni culturali, ecc.)
- `anncsu_civico_search` — query puntuali sui numeri civici certificati
  con filtri server-side (odonimo, civico)
- `censimento_sezione_search` — ranking o lookup sulle 127 variabili
  censuarie raw del Censimento Permanente 2023 a livello di singola
  sezione di censimento sub-comunale (modalità lookup con `sez_id` o
  ranking con `var_name` ± `denominator_var` per percentuali)
- `search` e `fetch` — schema fisso richiesto dal connettore MCP
  personalizzato di ChatGPT; wrappano `search_comune` e `comune_dashboard`

Il server implementa la spec MCP 2025-11-25 (JSON-RPC 2.0 stateless,
Streamable HTTP): ogni tool dichiara `outputSchema`, restituisce
`structuredContent` ed è annotato read-only. `mcp_info` e `/health`
espongono la versione del Worker e il tree hash git di `worker/`
(`BUILD_TREE`), verificabile sul repo con `git rev-parse <commit>:worker`.

La **cartografia catastale** (particelle e fogli AGE) è invece esposta
come REST sul percorso `/data/catasto_full/<istat>_map.geojson.gz` e
`/data/catasto_full/<istat>_ple.geojson.gz` (o split per foglio nei
comuni grandi). Per pattern d'uso e esempi vedi la skill MCP Claude
(sezione catasto) e il README dello ZIP `/data/<istat>.zip`.

**Rate limit**: 60 richieste/minuto per IP.

### Configurazione su Claude.ai

1. Settings → Connettori → Aggiungi connettore personalizzato
2. URL: `https://cruscotto-italia-mcp.agid.workers.dev/mcp`
3. Autenticazione: nessuna

### Skill Claude (opzionale)

È disponibile una skill Claude che documenta l'uso del connettore
(inventario dei 6 tool, schema di `comune_dashboard`, pattern operativi
e caveat per sezione, accesso REST alla cartografia catastale). Versione
corrente: `https://cruscotto-italia.dati.gov.it/data/skills/cruscotto-italia-workflow-v2.12.0.zip`.
È disponibile anche `cruscotto-cli-v0.2.0.zip`, una skill eseguibile che
interroga gli shard JSON statici senza passare dal server MCP (elenco
completo con storici in `docs/skills/README.md`).

### Esempi di domande supportate

- "Dimmi di Lecce" — overview completa
- "Quanti progetti PNRR ha Bergamo?" — focus PNRR
- "Confronto demografico tra Milano e Roma" — orchestrazione cross-comune
- "Quante farmacie attive ci sono a Matera?" — sanità territoriale
- "Quanti punti di ricarica EV attivi ci sono a Torino e quale percentuale è HPC/Ultra fast?"
- "Quanti civici certificati ANNCSU ci sono in via Roma a Lecce?"
- "Qual è la copertura FTTH a Bergamo? Confronto con Brescia."
- "Quanto costa il gasolio self a Lecce rispetto alla media nazionale?"
- "Quanti enti del Terzo Settore (ODV/APS) ha Matera? Quanti iscritti al 5x1000?"
- "Quanti pendolari escono ogni giorno da Bergamo verso Milano?"
- "Quante chiese tutelate ICCD ArCo ci sono a Lecce?"

### Limiti noti

- Tool ottimizzati per query **per-comune**, non per aggregati cross-comune
  (es. "top 10 PNRR per regione" richiede N chiamate).
- Il MCP è in sola lettura: nessun side-effect, nessuna scrittura.
- La cartografia catastale (particelle/fogli) NON è esposta via MCP ma
  via REST diretta: i tool MCP non leggono le geometrie catastali, il
  frontend e gli agenti la consumano direttamente dal percorso
  `/data/catasto_full/`.

---

## Sviluppo locale

### Prerequisiti

- Node.js ≥ 22 (richiesto da vitest 5 e dalla CI del Worker)
- Python ≥ 3.12
- `wrangler` CLI (`npm i -g wrangler`) per il Worker

### Setup

```bash
git clone https://github.com/AgID/cruscotto-italia.git
cd cruscotto-italia

# Worker (Cloudflare)
cd worker
npm install
npm run dev   # http://localhost:8787

# Frontend (statico)
cd ../frontend
python3 -m http.server 8000   # http://localhost:8000

# ETL Python — esempio: scarica i punti di ricarica PUN in /tmp/test/
cd ..
pip install -r etl/requirements.txt
DATA_DIR=/tmp/test python -m etl.sources.pun --outdir=/tmp/test/pun
```

Tutti gli ETL scrivono su filesystem locale (`--target=local`, ora unico
target supportato). L'output va in `DATA_DIR/<source>/<istat>.json`
con `DATA_DIR` env override (default `/var/www/cruscotto-italia/data/`).

### Deploy

Frontend e dati ETL girano sulla VM AgID via cron — non c'è "deploy
frontend" in senso CI/CD: il sito è un git pull sulla VM.

Il Worker MCP si deploya su Cloudflare AgID con:

```bash
cd worker
npm run typecheck && npm run deploy
```

Richiede `CLOUDFLARE_API_TOKEN` per l'account AgID nel profilo shell.

---

## Struttura del repo

```
cruscotto-italia/
├── DESIGN.md                 ← documento architetturale completo
├── DECISIONS.md              ← decisioni prese sui punti aperti
├── README.md                 ← questo file
├── LICENSE                   ← AGPL-3.0
│
├── worker/                   ← Cloudflare Worker (TypeScript)
│   ├── src/
│   │   ├── index.ts
│   │   ├── mcp.ts            ← JSON-RPC MCP transport
│   │   ├── http.ts           ← landing page + endpoint /data/anncsu_full/ + /data/catasto_full/
│   │   ├── tools/            ← un file per tool MCP
│   │   └── lib/              ← duckdb, ratelimit, data_fetch helpers
│   ├── wrangler.toml
│   └── package.json
│
├── frontend/                 ← single-file HTML (vanilla JS)
│   ├── index.html
│   ├── comune.html           ← vista comune-centric, 21 tab
│   ├── about.html            ← elenco fonti + metodologia + indice "Cosa cercare e dove"
│   ├── indice_sinonimi.json  ← sinonimi per l'indice dei contenuti
│   └── vendor/               ← Chart.js, Leaflet, JSZip, pako (SHA-384)
│
├── etl/                      ← Python ETL pipeline
│   ├── requirements.txt
│   ├── pyproject.toml        ← ruff + mypy + pytest config
│   ├── sources/              ← un modulo per fonte (tutti eseguiti dal cron della VM)
│   │   ├── anagrafica.py        ← spina dorsale ISTAT comuni + IPA
│   │   ├── anac.py              ← contratti pubblici (OCDS)
│   │   ├── bdap.py              ← BDAP-MOP opere pubbliche
│   │   ├── siope.py             ← SIOPE uscite+entrate multi-anno (siope.it)
│   │   ├── pnrr_progetti.py     ← progetti PNRR (Italia Domani/ReGiS)
│   │   ├── demografia.py        ← popolazione (POSAS)
│   │   ├── demografia_flussi.py ← dinamica demografica ISTAT D7B (nati/morti)
│   │   ├── istat_profilo.py     ← Censimento permanente *via Actions*
│   │   ├── istat_turismo.py     ← capacità + flussi turistici
│   │   ├── territorio.py        ← ISPRA Suolo, IdroGEO, Rifiuti
│   │   ├── aria.py              ← ISPRA SNPA qualità aria (serie storiche + Annuario)
│   │   ├── classificazione_sismica.py ← DPC classificazione sismica
│   │   ├── build_meteo.py       ← ItaliaMeteo ICON-2I previsioni
│   │   ├── scuole.py            ← MIUR anagrafe scuole statali
│   │   ├── veicoli.py           ← ISTAT + ACI LOD
│   │   ├── redditi.py           ← MEF Federalismo Fiscale (IRPEF)
│   │   ├── immobili_pa.py       ← MEF DE Beni Immobili Pubblici (ultima rilevazione)
│   │   ├── anncsu.py            ← ANNCSU civici (Agenzia Entrate + ISTAT)
│   │   ├── sanita_mds.py        ← Min. Salute (farmacie, ospedali)
│   │   ├── pun.py               ← GSE/MASE punti ricarica EV
│   │   ├── agcom_bbmap.py       ← AGCOM Broadband Map
│   │   ├── carburanti.py        ← MIMIT Osservatorio Prezzi
│   │   ├── runts.py             ← Min. Lavoro RUNTS Terzo Settore
│   │   ├── asia.py              ← ISTAT ASIA UL imprese *via Actions*
│   │   ├── pendolarismo.py      ← ISTAT matrice OD *via Actions*
│   │   ├── censimento.py        ← ISTAT Basi Territoriali 2021 (119 vars/sezione)
│   │   ├── beni_culturali.py    ← MiC ICCD ArCo (beni immobili tutelati)
│   │   ├── cultural_on.py       ← MiC Cultural-ON DBUnico 2.0 (Luoghi della Cultura)
│   │   ├── catasto_age.py       ← AdE Catasto Terreni INSPIRE (particelle + fogli)
│   │   ├── omi.py               ← AdE OMI quotazioni immobiliari per zona
│   │   ├── dcat_catalog.py      ← catalogo DCAT-AP_IT per harvesting dati.gov.it
│   │   └── dashboard.py         ← aggregator unified shard (A1)
│   └── lib/
│       ├── local_lookup.py   ← utility lookup local-first
│       ├── istat_sdmx.py     ← download SDMX ISTAT a blocchi da 35 comuni
│       ├── r2.py             ← kill-switch (R2 dismesso, sempre RuntimeError)
│       ├── duck.py
│       └── manifest.py
│
├── scripts/
│   ├── genera_indice.py      ← genera l'indice "Cosa cercare e dove" di about.html
│   └── etl/
│       ├── fetch_aci_artifact.py ← scarica i CSV ACI prodotti su GitHub Actions
│       └── pull_artifact.py  ← riserva: artifact dei workflow ISTAT manuali (non in cron)
│
├── .github/workflows/
│   ├── etl-daily.yml             ← smoke test daily (PUN + Carburanti)
│   ├── etl-weekly.yml            ← smoke test weekly (atteso fail su Azure WAF)
│   ├── etl-monthly.yml           ← smoke test monthly (atteso fail su Azure WAF)
│   ├── etl-annual.yml            ← smoke test annual (atteso fail su Azure WAF)
│   ├── etl-istat_profilo-refresh.yml ← producer ISTAT profilo, output artifact
│   ├── etl-asia-refresh.yml          ← producer ISTAT ASIA, output artifact
│   ├── etl-pendolarismo-refresh.yml  ← producer ISTAT pendolarismo
│   ├── etl-aci-refresh.yml           ← scarica i CSV ACI LOD (ETL veicoli resta sulla VM)
│   ├── deploy-worker.yml         ← deploy Cloudflare Worker su push main
│   ├── deploy-frontend.yml       ← sync frontend (legacy, in dismissione)
│   └── ci.yml                    ← CI lint & test (ruff, mypy, pytest, tsc)
│
├── docs/                     ← documentazione tecnica
├── scripts/                  ← utility (smoke-test-etl, pa11y-*, analytics)
└── tests/                    ← unit tests Python (etl) e Vitest (worker)
```

---

## Verifica freschezza dati

Il `comune_dashboard` di ogni comune contiene un campo top-level
`_generated_at` (timestamp ISO-8601 UTC) che indica quando la VM AgID
ha eseguito l'ultimo rebuild dell'aggregato A1. Esempio di verifica:

```bash
curl -s -X POST "https://cruscotto-italia-mcp.agid.workers.dev/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,
       "params":{"name":"comune_dashboard","arguments":{"istat_code":"075035"}}}' \
  | jq '.result.content[0].text | fromjson | {_generated_at, _missing}'
```

In condizioni operative normali `_generated_at` è recente (≤ 24h) e
`_missing` è vuoto o contiene solo source dove il comune non ha dati
upstream (es. comuni piccolissimi senza colonnine di ricarica o
distributori carburanti).

Attenzione: `_generated_at` dice quando è stato ricostruito l'aggregato,
non a quale periodo si riferiscono i dati. Le sezioni con un periodo di
riferimento lo espongono in `_data_period` (es. `agcom_bbmap`, `omi`):
è quello da guardare per sapere se una fonte è aggiornata.

Lo stesso principio guida il controllo automatico. Ogni mattina
`scripts/etl/freshness_check.py` verifica, oltre all'esecuzione degli ETL,
il **periodo del dato** delle fonti non giornaliere (`CONTROLLI_CONTENUTO`:
età della data per le mensili, anno minimo atteso per le annuali, finestra
di mesi per ANAC) e invia un allarme se resta indietro rispetto al
calendario di pubblicazione della fonte. Dall'audit del 05/10/2026 gli ETL
seguono queste regole: anni ed edizioni delle fonti risolti a runtime e mai
scritti nel codice, cache con scadenza (o con il periodo nel nome),
download su file temporaneo con rinomina a fine scaricamento, richieste
ISTAT a blocchi da 35 comuni in sequenza.

---

## Licenza

Il **codice** è rilasciato sotto **AGPL-3.0** — vedi [LICENSE](LICENSE).
Le derivate devono restare aperte.

I **dati** delle fonti sono pubblicati sotto le rispettive licenze:

- **CC BY 4.0** — la maggior parte delle fonti (ANAC, ISTAT moderni,
  MIUR, ACI, ISPRA, MEF DE Patrimonio, MEF-RGS SIOPE, Italia Domani PNRR,
  MiC ArCo, MiC Cultural-ON, AdE Catasto INSPIRE)
- **CC BY 3.0 IT** — MEF Federalismo Fiscale, alcuni dataset ISTAT storici
- **IODL 2.0** — BDAP-MOP, Ministero della Salute, MIMIT
- **CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)** — dati delle PA
  pubblicati senza licenza esplicita ("open by default"). Si applica per
  esempio a GSE/MASE (PUN punti di ricarica EV), AGCOM (Broadband Map
  FTTH/FTTC ex art. 22 Codice Comunicazioni Elettroniche) e Ministero
  del Lavoro (RUNTS anagrafica enti del Terzo Settore ex D.Lgs 117/2017
  art. 53 pubblicità legale), in coerenza con le Linee Guida Open Data
  AgID (Determinazione 183/2023).
- **Open Data ai sensi del Regolamento UE 2023/138 (HVD)** — ANNCSU
  (Agenzia delle Entrate + ISTAT)

Dettaglio per dataset in [`docs/data-licenses.md`](docs/data-licenses.md)
e nella pagina pubblica `about.html` con link diretti alle fonti.

---

## Conformità

- **Accessibilità WCAG 2.1 AA**: criteri verificati con Pa11y +
  Axe-Core su tutte le pagine pubbliche, inclusa la cartografia
  catastale (script `scripts/pa11y-catasto.sh`). Dichiarazione di
  accessibilità: <https://form.agid.gov.it/agid/Cruscotto_Italia/dichiarazione>.
- **Sicurezza**: HTTPS forzato, HSTS preload-ready, CSP restrictive,
  security headers completi (X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy), `server_tokens off` su nginx,
  rate limiting sul Worker MCP.
- **Privacy**: nessun analytics di terzi, nessun cookie di profilazione,
  solo cookie tecnici nginx. Gli artifact GitHub Actions contengono solo
  CSV pubblici ACI (retention 3 giorni).

---

## Contribuire

Issue e PR benvenuti. Per discussioni di design aprire una Discussion.
Pattern di commit: [Conventional Commits](https://www.conventionalcommits.org/).

---

## Crediti

Progettato e sviluppato da **Francesco Piero Paolicelli (Piersoft)**,
[@piersoft](https://github.com/piersoft) per AgID - Agenzia per l'Italia Digitale.

