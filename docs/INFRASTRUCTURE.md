# Infrastruttura

> Questo documento descrive l'architettura tecnica di Cruscotto Italia: componenti attivi, dipendenze tra moduli, e note operative per setup, deploy e maintenance.

**Ultimo aggiornamento**: 2026-05-13

---

## 1. Architettura attuale

Cruscotto Italia è strutturato in 4 livelli:

### 1.1 Frontend statico

- **Hosting**: server Aruba self-hosted (MCP-SERVER, user ghrunner)
- **Dominio**: cruscotto-italia.piersoftckan.biz
- **Stack**: HTML statico, Chart.js 4.4.1 + Leaflet 1.9.4 + JSZip 3.10.1 (tutti vendorizzati in `frontend/vendor/` con SHA-384 integrity, zero CDN runtime)
- **Design system**: Design Italia, font Titillium Web self-hosted (3 WOFF2, ~65KB)
- **Accessibilità**: WCAG 2.1 AA verificato Pa11y + W3C (0 errori)

### 1.2 Worker MCP (Cloudflare)

- **Account**: Cloudflare
- **Endpoint**: `cruscotto-italia-mcp.piersoftckan.biz/mcp` (custom domain) e fallback `cruscotto-italia-mcp.datigovit.workers.dev/mcp`
- **Stack**: TypeScript, Cloudflare Workers, binding R2 (`DATA`) + KV (`CACHE` + `MCP_RATE_LIMITER`)
- **Tools MCP esposti** (v0.10.0, 5 tool): `mcp_info`, `search_comune`, `comune_dashboard` (single-fetch A1, 21 sezioni), `comune_opere_dettaglio` (BDAP dettaglio filtrato 2025), `anncsu_civico_search` (query puntuali civici ANNCSU con filtri server-side)

### 1.3 Storage R2 (Cloudflare)

- **Bucket**: `cruscotto-italia-data`
- **Preview bucket**: `cruscotto-italia-data-preview`
- **Contenuto**: ~7896 shard per-comune (uno per codice ISTAT) sotto vari prefix:
  - `dashboard/<istat>.json` (A1 aggregato, ~7896 file)
  - `demografia/`, `profilo/`, `turismo/`, `pnrr/`, `territorio/`, `opere/`, `siope/`, `scuole/`, `aria/`, `veicoli/`, `redditi/`, `immobili_pa/`, `anncsu/` (shard per dominio)
  - `aci/`, `anac/`, `bdap/` (cache lookup e aggregati)
  - `lookup/` (file di lookup per nomi/codici)
  - `raw/` (cache CSV/dati grezzi per ETL — riduce drasticamente tempi di re-run)

### 1.4 ETL Python

- **Esecuzione**: GitHub Actions su self-hosted runner `ghrunner` (server Aruba, geo-located in Italia per accesso a fonti geo-bloccate come italiadomani.gov.it)
- **Linguaggio**: Python 3.12+ con structlog
- **Workflow** in `.github/workflows/`:
  - `etl-monthly.yml` (giorno 5 del mese): ANAC + ANNCSU + SIOPE + dashboard rebuild
  - `etl-annual.yml` (cron multipli: febbraio aria, aprile redditi + immobili_pa, luglio veicoli/turismo)
  - `deploy-worker.yml` (su push a main, deploya il Worker)
- **Sorgenti**: 18 ETL in `etl/sources/`, 13 dataset operativi da 10 istituzioni (ANAC, ANNCSU, BDAP, SIOPE, MIUR, ISPRA SNPA, ISTAT, ACI, MEF IRPEF, MEF Beni Immobili, Italia Domani)

---

## 2. Pre-flight checklist per nuovo setup

Chi clona il repo (fork o nuovo deploy su altro account Cloudflare) deve seguire questi step in ordine. Ogni step ha dipendenza dai precedenti.

### Step 1: Account Cloudflare

