/**
 * MCP server implementation.
 *
 * Espone tutti i tool definiti in src/tools/*.ts come MCP tools standard.
 * Usa il pattern JSON-RPC 2.0 over HTTP (Streamable HTTP transport).
 *
 * Per il MVP supporta solo POST /mcp con singola richiesta JSON-RPC.
 * Streaming SSE può essere aggiunto in v0.2.
 */

import type { Env } from "./index.js";
import { tools, type ToolName } from "./tools/index.js";
import { tryConsume } from "./lib/ratelimit.js";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id?: string | number | null;
  method: string;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

const SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];
const DEFAULT_PROTOCOL_VERSION = "2025-06-18";

// CERT-AgID-VA-02 #2: costo per tool (token consumati dal rate limiter).
const TOOL_COST: Record<string, number> = {
  comune_dashboard: 10,
  mcp_info: 10,
  anncsu_civico_search: 5,
  censimento_sezione_search: 3,
  search: 5,
  fetch: 5,
};

const SERVER_INSTRUCTIONS = `MCP Cruscotto Italia — dati aperti dei 7.918 comuni. Tool: search_comune (nome→ISTAT), comune_kpi, comune_dashboard (25 sezioni), anncsu_civico_search (civici georeferenziati).

GEOCODING CATASTALE (indirizzo → foglio/particella): il catasto NON è un tool; i dati sono file statici da elaborare lato client (point-in-polygon), mai lato server. Procedura:
1) search_comune per l'ISTAT;
2) anncsu_civico_search per le coordinate del civico (lon, lat);
3) scarica le particelle: GET https://cruscotto-italia.dati.gov.it/data/catasto_full/<ISTAT>_ple.geojson.gz (gzip; Roma è split per foglio, senza monolitico);
4) trova la particella che CONTIENE il punto (ray-casting) e leggi NATIONALCADASTRALREFERENCE (es. G273_0025D0.2365 → BELFIORE G273, foglio 0025D0, particella 2365). Non comporre il codice foglio a mano: leggilo dalla feature.
Gli endpoint /data/ hanno CORS aperto (Access-Control-Allow-Origin: *): scaricabili anche da browser.`;

function negotiateProtocolVersion(requested: unknown): string {
  if (typeof requested === "string" && SUPPORTED_PROTOCOL_VERSIONS.includes(requested)) {
    return requested;
  }
  return DEFAULT_PROTOCOL_VERSION;
}

