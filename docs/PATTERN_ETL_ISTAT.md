# Pattern Pull-Artifact per ETL ISTAT — architettura e governance

> Documento di riferimento per chi deve auditare, manutenere o estendere
> la pipeline ETL di Cruscotto Italia per le fonti istituzionali ISTAT.

## 1. Problema da risolvere

L'infrastruttura Cruscotto Italia è ospitata su **VM AgID FastWeb** (in
produzione dal 19/05/2026) e prima ancora era su una VM Aruba self-hosted.
Entrambe hanno indirizzi IP statici. ISTAT, dal proprio portale
`esploradati.istat.it`, applica un **rate-limiting host-based aggressivo**
sui propri endpoint SDMX: dopo qualche decina di richieste consecutive
ad alta concorrenza, l'IP di origine entra in una lista nera per
giorni/settimane.

In data 2026-05-15 l'IP Aruba di Cruscotto Italia è stato bannato durante
un test su `etl.sources.asia` (39 chiamate consecutive). Da allora, le
ETL ISTAT non possono più girare da quella VM senza incontrare 503/timeout
sistematici.

Per le ETL **non ISTAT** (ANAC, BDAP-MEF, MIMIT, SIOPE, RUNTS, AGCOM, ecc.)
il problema non si pone: girano normalmente in cron locale sulla VM.

## 2. Soluzione: pattern Pull-Artifact

### Idea base

Spostare l'esecuzione delle ETL ISTAT su **GitHub Actions runner
`ubuntu-latest`**, che hanno IP Azure freschi e diversi a ogni run. L'ETL
genera shard JSON sul runner; la VM li scarica via GitHub API quando
serve, poi li applica al proprio filesystem.

### Schema completo

```
┌─────────────────────────────────────────────────────────────────┐
│                       GitHub Actions                            │
│                                                                 │
│  Runner ubuntu-latest (IP Azure fresh)                          │
│  ┌───────────────────────────────────────────────┐              │
│  │ 1. Checkout repo AgID/cruscotto-italia        │              │
│  │ 2. pip install -r etl/requirements.txt        │              │
│  │ 3. python -m etl.sources.anagrafica           │              │
│  │    → data/lookup/comuni-bundle.json           │              │
│  │ 4. python -m etl.sources.<source>             │              │
│  │    → data/<source>/<istat>.json (~7896 file)  │              │
│  │ 5. tar czf <source>-shards.tar.gz <source>/   │              │
│  │ 6. actions/upload-artifact                    │              │
│  │    name: <source>-shards                      │              │
│  │    retention-days: 1                          │              │
│  └───────────────────────────────────────────────┘              │
│                                                                 │
└────────────────────┬────────────────────────────────────────────┘
                     │ HTTPS REST API
                     │ Auth: PAT con scope Actions:Read/Write
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                  VM AgID FastWeb (produzione)                   │
│                                                                 │
│  cron quotidiano                                                │
│   └─ scripts/etl/pull_artifact.py --all                         │
│       ├─ discovery .github/workflows/etl-<source>-refresh.yml   │
│       ├─ GET /repos/.../actions/artifacts (latest per source)   │
│       ├─ check idempotency vs _artifact_meta.json (last id)     │
│       ├─ GET /artifacts/<id>/zip → /tmp/<source>.zip            │
│       ├─ unzip → tar.gz interno → extract in DATA_DIR/<source>/ │
│       ├─ DELETE /artifacts/<id> (cleanup attivo)                │
│       └─ update _artifact_meta.json                             │
│                                                                 │
│  Filesystem: /var/www/cruscotto-italia/data/<source>/*.json     │
│  Servito da nginx in HTTPS al Worker MCP (DATA_BASE_URL)        │
└─────────────────────────────────────────────────────────────────┘
```

### Differenze rispetto ad alternative considerate

