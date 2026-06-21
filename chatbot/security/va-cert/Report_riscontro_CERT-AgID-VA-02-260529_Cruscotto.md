# Report di riscontro — Vulnerability Assessment CERT-AgID-VA-02-260529

**Oggetto:** Cruscotto Italia — `cruscotto-italia.dati.gov.it`
**Riferimento VA:** CERT-AgID-VA-02-260529 del 29/05/2026
**Asset:** `cruscotto-italia.dati.gov.it`, `cruscotto-italia-mcp.agid.workers.dev` (Cloudflare Worker), IP `89.97.159.17`
**Data riscontro:** 29/05/2026
**A cura di:** Francesco Piero Paolicelli — sviluppo e gestione Cruscotto Italia
**Destinatari:** CERT-AgID; Antonio Romano (AgID, referente privacy); Antonio Rotundo (AgID, service owner)
**Riferimenti remediation:**
- Frontend: commit `3b91394` e `3ba8920` su `AgID/cruscotto-italia-internal` (branch `main`).
- Worker MCP: commit `feat(worker): rate limit cost-weighted ...` su `AgID/cruscotto-italia-internal`; deploy Cloudflare versione `119e2280-64cb-43ed-b26e-545c66039556`.
- Reverse proxy: modifiche live su VM AgID in `/etc/nginx/conf.d/` e `/etc/nginx/snippets/`, con backup datati conservati sull'host.

---

## 1. Sintesi esecutiva

Sono stati presi in carico **tutti i 9 rilievi** del VA. L'analisi tecnica ha portato a:

- **6 rilievi accolti e risolti** — #1, #2, #3, #4 (mitigato), #6, #7;
- **1 rilievo riclassificato** come scelta intenzionale di hardening — #5;
- **1 rilievo respinto** come vulnerabilità e riqualificato — #8;
- **1 rilievo riqualificato** come scelta di processo documentata — #9.

In fase di verifica sono stati inoltre **individuati e risolti proattivamente 2 problemi** non presenti nel VA:

- l'endpoint statistiche `/simba-stats/` del servizio SIMBA (stessa VM) condivideva il file credenziali della blindatura pre-rilascio (stessa classe del #3);
- l'account amministrativo `admin` del file statistiche conservava ancora la password debole condivisa.

Tutte le correzioni sono state applicate e **verificate** (riscontri puntuali nelle sezioni di dettaglio). Le credenziali amministrative sono state separate dalla blindatura e ruotate; nessun dato è stato deployato senza validazione (`tsc --noEmit`, `nginx -t`).

---

## 2. Legenda classificazione

- **Accolto** — rilievo condiviso, corretto.
- **Mitigato** — rilievo condiviso; la correzione completa richiede refactoring dedicato, applicato un hardening sostanziale con limitazione residua documentata.
- **Riclassificato** — il comportamento esiste ma è una scelta intenzionale; natura/impatto differiscono da quanto indicato.
- **Respinto** — non costituisce vulnerabilità di sicurezza.

---

## 3. Tabella riepilogativa

| # | Liv. | Rilievo (sintesi) | Classificazione | Stato | Azione principale |
|---|------|-------------------|-----------------|-------|-------------------|
| 1 | Alto | XSS non persistente su `comune.html?istat=` | Accolto | Risolto | Escape dei messaggi d'errore + validazione `istat` lato client + messaggio pulito |
| 2 | Alto | DoS MCP: rate limit non proporzionato al costo | Accolto | Risolto | Rate limit *cost-weighted* per tool e per passthrough pesanti |
| 3 | Medio | Pannello `/stats/` esposto | Accolto (riformulato) | Risolto | Credenziali admin dedicate, scollegate dalla blindatura |
| 4 | Medio | CSP mancante o non sicura | Accolto | Mitigato | CSP rafforzata; `unsafe-inline` su script come limitazione documentata |
| 5 | Basso | Reset TCP/TLS invece di 404 | Riclassificato | Tenuto | `return 444` anti-scanner intenzionale |
| 6 | Basso | Info disclosure su `/assets/` `/vendor/` (403) | Accolto | Risolto | Directory statiche → 404 |
| 7 | Basso | Metodi HTTP inutilizzati abilitati | Accolto | Risolto | Solo `GET`/`HEAD`/`POST`, resto → 405 |
| 8 | Info | `/LICENSE.txt` pubblicamente accessibile | Respinto | Tenuto | Esposizione dovuta dalla licenza AGPL-3.0 |
| 9 | Info | Sorgente dichiarata AGPL ma link GitHub 404 | Riqualificato | Pianificato | Repo privati per scelta fino al go-live; sorgente pubblicato al rilascio |
| A | — | *(aggiuntivo)* `/simba-stats/` su credenziale di blindatura | Individuato in verifica | Risolto | Ripuntato al file credenziali admin dedicato |
| B | — | *(aggiuntivo)* Account `admin` con password debole | Individuato in verifica | Risolto | Password ruotata (bcrypt) |

