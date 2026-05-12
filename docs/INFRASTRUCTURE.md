# Infrastruttura

> Questo documento descrive l'architettura tecnica di Cruscotto Italia: componenti attivi, stato di migrazione su AgID, e pre-flight checklist per il setup operativo.

**Ultimo aggiornamento**: 2026-05-12

---

## 1. Architettura attuale (operativa su account Piersoft)

Cruscotto Italia gira oggi su infrastruttura personale di Francesco Piero Paolicelli. L'architettura è composta da 4 livelli:

### 1.1 Frontend statico

- **Hosting**: server Aruba self-hosted (MCP-SERVER, user ghrunner)
- **Dominio**: cruscotto-italia.piersoftckan.biz
- **Stack**: HTML statico, Chart.js 4.4.1 + Leaflet 1.9.4 + JSZip 3.10.1 (tutti vendorizzati in frontend/vendor/ con SHA-384 integrity, zero CDN runtime)
- **Design system**: Design Italia, font Titillium Web self-hosted (3 WOFF2, ~65KB)
- **Accessibilità**: WCAG 2.1 AA verificato Pa11y + W3C (0 errori)

### 1.2 Worker MCP (Cloudflare)

- **Account**: Cloudflare personale Piersoft
- **Endpoint**: cruscotto-italia-mcp.piersoftckan.biz/mcp (custom domain) e fallback cruscotto-italia-mcp.datigovit.workers.dev/mcp
- **Stack**: TypeScript, Cloudflare Workers, binding R2 (DATA) + KV (CACHE + MCP_RATE_LIMITER)
- **Versione**: 0.4.0 (12 dataset, 8 istituzioni)
- **Tools MCP esposti**: comune_dashboard (single-fetch A1), comune_demografia, comune_profilo, comune_turismo, comune_pnrr, comune_territorio, comune_opere, comune_siope, comune_scuole, comune_aria, comune_veicoli, comune_redditi, comune_contratti, mcp_info, search_comune

### 1.3 Storage R2 (Cloudflare)

- **Bucket**: cruscotto-italia-data
- **Preview bucket**: cruscotto-italia-data-preview
- **Contenuto**: ~7896 shard per-comune (uno per istat code) sotto vari prefix:
  - dashboard/<istat>.json (A1 aggregato, ~7896 file)
  - demografia/, profilo/, turismo/, pnrr/, territorio/, opere/, siope/, scuole/, aria/, veicoli/, redditi/ (legacy shard per dominio)
  - aci/, anac/, bdap/ (cache lookup e aggregati)
  - lookup/ (file di lookup per nomi/codici)
  - raw/ (cache CSV/dati grezzi per ETL)

### 1.4 ETL Python

- **Esecuzione**: GitHub Actions su self-hosted runner ghrunner (server Aruba, geo-located in Italia per accesso italiadomani.gov.it geo-bloccato)
- **Linguaggio**: Python 3.x con structlog
- **Workflow**: 3 file in .github/workflows/
  - etl-weekly.yml (lunedì): ANAC + PNRR + dashboard rebuild
  - etl-monthly.yml (giorno 5 del mese): anagrafica + BDAP + SIOPE + dashboard rebuild
  - etl-annual.yml (3 cron: 1 febbraio aria, 1 aprile redditi, 1 luglio veicoli/turismo)

### 1.5 Repository codice

- **Repo principale (autore)**: github.com/piersoft/cruscotto-italia
- **Repo istituzionale (AgID)**: github.com/AgID/cruscotto-italia (questo repo)
- **Relazione**: mirror manuale, no fork formale, repo indipendenti

---

## 2. Architettura target su infrastruttura AgID

Migrazione prevista entro giugno 2026. Tutti i valori marcati come **TBD** vanno decisi/forniti dall'admin AgID al momento del setup.

### 2.1 Frontend statico

