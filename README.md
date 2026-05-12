# Cruscotto Italia

> **Repo istituzionale curato da [AgID](https://www.agid.gov.it/).** Progetto sviluppato da [Francesco Piero Paolicelli (@piersoft)](https://piersoft.it), in adozione presso AgID. Il repo originale dell'autore è [piersoft/cruscotto-italia](https://github.com/piersoft/cruscotto-italia).


> La carta d'identità data-driven dei comuni italiani. I dataset pubblici dei principali enti istituzionali, federati e ricomposti per comune.

[![Deploy Worker](https://github.com/AgID/cruscotto-italia/actions/workflows/deploy-worker.yml/badge.svg)](https://github.com/AgID/cruscotto-italia/actions/workflows/deploy-worker.yml)
[![ETL Mensile](https://github.com/AgID/cruscotto-italia/actions/workflows/etl-monthly.yml/badge.svg)](https://github.com/AgID/cruscotto-italia/actions/workflows/etl-monthly.yml)
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

L'elenco completo, con licenze, frequenze di aggiornamento e link diretti alle fonti, è in [`about.html`](https://cruscotto-italia.piersoftckan.biz/about.html).
- 🏫 **Scuole** (MIUR — Anagrafe scuole statali)
- 🌫️ **Qualità dell'aria** (ISPRA SNPA — PM10/PM2.5/NO2)
- 🚗 **Parco veicoli e incidenti** (ISTAT 41_993 + ACI LOD)
- 💶 **Redditi e fisco** (MEF — Dichiarazioni IRPEF)
- 🏛️ **Patrimonio immobiliare PA** (MEF DE — Beni Immobili Pubblici)
- 🏠 **Civici e strade** (ANNCSU — Agenzia delle Entrate, Open Data HVD)

L'elenco completo, con licenze, frequenze di aggiornamento e link diretti alle fonti, è in [`about.html`](https://cruscotto-italia.piersoftckan.biz/about.html).

Tutto ricomposto sulla **spina dorsale anagrafica ISTAT comuni**.

## Architettura

```
Frontend (HTML statico) → Worker (Cloudflare) → R2 (JSON shard per comune)
                              ↑
              ETL Python (GitHub Actions, cadenze multiple)
                              ↑
   ANAC · BDAP-MOP · SIOPE · Italia Domani (PNRR) · ISTAT (POSAS, Censimento, Turismo, Veicoli, Incidenti)
   MIUR · ISPRA SNPA · ACI LOD · MEF (IRPEF, Beni Immobili) · Agenzia Entrate (ANNCSU)
```

Tutti i dettagli architetturali sono in [`DESIGN.md`](DESIGN.md).

## Status

🚧 In sviluppo. Vedi [DESIGN.md § 7 Roadmap](DESIGN.md#7-roadmap--milestones).

## Uso come MCP per chatbot AI

Cruscotto Italia espone un server [Model Context Protocol](https://modelcontextprotocol.io) che consente di interrogare i dati civici tramite chatbot AI compatibili (Claude, ChatGPT con wrapper, OpenWebUI, agenti custom).

**Endpoint pubblico**: `https://cruscotto-italia-mcp.piersoftckan.biz/mcp`

**Tool esposti** (11): `mcp_info`, `search_comune`, `comune_dashboard`, `comune_demografia`, `comune_profilo`, `comune_turismo`, `comune_pnrr`, `comune_territorio`, `comune_opere_dettaglio`, `comune_spese`, `comune_contratti`.

**Rate limit**: 60 richieste/minuto per IP.

### Configurazione su Claude.ai (Pro/Team/Enterprise)

1. Settings → Connettori → Aggiungi connettore personalizzato
2. URL: `https://cruscotto-italia-mcp.piersoftckan.biz/mcp`
3. Autenticazione: nessuna

### System prompt suggerito

Per ottenere risposte ottimali, suggerisci a Claude (o all'agente) un system prompt come questo:
~~~
Hai accesso al connector "Cruscotto Italia" che fornisce dati civici sui ~7900 comuni italiani.

Linee guida:
- Quando l'utente menziona un comune per nome, chiama PRIMA search_comune per ottenere il codice ISTAT esatto, poi usa quel codice negli altri tool.
- Per domande generali su un comune ("dimmi di Bergamo", "dati di Milano") usa comune_dashboard: contiene tutto in una chiamata.
- Usa i tool specifici (comune_pnrr, comune_spese, ecc.) solo se l'utente vuole dettagli mirati su un singolo aspetto.
- In caso di omonimi (es. "San Teodoro" esiste in Sardegna e Sicilia) mostra all'utente i match e chiedi quale.
- Se l'utente non specifica il comune, chiedi chiarimento prima di chiamare i tool.
- Cita sempre la fonte dati primaria nei tuoi output (ANAC, ISTAT, BDAP-MOP, ISPRA, MEF/RGS, Italia Domani).
~~~

> Nota: sostituisci `~~~` con `` ``` `` (triple backtick) quando copi nel system prompt — qui usiamo `~~~` per evitare interferenze con il markdown del README.

### Esempi di domande supportate

- "Dimmi di Lecce" — overview completa
- "Quanti progetti PNRR ha Bergamo?" — focus PNRR
- "Confronto demografico tra Milano e Roma" — orchestrazione cross-comune
- "Quante scuole ci sono nel comune di San Teodoro?" — gestione omonimi

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
| ETL Python `--target=r2` (push su bucket) | 3 var R2 in `.env` | locale |
| Worker `npm run dev` | nessuna credenziale (binding R2 in sola lettura) | locale |
| Worker `wrangler deploy` | `wrangler login` interattivo o env CF | locale |
| Workflow CI/CD GitHub Actions | 6 GitHub Secrets | Settings repo |

**Documentazione completa**:

- [`docs/SECRETS.md`](docs/SECRETS.md) — inventario dei 6 GitHub Secrets, procedure per crearli su Cloudflare, rotazione e revoca
- [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) — architettura completa, pre-flight checklist setup, note operative deploy/maintenance
- [`.env.example`](.env.example) — template variabili d'ambiente locali

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

# ETL
cd ../etl
pip install -r requirements.txt
python -m sources.anagrafica --target=local  # crea Parquet locali in /tmp
```

### Deploy

```bash
# Worker
cd worker
wrangler deploy

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
│   │   ├── tools/            ← un file per tool/endpoint
│   │   └── lib/              ← duckdb, r2, cache helpers
│   ├── wrangler.toml
│   ├── package.json
│   └── tsconfig.json
│
├── frontend/                 ← single-file HTML (vanilla JS)
│   ├── index.html            ← homepage
│   ├── comune.html           ← vista comune-centric
│   └── assets/
│
├── etl/                      ← Python ETL pipeline
│   ├── requirements.txt
│   ├── sources/              ← un modulo per fonte
│   │   ├── anac.py             ← contratti pubblici (OCDS)
│   │   ├── bdap_mop.py         ← opere pubbliche
│   │   ├── bdap_siope.py       ← flussi di cassa
│   │   ├── pnrr_progetti.py    ← progetti PNRR (Italia Domani/ReGiS)
│   │   ├── demografia.py       ← popolazione (POSAS)
│   │   ├── istat_profilo.py    ← Censimento permanente
│   │   ├── istat_turismo.py    ← capacità + flussi turistici
│   │   └── anagrafica.py       ← spina dorsale ISTAT comuni
│   └── lib/
│       ├── r2.py
│       ├── duck.py
│       └── manifest.py
│
├── .github/workflows/
│   ├── etl-weekly.yml        ← cron lunedì (ANAC + PNRR + dashboard)
│   ├── etl-monthly.yml       ← cron 5° del mese (anagrafica + BDAP + SIOPE + dashboard)
│   ├── etl-annual.yml        ← cron 1 feb / 1 apr / 1 lug (demografia, profilo, turismo, territorio)
│   ├── deploy-worker.yml     ← su push main (Cloudflare Workers)
│   ├── deploy-frontend.yml   ← su push main (server Aruba self-hosted)
│   └── ci.yml                ← CI su PR
│
├── docs/                     ← documentazione utente e API
├── scripts/                  ← utility scripts
└── tests/                    ← unit tests Python (etl) e Vitest (worker)
```

## Licenza

AGPL-3.0 — vedi [LICENSE](LICENSE). Codice copyleft, le derivate devono restare aperte.  
I dati delle fonti sono sotto le rispettive licenze (CC-BY 4.0, IODL 2.0, ecc.) — vedi [`docs/data-licenses.md`](docs/data-licenses.md).

## Contribuire

Issue e PR benvenuti. Per discussioni di design aprire una Discussion. Pattern di commit: [Conventional Commits](https://www.conventionalcommits.org/).

## Crediti

Progetto di [Francesco Piero Paolicelli (@piersoft)](https://piersoft.it). Una dimostrazione di cosa, già oggi, si può fare con gli open data italiani. Vedi [DESIGN.md](DESIGN.md).
