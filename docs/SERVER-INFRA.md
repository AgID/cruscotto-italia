# Server infrastructure — Setup operativo

> Guida per installare l'infrastruttura **lato server** di Cruscotto Italia su una macchina Linux pulita. Complementa `INFRASTRUCTURE.md` (architettura) e `SECRETS.md` (credenziali GitHub Actions): qui sono documentate le cose che vivono SUL SERVER (nginx, filesystem, cron, env files locali) e che NON sono versionate nel repo.

**Quando consultare questa guida**:
- Migrazione del servizio su una nuova macchina (es. AgID giugno 2026)
- Disaster recovery (server perso, ripristino da zero)
- Replicare l'ambiente per sviluppo/staging

**Cosa NON c'è in questo file**:
- Token, password, hash htpasswd in chiaro (mai in repo)
- Credenziali Cloudflare specifiche del piersoft account (vedi `SECRETS.md`)
- Architettura concettuale (vedi `INFRASTRUCTURE.md`)

**Convenzione di path nel documento**: uso `/home/<user>/cruscotto-italia` come placeholder. Sul server piersoft attuale è `/home/ubuntu/cruscotto-italia`. Sostituisci con il path del tuo deployment.

---

## 1. Prerequisiti macchina

Ambiente testato: **Ubuntu 22.04 LTS** (kernel 5.15+), x86_64.

Pacchetti di sistema richiesti:

```bash
sudo apt update
sudo apt install -y \
  nginx \
  python3 python3-pip python3-venv \
  git curl wget \
  apache2-utils \
  certbot python3-certbot-nginx \
  jq
```

Pacchetti per i test di accessibilità (opzionale, solo se vuoi girare `pa11y-*.sh`):

```bash
# Node.js 20 LTS (per Pa11y e wrangler)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs
sudo npm install -g pa11y
```

---

## 2. Layout filesystem

Il server contiene **5 aree distinte** con responsabilità diverse:

| Path | Owner | Contenuto | Versionato? |
|------|-------|-----------|-------------|
| `/home/<user>/cruscotto-italia/` | `<user>:<user>` | Repo Git principale (piersoft/cruscotto-italia) | Sì |
| `/home/<user>/cruscotto-italia-agid/` | `<user>:<user>` | Repo Git mirror (AgID/cruscotto-italia) | Sì |
| `/var/www/cruscotto-stats/` | `www-data:www-data` | Output pagina `/stats/` (HTML+JSON generati) | No |
| `/var/log/cruscotto-stats/` | `root:root` | Log dei job cron analytics | No |
| `/etc/cruscotto-analytics.env` | `root:root` `chmod 600` | Token Cloudflare per il fetcher analytics | No (sensibile) |

Le directory **non versionate** (`/var/www/cruscotto-stats/`, `/var/log/cruscotto-stats/`) vanno create manualmente dopo aver clonato il repo (vedi sez. 6).

---

## 3. Clone del repo

```bash
# Sostituisci <user> con il tuo username Linux di servizio
sudo -u <user> -i
cd ~
git clone https://github.com/piersoft/cruscotto-italia.git
# Se serve anche il mirror AgID:
git clone https://github.com/AgID/cruscotto-italia.git cruscotto-italia-agid
```

Per i due remote serve un **PAT GitHub fine-grained** con scope `Contents: Read and write` sul singolo repo. Embedded nel remote URL:

```bash
cd ~/cruscotto-italia
git remote set-url origin https://x-access-token:<PAT>@github.com/piersoft/cruscotto-italia.git
```

Stesso pattern per `cruscotto-italia-agid`. **Attenzione**: il token è visibile in chiaro a chiunque acceda al server. Mitigazioni:
- Token con scope minimo (un solo repo, solo Contents)
- Rotation annuale
- Su AgID a regime: passare a deploy key SSH o GitHub App (rimuove PAT in chiaro)

---

## 4. Configurazione nginx

### 4.1 Struttura

Il file di configurazione vive in `/etc/nginx/conf.d/cruscotto-italia.conf`. nginx carica tutto ciò che matcha `*.conf` in quella directory.

**Cosa NON fare**:
- File `.bak`, `.old`, `.swp` nella stessa dir (rischio caricamento parziale)
- `add_header` a livello server senza `always` (non si propaga ai location)
- Servire `/home/<user>/` direttamente come docroot (è bonificato dal repo, ma rimane permission-sensitive)

### 4.2 Template del server block

