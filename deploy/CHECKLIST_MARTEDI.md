# Checklist cutover VM AgID FastWeb — Martedì 19/05/2026

> **One-page emergency card**. Tutto quello che serve fare in ordine
> per migrare Cruscotto Italia da Aruba a VM AgID. Riferimenti completi:
> `deploy/HANDOFF.md` · `deploy/SETUP_CRON_NOTES.md` ·
> `PROMEMORIA_DEPLOY_MARTEDI.md` (project locale).

---

## ⏰ Timing complessivo

| Fase | Quando | Durata |
|---|---|---|
| 0. Pre-check VM consegnata | mattina | 15 min |
| 1. Bootstrap base | mattina | 30 min |
| 2. Frontend + nginx | pomeriggio | 1h |
| 3. Cron ETL + Worker | pomeriggio | 30 min |
| 4. DNS cutover | sera | 15 min |
| 5. Smoke test end-to-end | sera | 30 min |
| **Totale** | | **~3-4h** |

---

## ✅ Fase 0 — Pre-check VM

Quando il fornitore consegna la VM, **prima di toccare nulla**:

```bash
# Accesso SSH (dovrebbe funzionare con la tua chiave pubblica fornita)
ssh ubuntu@<IP_VM_AGID>

# Verifiche di sanity
lsb_release -a              # Ubuntu 22.04 o 24.04
free -h                     # ≥16 GB RAM
df -h /                     # ≥80 GB liberi
nproc                       # ≥4 vCPU
nvidia-smi                  # se GPU presente (per SIMBA, non per Cruscotto)
sudo ufw status             # firewall: 22, 80, 443 aperti

# Test egress (verifica WAF italiano non blocca subnet FastWeb)
curl -sI https://dati.anticorruzione.it/ | head -1
curl -sI https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv | head -1
# Atteso: HTTP/2 200 entrambi
```

⚠️ Se `dati.anticorruzione.it` da VM AgID dà 403 → **fonte ANAC bannerà
anche FastWeb**. Stop, riprodurre cron via UA workaround o richiedere
unlock al fornitore.

---

## ✅ Fase 1 — Bootstrap base

```bash
# Pacchetti
sudo apt update && sudo apt install -y \
  python3 python3-pip python3-venv \
  nginx \
  certbot python3-certbot-nginx \
  git curl jq

# Cartelle di lavoro
sudo mkdir -p /var/www/cruscotto-italia/data \
              /var/log/cruscotto-etl
sudo chown -R ubuntu:www-data /var/www/cruscotto-italia
sudo chown ubuntu:ubuntu /var/log/cruscotto-etl
sudo chmod 755 /var/www/cruscotto-italia/data

# Clone repo
cd /home/ubuntu
git clone https://github.com/AgID/cruscotto-italia.git cruscotto-italia-agid
cd cruscotto-italia-agid

# Python deps
pip install -r etl/requirements.txt
```

---

## ✅ Fase 2 — Frontend + nginx

```bash
# Frontend statico → /var/www
sudo cp -r frontend/* /var/www/cruscotto-italia/
sudo chown -R www-data:www-data /var/www/cruscotto-italia

# nginx vhost (template versionato)
sudo cp deploy/nginx/cruscotto-italia.conf /etc/nginx/sites-available/cruscotto-italia.dati.gov.it
sudo ln -s /etc/nginx/sites-available/cruscotto-italia.dati.gov.it \
           /etc/nginx/sites-enabled/

# Test config
sudo nginx -t
sudo systemctl reload nginx

# Cert SSL (Let's Encrypt — DNS deve già puntare alla VM)
sudo certbot --nginx -d cruscotto-italia.dati.gov.it --non-interactive --agree-tos -m piersoft2@gmail.com

# Smoke test locale
curl -kI https://localhost/ -H "Host: cruscotto-italia.dati.gov.it"
# Atteso: HTTP/2 200
```

---

## ✅ Fase 3 — Cron ETL + Worker

### 3.1 — Cron VM (ETL daily/weekly/monthly/annual)

