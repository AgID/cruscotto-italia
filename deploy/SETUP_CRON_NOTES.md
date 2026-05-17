# Setup cron Cruscotto Italia su VM AgID

Istruzioni operative per installare il cron ETL `cruscotto-etl` su una
VM nuova (es. VM AgID FastWeb consegnata martedì 19/05/2026), partendo
da un clone del repo `AgID/cruscotto-italia` già presente in
`/home/ubuntu/cruscotto-italia-agid/`.

Riferimento di alto livello: `deploy/HANDOFF.md`.

---

## Prerequisiti

Sulla VM target devono essere presenti:

- Python 3.12 (`python3 --version`)
- pacchetti pip di `etl/requirements.txt` installati (`pip install -r etl/requirements.txt`)
- Utente `ubuntu` con permessi di scrittura su:
  - `/var/www/cruscotto-italia/data/` (dati ETL)
  - `/var/log/cruscotto-etl/` (log ETL)
- Cartelle pre-create:
  ```bash
  sudo mkdir -p /var/www/cruscotto-italia/data /var/log/cruscotto-etl
  sudo chown -R ubuntu:www-data /var/www/cruscotto-italia/data
  sudo chmod 755 /var/www/cruscotto-italia/data
  sudo chown ubuntu:ubuntu /var/log/cruscotto-etl
  ```

---

## Passo 1 — Installa il file cron

Copia il template versionato `deploy/cron/cruscotto-etl` in `/etc/cron.d/`:

```bash
sudo cp /home/ubuntu/cruscotto-italia-agid/deploy/cron/cruscotto-etl \
        /etc/cron.d/cruscotto-etl
sudo chmod 644 /etc/cron.d/cruscotto-etl
sudo chown root:root /etc/cron.d/cruscotto-etl
sudo systemctl reload cron
```

Verifica:

```bash
sudo grep -c "^[0-9*]" /etc/cron.d/cruscotto-etl
# Atteso: ~23 righe cron attive

sudo grep "pull_artifact" /etc/cron.d/cruscotto-etl
# Atteso: 1 riga con "30 7 * * *" e "pull_artifact.py --all"
```

---

## Passo 2 — Crea file env per token GitHub

Il cron daily `pull_artifact.py --all` (entry alle 07:30 UTC) richiede
un PAT GitHub per chiamare l'API Actions. Il PAT va salvato in un file
env protetto, non hardcoded nel cron stesso.

```bash
sudo bash -c 'cat > /etc/cruscotto-github.env <<EOF
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF'
sudo chmod 600 /etc/cruscotto-github.env
sudo chown root:root /etc/cruscotto-github.env
```

Sostituisci `ghp_xxx...` con il valore reale del PAT.

### Requisiti del PAT

Tipo: **fine-grained personal access token** (più sicuro del classic).

Scope:
- Repository access: solo `AgID/cruscotto-italia`
- Repository permissions:
  - **Actions**: Read (per listare workflow runs e scaricare artifact)
  - **Metadata**: Read (richiesto da GitHub di default)
- Expiration: max 90 giorni, rotare regolarmente

Per generarne uno nuovo:
https://github.com/settings/personal-access-tokens/new

### Verifica del file env

```bash
sudo ls -la /etc/cruscotto-github.env
# Atteso: -rw------- 1 root root NNN ... (chmod 600, owner root)

sudo cat /etc/cruscotto-github.env
# Atteso: una sola riga "GITHUB_TOKEN=ghp_..."
```

---

## Passo 3 — Test manuale prima di affidarsi al cron

Esegui `pull_artifact.py --all` manualmente con lo stesso pattern del cron:

```bash
sudo bash -c 'set -a && . /etc/cruscotto-github.env && set +a && \
  cd /home/ubuntu/cruscotto-italia-agid && \
  sudo -u ubuntu env GITHUB_TOKEN="$GITHUB_TOKEN" \
    python3 scripts/etl/pull_artifact.py --all' \
  2>&1 | tee /tmp/pull-manual-test.log | tail -20
```

### Esiti attesi

**Caso A — artifact NON disponibili** (situazione normale per il cron daily):

```
{"event": "pull_all_start", "data_dir": "/var/www/cruscotto-italia/data", ...}
{"event": "workflows_discovered", "count": 3, "sources": ["asia", "istat_profilo", "pendolarismo"]}
{"event": "pull_source_start", "source": "asia", ...}
{"event": "no_workflow_runs", "workflow": "etl-asia-refresh.yml"}      # se mai triggerato
{"event": "no_matching_artifact", "workflow": "...", "runs_scanned": N}  # se gia' scaricato
{"event": "no_artifact_found", "source": "asia", ...}
... idem per istat_profilo, pendolarismo
{"event": "pull_all_complete", "total": 3, "downloaded": 0, "no_artifact": 3, "errors": 0}
```

Exit code: 0. Costo: ~2-5 secondi.

