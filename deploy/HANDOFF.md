# HANDOFF — Deployment AgID VM (Cruscotto Italia + SIMBA)

> Documento operativo per il cutover produzione VM AgID FastWeb.
> Sintetizza decisioni e stato dell'arte al 16/05/2026 sera, prima del
> go-live previsto martedì 19/05/2026.

---

## 1. Stato attuale architettura

Cruscotto Italia gira con **architettura B1** (no R2 binding diretto,
Worker MCP fa HTTPS fetch verso filesystem servito da nginx).

```
[Claude.ai / utenti MCP]
   │
   ▼ HTTPS MCP
[Worker AgID CF]      https://cruscotto-italia-mcp.agid.workers.dev
   │                  Stack: TypeScript, KV CACHE 25da2df6..., rate limit 60r/60s
   │
   ▼ HTTPS fetch /data/<prefix>/<istat>.json + edge cache 1h-24h
[nginx]               https://cruscotto-italia.piersoftckan.biz/data/    (transitorio)
                      https://cruscotto-italia.dati.gov.it/data/         (post-cutover)
   │
   ▼ filesystem
[/home/ubuntu/cruscotto-italia-data/]                                    (Aruba, transitorio)
[/var/www/cruscotto-italia/data/]                                        (VM AgID, post-cutover)
   ↑
   │ ETL scrivono direttamente
   │
[Cron VM AgID]        cadenze: daily, weekly, monthly, annual
[GitHub Actions]      stesso codice, solo workflow_dispatch (emergenza)
```

## 2. Risorse Cloudflare AgID attive

| Risorsa | Valore |
|---|---|
| Account CF | `Dati@agid.gov.it's Account` |
| Account ID | `9e615f727c341cba62841a333b2a42a4` |
| KV `CACHE` | `25da2df609974316a22cdcb71f92228e` |
| KV `AUTH_TOKENS` (orfano, slot) | `0c7b825cb6964821a2e8c208ee563443` |
| Worker | `cruscotto-italia-mcp` deployed |
| Subdomain workers.dev | `agid.workers.dev` |
| URL Worker | `https://cruscotto-italia-mcp.agid.workers.dev` |
| R2 bucket | **NON attivato** (no carta di credito AgID) |

**Architettura B1** = no R2 binding nativo. I dati shard sono fetched via
HTTPS dalla VM AgID (post-cutover) o da Aruba nginx (transitorio).

## 3. R2.dev piersoft (transitorio attivo)

Bucket R2 piersoft esposto come Public Development URL per il transitorio:
- URL: `https://pub-ef6fc3efe6a3426292508de9d233334f.r2.dev`
- Stato: ATTIVO ma non più in uso da Worker AgID (`DATA_BASE_URL` punta ad Aruba).
- Dopo cutover VM: **disabilitare** (dashboard piersoft → R2 → Settings →
  Public Development URL → Disable).

## 4. Repository allineate

- `AgID/cruscotto-italia` HEAD: `9ab3a84` (rimozione schedule workflow CI)
- `piersoft/cruscotto-italia`: **NON ancora allineata** con i commit B1
  (`9ab3a84..fd10f4c`). Sync futuro come fase 2.

Commit chiave su AgID dopo migrazione:
- `fd10f4c` Worker B1 refactor (HTTPS fetch instead of R2 binding)
- `72b140a` DATA_BASE_URL → Aruba nginx /data/
- `144ffb3` ETL --outdir produzione `/var/www/cruscotto-italia/data/<source>`
- `9bc985d` deploy/ template nginx + cron VM AgID
- `9ab3a84` rimozione schedule workflow CI (emergency-only)

## 5. Checklist cutover martedì 19/05

### Pre-flight VM AgID

Vedere `deploy/README.md`. Sintesi:

```bash
sudo mkdir -p /var/www/cruscotto-italia/{frontend,data}
sudo mkdir -p /var/www/cruscotto-stats /var/log/cruscotto-etl /var/log/cruscotto-stats
sudo chown -R ubuntu:www-data /var/www/cruscotto-italia
sudo chown ubuntu:ubuntu /var/log/cruscotto-etl
sudo chmod 755 /var/www/cruscotto-italia/data
```

### Clone repo

```bash
cd /home/ubuntu
git clone https://github.com/AgID/cruscotto-italia.git
cd cruscotto-italia
pip3 install -r etl/requirements.txt --break-system-packages
```

### Sync iniziale dati

```bash
sudo rsync -avz ubuntu@<aruba-ip>:/home/ubuntu/cruscotto-italia-data/ \
  /var/www/cruscotto-italia/data/
# tempo stimato: 5-10 min, ~6.7 GB
```

