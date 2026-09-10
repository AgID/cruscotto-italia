/**
 * outputSchema dei tool: ogni fixture reale in tests/worker/fixtures/ deve
 * essere conforme allo schema dichiarato dal tool corrispondente, e il
 * dispatcher deve esporre structuredContent / isError coerentemente.
 * Mini-validatore JSON Schema (type, properties, required, items, null):
 * copre il sottoinsieme usato dai nostri schemi, senza dipendenze.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { tools } from "../../worker/src/tools/index.js";
import { handleMcp } from "../../worker/src/mcp.js";
import type { Env } from "../../worker/src/index.js";

const FIXTURES = join(__dirname, "fixtures");

type Schema = {
  type?: string | string[];
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  additionalProperties?: boolean;
};

function jsType(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v === "number" ? "number" : typeof v;
}

/** Ritorna la lista di violazioni (vuota = conforme). */
function validate(value: unknown, schema: Schema, path = "$"): string[] {
  const errs: string[] = [];
  const allowed = schema.type === undefined ? null : ([] as string[]).concat(schema.type);
  const t = jsType(value);
  if (allowed && !allowed.includes(t)) {
    errs.push(`${path}: tipo ${t}, atteso ${allowed.join("|")}`);
    return errs;
  }
  if (t === "object" && schema.properties) {
    const obj = value as Record<string, unknown>;
    for (const r of schema.required ?? []) {
      if (!(r in obj)) errs.push(`${path}.${r}: required mancante`);
    }
    for (const [k, v] of Object.entries(obj)) {
      const sub = schema.properties[k];
      if (sub) errs.push(...validate(v, sub, `${path}.${k}`));
      else if (schema.additionalProperties === false) errs.push(`${path}.${k}: proprieta' non dichiarata`);
    }
  }
  if (t === "array" && schema.items) {
    (value as unknown[]).forEach((v, i) => errs.push(...validate(v, schema.items as Schema, `${path}[${i}]`)));
  }
  return errs;
}

function fixtures(prefix: string): Array<[string, unknown]> {
  return readdirSync(FIXTURES)
    .filter((f) => f.startsWith(prefix) && f.endsWith(".json"))
    .map((f) => [f, JSON.parse(readFileSync(join(FIXTURES, f), "utf-8"))]);
}

/** Tool con outputSchema -> prefisso delle fixture reali in tests/worker/fixtures/. */
const SCHEMA_FIXTURES: Array<[keyof typeof tools, string, string[]]> = [
  // [tool, prefisso fixture, gruppi/campi che lo schema deve dichiarare]
  ["comune_kpi", "kpi_", ["anagrafica", "demografia", "redditi_mef", "siope", "pnrr"]],
  ["search_comune", "search_", ["count", "results"]],
  ["anncsu_civico_search", "anncsu_", ["anagrafica", "count", "results"]],
];

for (const [toolName, prefix, mustHave] of SCHEMA_FIXTURES) {
  describe(`outputSchema — ${toolName}`, () => {
    const schema = tools[toolName].outputSchema as Schema;

    it("e' dichiarato ed e' un object schema con i campi principali", () => {
      expect(schema).toBeTypeOf("object");
      expect(schema.type).toBe("object");
      for (const k of mustHave) expect(schema.properties, k).toHaveProperty(k);
    });

    const fx = fixtures(prefix);
    it("ha almeno 3 fixture reali", () => {
      expect(fx.length).toBeGreaterThanOrEqual(3);
    });

    for (const [name, doc] of fx) {
      it(`fixture ${name} conforme allo schema`, () => {
        expect(validate(doc, schema)).toEqual([]);
      });
    }
  });
}

describe("validatore", () => {
  it("rileva le violazioni (sanity check su comune_kpi)", () => {
    const schema = tools.comune_kpi.outputSchema as Schema;
    const bad = { _generated_at: 1, _etl_version: "x", anagrafica: {}, demografia: { popolazione: "molti" } };
    const errs = validate(bad, schema);
    expect(errs.some((e) => e.includes("_generated_at"))).toBe(true);
    expect(errs.some((e) => e.includes("demografia.popolazione"))).toBe(true);
  });
});

describe("dispatcher — structuredContent / isError", () => {
  const ctx = { waitUntil: () => undefined, passThroughOnException: () => undefined } as unknown as ExecutionContext;
  const env = {
    DATA_BASE_URL: "https://example.invalid/data",
    MCP_ANALYTICS: { writeDataPoint: () => undefined },
    CACHE: { get: async () => null, put: async () => undefined },
  } as unknown as Env;

  afterEach(() => vi.unstubAllGlobals());

  async function call(name: string, args: Record<string, unknown>) {
    const req = new Request("https://mcp.test/mcp", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
    });
    return ((await (await handleMcp(req, env, ctx)).json()) as any).result;
  }

  it("risultato conforme -> structuredContent presente, isError assente", async () => {
    const [, doc] = fixtures("kpi_")[0];
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ _generated_at: "t", _etl_version: "v", kpi_summary: doc }), { status: 200 }));
    const r = await call("comune_kpi", { istat_code: "075035" });
    expect(r.isError).toBeUndefined();
    expect(r.structuredContent).toBeTypeOf("object");
    expect(r.structuredContent.anagrafica).toBeTypeOf("object");
    expect(validate(r.structuredContent, tools.comune_kpi.outputSchema as Schema)).toEqual([]);
  });

  it("shard assente -> isError:true e nessun structuredContent", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 404 }));
    const r = await call("comune_kpi", { istat_code: "000001" });
    expect(r.isError).toBe(true);
    expect(r.structuredContent).toBeUndefined();
    expect(JSON.parse(r.content[0].text).error).toBe("dashboard_shard_not_found");
  });
});
