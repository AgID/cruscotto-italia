/**
 * Handler HTTP non-MCP: info banner, health, admin.
 */

import type { Env } from "./index.js";

export async function handleInfo(_req: Request, _env: Env): Promise<Response> {
  const html = `<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Cruscotto Italia · MCP Server</title>
<meta name="description" content="Server Model Context Protocol che federa dataset pubblici sui comuni italiani. Connettilo a Claude.ai (web e mobile), Claude Desktop, ChatGPT o client MCP generici.">
<!-- Design Italia: stessi token e font del frontend Cruscotto Italia -->
<link rel="stylesheet" href="https://cruscotto-italia.dati.gov.it/css/tokens.css">
<link rel="stylesheet" href="https://cruscotto-italia.dati.gov.it/css/base.css">
<style>
/* === MCP server landing — stili specifici === */
body { padding: 0; }

main.mcp-page {
  max-width: 880px;
  margin: 0 auto;
  padding: var(--sp-7) var(--container-pad) var(--sp-8);
}

.eyebrow {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--mute);
  margin-bottom: var(--sp-4);
}

.mcp-page h1 {
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  letter-spacing: -0.02em;
  line-height: 1.05;
  margin: 0 0 var(--sp-4);
  color: var(--ink);
}
.mcp-page h1 em {
  font-style: normal;
  color: var(--blu-italia);
  font-weight: var(--fw-bold);
}

.lead {
  font-size: var(--fs-md);
  color: var(--ink-soft);
  max-width: 640px;
  margin: 0 0 var(--sp-7);
  line-height: 1.55;
}

.mcp-page h2 {
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
  letter-spacing: -0.01em;
  margin: var(--sp-7) 0 var(--sp-4);
  padding-bottom: var(--sp-3);
  border-bottom: 2px solid var(--blu-italia);
  color: var(--ink);
}
.mcp-page h2 em {
  font-style: normal;
  color: var(--blu-italia);
  font-weight: var(--fw-bold);
}

.mcp-page h3 {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--mute);
  font-weight: var(--fw-semibold);
  margin: var(--sp-5) 0 var(--sp-3);
}

.mcp-page p {
  margin: 0 0 var(--sp-4);
  max-width: 720px;
  font-size: var(--fs-base);
  color: var(--ink);
  line-height: 1.6;
}

code {
  font-family: var(--font-mono);
  font-size: 0.85em;
  background: var(--bg-alt);
  color: var(--ink);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-soft);
}

pre {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  background: var(--ink);
  color: #E6F1FB;
  padding: var(--sp-4) var(--sp-5);
  overflow-x: auto;
  line-height: 1.5;
  margin: var(--sp-3) 0 var(--sp-5);
  border-radius: var(--radius);
  border-left: 4px solid var(--blu-italia);
}
pre code {
  background: none;
  border: none;
  color: inherit;
  padding: 0;
  font-size: inherit;
}

/* Tool list */
.tools {
  border-top: 1px solid var(--border);
  margin-top: var(--sp-3);
}
.tool-row {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: var(--sp-5);
  padding: var(--sp-4) 0;
  border-bottom: 1px solid var(--border-soft);
  align-items: start;
}
.tool-name {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--blu-italia);
  letter-spacing: 0.01em;
}
.tool-desc {
  font-size: var(--fs-sm);
  color: var(--ink-soft);
  line-height: 1.55;
}
.tool-desc .stub {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 10px;
  background: var(--warning);
  color: #fff;
  padding: 2px 8px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-right: var(--sp-2);
  vertical-align: middle;
  border-radius: var(--radius-sm);
  font-weight: var(--fw-semibold);
}

/* Esempi prompt */
.example {
  background: var(--blu-italia-light);
  padding: var(--sp-4) var(--sp-5);
  border-left: 4px solid var(--blu-italia);
  margin: var(--sp-3) 0 var(--sp-4);
  font-size: var(--fs-base);
  color: var(--ink);
  line-height: 1.55;
  border-radius: 0 var(--radius) var(--radius) 0;
}
.example strong {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--blu-italia-dark);
  margin-bottom: var(--sp-2);
  font-weight: var(--fw-semibold);
}

/* Footer link bar */
.mcp-footer {
  margin-top: var(--sp-8);
  padding-top: var(--sp-5);
  border-top: 2px solid var(--border);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--mute);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-5);
  align-items: center;
}
.mcp-footer a {
  color: var(--mute);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: color var(--t-fast), border-color var(--t-fast);
}
.mcp-footer a:hover {
  color: var(--blu-italia);
  border-bottom-color: var(--blu-italia);
}

@media (max-width: 640px) {
  .mcp-page { padding: var(--sp-5) var(--container-pad) var(--sp-7); }
  .tool-row { grid-template-columns: 1fr; gap: var(--sp-1); }
  .mcp-page h1 { font-size: 2rem; }
  .mcp-page h2 { font-size: var(--fs-lg); }
}
</style>
</head>
<body>

<!-- ============ HEADER ISTITUZIONALE ============ -->
<div class="mini-mast">
  <div class="wrap">
    <div class="mini-brand-wrap">
      <a href="https://cruscotto-italia.dati.gov.it" class="mini-brand">Cruscotto <span class="it">Italia</span></a>
      <span class="mini-brand-sub">MCP Server · fonti istituzionali federate</span>
    </div>
  </div>
</div>

<main class="mcp-page">

  <div class="eyebrow">§ MCP Server</div>
  <h1>Cruscotto Italia <em>MCP</em>.</h1>
  <p class="lead">Server <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener">Model Context Protocol</a> che federa dataset pubblici sui comuni italiani (ANAC, BDAP-MOP, SIOPE, PNRR, ISPRA Suolo/IdroGEO/Rifiuti, ISPRA SNPA qualit&agrave; aria, Protezione Civile classificazione sismica comunale, ISTAT POSAS/Censimento/Veicoli/Incidenti, MIUR scuole, ACI nuove iscrizioni, MEF Federalismo Fiscale, MEF Patrimonio Immobiliare, Agenzia delle Entrate ANNCSU civici e strade, Ministero della Salute farmacie/parafarmacie/posti letto ospedalieri, GSE/MASE Piattaforma Unica Nazionale punti di ricarica veicoli elettrici, AGCOM Broadband Map copertura banda larga, MIMIT Osservatorio Prezzi Carburanti, Ministero del Lavoro Registro Unico Nazionale Terzo Settore RUNTS, ISTAT Archivio Statistico Imprese Attive ASIA UL, ISTAT Matrice Pendolarismo 2021 Censimento permanente, ISTAT Basi Territoriali 2021 e Variabili censuarie del Censimento permanente con 119 variabili per sezione di censimento, MiC ArCo + Cultural-ON beni culturali immobili tutelati e Luoghi della Cultura visitabili, ItaliaMeteo ICON-2I previsioni meteorologiche per tutti i comuni). Connettilo al tuo client LLM per interrogare i dati ufficiali con linguaggio naturale.</p>

  <h2>Come <em>connetterlo</em></h2>

  <h3>Claude.ai (web e mobile)</h3>
  <p>Disponibile sui piani <strong>Free</strong> (limite 1 connettore custom), <strong>Pro</strong>, <strong>Max</strong>, <strong>Team</strong> ed <strong>Enterprise</strong>.</p>
  <ol>
    <li>Apri <a href="https://claude.ai/settings/connectors" target="_blank" rel="noopener">claude.ai/settings/connectors</a> (oppure <em>Settings → Connectors</em>).</li>
    <li>Click sul pulsante <strong>+</strong> accanto a "Connectors" → <strong>Add custom connector</strong>.</li>
    <li>Compila il form:
      <ul>
        <li><strong>Name</strong>: <code>Cruscotto Italia</code></li>
        <li><strong>Remote MCP server URL</strong>: <code>https://cruscotto-italia-mcp.agid.workers.dev/mcp</code></li>
      </ul>
    </li>
    <li>Lascia <em>Advanced settings</em> vuoto (nessuna autenticazione richiesta).</li>
    <li>Click <strong>Add</strong> e poi <strong>Connect</strong>.</li>
  </ol>
  <p>Per attivare il connettore in una chat: pulsante <strong>+</strong> in basso a sinistra → <strong>Connectors</strong> → toggle <em>Cruscotto Italia</em>.</p>

  <h3>Claude Desktop</h3>
  <p>Aggiungi al file <code>claude_desktop_config.json</code>:</p>
  <pre><code>{
  "mcpServers": {
    "cruscotto-italia": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://cruscotto-italia-mcp.agid.workers.dev/mcp"
      ]
    }
  }
}</code></pre>
  <p>Riavvia Claude Desktop. Il file di configurazione si trova in <code>~/Library/Application Support/Claude/</code> (macOS) o <code>%APPDATA%\\Claude\\</code> (Windows).</p>

  <h3>Skill <em>opzionale</em> per Claude</h3>
  <p>Per ottenere risposte pi&ugrave; mirate quando l'utente chiede dati su uno o pi&ugrave; comuni italiani, &egrave; disponibile una <em>skill</em> Claude che documenta l'uso del connettore: inventario dei tool, schema completo di <code>comune_kpi</code> (~55 KPI sintetici, ~620 token) e <code>comune_dashboard</code> (vista completa con sezioni <code>immobili_pa</code>, <code>anncsu</code>, <code>sanita_mds</code> per farmacie/ospedali, <code>pun</code> per ricarica EV, <code>agcom_bbmap</code> per banda larga FTTH/FTTC, <code>carburanti</code> per prezzi MIMIT, <code>runts</code> per Terzo Settore, <code>asia</code> per imprese e addetti, <code>pendolarismo</code> per matrice flussi casa-lavoro, <code>censimento</code> per Basi Territoriali ISTAT 2021 con 119 variabili per sezione, <code>beni_culturali</code> per beni immobili tutelati MiC ArCo + Luoghi della Cultura visitabili Cultural-ON come chiese/palazzi/castelli/archeologia/musei/biblioteche/archivi, <code>meteo_italiameteo</code> per previsioni ItaliaMeteo ICON-2I temperatura/precipitazioni/vento/neve), endpoint REST <code>/data/anncsu_full/&lt;istat&gt;.json</code>, <code>/data/censimento_full/&lt;istat&gt;.geojson</code> e <code>/data/beni_culturali_full/&lt;istat&gt;.json</code> e cartografia catastale Agenzia delle Entrate <code>/data/catasto_full/&lt;istat&gt;_map.geojson.gz</code> (fogli) + <code>/data/catasto_full/&lt;istat&gt;_ple.geojson.gz</code> (particelle), pattern operativi e <em>caveat</em> per sezione.</p>
  <p>Scarica il pacchetto e caricalo nelle <em>Skills</em> di Claude (UI o API):</p>
  <p><a href="https://cruscotto-italia.dati.gov.it/data/skills/cruscotto-italia-workflow-v2.5.0.zip" download><strong>cruscotto-italia-workflow-v2.5.0.zip</strong></a></p>

  <h3>ChatGPT (Custom GPT)</h3>
  <p>Nei custom GPT con supporto MCP, aggiungi un nuovo server con URL <code>https://cruscotto-italia-mcp.agid.workers.dev/mcp</code> e tipo JSON-RPC 2.0. Nessuna autenticazione richiesta.</p>

  <h3>Client generico / curl</h3>
  <pre><code>curl -X POST https://cruscotto-italia-mcp.agid.workers.dev/mcp \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'</code></pre>

  <h2>Tool <em>disponibili</em></h2>
  <p>Strumenti MCP. <code>search_comune</code> va sempre chiamato per primo quando l'utente fornisce un nome di comune, per ottenere il codice ISTAT. Per <strong>query puntuali e confronti tra comuni</strong> (es. "popolazione di Bari", "Verona vs Bari su PNRR") usa <code>comune_kpi</code> (~620 token, 55 KPI sintetici). Per <strong>vista dettagliata di un singolo comune</strong> con tutte le sezioni complete (mappe, top liste, time series) usa <code>comune_dashboard</code> (vista completa di tutte le sezioni).</p>

  <div class="tools">
    <div class="tool-row"><div class="tool-name">mcp_info</div><div class="tool-desc">Metadata del server: versione, sorgenti integrate (27), freshness di ogni dataset.</div></div>
    <div class="tool-row"><div class="tool-name">search_comune</div><div class="tool-desc">Risolve un nome di comune in codice ISTAT a 6 cifre. Da chiamare per primo quando hai solo il nome.</div></div>
    <div class="tool-row"><div class="tool-name">comune_kpi</div><div class="tool-desc"><strong>Primo tool da chiamare per query puntuali e confronti.</strong> ~55 KPI sintetici di un comune in risposta leggera (~2.5KB / ~620 token). gruppi tematici: anagrafica, demografia, istruzione, lavoro, redditi MEF, scuole MIUR, contratti ANAC, opere BDAP, PNRR, spese SIOPE, patrimonio PA, ambiente, qualità aria, turismo, veicoli ACI, banda larga AGCOM, ricarica EV, carburanti MIMIT, civici ANNCSU, Terzo Settore RUNTS, sanità, imprese e addetti (ISTAT ASIA UL), pendolarismo casa&ndash;lavoro (ISTAT Censimento permanente 2021), censimento sezioni 2021 (ISTAT Basi Territoriali: n. sezioni, popolazione M/F, famiglie, abitazioni totali/occupate/vuote, edifici residenziali, stranieri UE/extra-UE, occupati 15-64), beni culturali tutelati (MiC ArCo + Cultural-ON: n_beni, n_visitabili, mix categorie, beni per 1000 abitanti). Importi finanziari anche in euro per abitante per facilitare confronti. Usa questo per: <em>"popolazione di Bari"</em>, <em>"Verona vs Bari su PNRR"</em>, <em>"top comuni per banda larga"</em>, <em>"pendolari in uscita da Bergamo"</em>, <em>"chiese tutelate a Matera"</em>. Per dettagli (mappe, top liste, time series) chiama <code>comune_dashboard</code>.</div></div>
    <div class="tool-row"><div class="tool-name">comune_dashboard</div><div class="tool-desc"><strong>Vista dettagliata singolo comune</strong> (~250K token). Stesso ambito di <code>comune_kpi</code> ma include array completi: top categorie merceologiche ANAC, settori BDAP, missioni PNRR, piramide età demografia, time series SIOPE mensili, mappa civici ANNCSU, punti ricarica EV, stabilimenti ospedalieri con discipline, top settori ATECO imprese (ASIA UL), top destinazioni e origini pendolari (Censimento permanente 2021). Sezioni complete: anagrafica, demografia (POSAS), profilo (Censimento ISTAT), turismo, PNRR, territorio (ISPRA Suolo, IdroGEO, Rifiuti; classificazione sismica Protezione Civile zone 1-4), aria (ISPRA SNPA: PM10/PM2.5/NO2), opere pubbliche (BDAP-MOP <strong>dettaglio progetti con CUP filtrato al 2025</strong>), contratti ANAC aggregati, spese SIOPE multi-anno, scuole (MIUR), veicoli e incidenti (ISTAT + ACI), redditi e fisco (MEF IRPEF), patrimonio immobiliare PA (MEF DE), ANNCSU (civici e strade Agenzia Entrate), sanita' territoriale (Ministero Salute), punti di ricarica veicoli elettrici (GSE/MASE PUN), copertura banda larga (AGCOM Broadband Map), distributori carburanti e prezzi (MIMIT Osservatorio Prezzi), enti del Terzo Settore (Min. Lavoro RUNTS - ODV/APS/EF/IS/SMS/ETS), imprese e addetti (ISTAT ASIA UL: unit&agrave; locali, addetti media annua, mix dimensionale, top settori ATECO, serie storica 2018-2023), pendolarismo casa&ndash;lavoro (ISTAT Censimento permanente 2021: matrice OD origine&ndash;destinazione, pendolari in uscita/entrata, saldo giornaliero, indice di auto-contenimento), <strong>censimento sezioni 2021</strong> (ISTAT Basi Territoriali + Variabili censuarie permanenti: KPI comune-level + 119 variabili demografiche/abitative per ogni sezione di censimento; geometrie complete via lazy fetch su <code>/data/censimento_full/&lt;istat&gt;.geojson</code>), <strong>beni culturali</strong> (MiC ArCo + Cultural-ON: 113.817 beni immobili tutelati + 6.603 Luoghi della Cultura visitabili, normalizzati in 11 categorie chiesa/palazzo/castello/archeologia/museo/biblioteca/archivio/monumento/infrastruttura/parco_giardino/altro; KPI + lista compatta cap 30, lista completa via <code>/data/beni_culturali_full/&lt;istat&gt;.json</code>), <strong>meteo</strong> (ItaliaMeteo ICON-2I: temperatura attuale e max/min 24h, precipitazioni, vento, raffica, nuvolosit&agrave;, neve, codice WMO; aggiornamento bi-giornaliero, CC BY 4.0 HVD Meteorologici).</div></div>
    <div class="tool-row"><div class="tool-name">anncsu_civico_search</div><div class="tool-desc">Query puntuali su numeri civici ANNCSU (Agenzia Entrate + ISTAT, HVD UE 2023/138) con filtri server-side su odonimo (substring) e/o civico (esatto). Output: coordinate, quota altimetrica, metodo georeferenziazione. Default 50 risultati, max 500. Per il dataset completo del comune (Roma 515.815, Milano ~280k civici) usare l'endpoint REST <code>GET /data/anncsu_full/&lt;istat&gt;.json</code>. Evita di buttare 500k civici nel context LLM.</div></div>
    <div class="tool-row"><div class="tool-name">censimento_sezione_search</div><div class="tool-desc">Query puntuali o ranking sulle 119 variabili censuarie raw del Censimento Permanente 2021 ISTAT (Basi Territoriali + Variabili censuarie, CC BY 3.0 IT) a livello di singola sezione di censimento sub-comunale. <strong>Distinto da <code>.censimento</code> di <code>comune_dashboard</code></strong> che espone gli aggregati comune-level: qui si lavora al dettaglio della sezione. Due modalit&agrave;: (1) <em>lookup</em> con <code>sez_id</code> per ottenere le 119 vars di una sezione specifica; (2) <em>ranking</em> con <code>var_name</code> (es. P1 popolazione, ST19 stranieri extra-UE, A3 abitazioni vuote, PF1 famiglie) per la top N sezioni, opzionalmente con <code>denominator_var</code> per ranking percentuale (es. var_name=ST19 denominator_var=ST1 = % extra-UE su stranieri). Filtro <code>min_pop</code> per evitare rumore su sezioni minuscole. Le 33% sezioni 'no_vars' (aree non residenziali) escluse automaticamente dal ranking. Use case: <em>"qual &egrave; la sezione di Lecce con pi&ugrave; abitazioni vuote in %?"</em>, <em>"dammi tutte le variabili censuarie della sezione 750350001012"</em>, <em>"top 10 sezioni di Roma per popolazione anziana"</em>.</div></div>
  </div>

  <h3>Sezioni di <code>comune_dashboard</code></h3>
  <p>Tutte le fonti integrate sono esposte come <em>sezioni</em> dentro la risposta di <code>comune_dashboard</code>. Per interrogarle, chiama <code>comune_dashboard(istat_code)</code> e leggi la chiave corrispondente:</p>
  <div class="tools">
    <div class="tool-row"><div class="tool-name">.aria</div><div class="tool-desc">Stazioni qualità dell'aria ISPRA SNPA (PM10, PM2.5, NO2): tipo zona/stazione, EU code, ultima rilevazione.</div></div>
    <div class="tool-row"><div class="tool-name">.scuole</div><div class="tool-desc">Anagrafe scuole MIUR: numero plessi, ordine/grado, indirizzi.</div></div>
    <div class="tool-row"><div class="tool-name">.veicoli</div><div class="tool-desc">Parco veicoli ISTAT 41_993 (classe Euro), nuove iscrizioni ACI LOD per alimentazione, incidenti ISTAT 41_983 (morti/feriti).</div></div>
    <div class="tool-row"><div class="tool-name">.redditi</div><div class="tool-desc">Dichiarazioni IRPEF su base comunale (MEF Dipartimento Finanze, a.i. 2020-2024): n. contribuenti, reddito medio, 8 fasce, addizionale, imposta netta.</div></div>
    <div class="tool-row"><div class="tool-name">.immobili_pa</div><div class="tool-desc">Beni immobili pubblici (MEF DE 2022): KPI aggregati su fabbricati/terreni con vincolo culturale e uso a terzi + fino a 500 punti georeferenziati con categoria.</div></div>
    <div class="tool-row"><div class="tool-name">.anncsu</div><div class="tool-desc">Civici e strade certificati Agenzia Entrate (HVD UE 2023/138): KPI odonimi, civici, % georeferenziazione, bilinguismo, distribuzione metodi. Sample 1000 punti. Per il dataset completo: <code>GET /data/anncsu_full/&lt;istat&gt;.json</code> (Roma 515.815 civici, Lecce 47.917). Per query puntuali su odonimo/civico usa il tool dedicato <code>anncsu_civico_search</code>.</div></div>
    <div class="tool-row"><div class="tool-name">.sanita_mds</div><div class="tool-desc">Bundle sanità territoriale Ministero della Salute (IODL v2.0): farmacie (~20.800 attive, dato quotidiano), parafarmacie (~7.200), posti letto ospedalieri 2023 (1.272 stabilimenti, ~213.000 PL). Geocoding incrociato con ANNCSU per coord MdS errate (campi lat_raw/lon_raw + coord_source: mds/anncsu/dropped/no_coord).</div></div>
    <div class="tool-row"><div class="tool-name">.pun</div><div class="tool-desc">Punti di ricarica veicoli elettrici (GSE/MASE Piattaforma Unica Nazionale): 66.619 PdR EVSE su 5.185 comuni (65,7% copertura), aggiornamento quotidiano. KPI per stato (Attivo/Non Attivo), tipologia corrente (AC/DC), categoria potenza (Slow/Quick/Fast/HPC/Ultra fast), potenza totale kW e mix. Punti georeferenziati con indirizzo, CAP, orario, restrizioni. Licenza CC BY 4.0 ex art. 52 c.2 CAD (open by default).</div></div>
    <div class="tool-row"><div class="tool-name">.agcom_bbmap</div><div class="tool-desc">Copertura banda larga AGCOM Broadband Map (BBmap, art. 22 Codice Comunicazioni Elettroniche): 7.896/7.896 comuni (100% copertura nazionale), aggiornamento trimestrale (dato corrente al 31/12/2025). KPI: copertura FTTH DESI %, copertura FTTH entro 20m %, confidenza DESI %, famiglie residenti, famiglie raggiunte FTTH (totali e a meno di 20m), celle 20×20m raggiunte da FTTH/FTTC, punti dichiarati e geo-distinti, indirizzi postali raggiunti. La rete dettagliata (polilinee strade FTTH/rame) è linkata via deep-link al Web AppBuilder ufficiale AGCOM. Licenza CC BY 4.0 ex art. 52 c.2 CAD (open by default).</div></div>
    <div class="tool-row"><div class="tool-name">.carburanti</div><div class="tool-desc">Distributori di carburante e prezzi praticati (MIMIT Osservatorio Prezzi Carburanti, art. 51 L.99/2009): ~23.700 impianti su 5.324 comuni (69%), snapshot quotidiano "Prezzo alle 8 di mattina". KPI: totale impianti, split Stradali/Autostradali, pompe bianche %, mix bandiere (top 5 + Altre), prezzo medio per 7 carburanti (Benzina/Gasolio self e servito, GPL, Metano, HVO), prezzo minimo nel comune, freshness % prezzi aggiornati negli ultimi 7 giorni. Tutti i punti georeferenziati con prezzi correnti e carburanti premium proprietari (Shell V-Power, Hi-Q, ecc.) in prezzi_extra. Licenza IODL 2.0 (compatibile CC BY).</div></div>
    <div class="tool-row"><div class="tool-name">.runts</div><div class="tool-desc">Enti del Terzo Settore iscritti al Registro Unico Nazionale (Min. Lavoro RUNTS, D.Lgs 117/2017 art. 53): 145.898 enti su 7.547 comuni (95,3% copertura). KPI: totale enti, mix per sezione (ODV organizzazioni di volontariato, APS associazioni di promozione sociale, EF enti filantropici, IS imprese sociali, SMS societ&agrave; di mutuo soccorso, ETS altri enti del Terzo Settore), numero e percentuale enti iscritti al beneficio 5x1000, n. enti aderenti a reti associative, iscrizioni per anno. Mix nazionale: APS 47% &gt; ODV 27% &gt; IS 15% &gt; ETS 11% &gt; EF 0,4% &gt; SMS 0,1%. Lista enti con codice fiscale, repertorio, denominazione, sezione, legale rappresentante, rete, 5x1000, data iscrizione (cap 5000 enti ordinati per data discendente; Roma 6.616, Milano 3.716). Aggiornamento quotidiano (XLSX bulk PostBack ASP.NET). Licenza CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) - open data di default delle PA; pubblicato ai sensi del D.Lgs 117/2017 art. 53 (pubblicit&agrave; legale RUNTS).</div></div>
    <div class="tool-row"><div class="tool-name">.asia</div><div class="tool-desc">Imprese e addetti per comune (ISTAT - Archivio Statistico Imprese Attive Unit&agrave; Locali, ASIA UL): copertura 100% dei 7.896 comuni. KPI: numero unit&agrave; locali (UL) attive, addetti media annua, dimensione media UL (addetti/UL), variazione % year-on-year, mix per classe dimensionale (W0_9 micro, W10_49 piccole, W50_249 medie, W_GE250 grandi), top 10 settori per UL e per addetti (ATECO 2 cifre NACE Rev.2 - 88 divisioni economiche), serie storica 2018-2023 (6 anni), dettaglio completo per ogni settore × classe addetti per l'anno pi&ugrave; recente. Dataflow SDMX 183_1163_DF_DICA_ASIAULP_TERRIFDATA_7 da esploradati.istat.it. Caveat: ASIA conta unit&agrave; locali, non imprese giuridiche — un'impresa con pi&ugrave; sedi in comuni diversi compare in ciascun comune. Aggiornamento annuale (~Q4 ISTAT, latency ~2 anni: nel 2026 latest_year=2023). Licenza CC BY 3.0 IT.</div></div>
    <div class="tool-row"><div class="tool-name">.pendolarismo</div><div class="tool-desc">Matrice di pendolarismo casa&ndash;lavoro del Censimento permanente 2021 (ISTAT, pubblicato il 2 ottobre 2025): rilevazione degli spostamenti quotidiani per motivi di lavoro della popolazione residente per coppia origine&ndash;destinazione tra comuni italiani. NOTA: ISTAT distribuisce per il Censimento 2021 solo la matrice per motivo lavoro, non quella per studio. KPI per comune: pendolari totali in uscita, pendolari in entrata, saldo giornaliero, indice di auto-contenimento (% di residenti che lavorano nel proprio comune), top 10 destinazioni e top 10 origini dei flussi. Copertura 100% dei 7.896 comuni, ~17,3M flussi pendolari totali. Aggiornamento decennale (prossimo: Censimento permanente 2031). Licenza CC BY 3.0 IT.</div></div>
    <div class="tool-row"><div class="tool-name">.censimento</div><div class="tool-desc">ISTAT Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021 (CC BY 3.0 IT). Geometrie delle 756.376 sezioni di censimento nazionali (poligoni WGS84 EPSG:4326 RFC 7946) accorpate per comune, integrate con 119 variabili demografiche/abitative per sezione: popolazione totale + sesso (P1-P3), 16 fasce et&agrave; 5-anni per totale/maschi/femmine (P14-P82), titolo di studio (P86-P100 nessuno/elementare/media/diploma/terziario per sesso), occupati 15-64 (P101-P103), italiani per fascia et&agrave; (IT1-IT12), stranieri UE/extra-UE per sesso/et&agrave;/occupazione (ST1-ST33), famiglie per numero componenti 1-6+ (PF1, PF3-PF8), abitazioni occupate/vuote/totali (A2, A3, A8), edifici residenziali (E3). Copertura 7904/7896 comuni (100% incluso TN/BZ). 252.467 sezioni 'no_vars' (33%) sono aree non residenziali (parchi, aree industriali, infrastrutture) non rilevate per assenza di residenti. KPI comune-level pre-calcolati in <code>.censimento</code> (~3-5 KB), geometrie complete + 119 variabili in <code>/data/censimento_full/&lt;istat&gt;.geojson</code> lazy-fetch (30 KB - 3 MB). Aggiornamento decennale (ultimo rilascio 14/05/2026).</div></div>
    <div class="tool-row"><div class="tool-name">.meteo</div><div class="tool-desc">Previsioni meteorologiche ItaliaMeteo ICON-2I (Agenzia Nazionale per la Meteorologia e Climatologia + Cineca): temperatura 2m attuale e max/min prossime 24h, precipitazioni cumulate 24h, umidit&agrave; relativa, velocit&agrave; e direzione vento 10m, raffica massima 24h, copertura nuvolosa, altezza neve, codice meteo WMO con descrizione italiana. Griglia 2,2&nbsp;km, copertura 7.895/7.895 comuni (100%). Aggiornamento bi-giornaliero (corse 00 e 12&nbsp;UTC, disponibili ~03:30 e ~14:30&nbsp;UTC). Licenza CC&nbsp;BY&nbsp;4.0 &middot; HVD Meteorologici (Regolamento UE 2023/138).</div></div>
    <div class="tool-row"><div class="tool-name">.beni_culturali</div><div class="tool-desc">Ministero della Cultura (MiC) — unione dei Linked Open Data ICCD/ArCo (Architecture of Knowledge, 113.817 beni culturali immobili tutelati: chiese, palazzi, castelli, ville, aree archeologiche, monumenti, parchi e giardini storici, edifici di culto) e Cultural-ON DBUnico 2.0 (6.603 Luoghi della Cultura visitabili: musei, biblioteche, archivi, aree archeologiche con orari di apertura e contatti). Per ogni bene: denominazione, tipologia ArCo granulare, indirizzo, coordinate WKT, foto, descrizione, soprintendenza di tutela. Categorie normalizzate in 11 macro-classi (chiesa, palazzo, castello, archeologia, museo, biblioteca, archivio, monumento, infrastruttura, parco_giardino, altro) + campo <code>fonte</code> (arco|cultural_on). KPI per comune: n_totale, n_arco, n_cultural_on, n_visitabili, n_con_coordinate, mix_categoria, pct_con_foto, pct_con_descrizione, beni_per_1000_ab. Copertura 6088/7896 comuni (77,1%). Lista compatta nello shard base (cap 30 beni), lista completa in <code>/data/beni_culturali_full/&lt;istat&gt;.json</code> per comuni grandi. Aggiornamento mensile via endpoint SPARQL <code>dati.beniculturali.it</code>. Licenza CC BY 4.0.</div></div>
  </div>

  <h2>Esempi di <em>prompt</em></h2>

  <div class="example"><strong>Panoramica</strong>Dammi una panoramica del comune di San Giovanni in Fiore: popolazione, opere pubbliche in corso, progetti PNRR.</div>

  <div class="example"><strong>Confronto</strong>Confronta consumo di suolo e raccolta differenziata tra Bergamo e Brescia.</div>

  <div class="example"><strong>Ricerca tematica</strong>Quali sono i 5 comuni con più progetti PNRR sulla missione M2 (Rivoluzione verde) tra Catanzaro, Cosenza, Crotone, Reggio Calabria e Vibo Valentia?</div>

  <div class="example"><strong>Analisi finanziaria</strong>Quanto ha speso il comune di Lecce nel 2025 per voci legate al personale? Mostra le top-10 voci SIOPE.</div>

  <div class="example"><strong>Sanita' territoriale</strong>Quante farmacie attive ci sono a Matera e qual è il rapporto abitanti/farmacia? Confronta con Potenza.</div>

  <div class="example"><strong>Civici e indirizzi</strong>Quanti civici certificati ANNCSU ci sono in via Roma a Lecce, e con quale metodo di georeferenziazione sono stati rilevati?</div>

  <div class="example"><strong>Catasto</strong>Trovami le coordinate del civico 29 di Via Vittorio Emanuele II a Lecce e il foglio e la particella catastale a cui appartiene.</div>

  <div class="example"><strong>Mobilità elettrica</strong>Quanti punti di ricarica attivi ci sono a Milano e qual è la percentuale di colonnine HPC/Ultra fast? Confronta con Roma e Torino.</div>

  <h2>Note <em>tecniche</em></h2>
  <p>Endpoint MCP: <code>POST /mcp</code> · Health check: <code>GET /health</code> · Rate limit: 60 richieste al minuto · Cache: 1 ora · Trasporto: JSON-RPC 2.0 over HTTP.</p>
  <p>Tutti i dati provengono da open data ufficiali con licenza CC-BY 4.0, IODL 2.0 o, in assenza di licenza espressa da parte di soggetti art. 2 c.2 CAD, CC BY 4.0 ai sensi del principio "open by default" di cui all'art. 52 c.2 D.Lgs 82/2005 (Codice dell'Amministrazione Digitale) e delle Linee Guida Open Data AgID (Det. 183/2023). L'aggregazione per codice ISTAT è eseguita da pipeline Python che pubblicano su Cloudflare R2; il worker fa solo lookup e composizione.</p>

  <footer class="mcp-footer">
    <a href="https://cruscotto-italia.dati.gov.it">↗ Frontend Cruscotto Italia</a>
    <a href="https://github.com/AgID/cruscotto-italia">↗ Codice GitHub</a>
    <a href="https://modelcontextprotocol.io">↗ Model Context Protocol</a>
  </footer>

</main>

</body>
</html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

export async function handleHealth(_req: Request, env: Env): Promise<Response> {
  // Verifica che DATA_BASE_URL e KV siano raggiungibili (B1: no R2 binding)
  let r2Ok = false;
  let kvOk = false;
  try {
    const r = await fetch(`${env.DATA_BASE_URL}/manifest.json`, {
      method: "HEAD",
      headers: env.DATA_BASIC_AUTH ? { "Authorization": `Basic ${env.DATA_BASIC_AUTH}` } : {},
    });
    r2Ok = r.ok;
  } catch {
    /* manifest may not exist yet on first deploy — treat as warning */
  }
  try {
    await env.CACHE.get("__health_probe__");
    kvOk = true;
  } catch {
    /* same */
  }

  const status = { service: "cruscotto-italia-mcp", version: "0.17.1", r2: r2Ok, kv: kvOk, timestamp: new Date().toISOString() };
  return new Response(JSON.stringify(status), {
    headers: { "Content-Type": "application/json" },
  });
}

export async function handleAdmin(req: Request, env: Env): Promise<Response> {
  // Auth: bearer token
  const auth = req.headers.get("Authorization");
  if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) {
    return new Response("Unauthorized", { status: 401 });
  }

  const url = new URL(req.url);
  if (url.pathname === "/admin/cache/purge" && req.method === "POST") {
    // Purge KV cache (lista chiavi e cancella in batch)
    const list = await env.CACHE.list({ prefix: "q:" });
    await Promise.all(list.keys.map((k) => env.CACHE.delete(k.name)));
    return new Response(JSON.stringify({ purged: list.keys.length }), {
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response("Admin route not found", { status: 404 });
}

/**
 * Pass-through R2 per shard ANNCSU FULL (Opzione C lazy fetch frontend).
 *
 * Espone /data/anncsu_full/<istat>.json mappato direttamente all'oggetto
 * R2 anncsu_full/<istat>.json. Risposta:
 *  - 200 + JSON body con cache 24h (sono dati immutabili, snapshot fissato)
 *  - 404 se shard non esiste (comune senza civici geo-ref, es. Aosta)
 *  - 503 se R2 down
 *
 * Sicurezza: solo questo path è esposto (validato in router con regex
 * /^\d{6}$/), non si può navigare in altre directory del bucket.
 *
 * Performance: l'utente scarica 0.4MB (Lecce) - 4.3MB (Roma) gzippato.
 * R2 egress è gratuito su Cloudflare quindi nessun cost concern.
 */
export async function handleDataAnncsuFull(istat: string, env: Env): Promise<Response> {
  const key = `anncsu_full/${istat}.json`;
  try {
    const r = await fetch(`${env.DATA_BASE_URL}/${key}`, {
      cf: { cacheTtl: 86400, cacheEverything: true },
      headers: env.DATA_BASIC_AUTH ? { "Authorization": `Basic ${env.DATA_BASIC_AUTH}` } : {},
    });
    if (r.status === 404) {
      return new Response(
        JSON.stringify({ error: "not_found", istat, key }),
        {
          status: 404,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          },
        }
      );
    }
    if (!r.ok) {
      throw new Error(`fetch ${r.status} on ${key}`);
    }
    return new Response(r.body, {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        // Body gzippato passthrough.
        "Cache-Control": "public, max-age=86400, immutable",
        "Access-Control-Allow-Origin": "*",
        // ETag già fornito da R2 per cache validation lato browser.
        ...(r.headers.get("etag") ? { "ETag": r.headers.get("etag") as string } : {}),
      },
    });
  } catch (err) {
    console.error("handleDataAnncsuFull error:", err);
    return new Response(
      JSON.stringify({ error: "r2_unavailable", message: String(err) }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}

/**
 * Serve un file della cartella skills/ su R2 come download zip.
 * Path consentiti: skills/<name>.zip (validazione regex nel routing).
 * Es: GET /skills/cruscotto-italia-workflow-v1.0.zip
 */
export async function handleDataSkill(filename: string, env: Env): Promise<Response> {
  const key = `skills/${filename}`;
  try {
    const r = await fetch(`${env.DATA_BASE_URL}/${key}`, {
      cf: { cacheTtl: 86400, cacheEverything: true },
      headers: env.DATA_BASIC_AUTH ? { "Authorization": `Basic ${env.DATA_BASIC_AUTH}` } : {},
    });
    if (r.status === 404) {
      return new Response(
        JSON.stringify({ error: "not_found", key }),
        {
          status: 404,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          },
        }
      );
    }
    if (!r.ok) {
      throw new Error(`fetch ${r.status} on ${key}`);
    }
    return new Response(r.body, {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${filename}"`,
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        ...(r.headers.get("etag") ? { "ETag": r.headers.get("etag") as string } : {}),
      },
    });
  } catch (err) {
    console.error("handleDataSkill error:", err);
    return new Response(
      JSON.stringify({ error: "r2_unavailable", message: String(err) }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}
