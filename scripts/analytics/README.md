# Cruscotto Italia — Analytics privati

Pipeline minimale per aggregare statistiche di accesso dal log nginx,
rispettando la policy privacy AgID di dati.gov.it.

## Cosa fa

- Legge il log nginx (`access.log` + `access.log.1`)
- Filtra bot, attacchi, asset interni, errori HTTP
- Aggrega in JSON + HTML: top comuni, visite/giorno, referer esterni
- Output va in area protetta htpasswd (es. `/stats/`)
- Non scrive mai IP, user-agent grezzi, query string completi: solo aggregati

## Conformità privacy AgID

- Log raw nginx ruotati a 7 giorni (`/etc/logrotate.d/nginx` con `rotate 7`)
- Aggregati JSON sono dati anonimi (no PII) → conservabili a lungo termine
- Nessun cookie, nessun fingerprinting, nessun beacon JS lato client
- Pagina `/stats/` non indicizzabile (`<meta name="robots" content="noindex">`)
- Privacy policy referenziata: https://www.dati.gov.it/policy

## Setup iniziale (una volta sola)

### 1. Verifica rotation log a 7 giorni

```bash
sudo sed -i 's/^\s*rotate 52$/        rotate 7/' /etc/logrotate.d/nginx
cat /etc/logrotate.d/nginx | grep rotate    # deve mostrare "rotate 7"
sudo find /var/log/nginx -name "*.log.*" -mtime +7 -delete
```

### 2. Crea directory output

```bash
sudo mkdir -p /var/www/cruscotto-stats
sudo chown www-data:www-data /var/www/cruscotto-stats
```

### 3. htpasswd protezione

```bash
sudo apt install apache2-utils    # se mancante
sudo htpasswd -c /etc/nginx/.htpasswd-stats piersoft
# Inserire password
```

### 4. nginx location

Aggiungi nel server block di `cruscotto-italia.piersoftckan.biz`:

```nginx
location /stats/ {
    alias /var/www/cruscotto-stats/;
    index index.html;
    auth_basic "Statistiche Cruscotto Italia";
    auth_basic_user_file /etc/nginx/.htpasswd-stats;
    add_header X-Robots-Tag "noindex, nofollow";
}
```

Riavvia nginx:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Crea mapping ISTAT → nome (facoltativo)

Crea `/var/www/cruscotto-stats/istat-names.json` da R2:

```bash
# Scarica anagrafica e produce mapping
curl -s 'https://pub-XXXX.r2.dev/comuni.json' | \
  jq 'reduce .[] as $c ({}; . + {($c.istat): $c.denominazione})' \
  > /var/www/cruscotto-stats/istat-names.json
```

Oppure crea il file manualmente con i top 100 comuni che ti aspetti di vedere.

## Esecuzione manuale

```bash
sudo python3 scripts/analytics/stats_aggregator.py \
  --logs /var/log/nginx/access.log /var/log/nginx/access.log.1 \
  --out /var/www/cruscotto-stats \
  --istat-names /var/www/cruscotto-stats/istat-names.json \
  --exclude-test-comuni
```

Poi apri `https://cruscotto-italia.piersoftckan.biz/stats/` (auth richiesta).

## Cron giornaliero (opzionale)

Quando il volume di traffico cresce, automatizza con cron:

```bash
sudo crontab -e
```

Aggiungi:

```
# Aggregazione stats Cruscotto Italia (04:00 UTC giornaliero)
0 4 * * * /usr/bin/python3 /home/ubuntu/cruscotto-italia/scripts/analytics/stats_aggregator.py --logs /var/log/nginx/access.log.1 --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --exclude-test-comuni >> /var/log/cruscotto-stats/cron.log 2>&1
```

(Legge `access.log.1` cioè il log della giornata precedente, già ruotato e completo).

## Esclusione dei comuni di test

Il flag `--exclude-test-comuni` rimuove dalle statistiche i 4 comuni usati
durante lo sviluppo (gonfierebbero artificiosamente i conteggi):

- 058091 Roma
- 075035 Lecce
- 077014 Matera
- 097055 Morterone

Per vedere statistiche complete, rimuovi il flag.

## Output

In `/var/www/cruscotto-stats/`:

- `index.html` — pagina di riepilogo con KPI cards + tabelle (visibile via browser)
- `stats.json` — stessi dati in formato JSON (per consumo programmatico)
- `mcp_stats.json` — analytics tool calls MCP (solo se è girato anche `mcp_stats_fetcher.py`)

## Analytics MCP (Worker Cloudflare)

Il Worker MCP scrive in KV `CACHE` un contatore per ogni tool call, chiave:

```
analytics:YYYY-MM-DD:<tool>:<istat>:<client>
```

Dove `istat` è il codice comune (6-digit) se presente negli args del tool, `_` altrimenti;
`client` è una categoria estratta dallo User-Agent (claude/chatgpt/cursor/python/node/curl/browser/other).

TTL automatico 35 giorni. Nessun IP, nessun UA grezzo: privacy AgID compliant.

### Setup fetcher (una volta sola)

1. **Crea un token Cloudflare API** su https://dash.cloudflare.com/profile/api-tokens
   con permessi:
   - `Account` → `Workers KV Storage:Read`
   - Scope: il tuo account

2. **Salva le credenziali fuori dal repo**, ad esempio in `/etc/cruscotto-analytics.env`:

   ```bash
   sudo tee /etc/cruscotto-analytics.env >/dev/null <<EOF
   CF_ACCOUNT_ID=f6973be57e2f5b597beeffdce3f218d1
   CF_KV_NAMESPACE=9251e463afc3406b83f81e555a6e12b7
   CF_API_TOKEN=<token-letto-solo-permesso-KV>
   EOF
   sudo chmod 600 /etc/cruscotto-analytics.env
   ```

3. **Aggiorna il cron** per chiamare prima il fetcher e poi l'aggregator:

   ```
   0 4 * * * set -a && . /etc/cruscotto-analytics.env && set +a && /usr/bin/python3 /home/ubuntu/cruscotto-italia/scripts/analytics/mcp_stats_fetcher.py --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --days 30 >> /var/log/cruscotto-stats/cron.log 2>&1 ; /usr/bin/python3 /home/ubuntu/cruscotto-italia/scripts/analytics/stats_aggregator.py --logs /var/log/nginx/access.log.1 --out /var/www/cruscotto-stats --istat-names /var/www/cruscotto-stats/istat-names.json --mcp-stats /var/www/cruscotto-stats/mcp_stats.json >> /var/log/cruscotto-stats/cron.log 2>&1 && /bin/chown -R www-data:www-data /var/www/cruscotto-stats
   ```

### Esecuzione manuale del fetcher

```bash
set -a && . /etc/cruscotto-analytics.env && set +a
python3 scripts/analytics/mcp_stats_fetcher.py \
  --out /var/www/cruscotto-stats \
  --istat-names /var/www/cruscotto-stats/istat-names.json \
  --days 30
```

### Costi

- Worker piano Free: ammessi 100k requests/giorno, 1k KV writes/giorno, 100k KV reads/giorno.
- Il fetcher fa ~10-50 KV reads via API REST (un bulk batch ogni 100 chiavi).
- Limite operativo prevedibile: ~1000 tool calls/giorno prima di toccare il limite KV writes.
