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

const PROTOCOL_VERSION = "2024-11-05";

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
    case "initialize":
      return rpcOk(body.id, {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: "cruscotto-italia-mcp", version: "0.1.0" },
      });

    case "tools/list":
      return rpcOk(body.id, {
        tools: Object.entries(tools).map(([name, def]) => ({
          name,
          description: def.description,
          inputSchema: def.inputSchema,
        })),
      });

    case "tools/call": {
      const params = body.params as { name?: ToolName; arguments?: Record<string, unknown> };
      if (!params?.name || !(params.name in tools)) {
        return rpcError(body.id ?? null, -32602, `Unknown tool: ${params?.name}`);
      }
      const tool = tools[params.name];
      try {
        const result = await tool.handler(params.arguments ?? {}, env);
        return rpcOk(body.id, {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        });
      } catch (err) {
        return rpcError(body.id ?? null, -32000, `Tool error: ${String(err)}`);
      }
    }

    case "ping":
      return rpcOk(body.id, {});

    default:
      return rpcError(body.id ?? null, -32601, `Method not found: ${body.method}`);
  }
}

function rpcOk(id: string | number | null | undefined, result: unknown): Response {
  const payload: JsonRpcResponse = { jsonrpc: "2.0", id: id ?? null, result };
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
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
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}
