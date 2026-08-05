# Claude Skills per Cruscotto Italia

Questa cartella contiene i pacchetti di "skill" per Claude (Anthropic) che
documentano l'uso del connettore MCP di Cruscotto Italia. Sono file di
sola documentazione: non vengono eseguiti dal Worker né dal frontend.

Una "skill" Claude è una cartella con un `SKILL.md` (entrypoint con
frontmatter `name`/`description`/`version`) e opzionali file di
riferimento. Claude carica la skill quando il `description` matcha
l'intent dell'utente.

## Pacchetto corrente

- **`cruscotto-italia-workflow-v2.9.1.zip`** — workflow del connettore MCP:
  **6 tool** (`mcp_info`, `search_comune`, `comune_kpi` ~620 token con 55 KPI in
  24 gruppi tematici, `comune_dashboard` ~250K token con le sezioni dettagliate,
  `anncsu_civico_search`, `censimento_sezione_search`), **28 dataset** integrati
  da **18 istituzioni fonte**, ~7.918 comuni coperti.
  Include il decision tree per la scelta del tool, i workflow pattern operativi
  (query puntuale, confronto multi-comune, vista approfondita, civico specifico,
  dettaglio opere pubbliche), il catalogo dei codici delle variabili censuarie
  e i caveat per sezione. Riferimenti: `references/dashboard_schema.md` e
  `references/catasto_geocoding.md`.
  Ultima aggiunta rispetto alla 2.5.0: morfologia del territorio CNR-IRPI
  HR-DTM 5m (`kpi_summary.morfologia_cnr` con quota, pendenza, esposizione,
  irraggiamento) e meteo ItaliaMeteo ICON-2I.

## Skill CLI (eseguibile)

- **`cruscotto-cli-v0.1.2.zip`** — a differenza dei pacchetti `cruscotto-italia-workflow`,
  questa skill **contiene codice eseguibile** (`scripts/cruscotto.py`, solo stdlib
  Python 3) e non documenta il connettore MCP: interroga direttamente gli shard
  JSON statici, via HTTPS pubblico oppure da filesystem locale
  (`CRUSCOTTO_BASE=/var/www/cruscotto-italia/data` sulla VM).
  Scopo: ridurre il consumo di contesto filtrando i dati **prima** che entrino nel
  modello, evitando di caricare shard da centinaia di KB per leggere pochi campi.
  Comandi: `find`, `kpi`, `sezioni`, `sez`, `q`, `full` (beni culturali, civici
  ANNCSU, censimento per sezione), `vars`.
  Guardrail: dump vietato sulle 6 sezioni pesanti, cap di output a 20 KB,
  filtro obbligatorio sugli archivi estesi, esclusione automatica delle sezioni
  censuarie non residenziali e **registro esplicito dei denominatori leciti**
  (verificato sui dati: es. il titolo di studio si rapporta a `P83`,
  popolazione 9 anni e piu', non a `P1`).
  Non sostituisce il Worker MCP, che resta il canale per i client AI esterni.
  Il sorgente e' versionato in `docs/skills/cruscotto-cli/`, lo zip e' l'artefatto
  di distribuzione.
  Versione 0.1.1: gestione corretta della verifica TLS. Il contesto parte sempre
  dallo store di sistema (che onora `SSL_CERT_FILE` e le CA aziendali) e ricorre
  a `certifi` solo se lo store risulta vuoto, come accade con il Python di
  python.org su macOS. In caso di errore lo script indica le soluzioni possibili
  e la skill vieta esplicitamente di disattivare la verifica del certificato.
  Versione 0.1.2: protezione del server. Lo script impone un intervallo minimo
  fra le richieste e si ferma oltre un numero massimo di comuni distinti in una
  finestra di dieci minuti; lo stato e' su file con lock esclusivo, perche' ogni
  invocazione e' un processo separato e il rischio reale sono le richieste
  parallele. La skill dichiara inoltre che non esistono aggregati regionali o
  provinciali e vieta di ricostruirli scaricando interi territori.

- `cruscotto-cli-v0.1.1.zip` (storico)

- `cruscotto-cli-v0.1.0.zip` (storico)

## Pacchetti storici

Le versioni precedenti restano disponibili per audit:

- `cruscotto-italia-workflow-v2.5.0.zip`

- `cruscotto-italia-workflow-v2.4.4.zip`

- `cruscotto-italia-workflow-v2.4.3.zip`

- `cruscotto-italia-workflow-v2.4.2.zip`

- `cruscotto-italia-workflow-v2.4.1.zip`

- `cruscotto-italia-workflow-v2.4.0.zip`

- `cruscotto-italia-workflow-v2.3.0.zip`

- `cruscotto-italia-workflow-v2.2.0.zip`

- `cruscotto-italia-workflow-v2.1.0.zip` (skill prima della 25a fonte beni_culturali MiC)

- `cruscotto-italia-workflow-v2.0.0.zip` (skill prima del 6° tool)
- `cruscotto-italia-workflow-v1.9.0.zip` (skill pre-censimento BT)
- `cruscotto-italia-workflow-v1.8.1.zip`
- `cruscotto-italia-workflow-v1.8.0.zip`
- `cruscotto-italia-workflow-v1.7.0.zip`
- `cruscotto-italia-workflow-v1.6.0.zip`
- `cruscotto-italia-workflow-v1.5.zip`
- `cruscotto-italia-workflow-v1.4.zip`
- `cruscotto-italia-workflow-v1.3.zip`
- `cruscotto-italia-workflow-v1.2.zip`
- `cruscotto-italia-workflow-v1.1.zip`
- `cruscotto-italia-workflow-v1.0.zip`

## Installazione lato Claude

Le skill vanno caricate manualmente nella memoria di Claude (UI o API).
Estrarre lo zip in modo che la cartella `cruscotto-italia-workflow/`
abbia `SKILL.md` in radice e `references/dashboard_schema.md` accanto.

## Aggiornamenti

Nuove versioni vanno pubblicate qui come zip versionato
(`<nome>-v<major>.<minor>.zip`). Mantenere le versioni storiche per
audit. Per modifiche minori di documentazione interna alla skill,
sostituire i file senza incrementare la versione.