```bash
# Token GitHub per pull_artifact.py
sudo bash -c 'cat > /etc/cruscotto-github.env <<EOF
GITHUB_TOKEN=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
EOF'
sudo chmod 600 /etc/cruscotto-github.env
sudo chown root:root /etc/cruscotto-github.env

# Cron file
sudo cp deploy/cron/cruscotto-etl /etc/cron.d/cruscotto-etl
sudo chmod 644 /etc/cron.d/cruscotto-etl
sudo chown root:root /etc/cron.d/cruscotto-etl
sudo systemctl reload cron

# Verifica
sudo grep -c "^[0-9*]" /etc/cron.d/cruscotto-etl  # atteso ~23 righe
```

### 3.2 — Test manuale pull_artifact (NON aspettare le 07:30)

```bash
sudo bash -c 'set -a && . /etc/cruscotto-github.env && set +a && \
  cd /home/ubuntu/cruscotto-italia-agid && \
  sudo -u ubuntu env GITHUB_TOKEN="$GITHUB_TOKEN" \
    python3 scripts/etl/pull_artifact.py --all' 2>&1 | tail -10
# Atteso: 3 source elaborati, conclusione no_artifact OR downloaded=N
```

### 3.3 — Worker Cloudflare (rimane lo stesso, aggiorna solo DATA_BASE_URL)

```bash
cd /home/ubuntu/cruscotto-italia-agid/worker

# Cambia DATA_BASE_URL dal dominio Aruba al dominio AgID
sed -i 's|DATA_BASE_URL = "https://cruscotto-italia.piersoftckan.biz/data"|DATA_BASE_URL = "https://cruscotto-italia.dati.gov.it/data"|' wrangler.toml
grep DATA_BASE_URL wrangler.toml

# Deploy (richiede CLOUDFLARE_API_TOKEN AgID già esportato)
export CLOUDFLARE_API_TOKEN=<token-AgID-wrangler>
export CLOUDFLARE_ACCOUNT_ID=9e615f727c341cba62841a333b2a42a4
npm run typecheck
npm run deploy

# Commit + push (mantiene sync tra repo)
git add wrangler.toml
git commit -m "feat(worker): switch DATA_BASE_URL da piersoftckan.biz a dati.gov.it"
git push origin main
```

---

## ✅ Fase 4 — DNS cutover

```bash
# 4.1 — Sulla tua workstation, verifica risoluzione attuale
dig +short A cruscotto-italia.dati.gov.it
# Se vuoto = DNS non ancora configurato (sollecita referente AgID,
# vedi RICHIESTA_DNS_AGID.md project)

# 4.2 — Una volta che DNS punta a IP VM AgID:
dig +short A cruscotto-italia.dati.gov.it
# Atteso: <IP VM AgID>

dig +short CNAME cruscotto-italia-mcp.dati.gov.it
# Atteso: cruscotto-italia-mcp.agid.workers.dev

# 4.3 — Custom Domain Worker (su dashboard CF AgID):
# Workers & Pages → cruscotto-italia-mcp → Settings → Triggers →
# Add Custom Domain → "cruscotto-italia-mcp.dati.gov.it"
# CF emette SSL Universal automatico in ~5 min
```

---

## ✅ Fase 5 — Smoke test end-to-end pubblico

```bash
# 5.1 — Sito frontend
curl -I https://cruscotto-italia.dati.gov.it/
# Atteso: HTTP/2 200

# 5.2 — Dati statici via nginx VM
curl -I https://cruscotto-italia.dati.gov.it/data/lookup/comuni-bundle.json
# Atteso: HTTP/2 200, content-type: application/json

# 5.3 — Worker MCP via Custom Domain dati.gov.it
curl -s -X POST "https://cruscotto-italia-mcp.dati.gov.it/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,
       "params":{"name":"comune_kpi","arguments":{"istat_code":"075035"}}}' \
  | jq '.result.content[0].text | fromjson | {nome_comune, popolazione}'
# Atteso: {nome_comune: "Lecce", popolazione: 95766}

# 5.4 — Dashboard freshness
curl -s -X POST "https://cruscotto-italia-mcp.dati.gov.it/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,
       "params":{"name":"comune_dashboard","arguments":{"istat_code":"075035"}}}' \
  | jq '.result.content[0].text | fromjson | {_generated_at, _missing}'
# Atteso: _generated_at di oggi, _missing: []

# 5.5 — Pagina pubblica comune con tab
curl -sL "https://cruscotto-italia.dati.gov.it/comune.html?istat=075035" \
  | grep -c "<title>"
# Atteso: 1 (la pagina si carica, contiene title)

# 5.6 — WCAG smoke
# Apri https://cruscotto-italia.dati.gov.it/ in browser, controllare:
# - Cookie banner: assente (no cookie di profilazione)
# - Mappa Leaflet di Lecce: carica
# - Tab "Carburanti": mostra impianti
# - Tab "Pendolarismo": ha dati
# - Footer: link a accessibilita.html funziona
```