export async function handleMcp(
  req: Request,
  env: Env,
  _ctx: ExecutionContext
): Promise<Response> {
  if (req.method !== "POST") {
    return new Response("Use POST with JSON-RPC payload", { status: 405 });
  }

  let body: JsonRpcRequest;
  try {
    body = (await req.json()) as JsonRpcRequest;
  } catch {
    return rpcError(null, -32700, "Parse error");
  }

  if (body.jsonrpc !== "2.0" || !body.method) {
    return rpcError(body.id ?? null, -32600, "Invalid Request");
  }

  switch (body.method) {
    case "initialize": {
      const requestedVersion = (body.params as { protocolVersion?: unknown } | undefined)?.protocolVersion;
      const negotiatedVersion = negotiateProtocolVersion(requestedVersion);
      return rpcOk(body.id, {
        protocolVersion: negotiatedVersion,
        capabilities: { tools: {} },
        serverInfo: { name: "cruscotto-italia-mcp", version: "0.15.1" },
        instructions: SERVER_INSTRUCTIONS,
      });
    }

    case "tools/list":
      return rpcOk(body.id, {
        tools: Object.entries(tools).map(([name, def]) => {
          const t: Record<string, unknown> = {
            name,
            description: def.description,
            inputSchema: def.inputSchema,
          };
          if (def.outputSchema) {
            t.outputSchema = def.outputSchema;
          }
          return t;
        }),
      });

    case "tools/call": {
      const params = body.params as { name?: ToolName; arguments?: Record<string, unknown> };
      if (!params?.name || !(params.name in tools)) {
        return rpcError(body.id ?? null, -32602, `Unknown tool: ${params?.name}`);
      }
      const tool = tools[params.name];
      const args = (params.arguments ?? {}) as Record<string, unknown>;
      const cost = TOOL_COST[params.name] ?? 1;
      if (cost > 1 && !(await tryConsume(req, env, cost - 1))) {
        return rpcError(
          body.id ?? null,
          -32000,
          `rate_limited: tool '${params.name}' (costo ${cost}) supera il limite per minuto`
        );
      }
      try {
        const result = await tool.handler(args, env);
        _ctx.waitUntil(trackToolCall(req, env, params.name, args, "ok"));
        // ChatGPT (OpenAI MCP custom connector) richiede structuredContent
        // al top-level del tool result per i tool `search` e `fetch`.
        // Per gli altri tool restiamo backward-compatible col formato MCP base.
        // Ref: https://developers.openai.com/api/docs/mcp
        const isOpenAICompat = params.name === "search" || params.name === "fetch";
        const responsePayload: Record<string, unknown> = {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
        if (isOpenAICompat) {
          responsePayload.structuredContent = result;
        }
        return rpcOk(body.id, responsePayload);
      } catch (err) {
        // Distinguo validation error (CERT-AgID rec.1+4) da tool error
        // generico per consentire monitoraggio mirato dei tentativi di
        // input non validi (possibili attacchi SSRF/injection).
        const errMsg = String(err);
        const isValidationError =
          errMsg.includes("must match pattern") ||
          errMsg.includes("must be a string") ||
          errMsg.includes("must be an integer") ||
          errMsg.includes("must be in range") ||
          errMsg.includes("must be a 6-digit") ||
          errMsg.includes("contains forbidden sequence");
        const trackStatus = isValidationError ? "validation_error" : "error";
        _ctx.waitUntil(trackToolCall(req, env, params.name, args, trackStatus));
        if (isValidationError) {
          // Codice -32602 = Invalid params (JSON-RPC standard)
          return rpcError(body.id ?? null, -32602, `Invalid params: ${errMsg.slice(0, 300)}`);
        }
        return rpcError(body.id ?? null, -32000, `Tool error: ${errMsg}`);
      }
    }

    case "ping":
      return rpcOk(body.id, {});

    default:
      return rpcError(body.id ?? null, -32601, `Method not found: ${body.method}`);
  }
}

/**
 * Analytics tracking via Cloudflare Analytics Engine — privacy AgID-compliant.
 *
 * Datapoint: blobs = [toolName, istat, client, status, term]
 *   - term valorizzato solo per search_comune (slug normalizzato, max 40 char)
 * Nessun IP, nessun UA grezzo: dati anonimi non personali (la retention 7gg
 * della privacy policy si applica ai soli dati di navigazione con identificativi;
 * AE conserva ~90gg, gestito da Cloudflare, append-only).
 * Migrato da KV l'11/06/2026: le PUT KV saturavano il free tier (1000/giorno).
 */
async function trackToolCall(
  req: Request,
  env: Env,
  toolName: string,
  args: Record<string, unknown>,
  status: "ok" | "error" | "validation_error"
): Promise<void> {
  try {
    const ua = (req.headers.get("user-agent") || "").toLowerCase();
    let client = "other";
    if (ua.includes("claude")) client = "claude";
    else if (ua.includes("chatgpt") || ua.includes("openai")) client = "chatgpt";
    else if (ua.includes("cursor")) client = "cursor";
    else if (ua.includes("python") || ua.includes("requests") || ua.includes("httpx")) client = "python";
    else if (ua.includes("node") || ua.includes("undici")) client = "node";
    else if (ua.includes("curl") || ua.includes("wget")) client = "curl";
    else if (ua.includes("mozilla")) client = "browser";

    const istatRaw = (args.istat ?? args.codice_istat ?? args.istat_code ?? "") as string;
    const istat = typeof istatRaw === "string" && /^\d{6}$/.test(istatRaw) ? istatRaw : "_";

    let term = "";
    // Termine loggato solo a selezione avvenuta per il browser (header dal
    // frontend al click sulla suggestion): evita i prefissi del type-ahead.
    // I client MCP (claude/chatgpt/curl/...) non "cliccano": loggano alla chiamata.
    const selected = req.headers.get("x-search-selected") === "1";
    if (toolName === "search_comune" && (client !== "browser" || selected)) {
      const termRaw = (args.nome ?? args.q ?? args.query ?? "") as string;
      if (typeof termRaw === "string" && termRaw.length > 0) {
        const slug = termRaw.toLowerCase()
          .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 40);
        if (slug.length >= 2) term = slug;
      }
    }

    env.MCP_ANALYTICS.writeDataPoint({
      blobs: [toolName, istat, client, status, term],
      doubles: [1],
      indexes: [toolName],
    });
  } catch {
    // Non-bloccante: errori silenziosi
  }
}

function rpcOk(id: string | number | null | undefined, result: unknown): Response {
  const payload: JsonRpcResponse = { jsonrpc: "2.0", id: id ?? null, result };
  return new Response(JSON.stringify(payload), {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "private, max-age=60",
    },
  });
}

function rpcError(
  id: string | number | null,
  code: number,
  message: string,
  data?: unknown
): Response {
  const payload: JsonRpcResponse = { jsonrpc: "2.0", id, error: { code, message, data } };
  return new Response(JSON.stringify(payload), {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}
