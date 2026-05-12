/**
 * Cruscotto Italia MCP Server
 *
 * Worker entrypoint. Esporta:
 *  - GET  /             → info banner (HTML)
 *  - GET  /mcp          → MCP server-sent events endpoint
 *  - POST /mcp          → MCP JSON-RPC endpoint
 *  - GET  /health       → health check (per CI/monitoring)
 *  - GET  /admin/*      → admin routes (cache purge, etc.) — auth required
 *
 * Architettura: vedi DESIGN.md § 1.2
 */

import { handleMcp } from "./mcp.js";
import { handleHealth, handleInfo, handleAdmin } from "./http.js";
import { rateLimit } from "./lib/ratelimit.js";

export interface Env {
  DATA: R2Bucket;
  CACHE: KVNamespace;
  MCP_RATE_LIMITER: RateLimit;
  LOG_LEVEL: string;
  CACHE_TTL_SECONDS: string;
  RATE_LIMIT_RPM: string;
  ADMIN_TOKEN?: string;
}
export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    // CORS preflight (frontend statico chiamerà /mcp da altro dominio)
    if (req.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    // Rate limit (escluso health)
    if (url.pathname !== "/health") {
      const limited = await rateLimit(req, env);
      if (limited) return limited;
    }

    // Routing
    try {
      if (url.pathname === "/" || url.pathname === "/info") {
        return handleInfo(req, env);
      }
      if (url.pathname === "/health") {
        return handleHealth(req, env);
      }
      if (url.pathname === "/mcp") {
        return handleMcp(req, env, ctx);
      }
      if (url.pathname.startsWith("/admin/")) {
        return handleAdmin(req, env);
      }
      return new Response("Not Found", { status: 404 });
    } catch (err) {
      console.error("Unhandled error:", err);
      return new Response(
        JSON.stringify({ error: "internal_error", message: String(err) }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }
  },
} satisfies ExportedHandler<Env>;
