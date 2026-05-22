# Claude Skills per Cruscotto Italia

Questa cartella contiene i pacchetti di "skill" per Claude (Anthropic) che
documentano l'uso del connettore MCP di Cruscotto Italia. Sono file di
sola documentazione: non vengono eseguiti dal Worker né dal frontend.

Una "skill" Claude è una cartella con un `SKILL.md` (entrypoint con
frontmatter `name`/`description`/`version`) e opzionali file di
riferimento. Claude carica la skill quando il `description` matcha
l'intent dell'utente.

## Pacchetto corrente

- **`cruscotto-italia-workflow-v2.0.0.zip`** — workflow del connettore MCP:
  inventario dei **6 tool** (introduce `comune_kpi` per query puntuali e
  confronti tra comuni: ~620 token, **24 gruppi tematici** di KPI
  sintetici), schema completo di `comune_dashboard` (**25 sezioni**
  inclusa la nuova `censimento` per Basi Territoriali ISTAT 2021 con
  119 variabili per sezione di censimento), tool `anncsu_civico_search`
  per query puntuali sui civici, endpoint REST non-MCP
  `/data/anncsu_full/<istat>.json` e `/data/censimento_full/<istat>.geojson`,
  decision tree tool selection, **Pattern 7 censimento sezioni 2021**
  con tabella dei 12 indicatori derivati (densità, % over 65,
  indice vecchiaia, ecc.), pattern operativi multi-comune e caveat
  per sezione.

## Pacchetti storici

Le versioni precedenti restano disponibili per audit:

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
