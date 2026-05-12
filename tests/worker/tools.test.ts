/**
 * Smoke test for the tools registry.
 * Validates that all tools have correct shape and basic invocation works.
 *
 * Note: comune_overview è stato deprecato dal registry MCP (2026-05-10)
 * ma resta disponibile come funzione interna per regressione.
 */
import { describe, it, expect } from "vitest";
import { tools } from "../../worker/src/tools/index.js";
import { comuneOverview } from "../../worker/src/tools/comune_overview.js";

describe("tool registry", () => {
  it("exports at least 3 tools (mcp_info, search_comune, comune_dashboard)", () => {
    expect(Object.keys(tools).length).toBeGreaterThanOrEqual(3);
    expect(tools).toHaveProperty("mcp_info");
    expect(tools).toHaveProperty("search_comune");
    expect(tools).toHaveProperty("comune_dashboard");
  });

  it("comune_overview is no longer in the public registry (deprecated)", () => {
    expect(tools).not.toHaveProperty("comune_overview");
  });

  it("every tool has description, inputSchema, handler", () => {
    for (const [name, def] of Object.entries(tools)) {
      expect(def.description, `${name}.description`).toBeTypeOf("string");
      expect(def.description.length).toBeGreaterThan(20);
      expect(def.inputSchema, `${name}.inputSchema`).toBeTypeOf("object");
      expect(def.handler, `${name}.handler`).toBeTypeOf("function");
    }
  });

  it.skip("search_comune returns suggestions for 'Lec' [requires R2 mock or live env]", async () => {
    const env = {} as any;
    const result = await tools.search_comune.handler({ query: "Lec", limit: 5 }, env);
    expect(result).toMatchObject({
      count: expect.any(Number),
      results: expect.any(Array),
    });
    const r = result as { count: number; results: { denominazione: string }[] };
    expect(r.count).toBeGreaterThan(0);
    expect(r.results[0].denominazione).toBe("Lecce");
  });

  it.skip("comuneOverview (legacy) returns anagrafica for ISTAT 075035 (Lecce) [requires R2 mock or live env]", async () => {
    const env = {} as any;
    const result = await comuneOverview.handler({ istat_code: "075035" }, env);
    expect(result).toMatchObject({
      anagrafica: expect.objectContaining({
        denominazione: "Lecce",
        provincia: "LE",
        regione: "Puglia",
      }),
    });
  });
});
