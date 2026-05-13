# Claude Skills per Cruscotto Italia

Questa cartella contiene i pacchetti di "skill" per Claude (Anthropic) che
documentano l'uso del connettore MCP di Cruscotto Italia. Sono file di
sola documentazione: non vengono eseguiti dal Worker né dal frontend.

Una "skill" Claude è una cartella con un `SKILL.md` (entrypoint con
frontmatter `name`/`description`/`version`) e opzionali file di
riferimento. Claude carica la skill quando il `description` matcha
l'intent dell'utente.

## Pacchetti disponibili

- **`cruscotto-italia-workflow-v1.1.zip`** *(corrente)* — workflow del connettore MCP:
  inventory dei 10 tool, schema completo di `comune_dashboard` (18 sezioni
  fra cui `immobili_pa`, `anncsu` e **`sanita_mds`**), endpoint REST non-MCP
  `/data/anncsu_full/<istat>.json`, pattern operativi multi-comune,
  lookup civici e lookup sanità territoriale (farmacie/parafarmacie/ospedali),
  caveat per sezione (turismo NUTS3, mobilità 2019, `comune_contratti` stub,
  aria sparsa, PNRR solo Soggetto Attuatore, posti letto ospedalieri MdS fermi al 2023).

  Allineato a MCP v0.6.0 (15 dataset, 11 istituzioni, ~7.918 comuni).

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