### Copia frontend

```bash
sudo cp -r /home/ubuntu/cruscotto-italia/frontend/* /var/www/cruscotto-italia/frontend/
```

### Copia segreti da Aruba

```bash
# Sul lato Aruba:
sudo cat /etc/nginx/.htpasswd-stats
sudo cat /etc/cruscotto-analytics.env
sudo cat /etc/nginx/snippets/security-headers.conf

# Sulla VM AgID, riproduci questi 3 file (chmod 600 per env, 644 per altri):
```

### Installa nginx config

```bash
sudo cp deploy/nginx/cruscotto-italia.dati.gov.it.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

### Installa cron

```bash
sudo cp deploy/cron/cruscotto-etl /etc/cron.d/
sudo systemctl reload cron
```

### Cert SSL

Cert AgID gestiti via ticket separato. Una volta installati in
`/etc/ssl/dati.gov.it/`, nginx li userà direttamente (path già configurato).

Alternativa Let's Encrypt:
```bash
sudo certbot --nginx -d cruscotto-italia.dati.gov.it
```

### DNS cutover

DNS configurati da AgID (vedi `RICHIESTA_DNS_AGID.md`):
- A `cruscotto-italia.dati.gov.it` → IP VM AgID (TTL 300)
- CNAME `cruscotto-italia-mcp.dati.gov.it` → `cruscotto-italia-mcp.agid.workers.dev`

### Switch DATA_BASE_URL Worker AgID

```bash
# Da macchina con wrangler + token AgID:
cd worker
# Edit wrangler.toml:
#   DATA_BASE_URL = "https://cruscotto-italia.dati.gov.it/data"
npm run typecheck && npm run deploy
```

### Disattivazione transitori

- Disattiva cron ETL Aruba (li sostituisce VM AgID): `sudo crontab -e -u ubuntu`
- Disabilita R2.dev Public URL piersoft (dashboard CF piersoft)
- Switch nginx Aruba (opzionale): rimuovi `location /data/` se vuoi
  bloccare l'accesso pubblico (cambio finale di owner)

## 6. Smoke test post-cutover

```bash
W=https://cruscotto-italia-mcp.dati.gov.it
F=https://cruscotto-italia.dati.gov.it

# Worker
curl -s $W/health
curl -s -X POST $W/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"comune_dashboard","arguments":{"istat_code":"075035"}}}' \
  | python3 -c "import json,sys; d=json.loads(json.load(sys.stdin)['result']['content'][0]['text']); print(f'Lecce sezioni: {len(d.keys())}')"

# Frontend nginx
curl -sI $F/                       # 200 HTML
curl -sI $F/data/dashboard/075035.json   # 200 JSON Lecce
curl -sI $F/data/anac/000.json     # 404 (whitelist exclude)
```

## 7. Architettura post-go-live: cosa cambia

| Componente | Aruba (oggi) | VM AgID (martedì +) |
|---|---|---|
| Frontend nginx | `cruscotto-italia.piersoftckan.biz` | `cruscotto-italia.dati.gov.it` |
| Worker MCP | `cruscotto-italia-mcp.piersoftckan.biz` | `cruscotto-italia-mcp.dati.gov.it` (CNAME a `agid.workers.dev`) |
| Storage dati | filesystem Aruba | filesystem VM AgID |
| Cron ETL | Aruba (attivo) | VM AgID (cron.d + dashboard rebuild) |
| Workflow GitHub | schedule (rimossi) | workflow_dispatch only (emergenza) |
| R2 piersoft | bucket + Public R2.dev URL (transitorio) | DISABILITATO |

## 8. Rollback emergency

Se qualcosa va storto durante il cutover:
1. **DNS revert**: A record `cruscotto-italia.dati.gov.it` torna a IP Aruba
   (vecchio frontend riprende a servire, ma con URL diverso)
2. **DATA_BASE_URL Worker AgID**: restore a `cruscotto-italia.piersoftckan.biz/data`
   con `npm run deploy`
3. **Cron Aruba**: re-attiva se disabilitato

## 9. Cosa NON migra alla VM AgID

- **Account piersoft CF**: resta attivo per altri progetti (ckan-mcp, ecc.)
- **Repo `piersoft/cruscotto-italia`**: resta come mirror personale
- **R2 piersoft bucket**: rimane allocato (decommissioning post-stable, +2-4
  settimane) come backup transitorio
- **GitHub Actions self-hosted runner** `ghrunner` Aruba: resta finché
  decidiamo cosa farne (fase 2, possibile migrazione su VM AgID)

