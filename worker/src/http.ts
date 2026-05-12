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
<meta name="description" content="Server Model Context Protocol che federa tredici dataset pubblici sui comuni italiani. Connettilo a Claude Desktop, ChatGPT o client MCP generici.">
<!-- Design Italia: stessi token e font del frontend Cruscotto Italia -->
<link rel="stylesheet" href="https://cruscotto-italia.piersoftckan.biz/css/tokens.css">
<link rel="stylesheet" href="https://cruscotto-italia.piersoftckan.biz/css/base.css">
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
      <a href="https://cruscotto-italia.piersoftckan.biz" class="mini-brand">Cruscotto <span class="it">Italia</span></a>
      <span class="mini-brand-sub">MCP Server · 13 fonti istituzionali</span>
    </div>
  </div>
</div>

<main class="mcp-page">

  <div class="eyebrow">§ MCP Server · v0.4</div>
  <h1>Cruscotto Italia <em>MCP</em>.</h1>
  <p class="lead">Server <a href="https://modelcontextprotocol.io" target="_blank" rel="noopener">Model Context Protocol</a> che federa tredici dataset pubblici sui comuni italiani (ANAC, BDAP-MOP, SIOPE, PNRR, ISPRA, ISTAT, MIUR, ACI, MEF Federalismo Fiscale, MEF Patrimonio Immobiliare). Connettilo al tuo client LLM per interrogare i dati ufficiali con linguaggio naturale.</p>

  <h2>Come <em>connetterlo</em></h2>

  <h3>Claude Desktop</h3>
  <p>Aggiungi al file <code>claude_desktop_config.json</code>:</p>
  <pre><code>{
  "mcpServers": {
    "cruscotto-italia": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://cruscotto-italia-mcp.piersoftckan.biz/mcp"
      ]
    }
  }
}</code></pre>
  <p>Riavvia Claude Desktop. Il file di configurazione si trova in <code>~/Library/Application Support/Claude/</code> (macOS) o <code>%APPDATA%\\Claude\\</code> (Windows).</p>

  <h3>ChatGPT (Custom GPT)</h3>
  <p>Nei custom GPT con supporto MCP, aggiungi un nuovo server con URL <code>https://cruscotto-italia-mcp.piersoftckan.biz/mcp</code> e tipo JSON-RPC 2.0. Nessuna autenticazione richiesta.</p>

  <h3>Client generico / curl</h3>
  <pre><code>curl -X POST https://cruscotto-italia-mcp.piersoftckan.biz/mcp \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'</code></pre>

  <h2>Tool <em>disponibili</em></h2>
  <p>11 strumenti MCP. Il tool <code>search_comune</code> va sempre chiamato per primo quando l'utente fornisce un nome di comune, per ottenere il codice ISTAT.</p>

  <div class="tools">
    <div class="tool-row"><div class="tool-name">mcp_info</div><div class="tool-desc">Metadata del server: versione, sorgenti integrate, freshness dei dati.</div></div>
    <div class="tool-row"><div class="tool-name">search_comune</div><div class="tool-desc">Risolve un nome di comune in codice ISTAT a 6 cifre. Da chiamare per primo.</div></div>
    <div class="tool-row"><div class="tool-name">comune_dashboard</div><div class="tool-desc">Vista completa in una sola chiamata: anagrafica, demografia, profilo, turismo, PNRR, territorio, aria, opere, contratti aggregati, SIOPE multi-anno, scuole, veicoli e incidenti, redditi IRPEF, patrimonio immobiliare PA.</div></div>
    <div class="tool-row"><div class="tool-name">comune_demografia</div><div class="tool-desc">Popolazione per età e sesso (POSAS al 1 gennaio 2026), piramide demografica, indici di vecchiaia e dipendenza.</div></div>
    <div class="tool-row"><div class="tool-name">comune_profilo</div><div class="tool-desc">Censimento permanente ISTAT: istruzione, lavoro, famiglie, mobilità, cittadinanza.</div></div>
    <div class="tool-row"><div class="tool-name">comune_turismo</div><div class="tool-desc">Capacità ricettiva comunale (alberghi ed extra-alberghiero) e flussi turistici provinciali.</div></div>
    <div class="tool-row"><div class="tool-name">comune_pnrr</div><div class="tool-desc">Progetti PNRR dove il comune è soggetto attuatore: missioni, finanziamento, stato avanzamento.</div></div>
    <div class="tool-row"><div class="tool-name">comune_territorio</div><div class="tool-desc">Profilo ambientale: consumo di suolo (ISPRA), rischio idrogeologico (IdroGEO), raccolta differenziata (Catasto rifiuti). Per qualità dell'aria usa <code>comune_dashboard</code> sezione <code>aria</code>.</div></div>
    <div class="tool-row"><div class="tool-name">comune_opere_dettaglio</div><div class="tool-desc">Lista completa delle opere pubbliche BDAP-MOP del comune con CUP, costi, finanziamenti, stato.</div></div>
    <div class="tool-row"><div class="tool-name">comune_contratti</div><div class="tool-desc"><span class="stub">stub v0.2</span>Lista dettagliata contratti per CIG. ETL ANAC OCDS non ancora implementato — usa <code>comune_dashboard</code> per dati ANAC aggregati.</div></div>
  </div>

  <h2>Esempi di <em>prompt</em></h2>

  <div class="example"><strong>Panoramica</strong>Dammi una panoramica del comune di San Giovanni in Fiore: popolazione, opere pubbliche in corso, progetti PNRR.</div>

  <div class="example"><strong>Confronto</strong>Confronta consumo di suolo e raccolta differenziata tra Bergamo e Brescia.</div>

  <div class="example"><strong>Ricerca tematica</strong>Quali sono i 5 comuni con più progetti PNRR sulla missione M2 (Rivoluzione verde) tra Catanzaro, Cosenza, Crotone, Reggio Calabria e Vibo Valentia?</div>

  <div class="example"><strong>Analisi finanziaria</strong>Quanto ha speso il comune di Ponte San Pietro nel 2025 per voci legate al personale? Mostra le top-10 voci SIOPE.</div>

  <h2>Note <em>tecniche</em></h2>
  <p>Endpoint MCP: <code>POST /mcp</code> · Health check: <code>GET /health</code> · Rate limit: 60 richieste al minuto · Cache: 1 ora · Trasporto: JSON-RPC 2.0 over HTTP.</p>
  <p>Tutti i dati provengono da open data ufficiali con licenza CC-BY 4.0 o IODL 2.0. L'aggregazione per codice ISTAT è eseguita da pipeline Python che pubblicano su Cloudflare R2; il worker fa solo lookup e composizione.</p>

  <footer class="mcp-footer">
    <a href="https://cruscotto-italia.piersoftckan.biz">↗ Frontend Cruscotto Italia</a>
    <a href="https://github.com/piersoft/cruscotto-italia">↗ Codice GitHub</a>
    <a href="https://modelcontextprotocol.io">↗ Model Context Protocol</a>
  </footer>

</main>

</body>
</html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

export async function handleHealth(_req: Request, env: Env): Promise<Response> {
  // Verifica che R2 e KV siano raggiungibili
  let r2Ok = false;
  let kvOk = false;
  try {
    await env.DATA.head("manifest.json");
    r2Ok = true;
  } catch {
    /* manifest may not exist yet on first deploy — treat as warning */
  }
  try {
    await env.CACHE.get("__health_probe__");
    kvOk = true;
  } catch {
    /* same */
  }

  const status = { service: "cruscotto-italia-mcp", version: "0.1.0", r2: r2Ok, kv: kvOk, timestamp: new Date().toISOString() };
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
