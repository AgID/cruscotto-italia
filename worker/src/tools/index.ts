/**
 * Registry of all MCP tools exposed by cruscotto-italia-mcp.
 *
 * Naming convention: snake_case, prefisso entità (comune_*, cig_*, cup_*).
 */

import type { Env } from "../index.js";

//import { comuneOverview } from "./comune_overview.js";
import { comuneContratti } from "./comune_contratti.js";
// comuneSpese rimosso 2026-05-10: tool live OData deprecato.
// Il frontend ora usa siope/<istat>.json pre-calcolato (schema v0.2.0 multi-anno)
// embedded nel comune_dashboard.
import { comuneOpereDettaglio } from "./comune_opere_dettaglio.js";
import { comuneDemografia } from "./comune_demografia.js";
import { comuneProfilo } from "./comune_profilo.js";
import { comuneTurismo } from "./comune_turismo.js";
import { comunePnrr } from "./comune_pnrr.js";
import { comuneTerritorio } from "./comune_territorio.js";
import { comuneDashboard } from "./comune_dashboard.js";
import { searchComune } from "./search_comune.js";
import { mcpInfo } from "./mcp_info.js";

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
  // Meta / admin
  mcp_info: mcpInfo,

  // Discovery
  search_comune: searchComune,

  // Comune-centric (MVP v0.1)
  // Single-fetch dashboard (preferito per /comune.html, sostituisce 6+ chiamate)
  comune_dashboard: comuneDashboard,

  //comune_overview: comuneOverview,
  comune_contratti: comuneContratti,  // stub, ETL ANAC arriva in v0.2
  // comune_spese rimosso: SIOPE multi-anno ora dentro comune_dashboard.siope.per_anno
  comune_opere_dettaglio: comuneOpereDettaglio, // BDAP shard per comune (filtri tab Opere)
  comune_demografia: comuneDemografia,        // ISTAT POSAS per piramide eta
  comune_profilo: comuneProfilo,        // ISTAT Censimento permanente — 5 sezioni socio-demografiche
  comune_turismo: comuneTurismo,        // ISTAT TUR_1 (capacita comunale) + TUR_7 (flussi provinciali)
  comune_pnrr: comunePnrr,              // Italia Domani / ReGiS - progetti PNRR del comune
  comune_territorio: comuneTerritorio,  // ISPRA Suolo + IdroGEO + Rifiuti

  // TODO v0.2/v0.3:
  // comune_opere, comune_spese, comune_progetti_coesione, comune_demografia
  // cig_lookup, cup_lookup, rup_lookup, fornitore_lookup, siope_codice_show
} satisfies Record<string, ToolDefinition>;

export type ToolName = keyof typeof tools;
