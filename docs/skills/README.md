# Claude Skills per Cruscotto Italia

Questa cartella contiene i pacchetti di "skill" per Claude (Anthropic) che
documentano l'uso del connettore MCP di Cruscotto Italia. Sono file di
sola documentazione: non vengono eseguiti dal Worker né dal frontend.

Una "skill" Claude è una cartella con un `SKILL.md` (entrypoint con
frontmatter `name`/`description`/`version`) e opzionali file di
riferimento. Claude carica la skill quando il `description` matcha
l'intent dell'utente.

## Pacchetto corrente

- **`cruscotto-italia-workflow-v2.3.0.zip`** — workflow del connettore MCP:
  inventario dei **6 tool** (incluso `censimento_sezione_search`
  per ranking/lookup sulle 119 variabili censuarie raw a livello di
  sezione di censimento sub-comunale; `comune_kpi` **25 gruppi tematici**;
  `comune_dashboard` 25 sezioni; `anncsu_civico_search` per civici;
  `search_comune` + `mcp_info`), endpoint REST non-MCP
  `/data/anncsu_full/<istat>.json`, `/data/censimento_full/<istat>.geojson`
  e `/data/beni_culturali_full/<istat>.json`, decision tree tool selection,
  **Pattern 8 nuovo: beni culturali e patrimonio tutelato** (con esempi
  d'uso "quanti monumenti tutelati a Matera", "top 10 comuni per beni
  culturali", "chiese e palazzi tutelati a Lecce"), catalogo codici
  variabili ISTAT più frequenti, pattern operativi multi-comune e
  caveat per sezione (incluso nuovo caveat `beni_culturali_mic` con
  spiegazione ArCo vs Cultural-ON e categorie normalizzate 9-classi).

## Pacchetti storici

Le versioni precedenti restano disponibili per audit:

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