---

## 4. Dettaglio dei rilievi

### #1 — XSS non persistente su `comune.html?istat=` *(Accolto, risolto)*
- **Riscontro VA:** un `istat` malformato (es. `?istat=<h1>XSS injection`) veniva riflesso e interpretato come HTML nella pagina d'errore.
- **Analisi:** il Worker MCP rifiuta l'input non conforme a `^\d{6}$` ma **rieccheggia il valore grezzo** nel messaggio d'errore; il frontend lo iniettava in `innerHTML`. Individuati **10 sink** in `comune.html` che scrivevano messaggi d'errore (`err.message` / `data.error`) in `innerHTML`.
- **Azione:**
  1. Introdotto un escape HTML applicato a tutti i 10 sink (sanitizzazione del valore riflesso).
  2. **Validazione di `istat` lato client** (`^\d{6}$`) *prima* della chiamata al Worker: input non valido → messaggio pulito *"Codice comune non valido"*, senza chiamata al server e **senza echo** dell'input.
  3. Nel `catch` generico, sostituito il dump tecnico grezzo con un **messaggio generico** all'utente; dettaglio tecnico solo in `console`.
- **Verifica post-fix (live):** `?istat=<h1>XSS injection` → il tag è mostrato come **testo** e intercettato a monte come *"Codice comune non valido"*; codice valido inesistente → messaggio generico; nessun echo dell'input in pagina.

### #2 — DoS server MCP: rate limit non proporzionato al costo *(Accolto, risolto)*
- **Riscontro VA:** il rate limit nativo conta solo il *numero* di richieste; tool pesanti (`mcp_info`, `comune_dashboard`, `anncsu_civico_search`) generavano molto traffico restando entro i 60 req/min (fino a 3 GB/min).
- **Analisi:** confermato che il tool `anncsu_civico_search` **clampa già** i risultati a max 500; il throughput da 3 GB/min osservato proviene in realtà dal **passthrough REST** `/data/anncsu_full/<istat>.json` (shard Roma ~50 MB), tariffato 1 solo token.
- **Azione (mitigazione di disponibilità):**
  1. Introdotto un meccanismo **cost-weighted**: ogni tool consuma N token (`comune_dashboard`=10, `mcp_info`=10, `anncsu_civico_search`=5, `censimento_sezione_search`/`search`/`fetch`=3–5, leggeri=1).
  2. Stesso costo applicato al passthrough `/data/anncsu_full/` (≈300 MB/min worst case contro i 3 GB/min precedenti).
  3. **Defense-in-depth del #1:** cap a 300 caratteri sul messaggio di errore di validazione, per i chiamanti API diretti.
- **Verifica post-fix (live):** `comune_kpi` (leggero) → HTTP 200; input di validazione abusivo (250 char) → messaggio d'errore limitato a 115 caratteri; deploy versione `119e2280`, binding R2/KV/rate-limit corretti.
- **Residuo opzionale:** `mcp_info` incorpora l'intero `manifest.json` (~13 MB); è un'ottimizzazione di contenuto pianificabile a parte, già mitigata sul piano DoS dal costo 10.

### #3 — Pannello `/stats/` esposto *(Accolto, riformulato e risolto)*
- **Riscontro VA:** pannello statistiche accessibile senza adeguati controlli.
- **Analisi:** `/stats/` **era già** protetto da `auth_basic`, ma puntava allo **stesso file credenziali** (`/etc/nginx/.htpasswd-stats`) usato dalla blindatura pre-rilascio del sito: chi accedeva al sito apriva anche le statistiche. Inoltre il file admin condiviso conteneva l'account di test di blindatura.
- **Azione:** `/stats/` ripuntato al file admin dedicato `/etc/nginx/.htpasswd-analytics`, da cui sono stati **rimossi gli account non amministrativi** (incl. quello di blindatura), lasciando il solo `admin`; vedi anche rilievi aggiuntivi A e B.
- **Verifica post-fix (live):** `agid:<REDACTED>` e `admin:<REDACTED>` → **401** su `/stats/`; accesso senza credenziali → 401; il resto del sito resta raggiungibile con le credenziali di blindatura (→ 200).

