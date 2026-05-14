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
 *
 * Tool attivi (5):
 *   - mcp_info: metadata server + freshness datasets
 *   - search_comune: nome -> codice ISTAT (preliminare a comune_dashboard)
 *   - comune_dashboard: workhorse, vista completa 21 sezioni
 *   - comune_opere_dettaglio: dettaglio BDAP filtrato al 2025 (non
 *     disponibile come dato aggregato in dashboard.bdap_kpi)
 *   - anncsu_civico_search: query puntuali civici ANNCSU con filtri
 *     odonimo/civico (sostituisce il fetch del full shard per query
 *     mirate, evita di buttare 500k civici di Roma nel context LLM)
 */

import type { Env } from "../index.js";

import { comuneOpereDettaglio } from "./comune_opere_dettaglio.js";
import { comuneDashboard } from "./comune_dashboard.js";
import { searchComune } from "./search_comune.js";
import { mcpInfo } from "./mcp_info.js";
import { anncsuCivicoSearch } from "./anncsu_civico_search.js";

export type ToolHandler = (
  args: Record<string, unknown>,
  env: Env
) => Promise<unknown>;

export interface ToolDefinition {
  description: string;
  inputSchema: Record<string, unknown>;
  handler: ToolHandler;
}

export const tools = {
  // Meta / discovery
  mcp_info: mcpInfo,
  search_comune: searchComune,

  // Workhorse: single-fetch per vista comunale completa (21 sezioni)
  comune_dashboard: comuneDashboard,

  // Specializzati: forniscono dati NON ridondanti rispetto a comune_dashboard
  comune_opere_dettaglio: comuneOpereDettaglio,  // BDAP dettaglio filtrato 2025
  anncsu_civico_search: anncsuCivicoSearch,      // query puntuali civici ANNCSU
} satisfies Record<string, ToolDefinition>;

export type ToolName = keyof typeof tools;
