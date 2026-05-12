# Note per la prossima sessione

Ultimo aggiornamento: 2026-05-08, dopo sessione tab Opere filtrabile.

## Cosa abbiamo fatto in questa sessione

- Tab Opere completamente filtrabile (pattern UX uniforme con Spese SIOPE)
- 7802 shard BDAP per comune su R2 (filtri 2025: data inizio >= 2025 OR stato ATTIVO)
- Worker tool nuovo `comune_opere_dettaglio`
- Discovery completa API ANAC: NON filtrabile per CF, solo bulk download
- Top settori BDAP e Top CPV ANAC ora ordinati per importo (insight migliore)
- Fix UX: 9999-12-31 reso come "In corso", date visibili in header riga

## Stato MVP (commit 67fffb1)

3 tab "veri" su 5 con UX armonica filtrabile:
- Contratti (top CPV per importo, MA solo marzo 2026 = limitato)
- Opere (filtri completi, dettaglio finanziamenti, 7800 comuni)
- Spese SIOPE (filtri completi, sparkline mensile, on-demand)

2 tab pending:
- Coesione (OpenCoesione, ETL nuovo)
- Demografia (POSAS gia scaricato, basta riusare)

## Pending alta priorita (ordine consigliato)

1. ANAC multi-mese (sblocca tab Contratti filtrabile)
   - ETL gia pronto in etl/sources/anac.py con streaming + dual-schema OCDS
   - 11 mesi 2025 disponibili (gen-nov, dic mancante)
   - Volume 38 GB JSON ma streaming via ijson on-the-fly
   - Throughput ~110 releases/sec, tempo stimato ~9 ore in nohup
   - Schema dual-version gia gestito (2025 e 2026 diversi)

2. Tab Contratti filtrabile (dopo ETL ANAC esteso)
   - Worker tool nuovo comune_contratti_dettaglio (analogo opere_dettaglio)
   - Shard JSON per comune con tutti i CIG del 2025
   - Frontend: search + dropdown CPV categoria + sort
   - Click espande con: data aggiudicazione, fornitore, valore

3. Tab Demografia (rapido, POSAS gia scaricato)
   - File in /tmp/posas-test/extracted/POSAS_2026_it_Comuni.csv
   - Estrarre matrice eta x sesso, fare piramide
   - KPI: popolazione, % over 65, indice di vecchiaia, eta media
   - Tempo stimato 30 min

4. Tab Coesione (OpenCoesione)
   - ETL nuovo, dataset OpenCoesione progetti UE per CUP
   - Mix per fondo (FESR, FSE, FSC) come dato chiave
   - Tempo stimato 45-60 min

5. v0.2 BDAP Localizzazione
   - CSV gia su disco a /tmp/cruscotto-bdap-cache/localizzazione-utf8.csv
   - Aggiungere "opere realizzate sul territorio (non gestite dal Comune)"

6. Card Spese in Panoramica
   - Oggi mostra "—" per via del lazy load
   - Una chiamata comune_spese in background dopo render Panoramica

## Comandi rapidi resume

ssh root@MCP-SERVER

cd /home/ubuntu/cruscotto-italia

source .venv/bin/activate

git pull

(.bashrc ha gia tutte le env: R2_*, CLOUDFLARE_API_TOKEN)

## Cache locali persistenti

- /tmp/cruscotto-anac-cache (download mensili ANAC + parquet)
- /tmp/cruscotto-bdap-cache (CSV Progetti + Localizzazione)
- /tmp/test-bdap-shard/output (7802 shard gia pronti, riusabili)
- /tmp/posas-test (CSV ISTAT POSAS popolazione)
- /tmp/anac-2025-test (sample 100MB ottobre 2025 per validation)

## Stack reference

- Server: Aruba 31.14.139.9
- GitHub: github.com/piersoft/cruscotto-italia
- Worker MCP: cruscotto-italia-mcp.datigovit.workers.dev/mcp
- Frontend: cruscotto-italia.piersoftckan.biz
- R2 bucket: cruscotto-italia-data
- Aggregati R2 attivi:
  - lookup/comuni-bundle.json (anagrafica unificata)
  - lookup/anac-aggregato.json (78% coverage, marzo 2026)
  - lookup/bdap-aggregato.json (99.4% coverage, top per costo)
  - bdap/dettaglio/<istat>.json (7802 file, ~120MB totali)
- Worker tools: comune_overview, comune_contratti, comune_opere_dettaglio,
  comune_spese, search_comune, mcp_info