```nginx
server {
    server_name cruscotto-italia.example.org;
    root /home/<user>/cruscotto-italia/frontend;
    index index.html;

    # Difesa file backup/temp
    location ~* \.(bak|backup|swp|swo|orig|old|tmp|~)$ {
        deny all;
        return 404;
    }

    # Security headers (snippet condiviso)
    include /etc/nginx/snippets/security-headers.conf;

    # Pagina statistiche private (htpasswd, vedi sez. 5)
    location ^~ /stats/ {
        alias /var/www/cruscotto-stats/;
        index index.html;
        auth_basic "Statistiche Cruscotto Italia";
        auth_basic_user_file /etc/nginx/.htpasswd-stats;
        add_header X-Robots-Tag "noindex, nofollow" always;
        include /etc/nginx/snippets/security-headers.conf;
    }

    location / {
        include /etc/nginx/snippets/security-headers.conf;
        try_files $uri $uri/ /index.html =404;
    }

    location ~ \.html$ {
        include /etc/nginx/snippets/security-headers.conf;
        expires 5m;
        add_header Cache-Control "public, must-revalidate";
    }

    location ~* \.(css|js|png|jpg|jpeg|gif|svg|webp|woff2?)$ {
        include /etc/nginx/snippets/security-headers.conf;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # SSL gestito da Certbot
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/cruscotto-italia.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cruscotto-italia.example.org/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    if ($host = cruscotto-italia.example.org) {
        return 301 https://$host$request_uri;
    }
    listen 80;
    server_name cruscotto-italia.example.org;
    return 404;
}
```

**Punti critici**:
- `location ^~ /stats/`: il modificatore `^~` ha priorità sulle regex `\.html$` successive. Senza `^~`, `/stats/index.html` verrebbe servita da `location ~ \.html$` con root sbagliata
- `alias` (NON `root`): `alias` mappa `/stats/` direttamente a `/var/www/cruscotto-stats/`
- `add_header always`: necessario quando la response è non-2xx (es. 401 da htpasswd)

### 4.3 Snippet security headers

File `/etc/nginx/snippets/security-headers.conf`:

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self' https://cruscotto-italia-mcp.example.org; frame-ancestors 'self';" always;
```

Adattare `connect-src` al dominio Worker MCP della propria deployment.

### 4.4 server_tokens

In `/etc/nginx/nginx.conf` blocco `http`:

```nginx
server_tokens off;
```

Nasconde la versione nginx nei banner `Server` e nelle error pages.

### 4.5 Certificato TLS

```bash
sudo certbot --nginx -d cruscotto-italia.example.org
# Certbot modifica automaticamente la config nginx per il listen 443 ssl
sudo systemctl enable certbot.timer
```

### 4.6 Validazione

```bash
sudo nginx -t                      # syntax check
sudo nginx -T | grep "server_name" # quante server_name (deve essere 2: HTTPS + HTTP redirect)
sudo systemctl reload nginx
```

---

## 5. Pagina /stats/ — autenticazione htpasswd

### 5.1 File htpasswd

```bash
sudo apt install -y apache2-utils  # se non già installato
sudo htpasswd -c /etc/nginx/.htpasswd-stats <username>
# Inserire password robusta (sarà richiesta dal browser per /stats/)
sudo chmod 644 /etc/nginx/.htpasswd-stats
sudo chown root:root /etc/nginx/.htpasswd-stats
```

**Permessi `644 root:root`** (non `640`): nginx gira come user `nginx` o `www-data` a seconda della distro. `644` rende il file world-readable sul filesystem, ma il contenuto è solo bcrypt hash, non password in chiaro. È il pattern standard Debian/Ubuntu.

### 5.2 Coesistenza con altri htpasswd

Il file `/etc/nginx/.htpasswd-stats` è **separato** da eventuali altri htpasswd di altri servizi (es. `.htpasswd-analytics` per SIMBA, `.htpasswd` generico). Le credenziali NON sono condivise tra location/servizi: ognuno ha il suo file.

### 5.3 Rotation password

Per cambiare password (no `-c`, sennò sovrascrive l'utente):

```bash
sudo htpasswd /etc/nginx/.htpasswd-stats <username>
```

---

## 6. Pipeline analytics (cron + fetcher CF)

### 6.1 Directory di lavoro

```bash
sudo mkdir -p /var/www/cruscotto-stats
sudo chown www-data:www-data /var/www/cruscotto-stats

