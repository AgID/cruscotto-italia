# Sicurezza del Worker MCP Cruscotto Italia

> Conformità al paper CERT-AgID "Analisi di sicurezza su implementazioni
> MCP open source" — Aprile 2026
> ([link al paper](https://www.agid.gov.it/sites/agid/files/2026-04/Paper%20CERTAGID%20Aprile%2026.pdf))

Il presente documento descrive come l'implementazione del Model Context
Protocol server di Cruscotto Italia
(`https://cruscotto-italia-mcp.dati.gov.it/mcp`) implementa le 4
raccomandazioni formali emanate dal CERT-AgID nel paper di aprile 2026,
relative alla mitigazione delle vulnerabilità di tipo Server-Side
Request Forgery (SSRF) e simili negli MCP server pubblici.

## Sintesi conformità

| # | Raccomandazione CERT-AgID | Stato Cruscotto MCP |
|---|---|---|
| 1 | Validazione vincolante (no fallback) | ✅ Implementata |
| 2 | Allowlist restrittive | ✅ Implementata (allowlist di 1 elemento) |
| 3 | Principio di minimo privilegio | ✅ Implementata by-design |
| 4 | Controllo e monitoraggio | ✅ Implementata (rate-limit + analytics) |

## 1. Validazione vincolante

> *"Ogni parametro deve essere verificato prima dell'esecuzione. I filtri
> devono essere bloccanti: è necessario eliminare ogni logica di
> 'riserva' (fallback) che possa aggirare i controlli in caso di errore."*

### Implementazione

Tutti gli argomenti dei tool MCP vengono validati **server-side** prima
di essere usati per costruire chiavi di accesso a dati o filtri di
query. La validazione è centralizzata nel modulo
[`worker/src/lib/validate.ts`](../worker/src/lib/validate.ts) e applicata
dagli handler dei tool **all'inizio dell'esecuzione**.

I JSON Schema dichiarati negli `inputSchema` dei tool MCP servono
unicamente da **documentazione per il client LLM** (tools/list response):
**non sono utilizzati come meccanismo di sicurezza**, dato che il
dispatcher JSON-RPC (`worker/src/mcp.ts`) non li enforce.

### Pattern di validazione

Esempio canonico (tool `comune_kpi`):

```typescript
const istatCode = args.istat_code !== undefined
  ? validateIstatCode(args.istat_code)
  : undefined;
const denominazione = args.denominazione !== undefined
  ? validateDenominazione(args.denominazione)
  : undefined;
```

`validateIstatCode` lancia un `Error` se l'input non rispetta esattamente
il pattern `^\d{6}$`. Il dispatcher cattura l'errore e ritorna al client
un errore JSON-RPC `-32602` (Invalid params).

### Validatori implementati

| Funzione | Pattern accettato | Lunghezza |
|---|---|---|
| `validateIstatCode` | `^\d{6}$` | 6 char esatte |
| `validateDenominazione` | `^[A-Za-zÀ-ÿ0-9' \-,.()/]+$` | 1-80 char |
| `validateQuery` | `^[A-Za-zÀ-ÿ0-9' \-,.]+$` | 3-50 char |
| `validateOdonimo` | `^[A-Za-zÀ-ÿ0-9' \-,.()/]+$` | 0-120 char |
| `validateCivico` | `^[A-Za-z0-9/ \-]+$` | 0-15 char |
| `validateLimit` | `int in [min, max]` | – |
| `validateFetchId` | `^\d{6}$` | 6 char esatte |

### No fallback insicuri

Eliminato il pattern `padStart(6, "0").slice(0, 6)` che era presente nel
tool `fetch` (compat OpenAI). Esempio precedente potenzialmente vulnerabile:

```typescript
// PRIMA (rimosso): fallback silenzioso
const istatCode = id.padStart(6, "0").slice(0, 6);

// DOPO (validazione vincolante): lancia Error se input invalido
const istatCode = validateFetchId(args.id);
```

### Difesa in profondità

Quando un codice ISTAT viene risolto a partire da una `denominazione`
(via lookup bundle), il valore risolto è ri-validato prima di essere
concatenato in una R2 key. Questo protegge anche da un'eventuale
compromissione del bundle anagrafica:

```typescript
resolvedIstat = match.istat_code;
validateIstatCode(resolvedIstat, "resolved_istat_code");
const shardKey = `dashboard/${resolvedIstat}.json`;
```

### Anti path-traversal

I parametri che accettano `.` e `/` (denominazione, odonimo) hanno un
check aggiuntivo che rifiuta le sequenze `..` e `//`:

```typescript
if (trimmed.includes("..") || trimmed.includes("//")) {
  throw new Error("Parameter contains forbidden sequence.");
}
```

## 2. Allowlist restrittive

> *"Per i tool che effettuano richieste di rete, bisogna limitare gli
> accessi esclusivamente a domini, protocolli e formati predefiniti."*

### Implementazione

**Nessun tool MCP del Worker Cruscotto accetta un URL come parametro**.
Tutti gli URL di fetch HTTP esterno sono costruiti server-side a partire
dalla costante `env.DATA_BASE_URL`, definita come variabile in
[`worker/wrangler.toml`](../worker/wrangler.toml):

```toml
DATA_BASE_URL = "https://cruscotto-italia.dati.gov.it/data"
```

L'allowlist di domini ammessi è di **un solo elemento** (il backend
istituzionale AgID), gestito a livello di configurazione del Worker e
non modificabile in runtime.

### Audit dei parametri tool

| Tool | Parametri accettati | URL accettato? |
|---|---|---|
| `mcp_info` | nessuno | ❌ no |
| `search_comune` | `query` (string), `limit` (int) | ❌ no |
| `comune_kpi` | `istat_code` (6 digits), `denominazione` (string) | ❌ no |
| `comune_dashboard` | `istat_code` (6 digits), `denominazione` (string) | ❌ no |
| `anncsu_civico_search` | `istat_code`, `odonimo`, `civico`, `limit` | ❌ no |
| `search` (OpenAI compat) | `query` (string) | ❌ no |
| `fetch` (OpenAI compat) | `id` (ISTAT 6 digits) | ❌ no |

### Path traversal mitigato

Per gli endpoint REST diretti
(`GET /data/anncsu_full/<istat>.json`, `GET /skills/<file>.zip`)
la validazione è applicata a livello di **router** ([`worker/src/index.ts`](../worker/src/index.ts))
tramite regex restrittive **prima** che la chiave venga costruita:

```typescript
// /data/anncsu_full/<istat>.json
const annFullMatch = url.pathname.match(/^\/data\/anncsu_full\/(\d{6})\.json$/);

// /skills/<name>.zip
const skillMatch = url.pathname.match(/^\/skills\/([a-zA-Z0-9._-]+\.zip)$/);
```

Path che non matchano queste regex ritornano 404, **prima** di qualsiasi
operazione di rete o accesso a storage.

## 3. Principio di minimo privilegio

> *"Ogni tool deve eseguire un'operazione circoscritta. Bisogna evitare
> funzioni generiche che possano essere riutilizzate per scopi diversi
> da quelli previsti."*

### Implementazione

Ogni tool MCP del Worker Cruscotto fa **una sola operazione tematica
circoscritta**, restituendo dati pre-aggregati dallo strato ETL Python
che gira separatamente sulla VM AgID. Non esistono tool generici tipo
`fetch_url` o `query_database`.

### Catalogo tool (versione 0.14.1)

| Tool | Operazione |
|---|---|
| `mcp_info` | Metadata server + freshness fonti |
| `search_comune` | Match testuale su anagrafica comuni ISTAT |
| `comune_kpi` | Lettura KPI sintetici di un comune (~620 token) |
| `comune_dashboard` | Lettura vista completa di un comune (sezioni federate) |
| `anncsu_civico_search` | Query civici/strade ANNCSU di un comune |
| `search` / `fetch` | Adapter ChatGPT MCP custom connector |

### Read-only

Tutti i tool sono **read-only**: nessuna operazione di scrittura, di
modifica dati, o di esecuzione comando. Lo strato di scrittura su R2
e' separato (ETL Python) e non e' accessibile via MCP.

## 4. Controllo e monitoraggio

> *"Introdurre autenticazione obbligatoria per l'invocazione dei tool,
> sistemi di rate limiting e log delle chiamate per individuare anomalie
> in tempo reale."*

### Implementazione

#### Rate limiting

Attivo su tutti gli endpoint eccetto `/health` tramite binding nativo
Cloudflare Rate Limiting (`MCP_RATE_LIMITER`):

```toml
[[ratelimits]]
name = "MCP_RATE_LIMITER"
namespace_id = "1001"
simple = { limit = 60, period = 60 }
```

**60 richieste / minuto / IP**, sliding window, sincronizzazione
cross-region. Richieste oltre la soglia ricevono HTTP 429 con
header `Retry-After: 60`. Implementazione in
[`worker/src/lib/ratelimit.ts`](../worker/src/lib/ratelimit.ts).

#### Logging strutturato

Ogni chiamata tool viene tracciata in **3 counter KV separati**:

| Prefix KV | Cosa traccia |
|---|---|
| `analytics:YYYY-MM-DD:<tool>:<istat>:<client>` | Chiamate con successo |
| `analytics-err:YYYY-MM-DD:<tool>:<client>` | Errori applicativi |
| `analytics-validation-err:YYYY-MM-DD:<tool>:<client>` | **Tentativi input non validi** (CERT-AgID rec. 4) |

I counter `analytics-validation-err:*` sono particolarmente rilevanti per
il rilevamento di anomalie: un alto numero di tentativi di input
malformati su un client/IP specifico è indice di tentativo di
exploitation (SSRF probing, path traversal, injection).

TTL: 35 giorni. Privacy: **nessun IP grezzo, nessun User-Agent grezzo**
viene memorizzato; il campo `<client>` è categorico (claude / chatgpt /
cursor / python / curl / browser / other).

Pipeline analytics consolidata su disco (statici aggregati) ogni 4 AM
via cron VM, vedi
[`scripts/analytics/mcp_stats_fetcher.py`](../scripts/analytics/mcp_stats_fetcher.py).

#### Autenticazione

Il Worker MCP di Cruscotto Italia espone **dati pubblici aperti** (CC BY
4.0 / IODL 2.0 / HVD UE 2023/138). Per coerenza con la natura aperta
del servizio (rif. art. 52 c.2 D.Lgs 82/2005 CAD: "open by default"),
non è richiesta autenticazione per consultazione.

L'autenticazione obbligatoria descritta dalla raccomandazione CERT-AgID
trova applicazione su MCP server che espongono dati riservati o
operazioni privilegiate (es. write su sistemi PA). Non si applica al
caso d'uso "publish open data via MCP".

Il backend di lettura R2 da `DATA_BASE_URL` è protetto Basic Auth
(transitoria durante migrazione VM AgID), come hardening difensivo per
ridurre attack surface dell'origin server. Il Basic Auth header è
iniettato server-side dal Worker, non è esposto al client.

## Riferimenti

- [Paper CERT-AgID Aprile 2026 — Analisi di sicurezza su implementazioni MCP open source](https://www.agid.gov.it/sites/agid/files/2026-04/Paper%20CERTAGID%20Aprile%2026.pdf)
- [Paper CERT-AgID Febbraio 2026 — Coerenza narrativa e vincoli di sicurezza negli LLM](https://www.agid.gov.it/sites/agid/files/2026-02/Coerenza_narrativa_e_vincoli_di_sicurezza_negli_LLM_che_controllano_gli_accessi_nei_sistemi_della_PA.pdf)
- [Linee Guida Open Data AgID — Det. 183/2023](https://www.agid.gov.it/it/dati/open-data)
- [Codice dell'Amministrazione Digitale — D.Lgs 82/2005 art. 52 c.2](https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:2005;82)
- [Model Context Protocol — specifications](https://modelcontextprotocol.io)

## Audit history

- **2026-05-17**: implementazione iniziale conformità CERT-AgID
  - `worker/src/lib/validate.ts` — modulo validazione centralizzato
  - Patch handler 7 tool con validazione vincolante runtime
  - Distinzione `validation_error` da `error` applicativo in `mcp.ts`
  - Counter KV dedicato `analytics-validation-err:*`
  - Anti path-traversal su parametri stringa con caratteri ammessi
