# Secrets e variabili di ambiente

> Questo documento elenca tutti i secrets richiesti dai workflow GitHub Actions di questo repo, le loro fonti, e le procedure per crearli.

**Stato attuale (2026-05-12)**: nessun secret configurato. I workflow ETL/deploy non sono operativi su `AgID/cruscotto-italia` finché i secrets non saranno popolati. Setup previsto entro giugno 2026, contestualmente alla migrazione su infrastruttura Cloudflare AgID.

---

## 1. Inventario dei secrets

I workflow attualmente referenziano 6 secrets:

| # | Nome | Tipo | Usato in workflow | Critico |
|---|------|------|-------------------|---------|
| 1 | `CLOUDFLARE_API_TOKEN` | Cloudflare | `deploy-worker.yml`, `deploy-frontend.yml` | Sì |
| 2 | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare | `deploy-worker.yml`, `deploy-frontend.yml` | Sì |
| 3 | `R2_ACCOUNT_ID` | Cloudflare R2 | `etl-weekly.yml`, `etl-monthly.yml`, `etl-annual.yml` | Sì |
| 4 | `R2_ACCESS_KEY_ID` | Cloudflare R2 | `etl-weekly.yml`, `etl-monthly.yml`, `etl-annual.yml` | Sì |
| 5 | `R2_SECRET_ACCESS_KEY` | Cloudflare R2 | `etl-weekly.yml`, `etl-monthly.yml`, `etl-annual.yml` | Sì |
| 6 | `WORKER_ADMIN_TOKEN` | Arbitrario | `etl-weekly.yml`, `etl-monthly.yml`, `etl-annual.yml` | Sì (per cache purge) |

---

## 2. Come ottenere ciascun secret

### 2.1 CLOUDFLARE_API_TOKEN

Token API per `wrangler` (deploy worker e frontend su Cloudflare).

**Procedura**:

1. Accedi alla dashboard Cloudflare AgID
2. Profile icon → My Profile → API Tokens → Create Token
3. Usa il template **"Edit Cloudflare Workers"** (raccomandato) oppure crea custom con permessi:
   - Account → Workers Scripts → Edit
   - Account → Workers Routes → Edit
   - Account → Cloudflare Pages → Edit (se si usa Pages per frontend)
   - Account → Account Settings → Read
   - Zone → Workers Routes → Edit (sulla zona di deploy)
4. Copia il token (visibile **una sola volta**)

**Validità**: token long-lived (no scadenza) oppure con scadenza personalizzata. Raccomandato: 1 anno con rotazione.

**Documentazione**: https://developers.cloudflare.com/fundamentals/api/get-started/create-token/

---

### 2.2 CLOUDFLARE_ACCOUNT_ID

ID univoco dell'account Cloudflare AgID.

**Procedura**:

1. Dashboard Cloudflare AgID
2. Sidebar destra (in qualsiasi pagina del dominio), sezione API
3. Copia il valore `Account ID` (formato esadecimale, 32 caratteri)

**Sensibilità**: non è strettamente segreto (è solo un identificatore), ma per convenzione si tiene come secret.

---

### 2.3 R2_ACCOUNT_ID

Identico a `CLOUDFLARE_ACCOUNT_ID` ma usato nei workflow ETL via Python `boto3` (formato S3-compatible).

**Valore**: stesso di `CLOUDFLARE_ACCOUNT_ID`.

Nota: il workflow lo tiene separato per chiarezza semantica e per consentire eventualmente account separati in futuro.

---

### 2.4 R2_ACCESS_KEY_ID e R2_SECRET_ACCESS_KEY

Coppia di credenziali S3-compatible per R2.

**Procedura**:

1. Dashboard Cloudflare AgID → R2 → Manage R2 API tokens
2. Create API token
3. Permessi: `Object Read & Write` sul bucket specifico (es. `cruscotto-italia-data` o il nome AgID equivalente)
4. Copia entrambi i valori (visibili **una sola volta**)

**Documentazione**: https://developers.cloudflare.com/r2/api/s3/tokens/

---

### 2.5 WORKER_ADMIN_TOKEN

Bearer token arbitrario per autenticare le chiamate ETL all'endpoint /admin/cache/purge del worker MCP. Viene chiamato a fine pipeline ETL per invalidare la cache KV del worker.

Generazione: produce una stringa casuale lunga (32+ caratteri) con questo comando shell:

    openssl rand -base64 48

Configurazione: lo stesso valore va impostato in 2 posti:

- GitHub Secrets, chiave WORKER_ADMIN_TOKEN (questo repo)
- Cloudflare Worker secrets, comando: wrangler secret put WORKER_ADMIN_TOKEN

Il worker valida l'header Authorization: Bearer <token> ricevuto.

Rotazione: si può ruotare in autonomia generando un nuovo valore e aggiornandolo nei 2 punti. Nessuna dipendenza da Cloudflare API.

---

## 3. Come aggiungere i secrets al repo GitHub

1. Vai su https://github.com/AgID/cruscotto-italia/settings/secrets/actions
2. Click New repository secret
3. Name: nome esatto dalla tabella (es. CLOUDFLARE_API_TOKEN)
4. Secret: incolla il valore
5. Save

Ripeti per tutti e 6.

Permessi richiesti: solo i ruoli Admin del repo possono creare/modificare secrets.

---

## 4. Test post-setup

Dopo aver popolato tutti e 6 i secrets, test minimo:

1. Vai su Actions, seleziona un workflow (es. etl-weekly), Run workflow (dispatch manuale)
2. Verifica i log: se i secrets sono configurati correttamente, il primo step passa e il secondo non solleva errori di credenziali
3. Se l'ETL gira fino in fondo e scrive su R2 il setup è completato

---

## 5. Rotazione e revoca

| Secret | Frequenza rotazione consigliata | Come revocare |
|---|---|---|
| CLOUDFLARE_API_TOKEN | 12 mesi | Cloudflare Dashboard, My Profile, API Tokens, Roll/Delete |
| R2_ACCESS_KEY_ID/SECRET | 12 mesi | Cloudflare R2, Manage API tokens, Delete |
| WORKER_ADMIN_TOKEN | 6 mesi o on-demand | Genera nuovo valore e aggiornalo nei 2 punti |

In caso di sospetto compromesso: revoca immediatamente su Cloudflare, ruota il secret, fai redeploy del worker se applicabile.

---

## 6. Riferimenti

- Workflow definitions in `.github/workflows/`
- Architettura completa in `docs/INFRASTRUCTURE.md`
- Secret locali al server (htpasswd, env files, PAT GitHub embedded, TLS) in `docs/SERVER-INFRA.md`
- Documento handoff migrazione AgID (storia del progetto): MIGRAZIONE_AGID_HANDOFF.md, fornito separatamente al team AgID, non versionato in questo repo