**Alternativa A — VPN/proxy uscente con IP rotante**
- Costo: licenze VPN, configurazione di rete su VM AgID
- Rischio: ISTAT può bannare anche IP VPN se concentrati
- Scartata: aumenta superficie operativa senza eliminare il problema

**Alternativa B — Self-hosted runner con IP residenziale Piersoft**
- Funziona ma viola il principio "tutto sotto AgID" richiesto dall'audit
- Scartata: introduce dipendenza dall'infrastruttura personale

**Alternativa C — `--target=local-repo` (committa shard direttamente)**
- Era il pattern del defunto `etl-pendolarismo-bootstrap.yml`
- Gonfia il git history di centinaia di MB di JSON a ogni run
- Richiede `permissions: contents: write` sul workflow
- Scartata: gonfia repo, perde la separazione codice/dati

**Alternativa D — Push diretto da Actions a R2 Cloudflare**
- Era il pattern del defunto `etl-asia-bootstrap.yml`
- Account AgID Cloudflare NON può creare bucket R2 (piano corrente)
- Reintroducerebbe dipendenza R2 piersoft, contraddicendo refactor 16/16
- Scartata: incompatibile con infrastruttura cloud AgID

**Pull-Artifact vince perché**:
- Niente credenziali R2 nel workflow (zero env sensibili)
- Niente push lato Actions verso infrastruttura terza
- VM AgID è l'unico endpoint di scrittura sul filesystem produttivo
- Audit trail completo nei log Actions + log strutturati di `pull_artifact.py`

## 3. Quando usare questo pattern

**SI** se l'ETL legge da:
- Endpoint `*.istat.it` (in particolare `esploradati.istat.it` SDMX)
- Endpoint che ha già dato 503 ripetuti a IP della VM
- Endpoint con rate-limiting host-based dichiarato in TOS

**NO** se l'ETL legge da:
- Bulk file su CDN pubblici (ANAC, BDAP-MEF, AGCOM ArcGIS, MIMIT)
- Portali con rate-limiting per-request (non per-IP)
- Cron interno della VM che già gira normalmente

Per gli ETL "NO" usare i 4 workflow ricorrenti tradizionali
(`etl-daily/weekly/monthly/annual.yml`) che eseguono in cron locale
sulla VM via lo script in `deploy/cron/cruscotto-etl`.

## 4. Naming e contratti

### Contratto del workflow

Nome file: **`.github/workflows/etl-<source>-refresh.yml`**

Dove `<source>` è il nome del modulo Python sotto `etl/sources/` (lowercase,
underscore ammessi). Esempio: `etl-istat_profilo-refresh.yml` →
`etl/sources/istat_profilo.py`.

Il workflow deve:
1. avere trigger `workflow_dispatch` (manuale) ed eventualmente `schedule:` cron
2. produrre un artifact `actions/upload-artifact@v4` con `name: <source>-shards`
3. il contenuto dell'artifact deve essere un file tar.gz contenente la cartella `<source>/` con dentro gli shard `<istat>.json`
4. avere `retention-days: 1` (minimizzazione esposizione, vedi sez. 5)
5. usare `permissions: contents: read` (workflow non scrive sul repo)

### Contratto dello script pull

`scripts/etl/pull_artifact.py` su VM:
- discovery dinamico: matcha `etl-<source>-refresh.yml` con regex
- per ogni source: GET ultimo artifact, confronta con `_artifact_meta.json`
  per idempotency, scarica solo se nuovo, estrae in `DATA_DIR/<source>/`
- elimina l'artifact remoto dopo download (cleanup attivo)
- exit code: 0 = OK, 2 = parziale, 4 = errore PAT

### Modalità di invocazione

