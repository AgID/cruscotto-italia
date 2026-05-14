# Claude Skills per Cruscotto Italia

Questa cartella contiene i pacchetti di "skill" per Claude (Anthropic) che
documentano l'uso del connettore MCP di Cruscotto Italia. Sono file di
sola documentazione: non vengono eseguiti dal Worker né dal frontend.

Una "skill" Claude è una cartella con un `SKILL.md` (entrypoint con
frontmatter `name`/`description`/`version`) e opzionali file di
riferimento. Claude carica la skill quando il `description` matcha
l'intent dell'utente.

## Pacchetti disponibili

- **`cruscotto-italia-workflow-v1.5.zip`** *(corrente)* — workflow del connettore MCP:
  inventory dei **5 tool** (allineato al cleanup v0.10.0: `mcp_info`, `search_comune`,
  `comune_dashboard`, `comune_opere_dettaglio`, **`anncsu_civico_search`** nuovo),
  schema completo di `comune_dashboard` (21 sezioni fra cui `immobili_pa`, `anncsu`,
  `sanita_mds`, `pun`, `agcom_bbmap`, `carburanti`), endpoint REST non-MCP
  `/data/anncsu_full/<istat>.json`, pattern operativi multi-comune, lookup civici
  via tool dedicato (3 livelli: dashboard sample, `anncsu_civico_search` puntuale,
  endpoint full), lookup sanità territoriale, lookup mobilità elettrica, lookup
  banda larga e lookup carburanti (KPI prezzo medio/min, mix bandiere, freshness,
  pattern di confronto con `_nazionale.json` e pattern API zonale 5km lato client),
  caveat per sezione.

  Allineata a MCP v0.10.0 (18 dataset, 14 istituzioni, 5 tool, ~7.918 comuni).

- **`cruscotto-italia-workflow-v1.4.zip`** *(storica)* — Allineata a MCP v0.9.0
  (18 dataset, 14 istituzioni, 10 tool). Aggiungeva la sezione `carburanti` (MIMIT
  Osservatorio Prezzi). Sostituita da v1.5 dopo il cleanup dei tool ridondanti.

- **`cruscotto-italia-workflow-v1.3.zip`** *(storica)* — Allineata a MCP v0.8.0
  (17 dataset, 13 istituzioni). 20 sezioni dashboard, con `agcom_bbmap`
  ma senza `carburanti`.

- **`cruscotto-italia-workflow-v1.2.zip`** *(storica)* — Allineata a MCP v0.7.0
  (16 dataset, 12 istituzioni). 19 sezioni dashboard, con `pun` ma senza `agcom_bbmap`.

- **`cruscotto-italia-workflow-v1.1.zip`** *(storica)* — Allineata a MCP v0.6.0
  (15 dataset, 11 istituzioni). 18 sezioni dashboard, senza `pun`.

- **`cruscotto-italia-workflow-v1.0.zip`** *(storica)* — versione iniziale.
  Allineata a MCP v0.5.0 (14 dataset, 10 istituzioni). Manteneva 17 sezioni
  dashboard (senza `sanita_mds`).

## Installazione lato Claude

Le skill vanno caricate manualmente nella memoria di Claude (UI o API).
Estrarre lo zip in modo che la cartella `cruscotto-italia-workflow/`
abbia `SKILL.md` in radice e `references/dashboard_schema.md` accanto.

## Aggiornamenti

Nuove versioni vanno pubblicate qui come zip versionato
(`<nome>-v<major>.<minor>.zip`). Mantenere le versioni storiche per
audit. Per modifiche minori di documentazione interna alla skill,
sostituire i file senza incrementare la versione.
