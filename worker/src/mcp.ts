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
        serverInfo: { name: "cruscotto-italia-mcp", version: "0.15.0" },
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
          return rpcError(body.id ?? null, -32602, `Invalid params: ${errMsg}`);
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
 * Analytics tracking via KV counter — privacy AgID-compliant.
 *
 * Schema chiavi:
 *   analytics:YYYY-MM-DD:<tool>:<istat>:<client>           — counter per tool call
 *   analytics-err:YYYY-MM-DD:<tool>:<client>               — counter errori (solo se status=error)
 *   analytics-term:YYYY-MM-DD:<term-slug>                  — termine cercato (solo search_comune)
 *
 * Nessun IP, nessun UA grezzo. TTL 35 giorni.
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

    const day = new Date().toISOString().slice(0, 10);
    const ttl = 60 * 60 * 24 * 35; // 35 giorni

    // 1) Counter principale (solo successi, mantiene retrocompatibilità con fetcher)
    if (status === "ok") {
      const istatRaw = (args.istat ?? args.codice_istat ?? args.istat_code ?? "") as string;
      const istat = typeof istatRaw === "string" && /^\d{6}$/.test(istatRaw) ? istatRaw : "_";
      const key = `analytics:${day}:${toolName}:${istat}:${client}`;
      const current = await env.CACHE.get(key);
      const next = (current ? parseInt(current, 10) : 0) + 1;
      await env.CACHE.put(key, String(next), { expirationTtl: ttl });
    }

    // 2) Counter errori (solo errori applicativi)
    if (status === "error") {
      const errKey = `analytics-err:${day}:${toolName}:${client}`;
      const current = await env.CACHE.get(errKey);
      const next = (current ? parseInt(current, 10) : 0) + 1;
      await env.CACHE.put(errKey, String(next), { expirationTtl: ttl });
    }

    // 2b) Counter validazione fallita (CERT-AgID raccomandazione 4: monitoraggio).
    // Separato da errori applicativi per consentire alert su anomalie input
    // (possibili tentativi SSRF, path traversal, injection).
    if (status === "validation_error") {
      const errKey = `analytics-validation-err:${day}:${toolName}:${client}`;
      const current = await env.CACHE.get(errKey);
      const next = (current ? parseInt(current, 10) : 0) + 1;
      await env.CACHE.put(errKey, String(next), { expirationTtl: ttl });
    }

    // 3) Termini di ricerca (solo per search_comune, qualsiasi status)
    if (toolName === "search_comune") {
      const termRaw = (args.nome ?? args.q ?? args.query ?? "") as string;
      if (typeof termRaw === "string" && termRaw.length > 0) {
        // Slug: lowercase, alpha-only, max 40 char (per non gonfiare cardinalità KV)
        const slug = termRaw.toLowerCase()
          .normalize("NFD").replace(/[\u0300-\u036f]/g, "") // strip accenti
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 40);
        if (slug.length >= 2) {
          const termKey = `analytics-term:${day}:${slug}`;
          const current = await env.CACHE.get(termKey);
          const next = (current ? parseInt(current, 10) : 0) + 1;
          await env.CACHE.put(termKey, String(next), { expirationTtl: ttl });
        }
      }
    }
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