```bash
# Tutti i workflow ETL ISTAT, sequenziale, con cleanup
GITHUB_TOKEN=$AGID_PAT python3 scripts/etl/pull_artifact.py --all

# Singolo ETL (per ri-run mirato)
GITHUB_TOKEN=$AGID_PAT python3 scripts/etl/pull_artifact.py istat_profilo

# Dry-run (debug)
GITHUB_TOKEN=$AGID_PAT python3 scripts/etl/pull_artifact.py --all --dry-run

# Solo alcuni ETL (whitelist)
GITHUB_TOKEN=$AGID_PAT python3 scripts/etl/pull_artifact.py --all --include=istat_profilo,pendolarismo

# Tutti tranne uno (blacklist)
GITHUB_TOKEN=$AGID_PAT python3 scripts/etl/pull_artifact.py --all --exclude=asia
```

## 5. Privacy e governance

### Tipo di dato trattato

I dataset ISTAT processati da questi workflow sono **open data CC BY 3.0 IT**
(licenza dichiarata da ISTAT su `dati.istat.it`). In particolare:

- ASIA UL: imprese e addetti per ATECO + classi dimensionali. Aggregato
  per comune, **nessun PII**.
- Pendolarismo Censimento permanente: matrice OD origine → destinazione
  aggregata, **nessun PII**.
- Profilo del comune: indicatori demografici/socio-economici aggregati,
  **nessun PII**.

In nessun caso questi workflow trattano dati personali ai sensi del GDPR.

### Esposizione degli artifact

Gli artifact GitHub:
- sono ospitati su `GitHub Inc.` (Stati Uniti)
- sono accessibili **solo** ai collaboratori del repo
  `AgID/cruscotto-italia` con scope `Actions: Read`
- il repo è **privato** (non public)
- hanno `retention-days: 1` impostato a livello workflow
- vengono **eliminati attivamente** da `pull_artifact.py` subito dopo
  il download riuscito

Il TTL effettivo di esposizione è quindi tipicamente di pochi minuti
(tempo tra fine workflow Actions e prossima esecuzione cron VM), non
di un giorno intero.

### Audit trail

Tre livelli di tracciamento:
- **GitHub Actions log**: ogni run di workflow è registrato con timestamp,
  utente che lo ha avviato, IP runner, output completo dei job.
- **JSON-line log di pull_artifact.py**: ogni operazione (discovery,
  download, estrazione, cancellazione) emette un record JSON timestamp +
  event + dati. Loggabile su VM in `/var/log/cruscotto-etl/`.
- **Idempotency file** `DATA_DIR/<source>/_artifact_meta.json`:
  ricorda l'ultimo artifact ID scaricato. Permette di verificare cosa
  è stato applicato e quando.

### Token di accesso

Il workflow non richiede credenziali esterne (il `GITHUB_TOKEN` automatico
basta per `permissions: contents: read` + `actions/upload-artifact`).

Lo script `pull_artifact.py` su VM richiede un **fine-grained PAT** con:
- repo `AgID/cruscotto-italia`
- scope `Actions: Read and write` (Read per download, Write per delete)
- expiration consigliata 90 giorni con rotazione documentata
- storage su VM in `/etc/cruscotto-etl.env` (chmod 600, owner root)

## 6. Lista workflow attivi (al 2026-05-17)

| Workflow | Source | Cadenza upstream | Timeout | Pre-step anagrafica |
|---|---|---|---|---|
| `etl-istat_profilo-refresh.yml` | `istat_profilo` | annuale (dic) | 30 min | sì |
| `etl-pendolarismo-refresh.yml` | `pendolarismo` | annuale (ott) | 30 min | no |
| `etl-asia-refresh.yml` | `asia` | annuale (dic) | 180 min | sì |

Schedule cron commentati per default: trigger è manuale (`workflow_dispatch`)
finché non si decide la policy di automazione AgID.

## 7. Come aggiungere un nuovo ETL ISTAT a questo pattern

Quando emerge un nuovo dataset ISTAT che soffre del problema rate-limiting:

1. Refactor del modulo `etl/sources/<source>.py` per renderlo local-first
   (legge da `local_lookup`, scrive in `--outdir`, niente push R2)
