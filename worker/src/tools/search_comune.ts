/**
 * Tool: search_comune
 *
 * Autocomplete search su comuni-index.json (caricato da R2, cachato in KV).
 * Algoritmo:
 *  1. Carica comuni-index.json (~730 KB) — viene cachato in KV per 1h
 *  2. Filter in memoria: prefix-match prima, poi substring-match
 *  3. Ritorna top N risultati (default 10, max 50)
 *
 * L'index è strutturato come array di {i,n,p,r,ipa,cf} (chiavi compatte
 * per ridurre dimensione JSON). Riespandiamo nei nomi leggibili in output.
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";
import { validateQuery, validateLimit } from "../lib/validate.js";

interface ComuneIndexEntry {
  i: string;   // istat_code
  n: string;   // denominazione
  p: string;   // provincia
  r: string;   // regione
  ipa: string | null;
  cf: string | null;
}

export const searchComune: ToolDefinition = {
  description:
    "Risolve un nome di comune italiano in codice ISTAT 6-digit. Usa SEMPRE questo tool come primo passo quando l'utente menziona un comune per nome (es. 'Lecce', 'Bergamo'), prima di chiamare comune_dashboard o altri tool. Ritorna i match ranked per rilevanza con codice ISTAT, denominazione esatta, provincia e regione. In caso di omonimi (es. 'San Teodoro' esiste in Sardegna e Sicilia) ritorna tutti i match e l'utente sceglie. Dati: anagrafica unificata ISTAT+IPA, ~7900 comuni.",
  inputSchema: {
    type: "object",
    properties: {
      query: { type: "string", minLength: 3, description: "Testo da cercare (minimo 3 caratteri, es. 'lec', 'milano')" },
      limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
    },
    required: ["query"],
    additionalProperties: false,
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    // Validazione vincolante CERT-AgID (paper 2026-04, raccomandazione 1).
    const q = validateQuery(args.query ?? "").toLowerCase();
    const limit = validateLimit(args.limit, 1, 50, 10);

    if (q.length < 3) {
      return { count: 0, results: [], warning: "query too short (min 3 chars)" };
    }

    // Load the comuni index from R2 (cached in KV)
    const index = await fetchR2Json<ComuneIndexEntry[]>(env, "lookup/comuni-index.json");
    if (!index || !Array.isArray(index)) {
      return {
        count: 0,
        results: [],
        error: "comuni_index_not_found",
        hint: "Run the anagrafica ETL: python -m etl.sources.anagrafica --target=r2",
      };
    }

    // Filter: prefix-match first (better UX), then substring
    const prefix: ComuneIndexEntry[] = [];
    const substring: ComuneIndexEntry[] = [];
    for (const e of index) {
      const lower = e.n.toLowerCase();
      if (lower.startsWith(q)) {
        prefix.push(e);
      } else if (lower.includes(q)) {
        substring.push(e);
      }
      if (prefix.length >= limit) break;
    }

    const matches = [...prefix, ...substring].slice(0, limit);

    return {
      count: matches.length,
      query: q,
      total_in_index: index.length,
      results: matches.map((e) => ({
        istat_code: e.i,
        denominazione: e.n,
        provincia: e.p,
        regione: e.r,
        codice_ipa: e.ipa,
        codice_fiscale: e.cf,
      })),
    };
  },
};