- **Hosting**: TBD (probabili opzioni: Cloudflare Pages account AgID, server proprio AgID, oppure resta su Aruba in transitorio)
- **Dominio**: cruscotto-italia.dati.gov.it (oppure altro subdomain di dati.gov.it concordato con AgID)
- **Certificato SSL**: gestito da AgID o Cloudflare a seconda dell'hosting scelto

### 2.2 Worker MCP

- **Account Cloudflare**: AgID (nuovo, da creare)
- **Endpoint atteso**: TBD. Opzioni plausibili:
  - cruscotto-italia-mcp.workers.dev (default Cloudflare, no custom domain)
  - mcp.cruscotto-italia.dati.gov.it (custom subdomain AgID)
  - cruscotto-italia.dati.gov.it/mcp (path sullo stesso dominio frontend)
- **Naming worker**: cruscotto-italia-mcp (riusabile, no conflitti perché account separato)

### 2.3 Storage R2

- **Bucket nome**: TBD (proposta: cruscotto-italia-data, oppure agid-cruscotto-data se AgID preferisce prefissi org)
- **Preview bucket**: stesso nome con suffisso -preview
- **Migrazione dati**: ~7896 shard da copiare da bucket Piersoft a bucket AgID con rclone (configurazione due remote S3-compatible, sync una tantum, ~30 minuti stimati)

### 2.4 KV namespaces

- **MCP_RATE_LIMITER**: rate limiting Worker (TTL 60s)
- **CACHE**: cache risposte MCP (TTL configurato)
- **AUTH_TOKENS**: se si decide di proteggere il worker AgID con Bearer token (analogo al pattern già usato per piersoft/ckan-mcp-server)
- I namespace ID sono specifici per account Cloudflare e vanno rigenerati su account AgID, poi inseriti in worker/wrangler.toml

### 2.5 Self-hosted GitHub Actions runner

**Decisione organizzativa pendente**:

