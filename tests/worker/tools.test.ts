/**
 * Test offline del worker MCP: registry, annotations, validazione input,
 * negoziazione protocollo, gestione argomenti. Nessuna rete, nessun binding
 * Cloudflare reale (env e ctx sono mock minimi).
 */
import { describe, it, expect } from "vitest";
import { tools, READ_ONLY_ANNOTATIONS } from "../../worker/src/tools/index.js";
import { handleMcp } from "../../worker/src/mcp.js";
import {
  ValidationError,
  validateIstatCode,
  validateDenominazione,
  validateQuery,
  validateOdonimo,
  validateCivico,
  validateLimit,
  validateFetchId,
} from "../../worker/src/lib/validate.js";
import type { Env } from "../../worker/src/index.js";

const EXPECTED_TOOLS = [
  "mcp_info",
  "search_comune",
  "comune_kpi",
  "comune_dashboard",
  "anncsu_civico_search",
  "censimento_sezione_search",
  "search",
  "fetch",
];

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

async function rpc(body: unknown, headers: Record<string, string> = {}) {
  const req = new Request("https://mcp.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const res = await handleMcp(req, envMock, ctxMock);
  return { status: res.status, headers: res.headers, json: (await res.json()) as any };
}

describe("tool registry", () => {
  it("espone esattamente gli 8 tool attesi", () => {
    expect(Object.keys(tools).sort()).toEqual([...EXPECTED_TOOLS].sort());
  });

  it("ogni tool ha description, inputSchema con properties e additionalProperties:false, handler", () => {
    for (const [name, def] of Object.entries(tools)) {
      expect(def.description, `${name}.description`).toBeTypeOf("string");
      expect(def.description.length, `${name}.description`).toBeGreaterThan(20);
      expect(def.inputSchema, `${name}.inputSchema`).toBeTypeOf("object");
      expect(def.inputSchema.properties, `${name}.inputSchema.properties`).toBeTypeOf("object");
      expect(def.inputSchema.additionalProperties, `${name}.additionalProperties`).toBe(false);
      expect(def.handler, `${name}.handler`).toBeTypeOf("function");
    }
  });

  it("le annotations di default dichiarano un server di sola lettura", () => {
    expect(READ_ONLY_ANNOTATIONS).toEqual({
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    });
  });
});

describe("validate.ts", () => {
  it("accetta input validi", () => {
    expect(validateIstatCode("075035")).toBe("075035");
    expect(validateDenominazione(" Lecce ")).toBe("Lecce");
    expect(validateQuery("Matera")).toBe("Matera");
    expect(validateOdonimo("Via S. Antonio / V. Roma")).toBe("Via S. Antonio / V. Roma");
    expect(validateOdonimo("")).toBe("");
    expect(validateCivico("12/A")).toBe("12/A");
    expect(validateLimit(undefined, 1, 500, 50)).toBe(50);
    expect(validateLimit(7, 1, 500, 50)).toBe(7);
    expect(validateFetchId("058091")).toBe("058091");
  });

  it("rifiuta input invalidi con ValidationError", () => {
    const cases: Array<() => unknown> = [
      () => validateIstatCode("../x"),
      () => validateIstatCode(75035),
      () => validateIstatCode("0750350"),
      () => validateDenominazione("a/../b"),
      () => validateDenominazione("x".repeat(81)),
      () => validateQuery("ab"),
      () => validateQuery("<script>"),
      () => validateOdonimo("Via ../etc"),
      () => validateOdonimo("Via //x"),
      () => validateCivico("12345678901234567"),
      () => validateLimit(0, 1, 500, 50),
      () => validateLimit(501, 1, 500, 50),
      () => validateLimit(1.5, 1, 500, 50),
      () => validateFetchId("lecce"),
    ];
    for (const fn of cases) {
      expect(fn).toThrow(ValidationError);
    }
  });
});

describe("handleMcp — protocollo", () => {
  it("negozia 2025-11-25 e lo usa come default", async () => {
    const a = await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-11-25" } });
    expect(a.status).toBe(200);
    expect(a.json.result.protocolVersion).toBe("2025-11-25");
    expect(a.json.result.serverInfo.name).toBe("cruscotto-italia-mcp");

    const b = await rpc({ jsonrpc: "2.0", id: 2, method: "initialize", params: { protocolVersion: "1999-01-01" } });
    expect(b.json.result.protocolVersion).toBe("2025-11-25");

    const c = await rpc({ jsonrpc: "2.0", id: 3, method: "initialize", params: { protocolVersion: "2025-03-26" } });
    expect(c.json.result.protocolVersion).toBe("2025-03-26");
  });

  it("header MCP-Protocol-Version non supportato -> HTTP 400 e -32600", async () => {
    const r = await rpc({ jsonrpc: "2.0", id: 4, method: "ping" }, { "MCP-Protocol-Version": "1999-01-01" });
    expect(r.status).toBe(400);
    expect(r.json.error.code).toBe(-32600);
    expect(r.json.error.data.supported).toContain("2025-11-25");
  });

  it("header MCP-Protocol-Version supportato -> 200", async () => {
    const r = await rpc({ jsonrpc: "2.0", id: 5, method: "ping" }, { "MCP-Protocol-Version": "2025-06-18" });
    expect(r.status).toBe(200);
    expect(r.json.result).toEqual({});
  });

  it("tools/list espone annotations read-only su ogni tool", async () => {
    const r = await rpc({ jsonrpc: "2.0", id: 6, method: "tools/list" });
    expect(r.json.result.tools).toHaveLength(EXPECTED_TOOLS.length);
    for (const t of r.json.result.tools) {
      expect(t.annotations, t.name).toMatchObject(READ_ONLY_ANNOTATIONS);
      expect(t.inputSchema, t.name).toBeTypeOf("object");
    }
  });

  it("risposte JSON con X-Content-Type-Options: nosniff", async () => {
    const ok = await rpc({ jsonrpc: "2.0", id: 7, method: "ping" });
    expect(ok.headers.get("x-content-type-options")).toBe("nosniff");
    const err = await rpc({ jsonrpc: "2.0", id: 8, method: "nope" });
    expect(err.headers.get("x-content-type-options")).toBe("nosniff");
    expect(err.json.error.code).toBe(-32601);
  });
});

describe("handleMcp — tools/call", () => {
  it("argomento non dichiarato -> -32602 senza invocare il tool", async () => {
    const r = await rpc({
      jsonrpc: "2.0", id: 9, method: "tools/call",
      params: { name: "comune_kpi", arguments: { istat_code: "075035", foo: "bar" } },
    });
    expect(r.json.error.code).toBe(-32602);
    expect(r.json.error.message).toContain("'foo'");
  });

  it("ValidationError del tool -> -32602 (non -32000)", async () => {
    const a = await rpc({
      jsonrpc: "2.0", id: 10, method: "tools/call",
      params: { name: "comune_kpi", arguments: { istat_code: "../x" } },
    });
    expect(a.json.error.code).toBe(-32602);

    const b = await rpc({
      jsonrpc: "2.0", id: 11, method: "tools/call",
      params: { name: "comune_kpi", arguments: {} },
    });
    expect(b.json.error.code).toBe(-32602);
    expect(b.json.error.message).toContain("is required");
  });

  it("tool sconosciuto -> -32602", async () => {
    const r = await rpc({ jsonrpc: "2.0", id: 12, method: "tools/call", params: { name: "drop_tables", arguments: {} } });
    expect(r.json.error.code).toBe(-32602);
  });

  it("body non JSON -> -32700", async () => {
    const req = new Request("https://mcp.test/mcp", { method: "POST", body: "{nope" });
    const res = await handleMcp(req, envMock, ctxMock);
    const j = (await res.json()) as any;
    expect(j.error.code).toBe(-32700);
  });
});