2. Copia template da `etl-istat_profilo-refresh.yml` (con pre-step anagrafica
   se l'ETL ne ha bisogno) o da `etl-pendolarismo-refresh.yml` (no pre-step)
3. Adatta nomi: `<source>`, percorsi `data/<source>/`, artifact name
   `<source>-shards`
4. Imposta `timeout-minutes` realistico (test in dry-run prima)
5. Commit + push. La discovery `pull_artifact.py --all` lo trova
   automaticamente alla prossima esecuzione cron VM.

Niente modifica a `pull_artifact.py` necessaria.

## 8. Riferimenti incrociati

- `scripts/etl/pull_artifact.py` — implementazione lato VM
- `.github/workflows/etl-<source>-refresh.yml` — workflow Actions
- `etl/lib/local_lookup.py` — accesso filesystem locale per lookup
- `etl/lib/r2.py` — kill-switch storico (NO chiamanti residui post-refactor 16/16)
- `docs/INFRASTRUCTURE.md` — architettura VM AgID FastWeb
- `docs/SECRETS.md` — gestione token e credenziali

## 9. Open items / TODO post-deploy

Annotazioni di debito tecnico/documentazione emerse durante la
preparazione audit ma da affrontare **dopo il go-live del 19/05/2026**,
per non destabilizzare il deploy.

### 9.1 Fonte IPA (IndicePA AgID) non dichiarata nei conteggi

Scoperto il 2026-05-17 durante la review del primo workflow Actions
`etl-istat_profilo-refresh.yml`: il pre-step `anagrafica.py` scarica
dati dal portale **IPA — Indice della Pubblica Amministrazione** (AgID),
in particolare il dataset `enti` via CKAN API:

- Endpoint: `https://indicepa.gov.it/ipa-dati/api/3/action`
- Modalità: `package_show` → `datastore/dump/{resource_id}?bom=True`
- Licenza: **CC-BY 4.0** (cfr. `docs/data-licenses.md` riga 11)
- Uso: arricchimento di `data/lookup/comuni-bundle.json` con
  `codice_fiscale` ente comunale e altri metadati IPA (`Codice_IPA`
  come chiave di match)
- Implementazione: `etl/sources/anagrafica.py` linee 52-130 + sezione
  build (linea 311)

**Cosa manca per dichiarare correttamente questa fonte**:

| File | Modifica richiesta |
|---|---|
| `worker/src/tools/mcp_info.ts` | Aggiungere `sources.ipa: { canonical, license, datasets }` + incrementare counter `datasets: 23→24` e `institutions: 15→16` + aggiungere "IPA AgID" nella description |
| `worker/src/http.ts` (landing) | Aggiungere "IPA (Indice PA AgID)" alla long string di fonti elencate al paragrafo `<p class="lead">` |
| `frontend/index.html` | Aggiungere card / aggiornare badge conteggio fonti+istituzioni |
| `frontend/about.html` | Idem (conteggio fonti+istituzioni) |
| `docs/PATTERN_ETL_ISTAT.md` (questo file) | Aggiungere riga IPA nella tabella sezione 6 quando esisterà workflow dedicato (in realtà IPA NON ha workflow dedicato: viene fetchato come parte di `anagrafica` ogni volta, e `anagrafica` gira come pre-step nei workflow ETL — è una dipendenza trasversale, non un ETL autonomo) |

**Stato di rischio audit**: BASSO. La fonte è documentata in
`docs/data-licenses.md` e nel codice è citata correttamente come "IPA"
("Indice della Pubblica Amministrazione"). L'omissione riguarda solo
i contatori metadati esposti dal Worker MCP e dal frontend. Non c'è
trattamento opaco di dati: tutto open data con licenza CC-BY 4.0.

**Priorità di fix**: dopo il go-live, in una sessione dedicata. Comporta:
- 1 commit nel Worker (rebuild + redeploy)
- 1 commit nel frontend (rebuild deploy nginx)
- aggiornamento di questa doc per chiudere il TODO