---

## ✅ Trasferimento dati (one-shot)

Prima di avviare il cron, copia i dati ETL già prodotti su Aruba:

```bash
# Da Aruba (workstation o ssh):
rsync -avz --progress \
  ubuntu@<IP_ARUBA>:/var/www/cruscotto-italia/data/ \
  /var/www/cruscotto-italia/data/
# ~7 GB, dura ~30-60 min in base alla banda
```

Senza questo trasferimento, il sito mostrerà "no data" finché il primo
cron daily (martedì 08:00 UTC del giorno DOPO) non avrà girato.

---

## 🆘 Rollback

Se il cutover va male, riportare DNS su Aruba:

```bash
# Cambiare A record cruscotto-italia.dati.gov.it → IP Aruba originale
# Aruba è ancora attivo, htpasswd-stats da rimuovere se serve:
# (su Aruba) sudo nano /etc/nginx/conf.d/cruscotto-italia.conf
# rimuovere "auth_basic ..." lines, reload nginx
```

**Non spegnere Aruba per 7-14 giorni** dopo il cutover.

---

## 📋 Conferme da fare al revisore AgID (mercoledì/giovedì)

| Punto audit | Dimostrazione |
|---|---|
| Sito raggiungibile | `https://cruscotto-italia.dati.gov.it/` |
| HTTPS forzato + HSTS | header `Strict-Transport-Security` presente |
| WCAG 2.1 AA | report Pa11y in `scripts/pa11y-*.sh` (17 criteri) |
| Worker MCP funzionante | `cruscotto-italia-mcp.dati.gov.it/mcp` POST tool list |
| Dati freschi | `_generated_at` recente in `comune_dashboard` (≤ 24h dall'ultimo cron daily) |
| Pipeline ETL | log `/var/log/cruscotto-etl/*-YYYYMMDD.log` freschi |
| Workflow Actions visibili | UI Actions con run di `etl-daily smoke test`, `etl-istat_profilo-refresh`, `etl-pendolarismo-refresh`, `etl-asia-refresh` (verdi) e `etl-weekly/monthly/annual smoke test` (rossi con motivazione WAF cloud-restrict in header YAML) |
| Sicurezza | `docs/INFRASTRUCTURE.md` § 7 con security headers nginx |
| Licenze dati | `frontend/about.html` con CC BY 4.0 / IODL 2.0 / HVD per fonte |
| Tracciabilità | ogni shard ha `_source`, `_source_url`, `_license`, `_generated_at` |

---

## 🔑 Credenziali e file da avere a portata

| Risorsa | Dove ottenerla |
|---|---|
| Chiave SSH VM AgID | fornita dal fornitore via canale sicuro |
| GitHub PAT (AgID/cruscotto-italia) | `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` (rotare dopo audit) |
| Cloudflare AgID API token | dashboard CF AgID → My Profile → API Tokens |
| Cloudflare AgID Account ID | `9e615f727c341cba62841a333b2a42a4` |

---

## 📞 Contatti operativi

- **Piersoft** (sviluppatore): piersoft2@gmail.com
- **Referente DNS AgID**: vedi `RICHIESTA_DNS_AGID.md` (project)
- **Fornitore VM (FastWeb)**: vedi handoff fornitore AgID

---

**Ultima revisione**: 2026-05-17
**Versione**: 1.0 (pre-cutover)