- [ ] Account Cloudflare attivato (anche free tier copre l'MVP)
- [ ] Almeno un utente con permessi di amministrazione
- [ ] Annotato l'Account ID (32 caratteri esadecimali, sidebar destra del dashboard)

### Step 2: API Token Cloudflare

- [ ] Generato token con permessi Workers Scripts/Routes Edit, Workers KV Storage Edit, Account Settings Read
- [ ] Token salvato in modo sicuro (visibile una sola volta)
- [ ] Documentato come `CLOUDFLARE_API_TOKEN` (vedi `docs/SECRETS.md`)

### Step 3: R2 bucket e credenziali

- [ ] Creato bucket R2 con nome `cruscotto-italia-data` (o nome custom — in tal caso aggiornare `worker/wrangler.toml` e var `R2_BUCKET`)
- [ ] Creato preview bucket con suffisso `-preview`
- [ ] Generata coppia API token R2 (Object Read & Write sul bucket)
- [ ] Credenziali documentate come `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`

### Step 4: KV namespaces

- [ ] Creato namespace `MCP_RATE_LIMITER`, annotato l'ID
- [ ] Creato namespace `CACHE`, annotato l'ID
- [ ] ID inseriti in `worker/wrangler.toml` (sezione `[[kv_namespaces]]`)

```bash
cd worker
wrangler kv namespace create CACHE
wrangler kv namespace create MCP_RATE_LIMITER
# copia gli ID stampati in wrangler.toml
```

### Step 5: GitHub Secrets

Vedi `docs/SECRETS.md` per la procedura completa.

- [ ] Tutti i 6 secrets popolati su `https://github.com/<user>/<repo>/settings/secrets/actions`
- [ ] `WORKER_ADMIN_TOKEN` coordinato tra GitHub Secret e `wrangler secret put`

### Step 6: Self-hosted runner (per ETL)

- [ ] Runner GitHub Actions configurato su un server in Italia (necessario per accesso a italiadomani.gov.it geo-bloccato fuori UE)
- [ ] Runner labellato come `self-hosted` e accessibile dal repo
- [ ] Verifica accesso a italiadomani.gov.it (curl test)

### Step 7: Primo deploy worker

```bash
cd worker
npm install
npm run typecheck     # no errori attesi
npm run deploy        # NON 'wrangler deploy --env production' (vedi §5.1)
```

- [ ] Worker raggiungibile sull'endpoint generato (es. `<worker-name>.<account>.workers.dev`)
- [ ] Custom domain configurato (opzionale)

### Step 8: Bootstrap dati R2

Senza dati su R2 il Worker e il frontend rispondono ma sono vuoti. Bootstrap iniziale:

```bash
# Trigger manuale di un ETL completo:
gh workflow run etl-monthly.yml
# oppure direttamente in locale con env caricate:
set -a; source .env; set +a
python -m etl.sources.anagrafica --target=r2
python -m etl.sources.demografia --target=r2
# ... ripetere per ogni ETL desiderato
python -m etl.sources.dashboard --target=r2   # ultimo: aggrega tutto
```

### Step 9: Frontend hosting

- [ ] Frontend statico deployato su hosting a scelta (server proprio, Cloudflare Pages, GitHub Pages disattivato di default)
- [ ] DNS configurato per puntare al frontend
- [ ] Test caricamento end-to-end: cerca un comune, verifica che le tab si popolino

---

## 3. Dipendenze tra componenti

Diagramma testuale (componente X dipende dal componente Y prima di funzionare):

```
Frontend statico
    └── Worker MCP (URL hardcoded in HTML)
          ├── R2 bucket (binding DATA)
          ├── KV CACHE (binding CACHE)
          └── KV MCP_RATE_LIMITER (binding)

ETL Python (su runner)
    ├── R2 bucket (boto3 con R2_ACCESS_KEY_ID/SECRET)
    ├── Internet (accesso a fonti istituzionali)
    │     └── Italia Domani: geo-block su IP italiano
    └── Worker /admin/cache/purge (con WORKER_ADMIN_TOKEN)

GitHub Actions
    ├── GitHub Secrets (6 secrets)
    ├── Self-hosted runner (per ETL)
    └── Ubuntu-latest runner (per deploy worker)
```

---

## 4. Note operative

### 4.1 Comando deploy worker

**NON** usare `wrangler deploy --env production`: crea un worker secondario senza binding R2/KV/ratelimit, causa errori a runtime.

Usare invece:

```bash
cd worker
npm install
npm run typecheck
npm run deploy
```

Lo script `npm run deploy` è configurato in `worker/package.json` e usa il blocco top-level di `wrangler.toml` con tutti i binding corretti.

### 4.2 Pattern push R2 efficiente

Per shard per-comune (~7000+ file piccoli), MAI fare `r2.head()` in loop (causa 20+ minuti seriali). Pattern corretto documentato in `etl/sources/aria.py` e `etl/sources/veicoli.py` (`push_to_r2` / `push_shards_r2`):

1. Una sola `list_objects_v2` paginata sul prefix per ottenere tutti gli ETag remoti
2. Calcolo MD5 locale dei file da pushare, diff con remote ETag
3. Upload paralleli con `ThreadPoolExecutor(max_workers=24)`
4. Progress log ogni 200 file con ETA

Tempo risultante: 1-2 minuti vs 20+ minuti.

### 4.3 SIOPE: aggiornamento annuale

A gennaio/febbraio di ogni anno, aggiornare `etl/sources/siope.py`:

- Aggiungere nuovo anno a `SUPPORTED_YEARS`
- Aggiungere 20 UUID nuovi a `SIOPE_RESOURCE_IDS` (uno per regione)
- Aggiornare `PARTIAL_YEARS` (anno in corso)
- Aggiornare `etl-monthly.yml` con `--anni=<anno>`

### 4.4 Smoke test ETL

`scripts/smoke-test-etl.py` lancia tutti gli ETL con `--target=local` (scrivono su disco, nessuna scrittura R2). Permette di validare l'intera pipeline in ~2 minuti, idealmente prima di un deploy o dopo aver modificato un parser.

```bash
python3 scripts/smoke-test-etl.py            # tutti gli ETL
python3 scripts/smoke-test-etl.py --tier fast  # solo veloci
python3 scripts/smoke-test-etl.py --dry        # piano senza esecuzione
```

---

## 5. Security hardening nginx

VA di primo livello eseguita su `cruscotto-italia.piersoftckan.biz` (frontend Aruba).

### 5.1 Snippet security headers condiviso

File: `/etc/nginx/snippets/security-headers.conf`

Include 6 header di sicurezza (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS, Permissions-Policy, CSP). Va incluso sia a livello server sia in ogni location block che usa `add_header` (nginx gotcha: `add_header` non eredita se nel location ci sono altri `add_header`).

CSP attuale permette: `'self'` + Google Fonts + Worker MCP (sia `.workers.dev` sia custom domain `piersoftckan.biz`) + tile OSM.

### 5.2 Difesa file backup

Location regex nel vhost che blocca file `.bak`/`.backup`/`.swp`/`.swo`/`.orig`/`.old`/`.tmp`/`~` (rifiuta con 404 senza esporre il file). Necessario perché lavorando con `sed`/Python su file in produzione si creano spesso file `.bak` temporanei.

### 5.3 server_tokens off

In `/etc/nginx/nginx.conf` blocco `http`: nasconde versione nginx nei banner `Server` e nelle error pages default.

### 5.4 TLS

Configurazione TLS gestita da Certbot (Let's Encrypt R12). Già conforme: TLS 1.2 e 1.3 only, ECDHE-RSA-AES256-GCM-SHA384 + TLS_AES_256_GCM_SHA384, X25519. TLS 1.0/1.1 disabilitati. Auto-rinnovo certbot attivo.

### 5.5 Miglioramenti residui (bassa priorità)

- OCSP stapling (gestito automaticamente lato Cloudflare per il Worker)
- CSP senza `'unsafe-inline'` (richiede refactor inline scripts/styles con nonce/hash, ~2-3h di lavoro)
- Rate limiting nginx (oggi solo sul Worker via KV)

---

## 6. Riferimenti

- `docs/SECRETS.md` — inventario operativo dei 6 secrets GitHub Actions
- `.env.example` — template variabili d'ambiente locali
- Workflow GitHub Actions in `.github/workflows/`
- `README.md` — descrizione progetto e overview architetturale
