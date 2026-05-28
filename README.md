# Cruscotto Italia

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
- 💰 **Flussi di cassa** (SIOPE — MEF/RGS)
- 🇪🇺 **Progetti PNRR** (Italia Domani — Sistema ReGiS)
- 👥 **Demografia comunale** (ISTAT POSAS)
- 🎓 **Profilo socioeconomico** (ISTAT Censimento permanente)
- 🏨 **Turismo** (ISTAT capacità ricettiva + flussi provinciali)
- 🏫 **Scuole** (MIUR — Anagrafe scuole statali)
- 🌫️ **Qualità dell'aria** (ISPRA SNPA — PM10/PM2.5/NO2)
- 🚗 **Parco veicoli e incidenti** (ISTAT 41_993 + ACI LOD)
- 💶 **Redditi e fisco** (MEF — Dichiarazioni IRPEF)
- 🏛️ **Patrimonio immobiliare PA** (MEF DE — Beni Immobili Pubblici)
- 🏠 **Civici e strade** (ANNCSU — Agenzia delle Entrate, Open Data HVD)
- 💊 **Sanità territoriale** (Ministero della Salute — farmacie, parafarmacie, posti letto ospedalieri)
- ⚡ **Punti di ricarica veicoli elettrici** (GSE/MASE — Piattaforma Unica Nazionale)
- ⛽ **Distributori carburante e prezzi** (MIMIT — Osservatorio Prezzi Carburanti)
- 🤝 **Enti del Terzo Settore** (Ministero del Lavoro — RUNTS, D.Lgs 117/2017: ODV, APS, EF, IS, SMS, ETS)
- 🏭 **Imprese e addetti** (ISTAT — ASIA UL, serie 2018-2023)
- 🚌 **Pendolarismo** (ISTAT Censimento permanente 2021 — matrice OD origine/destinazione lavoro)
- 🗺️ **Censimento sezioni 2021** (ISTAT Basi Territoriali — 756.376 sezioni di censimento con 119 variabili demografiche/abitative per sezione)
- 🏛️ **Beni culturali** (MiC — ICCD ArCo per beni immobili tutelati: chiese, palazzi, castelli, archeologia, ville, monumenti, soprintendenze; Cultural-ON DBUnico 2.0 per Luoghi della Cultura visitabili: musei, biblioteche, archivi con orari e contatti)
- 🗺️ **Cartografia catastale** (Agenzia delle Entrate — Catasto Terreni INSPIRE: particelle e fogli di mappa per 19 regioni italiane, dataset bulk semestrale CC BY 4.0)

