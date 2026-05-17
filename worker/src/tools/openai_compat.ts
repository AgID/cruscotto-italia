/**
 * OpenAI ChatGPT MCP custom connector compatibility tools.
 *
 * ChatGPT (senza Developer Mode) richiede OBBLIGATORIAMENTE due tool con
 * nomi esatti `search` e `fetch` e schemi specifici per accettare il
 * connettore custom. Questi sono wrapper attorno a search_comune e
 * comune_dashboard, esposti nel formato atteso da OpenAI.
 *
 * Ref: https://developers.openai.com/api/docs/mcp
 * Ref: https://www.elastic.co/search-labs/blog/chatgpt-connector-mcp-server-github-elasticsearch
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { searchComune } from "./search_comune.js";
import { comuneDashboard } from "./comune_dashboard.js";
import { validateQuery, validateFetchId } from "../lib/validate.js";

const PUBLIC_BASE_URL = "https://cruscotto-italia.piersoftckan.biz";

interface SearchComuneResult {
  istat_code: string;
  denominazione: string;
  provincia?: string;
  regione?: string;
  popolazione?: number;
}

interface SearchComuneResponse {
  results?: SearchComuneResult[];
}

export const openaiSearch: ToolDefinition = {
  description:
    "Cerca comuni italiani per nome (es. 'Lecce', 'Milano', 'Cogne'). Ritorna risultati con id (codice ISTAT 6 cifre), titolo, descrizione e URL del dashboard pubblico. Usa fetch sull'id per ottenere i dati completi del comune. Tool richiesto da ChatGPT MCP custom connector.",
  inputSchema: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "Nome o parte del nome di un comune italiano da cercare",
      },
    },
    required: ["query"],
    additionalProperties: false,
  },
  outputSchema: {
    type: "object",
    properties: {
      results: {
        type: "array",
        items: {
          type: "object",
          properties: {
            id: { type: "string" },
            title: { type: "string" },
            text: { type: "string" },
            url: { type: "string" },
          },
          required: ["id", "title", "text", "url"],
        },
      },
    },
    required: ["results"],
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    // Validazione vincolante CERT-AgID (paper 2026-04, raccomandazione 1).
    // Su search di OpenAI tolleriamo input < 3 char (ritorniamo results: [])
    // invece di lanciare, perche' ChatGPT puo' fare query progressive
    // mentre l'utente digita.
    let query: string;
    try {
      query = validateQuery(args.query ?? "");
    } catch {
      return { results: [] };
    }

    const inner = (await searchComune.handler(
      { query, limit: 10 },
      env
    )) as SearchComuneResponse;

    const matches = inner?.results ?? [];

    const results = matches.map((c) => {
      const prov = c.provincia ? ` (${c.provincia})` : "";
      const reg = c.regione ? `, ${c.regione}` : "";
      const pop = c.popolazione ? `, popolazione ${c.popolazione.toLocaleString("it-IT")}` : "";
      return {
        id: c.istat_code,
        title: `${c.denominazione}${prov}`,
        text: `Comune italiano di ${c.denominazione}${prov ? " in provincia di " + c.provincia : ""}${reg}${pop}. Codice ISTAT: ${c.istat_code}. Usa fetch su questo id per ottenere il dashboard completo (21 sezioni: anagrafica, demografia, opere, scuole, redditi, sanita, banda larga, carburanti, etc.).`,
        url: `${PUBLIC_BASE_URL}/comune.html?istat=${c.istat_code}`,
      };
    });

    return { results };
  },
};

export const openaiFetch: ToolDefinition = {
  description:
    "Recupera i dati completi di un comune italiano dato il suo codice ISTAT (id ritornato da search). Ritorna anagrafica, demografia, censimento, opere pubbliche, scuole, redditi IRPEF, sanita territoriale, punti ricarica EV, banda larga FTTH, distributori carburanti e molto altro. Tool richiesto da ChatGPT MCP custom connector.",
  inputSchema: {
    type: "object",
    properties: {
      id: {
        type: "string",
        description: "Codice ISTAT 6 cifre del comune (es. '075035' per Lecce). Ottenuto da una precedente chiamata a search.",
      },
    },
    required: ["id"],
    additionalProperties: false,
  },
  outputSchema: {
    type: "object",
    properties: {
      id: { type: "string" },
      title: { type: "string" },
      text: { type: "string" },
      url: { type: "string" },
      metadata: { type: "object", additionalProperties: true },
    },
    required: ["id", "title", "text", "url"],
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    // Validazione vincolante CERT-AgID (paper 2026-04, raccomandazione 1).
    // padStart precedente era un fallback non sicuro: scartato in favore
    // di validazione vincolante esplicita (6 cifre esatte).
    const istatCode = validateFetchId(args.id);

    const dashboard = (await comuneDashboard.handler(
      { istat_code: istatCode },
      env
    )) as Record<string, unknown>;

    // Alleggerimento payload per ChatGPT: trim aggressivo array.
    // Roma altrimenti sforerebbe il context (~700k token).
    // Mantieni: scalari (string/number/boolean), oggetti annidati ricorsivamente.
    // Sostituisci array lunghi (>10 elementi) con placeholder.
    // I dati restano accessibili via comune_dashboard nativo.
    function trimValue(v: unknown, depth: number = 0): unknown {
      if (depth > 6) return "[nested too deep]";
      if (v === null || v === undefined) return v;
      if (Array.isArray(v)) {
        if (v.length === 0) return v;
        if (v.length <= 10) return v.map((x) => trimValue(x, depth + 1));
        return `[${v.length} elementi omessi per brevita\'; usa comune_dashboard nativo per la lista completa]`;
      }
      if (typeof v === "object") {
        const obj = v as Record<string, unknown>;
        const out: Record<string, unknown> = {};
        for (const [k, val] of Object.entries(obj)) {
          out[k] = trimValue(val, depth + 1);
        }
        return out;
      }
      return v;
    }
    const trimmed = trimValue(dashboard) as Record<string, unknown>;

    const anagrafica = (trimmed.anagrafica as Record<string, unknown> | undefined) ?? {};
    const denominazione = (anagrafica.denominazione as string) ?? istatCode;
    const provincia = anagrafica.provincia as string | undefined;
    const regione = anagrafica.regione as string | undefined;

    // Lista sezioni effettivamente presenti (non null)
    const sezioni = Object.entries(trimmed)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k]) => k);

    return {
      id: istatCode,
      title: `${denominazione}${provincia ? " (" + provincia + ")" : ""}`,
      text: JSON.stringify(trimmed, null, 2),
      url: `${PUBLIC_BASE_URL}/comune.html?istat=${istatCode}`,
      metadata: {
        istat: istatCode,
        denominazione,
        provincia,
        regione,
        sezioni_disponibili: sezioni,
        source: "Cruscotto Italia - aggregato di 16 fonti open data istituzionali italiane",
      },
    };
  },
};