sudo mkdir -p /var/log/cruscotto-stats
sudo chown root:root /var/log/cruscotto-stats
```

### 6.2 Mapping ISTAT → nome comune

Per visualizzare i nomi comuni invece dei codici ISTAT, generare il mapping una volta:

```bash
sudo python3 <<'PYEOF'
import json, glob
out = {}
for path in glob.glob('/home/<user>/cruscotto-italia/output/veicoli/*.json'):
    d = json.load(open(path))
    if d.get('istat_code') and d.get('denominazione'):
        out[d['istat_code']] = d['denominazione']
with open('/var/www/cruscotto-stats/istat-names.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False)
print(f"Comuni mappati: {len(out)}")
PYEOF
sudo chown www-data:www-data /var/www/cruscotto-stats/istat-names.json
```

Output atteso: ~7896 comuni in `istat-names.json` (~200KB). Il file va rigenerato solo se cambiano i confini amministrativi (fusioni comunali, rari).

### 6.3 Env file per analytics MCP

Lo script `mcp_stats_fetcher.py` legge i counter dal KV Cloudflare via API REST. Richiede 3 credenziali in env vars.

**Crea token Cloudflare API**: dashboard CF → `My Profile` → `API Tokens` → `Create Token` → Custom Token con:
- Permission: `Account` → `Workers KV Storage` → **Read** (no write)
- Account Resources: solo il proprio account
- TTL: a scelta (annuale consigliato)

**Salva credenziali fuori repo**:

```bash
sudo tee /etc/cruscotto-analytics.env >/dev/null <<EOF
CF_ACCOUNT_ID=<account-id-cloudflare>
CF_KV_NAMESPACE=<id-namespace-CACHE>
CF_API_TOKEN=<token-letto-solo-permesso-KV>
EOF
sudo chmod 600 /etc/cruscotto-analytics.env
sudo chown root:root /etc/cruscotto-analytics.env
```

Il `chmod 600` è critico: solo root deve leggere il token. Il cron gira come root, quindi accede.

### 6.4 Cron giornaliero

```bash
sudo crontab -e
```

Aggiungi (sostituisci `<user>` con il tuo username):

```cron
# Aggregazione stats Cruscotto Italia (04:00 UTC giornaliero)
# 1) Fetch analytics MCP da Cloudflare KV (3 prefix: analytics, analytics-err, analytics-term)
# 2) Parse log nginx + integra MCP stats nell'HTML
# 3) Riallinea ownership a www-data
0 4 * * * set -a && . /etc/cruscotto-analytics.env && set +a && /usr/bin/python3 /home/<user>/cruscotto-italia/scripts/analytics/mcp_stats_fetcher.py --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --days 30 >> /var/log/cruscotto-stats/cron.log 2>&1 ; /usr/bin/python3 /home/<user>/cruscotto-italia/scripts/analytics/stats_aggregator.py --logs /var/log/nginx/access.log.1 --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --mcp-stats /var/www/cruscotto-stats/mcp_stats.json >> /var/log/cruscotto-stats/cron.log 2>&1 && /bin/chown -R www-data:www-data /var/www/cruscotto-stats
```

**Logica**:
- `set -a && . /etc/cruscotto-analytics.env && set +a`: carica le env vars CF
- `;` tra fetcher e aggregator: se CF è giù, l'aggregator gira comunque (log nginx-only)
- `&&` prima del `chown`: si fa solo se aggregator finisce ok
- Orario 04:00 UTC: dopo logrotate (03:00 default), `access.log.1` è il log completo del giorno prima

### 6.5 Verifica logrotate nginx

Default Ubuntu: `daily` + `rotate 14`. Per policy AgID (conservazione log 7 giorni):

```bash
sudo sed -i 's/^\s*rotate.*$/        rotate 7/' /etc/logrotate.d/nginx
cat /etc/logrotate.d/nginx | grep rotate    # verifica
```

### 6.6 Test manuale del cron

Per eseguire l'aggregazione fuori orario (utile dopo install):

```bash
sudo bash -c 'set -a && . /etc/cruscotto-analytics.env && set +a && \
/usr/bin/python3 /home/<user>/cruscotto-italia/scripts/analytics/mcp_stats_fetcher.py --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --days 30 && \
/usr/bin/python3 /home/<user>/cruscotto-italia/scripts/analytics/stats_aggregator.py --logs /var/log/nginx/access.log /var/log/nginx/access.log.1 --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --mcp-stats /var/www/cruscotto-stats/mcp_stats.json && \
chown -R www-data:www-data /var/www/cruscotto-stats'
```

(Manualmente conviene aggiungere `access.log` ai `--logs` per vedere il giorno corrente.)

---

## 7. ETL Python — environment

Gli ETL girano in 2 contesti:

### 7.1 GitHub Actions (CI primario)

Self-hosted runner Linux installato sul server stesso (vedi `INFRASTRUCTURE.md` § 2.6). Secrets configurati in GitHub repo settings (vedi `SECRETS.md`).

### 7.2 Esecuzione manuale (debug, smoke test)

Per girare un ETL manualmente sul server:

```bash
cd /home/<user>/cruscotto-italia
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Le credenziali R2 vanno passate via env vars (NON committate)
export R2_ACCOUNT_ID=<...>
export R2_ACCESS_KEY_ID=<...>
export R2_SECRET_ACCESS_KEY=<...>

# Smoke test (target locale, non scrive su R2)
python3 scripts/smoke-test-etl.py --target=local

# Singolo ETL con target R2
python3 etl/sources/redditi.py --target=r2
```

Alternativa pulita: file `/etc/cruscotto-etl.env` con le 3 R2 var, caricato con `set -a && . /etc/cruscotto-etl.env && set +a` (stesso pattern dell'analytics env). Non versionato.

---

## 8. Worker Cloudflare — operatività dal server

Il Worker MCP deploya da Cloudflare, ma alcuni comandi operativi conviene girarli dal server (dove c'è già il repo e Node):

### 8.1 Installazione tooling

```bash
cd /home/<user>/cruscotto-italia/worker
npm install
```

### 8.2 Deploy manuale del Worker

Il workflow `deploy-worker.yml` è instabile su questo ambiente. Per evitare deploy parziali:

```bash
cd /home/<user>/cruscotto-italia/worker
npm run typecheck && npm run deploy
```

**Mai usare**: `npx wrangler deploy --env production` (crea secondary worker senza R2/KV bindings).

### 8.3 Ispezione KV (debug analytics)

```bash
cd /home/<user>/cruscotto-italia/worker
npx wrangler kv key list --binding=CACHE --prefix="analytics:" --remote
```

Richiede env `CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID` (token con permission Workers KV). Se non sono setati, wrangler li chiede via OAuth interattivo (apre browser).

---

## 9. Checklist di migrazione

Per spostare il servizio su una nuova macchina, eseguire in ordine:

- [ ] **Macchina** Ubuntu 22.04+ con package list di § 1 installati
- [ ] **DNS**: punta `cruscotto-italia.<dominio>` e `cruscotto-italia-mcp.<dominio>` (custom domain Worker) alla nuova macchina/Worker
- [ ] **User di servizio** creato (`<user>`, non root, con shell bash)
- [ ] **Repo cloni** con PAT GitHub embedded (§ 3)
- [ ] **nginx**: server block + snippet security + Certbot (§ 4)
- [ ] **htpasswd-stats** creato con password robusta (§ 5)
- [ ] **Dir output e log** create con owner corretti (§ 6.1)
- [ ] **Mapping ISTAT→nome** generato da `output/veicoli/*.json` (§ 6.2)
- [ ] **Token CF Workers KV Read** creato + `/etc/cruscotto-analytics.env` chmod 600 (§ 6.3)
- [ ] **Logrotate nginx**: rotate 7 (§ 6.5)
- [ ] **Crontab root**: entry analytics installata (§ 6.4)
- [ ] **Smoke test** ETL + run manuale fetcher+aggregator (§ 6.6, § 7.2)
- [ ] **Browser test**: `/stats/` chiede password e mostra KPI dopo login
- [ ] **GitHub Secrets**: replicati su nuovo repo (vedi `SECRETS.md`)
- [ ] **Self-hosted runner**: installato e registrato (vedi `INFRASTRUCTURE.md` § 2.6)
- [ ] **Worker MCP**: prima deploy dalla nuova macchina (§ 8.2)
- [ ] **DNS switch**: cambio CNAME/A per i due domini, attesa propagazione

### 9.1 Cose che NON migrano automaticamente

- **Stato KV `analytics:*`**: ripartono da zero. Lo storico precedente resta sul KV vecchio fino al TTL 35d. Volendo si possono migrare con `wrangler kv key list` + `put` script
- **htpasswd password**: vanno create ex-novo (non si copiano gli hash, sono per-utente diversi)
- **PAT GitHub embedded**: vanno rigenerati per il nuovo host (anche se tecnicamente funzionano i vecchi, è igienico ruotare)
- **Log nginx storici**: 7 giorni rolling, accettabile la perdita

### 9.2 Verifica post-migrazione

Test minimi da fare nelle 24h dopo lo switch DNS:

```bash
# 1. Frontend
curl -sI https://cruscotto-italia.<dominio>/ | head -3      # → 200

# 2. Worker MCP
curl -sX POST https://cruscotto-italia-mcp.<dominio>/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_comune","arguments":{"nome":"Lecce"}}}' \
  | head -c 200

# 3. /stats/ con htpasswd
curl -sI https://cruscotto-italia.<dominio>/stats/ | head -3    # → 401

# 4. /stats/ con auth
curl -s -u <username>:<password> https://cruscotto-italia.<dominio>/stats/ \
  | head -c 500

# 5. Cron entry attiva
sudo crontab -l | grep cruscotto

# 6. Logs cron (dopo passaggio 04:00 UTC)
sudo tail /var/log/cruscotto-stats/cron.log
```

---

## 10. Inventario dei secret locali

Riepilogo dei **secret che vivono sul server** (NON in repo, NON in GitHub Actions secrets):

| Secret | Path | Permessi | Cosa contiene | Come rigenerarlo |
|--------|------|----------|---------------|------------------|
| PAT GitHub piersoft repo | `/home/<user>/cruscotto-italia/.git/config` (URL) | `<user>` only | Token PAT GitHub fine-grained, scope `Contents: RW` | Settings → Developer settings → PAT |
| PAT GitHub AgID repo | `/home/<user>/cruscotto-italia-agid/.git/config` (URL) | `<user>` only | Stesso pattern, repo diverso | Stessa procedura |
| htpasswd `/stats/` | `/etc/nginx/.htpasswd-stats` | `644 root:root` | bcrypt hash di `<username>:<password>` | `sudo htpasswd /etc/nginx/.htpasswd-stats <user>` |
| CF API token KV Read | `/etc/cruscotto-analytics.env` | `600 root:root` | `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE`, `CF_API_TOKEN` | dashboard CF → API Tokens |
| R2 credentials per ETL manuale | `/etc/cruscotto-etl.env` (opzionale) | `600 root:root` | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | dashboard CF → R2 → Manage R2 API Tokens |
| Worker admin token | env del Worker (Cloudflare dashboard) | n/a | `ADMIN_TOKEN` per cache purge | Generato a mano (es. `openssl rand -hex 32`), copiato anche in GitHub Secret `WORKER_ADMIN_TOKEN` |
| TLS certificati Let's Encrypt | `/etc/letsencrypt/live/<dominio>/` | `root:root` | Coppia chiavi privata/pubblica + chain | `sudo certbot certonly --nginx -d <dominio>` |

### 10.1 Policy di rotazione

| Asset | Frequenza consigliata | Procedura |
|-------|----------------------|-----------|
| PAT GitHub | Annuale, o subito se compromesso | Nuovo PAT, `git remote set-url` |
| htpasswd | Su richiesta (es. cambio team) | `sudo htpasswd /etc/nginx/.htpasswd-stats <user>` |
| CF API token | Annuale (matcha TTL token) | Nuovo token, aggiorna `/etc/cruscotto-analytics.env`, niente restart cron |
| R2 credentials | Annuale | Nuovo token CF, aggiorna env file e GitHub Secret |
| Worker admin token | Su compromissione | Rigenera, push a CF dashboard env var + GitHub Secret |
| TLS certificati | Auto-renew Certbot ogni 60 giorni | `sudo systemctl status certbot.timer` |

### 10.2 Cosa fare se un secret viene compromesso

1. **Revoca subito** dalla sorgente (dashboard CF, GitHub settings, etc.)
2. **Rigenera** e applica al server
3. **Audit log**: controlla nginx access log, Worker tail, GitHub Actions runs delle ultime 24-72h
4. **Notifica**: se i dati esposti riguardano terze parti (utenti), valuta obblighi GDPR di notifica al Garante (72h)

---

## 11. Riferimenti

- `INFRASTRUCTURE.md` — architettura tecnica complessiva
- `SECRETS.md` — secrets per GitHub Actions (workflow CI/CD)
- `data-licenses.md` — licenze delle fonti dati pubbliche
- nginx docs — https://nginx.org/en/docs/
- Cloudflare Workers KV — https://developers.cloudflare.com/kv/
- AgID Linee Guida sicurezza — https://www.agid.gov.it/it/sicurezza