### #4 — Content-Security-Policy *(Accolto, mitigato)*
- **Riscontro VA:** CSP mancante o non sicura.
- **Analisi:** una CSP è presente ed è già restrittiva (`object-src 'none'`, `connect-src` mirata, nessun wildcard generico). La debolezza effettiva è `'unsafe-inline'` su `script-src`/`style-src`. La rimozione completa richiederebbe nonce/hash su un'unica `<script>` inline di grandi dimensioni **e** la conversione di 8 handler inline (`onclick`/`onchange`/`onerror`/`oninput`/`onload`), alcuni su elementi generati dinamicamente: refactoring dedicato a rischio regressione.
- **Azione:** scelta concordata di **hardening pragmatico** (coerente con il trattamento del rilievo CSP di SIMBA): aggiunti `base-uri 'none'` e `form-action 'self'`; `object-src 'none'` e `frame-ancestors 'self'` mantenuti. `'unsafe-inline'` su script è **conservato come limitazione accettata e documentata** in-place nella configurazione. **Rilevante:** il *sink* XSS riflesso effettivo è già stato chiuso col rilievo #1, quindi l'exploitabilità pratica residua è bassa. Rimozione di `unsafe-inline` via nonce/refactor inserita a roadmap.
- **Verifica post-fix (live):** header CSP di `comune.html` include `base-uri 'none'` e `form-action 'self'`; il servizio SIMBA (CSP propria) non è impattato.

### #5 — Reset TCP/TLS invece di 404 *(Riclassificato, tenuto)*
- **Riscontro VA:** richieste a numerosi path (es. `/admin/`, `/api/`) chiudono la connessione senza risposta HTTP valida.
- **Analisi:** comportamento prodotto da una regola **`return 444`** su un'espressione anti-scanner. È una scelta di hardening **intenzionale**: la chiusura della connessione non rivela informazioni (a differenza di un 404) e penalizza gli scanner automatici. Non costituisce vulnerabilità.
- **Azione:** mantenuto. Disponibilità a normalizzare a `404` qualora AgID preferisca risposte standard-compliant.