L'elenco completo, con licenze, frequenze di aggiornamento e link diretti
alle fonti istituzionali, è disponibile nella pagina pubblica `about.html`
del sito.

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
                  │  cruscotto-italia-mcp.dati.gov.it      │
                  │  - 6 tool MCP (search_comune,          │
                  │    comune_dashboard, ecc.)             │
                  │  - JSON-RPC 2.0 stateless              │
                  └────────────────┬───────────────────────┘
                                   │ HTTPS pull
                                   ▼
                  ┌────────────────────────────────────────┐
                  │  VM AgID (FastWeb)                     │
                  │  - nginx serve /var/www/.../data/*     │
                  │  - cron /etc/cron.d/cruscotto-etl      │
                  │  - 19 ETL Python (daily/weekly/monthly │
                  │    /annual/semestrale)                 │
                  │  - pull_artifact.py (daily 07:30 UTC)  │
                  │    scarica i 3 ETL ISTAT da Actions    │
                  └────────────┬───────────────────────────┘
                               │
                ┌──────────────┴──────────────────┐
                │                                 │
                ▼                                 ▼
   ┌─────────────────────────┐         ┌──────────────────────────┐
   │ Fonti istituzionali IT  │         │ GitHub Actions           │
   │ (cron VM, IP italiano)  │         │ ubuntu-latest            │
   │                         │         │ (per 3 ETL ISTAT bloc-   │
   │  ANAC · BDAP · SIOPE    │         │  cati da IP italiani:    │
   │  PNRR · MEF · ISPRA     │         │  istat_profilo · ASIA    │
   │  MIUR · ACI · ANNCSU    │         │  · pendolarismo)         │
   │  Salute · MIMIT · GSE   │         │                          │
   │  AGCOM · Lavoro · MiC   │         │  Output: artifact ZIP    │
   │  AdE Catasto INSPIRE    │         │  scaricato dalla VM via  │
   │                         │         │  GitHub API + pull_artifact.py │
   └─────────────────────────┘         └──────────────────────────┘
```

Dettagli architetturali completi: [`DESIGN.md`](DESIGN.md) ·
[`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) · [`docs/SECURITY.md`](docs/SECURITY.md).

### Cadenze ETL

| Cadenza | Fonti | Esecuzione | Trigger |
|---|---|---|---|
| **Daily** (08:00 UTC) | PUN punti ricarica, MIMIT carburanti, dashboard rebuild | cron VM AgID | automatico |
| **Daily Pull-Artifact** (07:30 UTC) | scarica artifact dei 3 ETL ISTAT da GitHub Actions | cron VM AgID | automatico |
| **Weekly** (lunedì 04:00 UTC) | ANAC OCDS, PNRR, sanità MdS, RUNTS, dashboard | cron VM AgID | automatico |
| **Monthly** (5° del mese 04:00 UTC) | anagrafica, BDAP-MOP, SIOPE, ANNCSU, AGCOM banda larga, beni culturali (ArCo + Cultural-ON) | cron VM AgID | automatico |
| **Annual** (1 feb / 1 apr / 1 lug, 04:00 UTC) | demografia POSAS, profilo Censimento, turismo, territorio, scuole, veicoli, redditi IRPEF, immobili PA | cron VM AgID | automatico |
| **Semestrale** (1 marzo / 1 settembre, 03:00 UTC) | cartografia catastale AGE (particelle + fogli, 19 regioni) | cron VM AgID | automatico |
| **Decennale** (manuale, prossimo 2031) | censimento Basi Territoriali (sezioni + 119 vars) | run manuale `python -m etl.sources.censimento` su VM | `workflow_dispatch` |
| **ISTAT refresh** (manuale) | istat_profilo, asia, pendolarismo | GitHub Actions `ubuntu-latest` | `workflow_dispatch` |

### Perché 2 esecutori distinti

Le **fonti istituzionali italiane** sono protette da WAF (F5 Volterra,
Akamai, ecc.) che bloccano con HTTP 403 le richieste da IP cloud (Azure
GitHub Actions, AWS, GCP). Vengono quindi interrogate solo dalla VM AgID
(IP italiano).

**ISTAT esploradati**, viceversa, ha imposto un host-based ban su alcuni IP
italiani per precedente uso intensivo. I 3 ETL ISTAT pesanti
(profilo Censimento, ASIA UL, matrice pendolarismo) vengono quindi
eseguiti da GitHub Actions (IP Azure non bannato) e i risultati vengono
trasferiti alla VM via artifact GitHub + script
[`scripts/etl/pull_artifact.py`](scripts/etl/pull_artifact.py) (retention
1 giorno, cleanup attivo per privacy).

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

**Tool esposti** (6):

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
- `censimento_sezione_search` — ranking o lookup sulle 119 variabili
  censuarie raw del Censimento Permanente 2021 a livello di singola
  sezione di censimento sub-comunale (modalità lookup con `sez_id` o
  ranking con `var_name` ± `denominator_var` per percentuali)

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
corrente: `https://cruscotto-italia-mcp.dati.gov.it/skills/cruscotto-italia-workflow-v2.4.0.zip`
(elenco completo con storici in `docs/skills/README.md`).

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

- Node.js ≥ 20
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
│   ├── comune.html           ← vista comune-centric, 22 tab
│   ├── about.html            ← elenco fonti + metodologia
│   └── vendor/               ← Chart.js, Leaflet, JSZip, pako (SHA-384)
│
├── etl/                      ← Python ETL pipeline
│   ├── requirements.txt
│   ├── pyproject.toml        ← ruff + mypy + pytest config
│   ├── sources/              ← un modulo per fonte (19 ETL VM + 3 ETL ISTAT su Actions)
│   │   ├── anagrafica.py        ← spina dorsale ISTAT comuni + IPA
│   │   ├── anac.py              ← contratti pubblici (OCDS)
│   │   ├── bdap.py              ← BDAP-MOP opere pubbliche
│   │   ├── siope.py             ← SIOPE Spese multi-anno
│   │   ├── pnrr_progetti.py     ← progetti PNRR (Italia Domani/ReGiS)
│   │   ├── demografia.py        ← popolazione (POSAS)
│   │   ├── istat_profilo.py     ← Censimento permanente *via Actions*
│   │   ├── istat_turismo.py     ← capacità + flussi turistici
│   │   ├── territorio.py        ← ISPRA Suolo, IdroGEO, Rifiuti
│   │   ├── aria.py              ← ISPRA SNPA qualità aria
│   │   ├── scuole.py            ← MIUR anagrafe scuole statali
│   │   ├── veicoli.py           ← ISTAT + ACI LOD
│   │   ├── redditi.py           ← MEF Federalismo Fiscale (IRPEF)
│   │   ├── immobili_pa.py       ← MEF DE Beni Immobili Pubblici 2022
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
│   │   └── dashboard.py         ← aggregator unified shard (A1)
│   └── lib/
│       ├── local_lookup.py   ← utility lookup local-first
│       ├── r2.py             ← kill-switch (R2 dismesso, sempre RuntimeError)
│       ├── duck.py
│       └── manifest.py
│
├── scripts/
│   └── etl/
│       └── pull_artifact.py  ← scarica artifact GitHub dei 3 ETL ISTAT
│
├── .github/workflows/
│   ├── etl-daily.yml             ← smoke test daily (PUN + Carburanti)
│   ├── etl-weekly.yml            ← smoke test weekly (atteso fail su Azure WAF)
│   ├── etl-monthly.yml           ← smoke test monthly (atteso fail su Azure WAF)
│   ├── etl-annual.yml            ← smoke test annual (atteso fail su Azure WAF)
│   ├── etl-istat_profilo-refresh.yml ← producer ISTAT profilo, output artifact
│   ├── etl-asia-refresh.yml          ← producer ISTAT ASIA, output artifact
│   ├── etl-pendolarismo-refresh.yml  ← producer ISTAT pendolarismo
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
curl -s -X POST "https://cruscotto-italia-mcp.dati.gov.it/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,
       "params":{"name":"comune_dashboard","arguments":{"istat_code":"075035"}}}' \
  | jq '.result.content[0].text | fromjson | {_generated_at, _missing}'
```

In condizioni operative normali `_generated_at` è recente (≤ 24h) e
`_missing` è vuoto o contiene solo source dove il comune non ha dati
upstream (es. comuni piccolissimi senza colonnine di ricarica o
distributori carburanti).

---

## Licenza

Il **codice** è rilasciato sotto **AGPL-3.0** — vedi [LICENSE](LICENSE).
Le derivate devono restare aperte.

I **dati** delle fonti sono pubblicati sotto le rispettive licenze:

- **CC BY 4.0** — la maggior parte delle fonti (ANAC, ISTAT moderni,
  MIUR, ACI, ISPRA, MEF DE Patrimonio, Italia Domani PNRR, MiC ArCo,
  MiC Cultural-ON, AdE Catasto INSPIRE)
- **CC BY 3.0 IT** — MEF Federalismo Fiscale, alcuni dataset ISTAT storici
- **IODL 2.0** — BDAP-MOP, BDAP-SIOPE, Ministero della Salute, MIMIT
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
  accessibilità pubblicata in `accessibilita.html`.
- **Sicurezza**: HTTPS forzato, HSTS preload-ready, CSP restrictive,
  security headers completi (X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy), `server_tokens off` su nginx,
  rate limiting sul Worker MCP.
- **Privacy**: nessun analytics di terzi, nessun cookie di profilazione,
  solo cookie tecnici nginx. Artifact GitHub Actions con retention 1
  giorno e cleanup attivo dopo il pull lato VM (vedi
  `scripts/etl/pull_artifact.py`).

---

## Contribuire

Issue e PR benvenuti. Per discussioni di design aprire una Discussion.
Pattern di commit: [Conventional Commits](https://www.conventionalcommits.org/).

---

## Crediti

Progettato e sviluppato da **Francesco Piero Paolicelli (Piersoft)**,
[@piersoft](https://github.com/piersoft) per AgID - Agenzia per l'Italia Digitale.