- **Opzione A**: AgID configura un proprio runner su un server italiano (necessario per accesso italiadomani.gov.it, geo-bloccato fuori Italia)
- **Opzione B**: si mantiene il runner ghrunner attuale su server Aruba Piersoft come servizio di transizione (con permessi AgID per attivare workflow)
- **Opzione C**: si trova un workaround per il geo-block (proxy italiano, scraping da fonte alternativa, mirror del dataset Italia Domani su R2 prima dell'ETL)

### 2.6 DNS

- **cruscotto-italia.dati.gov.it**: punterà al frontend AgID (CNAME o A record)
- **Eventuale subdomain MCP**: gestito su DNS dati.gov.it da team AgID
- I record DNS vengono gestiti da AgID, non da chi sviluppa il codice

### 2.7 Repository codice

Post-migrazione:
- **AgID/cruscotto-italia**: repo principale operativo (questo)
- **piersoft/cruscotto-italia**: repo personale dell'autore, può continuare a esistere come copia di sviluppo o essere archiviato

---

## 3. Pre-flight checklist setup AgID

Lista ordinata di ciò che serve creare/configurare PRIMA che i workflow possano girare con successo. Ogni step ha una dipendenza dai precedenti.

### Step 1: Account Cloudflare AgID

- [ ] Account Cloudflare AgID attivato (richiesta business/enterprise da parte AgID, oppure free tier per testing)
- [ ] Almeno un utente con permessi di amministrazione
- [ ] Annotato l'Account ID (32 caratteri esadecimali, recuperabile dalla sidebar destra del dashboard)

### Step 2: API Token Cloudflare

- [ ] Generato token con permessi Workers Scripts/Routes Edit, Pages Edit (se applicabile), Account Settings Read
- [ ] Token salvato in modo sicuro (è visibile una sola volta)
- [ ] Documentato in docs/SECRETS.md come `CLOUDFLARE_API_TOKEN`

### Step 3: R2 bucket e credenziali

- [ ] Creato bucket R2 con nome scelto (es. cruscotto-italia-data)
- [ ] Creato preview bucket con suffisso -preview
- [ ] Generata coppia API token R2 (Object Read & Write sul bucket)
- [ ] Credenziali documentate in docs/SECRETS.md (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)

### Step 4: KV namespaces

- [ ] Creato namespace MCP_RATE_LIMITER, annotato l'ID
- [ ] Creato namespace CACHE, annotato l'ID
- [ ] (Opzionale) Creato namespace AUTH_TOKENS se si protegge il worker

### Step 5: Refactor config.json (codice)

Vedi MIGRAZIONE_AGID_HANDOFF.md (documento fornito separatamente). Refactor stimato 6 ore.

- [ ] Creato config.json master con tutti i valori AgID
- [ ] Script scripts/apply-config.py funzionante
- [ ] Refactor 4 file frontend (HTML) per usare placeholder
- [ ] Refactor 4 file worker (TypeScript) per usare config.generated.ts
- [ ] Fix 2 bucket hardcoded in etl/sources/demografia.py e etl/sources/bdap.py
- [ ] wrangler.toml aggiornato con valori AgID (account_id, bucket_name, KV IDs)

### Step 6: Sync dati R2

- [ ] Configurato rclone con due remote (R2 Piersoft come source, R2 AgID come dest)
- [ ] Sync di ~7896 shard (~50-100MB totali stimati) con rclone copy
- [ ] Verifica coverage post-sync (count file per prefix)

### Step 7: Self-hosted runner

Scelta tra opzione A, B, C documentate in §2.5.

- [ ] Runner configurato e online sul repo AgID/cruscotto-italia
- [ ] Verifica accesso a italiadomani.gov.it (geo-block test)

### Step 8: GitHub Secrets

Vedi docs/SECRETS.md per la procedura.

- [ ] Tutti i 6 secrets popolati su github.com/AgID/cruscotto-italia/settings/secrets/actions
- [ ] WORKER_ADMIN_TOKEN coordinato tra GitHub Secret e wrangler secret put

### Step 9: Primo deploy worker

- [ ] cd worker && npm install && npm run typecheck (no errori)
- [ ] npm run deploy (NON wrangler deploy --env production, vedi memoria progetto)
- [ ] Verifica worker raggiungibile su endpoint AgID

### Step 10: Primo deploy frontend

- [ ] Build frontend (statico, no build step richiesto: copia diretta)
- [ ] Deploy su hosting target (CF Pages, Aruba, o altro)
- [ ] Verifica frontend caricabile e che parli col nuovo worker

### Step 11: Test ETL minimale

- [ ] Trigger manuale di etl-weekly.yml via Run workflow
- [ ] Verifica scrittura su bucket AgID (non più Piersoft)
- [ ] Verifica cache purge call (WORKER_ADMIN_TOKEN funzionante)

### Step 12: DNS cutover

- [ ] Configurato DNS cruscotto-italia.dati.gov.it → hosting AgID
- [ ] Eventuale subdomain MCP configurato
- [ ] Test end-to-end da dominio finale

### Step 13: Abilitazione GitHub Actions

- [ ] Abilita Actions nel repo (Settings → Actions → Enable)
- [ ] Verifica trigger schedulati attivi (cron)
- [ ] Aggiunta badge stato workflow al README (opzionale, già pronti)

---

## 4. Dipendenze tra componenti

Diagramma testuale delle dipendenze (componente X dipende dal componente Y prima di funzionare):

    Frontend statico
        └── Worker MCP (URL hardcoded in HTML, fix con config.json)
              ├── R2 bucket (binding DATA)
              ├── KV CACHE (binding CACHE)
              └── KV MCP_RATE_LIMITER (binding)

    ETL Python (su runner)
        ├── R2 bucket (boto3 con R2_ACCESS_KEY_ID/SECRET)
        ├── Internet (accesso a fonti istituzionali: ANAC, BDAP, ISTAT, MEF, ISPRA, ACI, MIUR, Italia Domani)
        │     └── Italia Domani: geo-block su IP italiano
        └── Worker /admin/cache/purge (con WORKER_ADMIN_TOKEN)

    GitHub Actions
        ├── GitHub Secrets (6 secrets)
        ├── Self-hosted runner (per ETL)
        └── Ubuntu-latest runner (per deploy worker/frontend)

---

## 5. Note operative

### 5.1 Comando deploy worker

NON usare `wrangler deploy --env production` (crea worker secondario senza binding R2/KV/ratelimit, causa errori a runtime).

Usare invece:

    cd worker
    npm install
    npm run typecheck
    npm run deploy

Lo script npm run deploy è configurato in worker/package.json e usa il blocco top-level di wrangler.toml con tutti i binding corretti.

### 5.2 Pattern push R2 efficiente

Per shard per-comune (~7000+ file piccoli), MAI fare r2.head() in loop (causa 20+ minuti seriali). Pattern corretto documentato in etl/sources/aria.py e etl/sources/veicoli.py (push_to_r2 / push_shards_r2):

1. Una sola list_objects_v2 paginata sul prefix per ottenere tutti gli ETag remoti
2. Calcolo MD5 locale dei file da pushare, diff con remote ETag
3. Upload paralleli con ThreadPoolExecutor (max_workers=24)
4. Progress log ogni 200 file con ETA

Tempo risultante: 1-2 minuti vs 20+ minuti.

### 5.3 SIOPE: aggiornamento annuale

A gennaio/febbraio di ogni anno, aggiornare etl/sources/siope.py:

- Aggiungere nuovo anno a SUPPORTED_YEARS
- Aggiungere 20 UUID nuovi a SIOPE_RESOURCE_IDS (uno per regione)
- Aggiornare PARTIAL_YEARS (anno in corso)
- Aggiornare etl-monthly.yml --anni=<anno>

---

## 6. Riferimenti

- docs/SECRETS.md (inventario operativo dei 6 secrets GitHub Actions)
- MIGRAZIONE_AGID_HANDOFF.md (piano completo refactor config.json, fornito separatamente)
- Workflow GitHub Actions in .github/workflows/
- README.md (descrizione progetto e overview architetturale)

---

## 7. Security hardening nginx (2026-05-12)

VA di primo livello eseguita su cruscotto-italia.piersoftckan.biz (frontend Aruba). Configurazione applicata da replicare a giugno su infrastruttura AgID.

### 7.1 Snippet security headers condiviso

File: /etc/nginx/snippets/security-headers.conf

Include 6 header di sicurezza (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS, Permissions-Policy, CSP). Va incluso sia a livello server sia in ogni location block che usa add_header (nginx gotcha: add_header non eredita se nel location ci sono altri add_header).

CSP attuale permette: 'self' + Google Fonts + Worker MCP (sia .workers.dev sia custom domain piersoftckan.biz) + tile OSM. Da aggiornare a giugno con il dominio worker AgID.

### 7.2 Difesa file backup

Location regex nel vhost che blocca file .bak/.backup/.swp/.swo/.orig/.old/.tmp/~ (rifiuta con 404 senza esporre il file). Necessario perché lavorando con sed/python su file in produzione si creano spesso file .bak temporanei.

### 7.3 server_tokens off

In /etc/nginx/nginx.conf blocco http: nasconde versione nginx nei banner Server e nelle error pages default. Beneficio per tutti i vhost del server.

### 7.4 TLS

Configurazione TLS attuale gestita da Certbot (Let's Encrypt R12). Già conforme: TLS 1.2 e 1.3 only, ECDHE-RSA-AES256-GCM-SHA384 + TLS_AES_256_GCM_SHA384, X25519. TLS 1.0/1.1 disabilitati.

Auto-rinnovo certbot attivo. Su infrastruttura AgID a giugno: replicare con certbot o equivalente, oppure usare cert AgID istituzionale se disponibile.

### 7.5 Miglioramenti residui (bassa priorità)

- OCSP stapling (attualmente assente lato Aruba, gestito automaticamente lato Cloudflare)
- CSP senza 'unsafe-inline' (richiede refactor inline scripts/styles con nonce/hash, ~2-3h di lavoro)
- Rate limiting nginx (oggi solo sul Worker via KV)