**Caso B — un artifact è disponibile** (es. dopo aver triggerato manualmente
un workflow `etl-istat_profilo-refresh.yml` da UI Actions):

```
{"event": "pull_source_start", "source": "istat_profilo", ...}
{"event": "artifact_found", "artifact_id": NNN, "size_bytes": 1165681, ...}
{"event": "download_done", "files_extracted": 7926, ...}
{"event": "tar_strip_top_dir_applied", "prefix": "profilo", "members": 7927}
{"event": "extract_done", "files_extracted": 7926, ...}
{"event": "cleanup_done", "deleted": true}
{"event": "pull_all_complete", "total": 3, "downloaded": 1, ...}
```

Exit code: 0. Costo: ~30 secondi (per istat_profilo).

### Errori comuni

- `401 Unauthorized` → PAT scaduto o senza scope `Actions: Read`. Verifica
  con `curl -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/AgID/cruscotto-italia/actions/workflows`
- `404 Not Found` su workflow → il workflow non esiste su `main` del repo
  AgID o ha nome diverso da quello atteso da `pull_artifact.py`
- `permission denied` su `/var/www/cruscotto-italia/data/` → cartella
  non writeable dall'utente `ubuntu` (rifare passo Prerequisiti)

---

## Passo 4 — Verifica scheduling cron

Il cron è ora attivo. Verifica i prossimi run schedulati:

```bash
# Visualizza tutti i job programmati per oggi
sudo systemctl status cron --no-pager | tail -5

# Verifica che il cron daemon abbia ricaricato /etc/cron.d/
sudo journalctl -u cron --since "5 minutes ago" | tail -10
```

Prima esecuzione automatica:
- **Daily ETL** (PUN + Carburanti): ogni giorno alle 08:00 UTC
- **Daily Pull-Artifact**: ogni giorno alle 07:30 UTC (PRIMA del dashboard)
- **Daily Dashboard rebuild**: ogni giorno alle 08:30 UTC
- **Weekly ETL**: ogni lunedì dalle 04:00 alle 06:00 UTC
- **Monthly ETL**: il 5 di ogni mese dalle 04:00 alle 08:30 UTC
- **Annual ETL**: 1 febbraio / 1 aprile / 1 luglio dalle 04:00 alle 14:00 UTC

---

## Passo 5 — Verifica log dopo prima esecuzione

Dopo la prima finestra cron (entro 24h) verifica i log:

```bash
ls -la /var/log/cruscotto-etl/
# Atteso: log freschi *-YYYYMMDD.log

# Spot check
sudo grep -E "(_completed|_skip|etl_done)" /var/log/cruscotto-etl/*-$(date +%Y%m%d)*.log | head -5

# Verifica freshness via Worker MCP (deve riflettere il rebuild dashboard)
curl -s -X POST "https://cruscotto-italia-mcp.agid.workers.dev/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,
       "params":{"name":"comune_dashboard","arguments":{"istat_code":"075035"}}}' \
  | jq -r '.result.content[0].text | fromjson | "_generated_at = \(._generated_at)"'
```

---

## File correlati

| File | Scopo |
|---|---|
| `/etc/cron.d/cruscotto-etl` | Cron VM (questo file) |
| `/etc/cruscotto-github.env` | Token GitHub (chmod 600 root) |
| `/etc/cruscotto-analytics.env` | Token Cloudflare per analytics MCP (diverso, vedi `deploy/cron/cruscotto-etl` righe ~112) |
| `/var/log/cruscotto-etl/` | Log ETL giornalieri |
| `/var/www/cruscotto-italia/data/` | Output ETL servito da nginx |
| `deploy/cron/cruscotto-etl` (repo) | Template versionato |
| `deploy/SETUP_CRON_NOTES.md` (repo) | Questo documento |

---

## Sicurezza

Il file `/etc/cruscotto-github.env` contiene un PAT GitHub in chiaro.
Mitigazioni in atto:

- chmod 600 (leggibile solo da root)
- owner root:root
- file fuori dalla home utente, in `/etc/`
- NON committato in repo (è gitignore-by-design, non viene mai versionato)
- PAT con scope minimo (Actions: Read, no write su repo)
- Rotazione consigliata: ogni 90 giorni o dopo cambi di operatore

In caso di compromissione: revocare il PAT dal pannello GitHub
(https://github.com/settings/personal-access-tokens), generarne uno
nuovo, sostituire il valore in `/etc/cruscotto-github.env`.

---

## Rollback

Se il cron causa problemi, disattivarlo senza disinstallarlo:

```bash
sudo mv /etc/cron.d/cruscotto-etl /etc/cron.d/cruscotto-etl.disabled
sudo systemctl reload cron
```

Per riattivare:

```bash
sudo mv /etc/cron.d/cruscotto-etl.disabled /etc/cron.d/cruscotto-etl
sudo systemctl reload cron
```

---

**Ultima revisione**: 2026-05-17