### #6 — Info disclosure su `/assets/` `/vendor/` (403) *(Accolto, risolto)*
- **Riscontro VA:** `/assets` → 301, `/assets/` → 403, `/vendor/` → 403 (rivelazione dell'esistenza delle directory).
- **Analisi:** comportamento di `try_files $uri $uri/ /index.html` per directory senza index (403 con autoindex off).
- **Azione:** aggiunta una location che restituisce **404** alle directory statiche (`assets|vendor|css|js|img|fonts`), mantenendo serviti i file al loro interno.
- **Verifica post-fix (live):** `/vendor/` `/assets/` `/css/` → **404**; `/vendor/chart.umd.min.js` → **200** (file ancora serviti).

### #7 — Metodi HTTP inutilizzati *(Accolto, risolto)*
- **Riscontro VA:** abilitati `GET, HEAD, OPTIONS, PUT, DELETE, POST, PATCH, CONNECT`.
- **Azione:** vincolo a livello server al solo set necessario: `GET`/`HEAD`/`POST`; ogni altro metodo → **405**. (`HEAD` mantenuto in quanto variante header-only di `GET`, necessaria a monitoring/caching.)
- **Verifica post-fix (live):** `PUT`, `DELETE`, `OPTIONS` su `/` → **405**.
- **Nota:** per la copertura delle richieste al **nudo IP** (non al `server_name`), il default server della VM andrebbe parimenti irrigidito; intervento infrastrutturale segnalato per un passo successivo.

### #8 — `/LICENSE.txt` pubblicamente accessibile *(Respinto)*
- **Riscontro VA:** `LICENSE.txt` accessibile pur non essendo linkato.
- **Analisi:** l'applicativo è distribuito sotto **AGPL-3.0**; l'esposizione del testo di licenza è **corretta e dovuta**, non una vulnerabilità. La sua rimozione contraddirebbe inoltre l'obbligo correlato al #9.
- **Azione:** mantenuto; al go-live sarà inoltre raggiungibile dal footer insieme al link al sorgente.

### #9 — Sorgente dichiarata AGPL ma link GitHub 404 *(Riqualificato, pianificato)*
- **Riscontro VA:** il footer dichiara AGPL-3.0 ma il link «GitHub» rimanda a un repository inesistente/privato.
- **Analisi e posizione:** i repository AgID di **SIMBA e Cruscotto Italia sono volutamente mantenuti privati fino al go-live**, in quanto i servizi sono attualmente in fase di pre-rilascio (sito blindato da Basic Auth, non ancora pubblico). L'obbligo AGPL §13 di offrire il *Corresponding Source* agli utenti che interagiscono con il programma via rete si attiva con il **rilascio pubblico**: in quel momento il sorgente sarà pubblicato (repository pubblico dedicato, ripulito da documentazione interna e segreti) e il link nel footer punterà ad esso.
- **Azione:** allineamento del link del footer al repository pubblico contestualmente al go-live. Fino ad allora la condizione è una **scelta di processo documentata**, non un'omissione.

---

## 5. Rilievi aggiuntivi individuati in fase di verifica

### A — `/simba-stats/` su credenziale di blindatura *(Risolto)*
- **Problema:** l'endpoint statistiche `/simba-stats/` del servizio SIMBA (stessa VM) puntava ancora a `/etc/nginx/.htpasswd-stats`, ossia al file della blindatura pre-rilascio: era quindi apribile con le credenziali di accesso generale al sito. Stessa classe del #3. *(Integra/corregge la nota del riscontro SIMBA VA-03 che indicava `/simba-stats/` come già protetto da Basic Auth dedicata.)*
- **Azione:** ripuntato a `/etc/nginx/.htpasswd-analytics` (file admin dedicato).
- **Verifica post-fix (live):** `agid:<REDACTED>`, `admin:<REDACTED>` e accesso anonimo → **401** su `https://chatbot.dati.gov.it/simba-stats/`.

### B — Account `admin` con password debole *(Risolto)*
- **Problema:** il file admin condiviso (`/etc/nginx/.htpasswd-analytics`) conteneva, oltre agli account amministrativi, l'utente di blindatura `agid` e l'account `admin` con la **stessa password debole** (`<REDACTED>`) della pre-release; la sola repointing del #3 non sarebbe bastata.
- **Azione:** rimossi gli account non necessari dal file admin (rimasto il solo `admin`); password di `admin` **ruotata** con hashing **bcrypt** (`htpasswd -B`).
- **Verifica post-fix (live):** `admin:<REDACTED>` → **401** su tutti i pannelli statistici.

---

## 6. Note tecniche e igiene

- **Separazione credenziali:** introdotta una netta separazione tra **blindatura pre-rilascio** (`.htpasswd-stats`, accesso generale al sito) e **accesso amministrativo** ai pannelli statistici (`.htpasswd-analytics`, unico account `admin` con password forte). Il file admin ora copre uniformemente `/stats/` (Cruscotto), `/analytics` e `/simba-stats/` (SIMBA).
- **Validazione prima del deploy:** ogni modifica al Worker è stata validata con `tsc --noEmit`; ogni modifica nginx con `nginx -t`, con backup datati e ripristino automatico in caso di test fallito.
- **Sorgenti immutabili:** la sanitizzazione del #1 è applicata **lato frontend**; i dati di origine e i messaggi del Worker non sono stati alterati oltre al cap difensivo sull'eco di input.
- **Riservatezza:** i messaggi di commit pubblici sono volutamente generici; il dettaglio finding-per-finding è contenuto unicamente in questo documento riservato.
- **Versionamento configurazione:** si raccomanda di conservare le configurazioni nginx aggiornate nel repository di deploy privato per tracciabilità e disaster recovery (i backup `.bak-va02*` sono attualmente sull'host).

---

## 7. Stato complessivo

Tutti i rilievi del VA risultano **chiusi, mitigati o motivatamente riclassificati/respinti**; il #9 è una **scelta di processo** (repository privati fino al go-live, pubblicazione del sorgente al rilascio). I due problemi aggiuntivi emersi in verifica sono stati anch'essi risolti. Si resta a disposizione per un **re-test di conferma** da parte del CERT-AgID.
