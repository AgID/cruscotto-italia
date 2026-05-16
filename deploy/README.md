# deploy/ — Template configurazione VM AgID FastWeb

Configurazioni infrastrutturali pronte per cutover produzione martedì 19/05/2026.

## Contenuto

```
deploy/
├── nginx/
│   └── cruscotto-italia.dati.gov.it.conf   # vhost frontend + /data/ whitelist + /stats/
└── cron/
    └── cruscotto-etl                        # cron jobs ETL daily/weekly/monthly/annual + analytics
```

## Pre-flight VM AgID (prima di copiare i file)

```bash
# 1. Directory base
sudo mkdir -p /var/www/cruscotto-italia/{frontend,data}
sudo mkdir -p /var/www/cruscotto-stats
sudo mkdir -p /var/log/cruscotto-etl /var/log/cruscotto-stats

# 1b. nginx log dedicati (creati automaticamente dal vhost al primo write,
# ma confermare /var/log/nginx/ esiste già con permessi nginx user)
sudo ls -la /var/log/nginx/ | head -3

# 2. Ownership
sudo chown -R ubuntu:www-data /var/www/cruscotto-italia
sudo chown -R www-data:www-data /var/www/cruscotto-stats
sudo chown ubuntu:ubuntu /var/log/cruscotto-etl
sudo chown www-data:www-data /var/log/cruscotto-stats

# 3. Permissioni
sudo chmod 755 /var/www/cruscotto-italia/data
sudo chmod 755 /var/log/cruscotto-etl

# 4. Snippet security headers comune (copia da Aruba)
sudo cp /etc/nginx/snippets/security-headers.conf /etc/nginx/snippets/   # sulla VM AgID

# 5. htpasswd stats (copia da Aruba)
sudo cp /etc/nginx/.htpasswd-stats /etc/nginx/

# 6. Env analytics (copia da Aruba, contiene CF_API_TOKEN per KV read)
sudo cp /etc/cruscotto-analytics.env /etc/
sudo chmod 600 /etc/cruscotto-analytics.env
sudo chown root:root /etc/cruscotto-analytics.env
```

## Setup applicativo

```bash
# Clone repo
cd /home/ubuntu
git clone https://github.com/AgID/cruscotto-italia.git
cd cruscotto-italia
pip3 install -r etl/requirements.txt --break-system-packages   # o venv

# Frontend statico
sudo cp -r frontend/* /var/www/cruscotto-italia/frontend/

# Sync iniziale dati (da Aruba o da R2 piersoft)
sudo rsync -avz ubuntu@<aruba-ip>:/home/ubuntu/cruscotto-italia-data/ \
  /var/www/cruscotto-italia/data/
# oppure: rclone sync piersoft-r2:cruscotto-italia-data /var/www/cruscotto-italia/data
```

## Deploy nginx

```bash
sudo cp deploy/nginx/cruscotto-italia.dati.gov.it.conf /etc/nginx/conf.d/
sudo nginx -t
sudo systemctl reload nginx

# Smoke test
curl -k https://localhost/data/dashboard/075035.json    # Lecce dovrebbe rispondere 200
curl -k https://localhost/data/anac/000.json            # blocked (404 atteso)
```

## Deploy cron

```bash
sudo cp deploy/cron/cruscotto-etl /etc/cron.d/
sudo chmod 644 /etc/cron.d/cruscotto-etl
sudo systemctl reload cron

# Verifica
sudo crontab -l -u ubuntu   # mostra solo crontab utente
sudo ls -la /etc/cron.d/
sudo grep CRON /var/log/syslog | tail -10
```

## Cert SSL

Cert per `cruscotto-italia.dati.gov.it` gestiti separatamente tramite
ticket AgID al provider cert. Una volta installati in
`/etc/ssl/dati.gov.it/`, la config nginx li userà direttamente.

Se invece useremo Let's Encrypt:
```bash
sudo certbot --nginx -d cruscotto-italia.dati.gov.it
```

## DNS

Vedi `RICHIESTA_DNS_AGID.md` (record A `cruscotto-italia.dati.gov.it`
verso IP VM, e CNAME `cruscotto-italia-mcp.dati.gov.it` verso
`cruscotto-italia-mcp.agid.workers.dev`).

## Worker MCP

Una volta che il sito frontend è raggiungibile su
`https://cruscotto-italia.dati.gov.it`, aggiornare
`DATA_BASE_URL` del Worker AgID:

```bash
# Da macchina con wrangler + token AgID:
cd worker
# Edit wrangler.toml: DATA_BASE_URL = "https://cruscotto-italia.dati.gov.it/data"
npm run deploy
```

## Note operative

- **Default --target=local + --outdir produzione** in tutti gli ETL: il cron
  non passa argomenti, gli ETL scrivono direttamente in
  `/var/www/cruscotto-italia/data/<source>/`.
- **GitHub Actions** dei workflow ETL restano come **fallback emergenza**
  (workflow_dispatch manuale dalla UI GitHub). Devono essere riconfigurati
  per scrivere su filesystem VM via SSH push, oppure pushati su R2 piersoft
  come backup secondario. Decisione fase 2.
- **Backup**: il fornitore VM dovrebbe avere snapshot. In aggiunta, considerare
  rsync notturno di `/var/www/cruscotto-italia/data/` verso storage esterno.
- **Monitoraggio**: vedi `/etc/cron.d/cruscotto-etl` sezione analytics per
  job orario di aggregazione + alert.
