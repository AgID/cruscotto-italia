/**
 * Registry of all MCP tools exposed by cruscotto-italia-mcp.
 *
 * Naming convention: snake_case, prefisso entita' (comune_*, anncsu_*).
 *
 * Storia design:
 *   - v0.1-v0.4: tool dedicato per ogni fonte (comune_demografia,
 *     comune_profilo, comune_turismo, comune_pnrr, comune_territorio,
 *     comune_contratti), pattern "un tool per dataset"
 *   - v0.5+: introdotto comune_dashboard come single-fetch che restituisce
 *     tutte le 21 sezioni in una chiamata. I tool dedicati restavano per
 *     backward compatibility
 *   - v0.10.0 (2026-05-14): rimossi i 6 tool ridondanti dedicati che
 *     duplicavano una sezione di comune_dashboard. Il choice del modello
 *     LLM diventa ovvio: per qualsiasi vista comunale, comune_dashboard.
 *     I tool legacy creavano confusione (Claude/GPT a volte sceglieva
 *     il dedicato facendo 2-3 round-trip invece di 1).
 *   - v0.11.0 (2026-05-15): introdotto comune_kpi (~620 token) come
 *     "primo contatto" per agent AI. Risolve l'over-fetching di
 *     comune_dashboard (~250K token, oltre il limite Claude Connector
 *     di 25K token per tool response). Pattern d'uso:
 *       - Query puntuali e confronti N-comuni → comune_kpi (leggero)
 *       - Vista dettagliata singolo-comune → comune_dashboard (pesante)
 *       - Dettaglio specifico → anncsu_civico_search
 *
 * Tool attivi (5):
 *   - mcp_info: metadata server + freshness datasets
 *   - search_comune: nome -> codice ISTAT (preliminare ai tool comune_*)
 *   - comune_kpi: KPI sintetici (~620 token, primo tool da chiamare)
 *   - comune_dashboard: workhorse pesante, vista completa 21 sezioni
 *   - anncsu_civico_search: query puntuali civici ANNCSU
 *
 * Storico: comune_opere_dettaglio (tool BDAP dedicato) deprecato il
 * 18/05/2026: dati gia' inclusi in comune_dashboard.opere, e endpoint
 * /data/bdap/dettaglio/ non era allowlisted nel template nginx AgID
 * (faceva fallback silenzioso a 'n_progetti: 0' per ogni comune).
 */

import type { Env } from "../index.js";

import { comuneDashboard } from "./comune_dashboard.js";
import { comuneKpi } from "./comune_kpi.js";
import { searchComune } from "./search_comune.js";
import { mcpInfo } from "./mcp_info.js";
import { anncsuCivicoSearch } from "./anncsu_civico_search.js";
import { openaiSearch, openaiFetch } from "./openai_compat.js";

export type ToolHandler = (
  args: Record<string, unknown>,
  env: Env
) => Promise<unknown>;

export interface ToolDefinition {
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  handler: ToolHandler;
}

export const tools = {
  // Meta / discovery
  mcp_info: mcpInfo,
  search_comune: searchComune,

  // Workhorse leggero: KPI sintetici, primo tool da chiamare per query
  // puntuali e confronti tra comuni (~620 token, vs 250K di comune_dashboard)
  comune_kpi: comuneKpi,

  // Workhorse pesante: single-fetch per vista comunale completa (21 sezioni)
  comune_dashboard: comuneDashboard,

  // Specializzato: query puntuali civici ANNCSU (non ridondante con dashboard)
  anncsu_civico_search: anncsuCivicoSearch,
  // OpenAI ChatGPT MCP custom connector compatibility (obbligatori, schema fisso)
  search: openaiSearch,
  fetch: openaiFetch,
} satisfies Record<string, ToolDefinition>;

export type ToolName = keyof typeof tools;
