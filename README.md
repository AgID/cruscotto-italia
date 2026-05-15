# Cruscotto Italia

> La carta d'identità data-driven dei comuni italiani. I dataset pubblici dei principali enti istituzionali, federati e ricomposti per comune.

[![Deploy Worker](https://github.com/piersoft/cruscotto-italia/actions/workflows/deploy-worker.yml/badge.svg)](https://github.com/piersoft/cruscotto-italia/actions/workflows/deploy-worker.yml)
[![ETL Daily](https://github.com/piersoft/cruscotto-italia/actions/workflows/etl-daily.yml/badge.svg)](https://github.com/piersoft/cruscotto-italia/actions/workflows/etl-daily.yml)
[![ETL Monthly](https://github.com/piersoft/cruscotto-italia/actions/workflows/etl-monthly.yml/badge.svg)](https://github.com/piersoft/cruscotto-italia/actions/workflows/etl-monthly.yml)
[![CI lint & test](https://github.com/piersoft/cruscotto-italia/actions/workflows/ci.yml/badge.svg)](https://github.com/piersoft/cruscotto-italia/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Cerchi un comune ("Lecce") e ottieni una vista a 360° su:

- 🏗️ **Contratti pubblici** (ANAC OCDS-IT)
- 🚧 **Opere pubbliche** (BDAP-MOP)
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
- 💊 **Sanità territoriale** (Ministero Salute — farmacie, parafarmacie, posti letto ospedalieri)
- ⚡ **Punti di ricarica veicoli elettrici** (GSE/MASE — Piattaforma Unica Nazionale)
- ⛽ **Distributori carburante e prezzi** (MIMIT — Osservatorio Prezzi Carburanti)
- 🤝 **Enti del Terzo Settore** (Ministero del Lavoro — RUNTS, D.Lgs 117/2017: ODV, APS, EF, IS, SMS, ETS)

L'elenco completo, con licenze, frequenze di aggiornamento e link diretti alle fonti, è in [`about.html`](https://cruscotto-italia.piersoftckan.biz/about.html).

Tutto ricomposto sulla **spina dorsale anagrafica ISTAT comuni** (~7.896 comuni).

## Architettura

```
Frontend (HTML statico) → Worker (Cloudflare) → R2 (JSON shard per comune)
                              ↑
              ETL Python (GitHub Actions, cadenze multiple)
                              ↑
  19 fonti istituzionali · 15 enti emittenti:
   ANAC · BDAP-MOP · SIOPE · Italia Domani (PNRR)
   ISTAT (POSAS, Censimento, Turismo, Veicoli, Incidenti)
   MIUR · ISPRA (Suolo/IdroGEO/Rifiuti, SNPA aria) · ACI LOD
   MEF (Federalismo Fiscale IRPEF, Patrimonio Immobiliare PA)
   Agenzia delle Entrate (ANNCSU) · Ministero della Salute
   GSE/MASE (Piattaforma Unica Nazionale punti di ricarica)
   AGCOM (Broadband Map: copertura banda larga FTTH/FTTC)
   MIMIT (Osservatorio Prezzi Carburanti — distributori + prezzi)
   Ministero del Lavoro (RUNTS — Registro Unico Nazionale Terzo Settore)
```

Tutti i dettagli architetturali sono in [`DESIGN.md`](DESIGN.md).

## Status

🚧 In sviluppo. Vedi [DESIGN.md § 7 Roadmap](DESIGN.md#7-roadmap--milestones).

## Uso come MCP per chatbot AI

Cruscotto Italia espone un server [Model Context Protocol](https://modelcontextprotocol.io) che consente di interrogare i dati civici tramite chatbot AI compatibili (Claude, ChatGPT con wrapper, OpenWebUI, agenti custom).

**Endpoint pubblico**: `https://cruscotto-italia-mcp.piersoftckan.biz/mcp`

**Tool esposti** (5): `mcp_info`, `search_comune`, `comune_dashboard`, `comune_opere_dettaglio`, `anncsu_civico_search`.

`comune_dashboard` è il tool principale: una sola chiamata restituisce 22 sezioni con tutti i dati del comune. `comune_opere_dettaglio` fornisce la lista dei singoli progetti BDAP filtrati al 2025. `anncsu_civico_search` consente query puntuali sui numeri civici ANNCSU con filtri server-side (odonimo, civico).

**Rate limit**: 60 richieste/minuto per IP.

### Configurazione su Claude.ai (Free/Pro/Max/Team/Enterprise)

1. Settings → Connettori → Aggiungi connettore personalizzato
2. URL: `https://cruscotto-italia-mcp.piersoftckan.biz/mcp`
3. Autenticazione: nessuna

### Skill Claude opzionale

Per ottenere risposte più mirate è disponibile una skill Claude che documenta l'uso del connettore (inventario dei 5 tool, schema di `comune_dashboard` con tutte le sezioni — inclusa la nuova `runts` per gli enti del Terzo Settore, endpoint REST `/data/anncsu_full/<istat>.json`, pattern operativi e caveat per sezione). Scaricabile da [`/skills/cruscotto-italia-workflow-v1.6.0.zip`](https://cruscotto-italia-mcp.piersoftckan.biz/skills/cruscotto-italia-workflow-v1.6.0.zip).

### System prompt suggerito

Per ottenere risposte ottimali, suggerisci a Claude (o all'agente) un system prompt come questo:

~~~
Hai accesso al connector "Cruscotto Italia" che fornisce dati civici sui ~7.900 comuni italiani.

Linee guida:
- Quando l'utente menziona un comune per nome, chiama PRIMA search_comune per ottenere il codice ISTAT esatto, poi usa quel codice negli altri tool.
- Per domande generali su un comune ("dimmi di Bergamo", "dati di Milano") usa comune_dashboard: contiene tutto in una chiamata (22 sezioni: anagrafica, demografia, profilo, turismo, PNRR, territorio, aria, opere, contratti ANAC, spese SIOPE, scuole, veicoli, redditi, immobili PA, ANNCSU, sanità, punti di ricarica EV, banda larga FTTH/FTTC, distributori carburanti, enti del Terzo Settore RUNTS).
- Per il dettaglio dei progetti BDAP (lista CUP filtrabile al 2025) usa comune_opere_dettaglio; per query puntuali su civici (es. "quote di Via X", "esiste il civico Y in Z") usa anncsu_civico_search.
- In caso di omonimi (es. "San Teodoro" esiste in Sardegna e Sicilia) mostra all'utente i match e chiedi quale.
- Se l'utente non specifica il comune, chiedi chiarimento prima di chiamare i tool.
- Cita sempre la fonte dati primaria nei tuoi output (ANAC, ISTAT, BDAP-MOP, ISPRA, MEF, MIUR, ACI, Agenzia Entrate, Ministero Salute, GSE/MASE, AGCOM, MIMIT, Ministero del Lavoro).
~~~

> Nota: sostituisci `~~~` con triple backtick quando copi nel system prompt.

### Esempi di domande supportate

- "Dimmi di Lecce" — overview completa
- "Quanti progetti PNRR ha Bergamo?" — focus PNRR
- "Confronto demografico tra Milano e Roma" — orchestrazione cross-comune
- "Quante farmacie attive ci sono a Matera?" — sanità territoriale
- "Quanti punti di ricarica EV attivi ci sono a Torino e quale percentuale è HPC/Ultra fast?" — mobilità elettrica
- "Quanti civici certificati ANNCSU ci sono in via Roma a Lecce?" — civici georeferenziati
- "Qual è la copertura FTTH a Bergamo? Confronto con Brescia." — banda larga AGCOM
- "Quanto costa il gasolio self a Lecce rispetto alla media nazionale?" — distributori MIMIT
- "Quanti enti del Terzo Settore (ODV/APS) ha Matera? Quanti iscritti al 5x1000?" — RUNTS Min. Lavoro

### Limiti noti

- Tool ottimizzati per query **per-comune**, non per aggregati cross-comune (es. "top 10 PNRR per regione" richiede N chiamate)
- Il MCP è in solo lettura: nessun side-effect, nessuna scrittura

## Quick start

### Prerequisiti

- Node.js ≥ 20
- Python ≥ 3.12
- Account Cloudflare (Worker + R2) — i livelli free coprono il MVP
- `wrangler` CLI (`npm i -g wrangler`)

### Credenziali

Cruscotto Italia gira su Cloudflare (Worker + R2) e GitHub Actions (CI/CD).
Servono credenziali in 3 posti diversi a seconda dello scenario:

| Scenario | Cosa serve | Dove |
|---|---|---|
| Smoke test ETL (`scripts/smoke-test-etl.py`) | nessuna credenziale | locale |
| ETL Python `--target=local` (default) | nessuna credenziale | locale |
| ETL Python `--target=r2` (push su bucket) | 4 export `R2_*` nel profilo shell | `~/.bashrc` |
| Worker `npm run dev` | nessuna credenziale (binding R2 in sola lettura) | locale |
| Worker `wrangler deploy` | `wrangler login` interattivo o `CLOUDFLARE_API_TOKEN` | `~/.bashrc` |
| Workflow CI/CD GitHub Actions | 6 GitHub Secrets | Settings repo |

In locale, le credenziali vivono come `export VAR=...` in `~/.bashrc` (o equivalente) dell'utente che esegue ETL e Wrangler. Vedi `docs/SECRETS.md` § 6 per il dettaglio delle export richieste.

**Documentazione completa**:

- [`docs/SECRETS.md`](docs/SECRETS.md) — inventario dei 6 GitHub Secrets + procedure per crearli su Cloudflare + rotazione e revoca + setup locale via `~/.bashrc`
- [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) — architettura completa, pre-flight checklist setup, note operative deploy/maintenance
- [`docs/SERVER-INFRA.md`](docs/SERVER-INFRA.md) — setup operativo lato server (nginx, htpasswd, cron, env files, secret locali): guida riproducibile per migrazione o disaster recovery

### Setup locale

```bash
git clone https://github.com/piersoft/cruscotto-italia.git
cd cruscotto-italia

# Worker
cd worker
npm install
npm run dev   # http://localhost:8787

# Frontend
cd ../frontend
python3 -m http.server 8000   # http://localhost:8000

# ETL — esempio: scarica i punti di ricarica PUN in data/pun/
cd ..
pip install -r etl/requirements.txt
python -m etl.sources.pun --target=local
```

### Deploy

```bash
# Worker
cd worker
npm run typecheck && npm run deploy

# Frontend → server Aruba self-hosted (cruscotto-italia.piersoftckan.biz)
# Su push a main, GitHub Actions self-hosted runner sincronizza il frontend
```

## Struttura del repo

```
cruscotto-italia/
├── DESIGN.md                 ← documento architetturale completo (single source of truth)
├── DECISIONS.md              ← decisioni prese sui punti aperti del DESIGN.md
├── README.md
├── LICENSE                   ← AGPL-3.0
├── .gitignore
│
├── worker/                   ← Cloudflare Worker (TypeScript)
│   ├── src/
│   │   ├── index.ts          ← entrypoint
│   │   ├── mcp.ts            ← JSON-RPC MCP transport
│   │   ├── http.ts           ← landing page pubblica + endpoint /data/
│   │   ├── tools/            ← un file per tool/endpoint MCP
│   │   └── lib/              ← duckdb, r2cache, ratelimit helpers
│   ├── wrangler.toml
│   ├── package.json
│   └── tsconfig.json
│
├── frontend/                 ← single-file HTML (vanilla JS)
│   ├── index.html            ← homepage
│   ├── comune.html           ← vista comune-centric
│   ├── about.html            ← elenco fonti + metodologia
│   └── vendor/               ← Chart.js, Leaflet, JSZip (SHA-384 integrity)
│
├── etl/                      ← Python ETL pipeline
│   ├── requirements.txt
│   ├── pyproject.toml        ← ruff + mypy + pytest config
│   ├── sources/              ← un modulo per fonte
│   │   ├── anagrafica.py       ← spina dorsale ISTAT comuni + IPA
│   │   ├── anac.py             ← contratti pubblici (OCDS)
│   │   ├── bdap.py             ← BDAP-MOP opere pubbliche (CUP, progetti) + aggregato per ANAC lookup
│   │   ├── siope.py            ← SIOPE Spese multi-anno (CKAN)
│   │   ├── pnrr_progetti.py    ← progetti PNRR (Italia Domani/ReGiS)
│   │   ├── demografia.py       ← popolazione (POSAS)
│   │   ├── istat_profilo.py    ← Censimento permanente
│   │   ├── istat_turismo.py    ← capacità + flussi turistici
│   │   ├── territorio.py       ← ISPRA Suolo, IdroGEO, Rifiuti
│   │   ├── aria.py             ← ISPRA SNPA qualità aria (PM10/PM2.5/NO2)
│   │   ├── scuole.py           ← MIUR anagrafe scuole statali
│   │   ├── veicoli.py          ← ISTAT 41_993/41_983 + ACI LOD
│   │   ├── redditi.py          ← MEF Federalismo Fiscale (IRPEF)
│   │   ├── immobili_pa.py      ← MEF DE Beni Immobili Pubblici 2022
│   │   ├── anncsu.py           ← ANNCSU civici e strade (Agenzia Entrate + ISTAT)
│   │   ├── sanita_mds.py       ← Ministero Salute (farmacie, ospedali, posti letto)
│   │   ├── pun.py              ← GSE/MASE punti di ricarica veicoli elettrici
│   │   ├── agcom_bbmap.py      ← AGCOM Broadband Map (copertura banda larga FTTH/FTTC)
│   │   ├── carburanti.py       ← MIMIT Osservatorio Prezzi Carburanti (distributori + prezzi)
│   │   ├── runts.py            ← Min. Lavoro RUNTS (enti del Terzo Settore: ODV/APS/EF/IS/SMS/ETS)
│   │   └── dashboard.py        ← unified shard A1 (single-fetch per comune)
│   └── lib/
│       ├── r2.py
│       ├── duck.py
│       └── manifest.py
│
├── .github/workflows/
│   ├── etl-daily.yml         ← cron 04:30 UTC (PUN punti ricarica + MIMIT carburanti + dashboard rebuild)
│   ├── etl-weekly.yml        ← cron lunedì 04:00 UTC (ANAC + PNRR + sanità MdS + RUNTS Min. Lavoro + dashboard)
│   ├── etl-monthly.yml       ← cron 5° del mese (anagrafica + BDAP + SIOPE + ANNCSU + sanità + AGCOM banda larga + dashboard)
│   ├── etl-annual.yml        ← cron 1 feb / 1 apr / 1 lug (demografia, profilo, turismo, territorio, scuole, veicoli, redditi, immobili PA)
│   ├── deploy-worker.yml     ← su push main → Cloudflare Workers
│   ├── deploy-frontend.yml   ← su push main → Cloudflare Pages
│   └── ci.yml                ← CI lint & test (ruff, mypy, pytest, tsc)
│
├── docs/                     ← documentazione utente e API
├── scripts/                  ← utility scripts (smoke-test-etl, pa11y-*, ecc.)
└── tests/                    ← unit tests Python (etl) e Vitest (worker)
```

## Licenza

AGPL-3.0 — vedi [LICENSE](LICENSE). Codice copyleft, le derivate devono restare aperte.

I dati delle fonti sono sotto le rispettive licenze:

- **CC BY 4.0** — la maggior parte delle fonti (ANAC, ISTAT, MIUR, ACI, ISPRA, MEF DE Patrimonio, Italia Domani PNRR)
- **CC BY 3.0 IT** — MEF Federalismo Fiscale, alcuni dataset ISTAT storici
- **IODL 2.0** — BDAP-MOP, BDAP-SIOPE, Ministero della Salute, MIMIT (Osservatorio Prezzi Carburanti)
- **CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD)** — "open by default" per i dati delle PA pubblicati senza licenza esplicita. Si applica per esempio a GSE/MASE (PUN punti di ricarica EV), AGCOM (Broadband Map FTTH/FTTC ex art. 22 Codice Comunicazioni Elettroniche) e Ministero del Lavoro (RUNTS anagrafica enti del Terzo Settore ex D.Lgs 117/2017 art. 53 pubblicità legale), in coerenza con le Linee Guida Open Data AgID (Determinazione 183/2023)
- **Open Data ai sensi del Regolamento UE 2023/138 (HVD)** — ANNCSU (Agenzia delle Entrate + ISTAT)

Vedi [`docs/data-licenses.md`](docs/data-licenses.md) per il dettaglio per dataset, e [`about.html`](https://cruscotto-italia.piersoftckan.biz/about.html) per i link diretti alle fonti.

## Contribuire

Issue e PR benvenuti. Per discussioni di design aprire una Discussion. Pattern di commit: [Conventional Commits](https://www.conventionalcommits.org/).

## Crediti

Progetto di [Francesco Piero Paolicelli (@piersoft)](https://piersoft.it). Una dimostrazione di cosa, già oggi, si può fare con gli open data italiani. Vedi [DESIGN.md](DESIGN.md).
