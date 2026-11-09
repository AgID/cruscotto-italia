/**
 * Test offline del routing HTTP su /mcp per metodi diversi da POST.
 * HEAD e GET senza text/event-stream rispondono come la radice;
 * GET con text/event-stream resta 405; POST JSON-RPC invariato.
 */
import { describe, it, expect } from "vitest";
import worker from "../../worker/src/index.js";
import type { Env } from "../../worker/src/index.js";

const envMock = {
  DATA_BASE_URL: "https://example.invalid/data",
  LOG_LEVEL: "error",
  CACHE_TTL_SECONDS: "0",
  RATE_LIMIT_RPM: "60",
  MCP_ANALYTICS: { writeDataPoint: () => undefined },
  MCP_RATE_LIMITER: { limit: async () => ({ success: true }) },
  CACHE: { get: async () => null, put: async () => undefined, list: async () => ({ keys: [] }), delete: async () => undefined },
} as unknown as Env;

const ctxMock = { waitUntil: () => undefined, passThroughOnException: () => undefined } as unknown as ExecutionContext;

function call(path: string, init: RequestInit = {}) {
  return worker.fetch(new Request("https://mcp.test" + path, init), envMock, ctxMock);
}

describe("/mcp metodi diversi da POST", () => {
  it("GET senza text/event-stream: 200 come la radice", async () => {
    const root = await call("/");
    const res = await call("/mcp");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe(root.headers.get("content-type"));
    expect(await res.text()).toBe(await root.text());
  });

  it("HEAD: 200, stessi header della radice, senza corpo", async () => {
    const root = await call("/");
    const res = await call("/mcp", { method: "HEAD" });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe(root.headers.get("content-type"));
    expect(await res.text()).toBe("");
  });

  it("GET con Accept text/event-stream: 405", async () => {
    const res = await call("/mcp", { headers: { Accept: "application/json, text/event-stream" } });
    expect(res.status).toBe(405);
  });

  it("POST JSON-RPC initialize: invariato", async () => {
    const res = await call("/mcp", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-11-25" } }),
    });
    expect(res.status).toBe(200);
    const json = (await res.json()) as any;
    expect(json.result.serverInfo.name).toBeTruthy();
  });
});
