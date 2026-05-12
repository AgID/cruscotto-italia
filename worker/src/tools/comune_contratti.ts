/**
 * Tool: comune_contratti
 *
 * Lista contratti pubblici di un comune.
 * Sorgente: dati ANAC OCDS-IT bulk (Parquet su R2, popolato da etl/sources/anac.py).
 *
 * STATUS v0.1: STUB. L'ETL ANAC non è ancora stato implementato.
 * Ritorna un payload conforme allo schema finale ma con dati vuoti, in modo
 * che il frontend possa già renderizzare la struttura (lista contratti +
 * paginazione + breakdown).
 *
 * v0.2: query reale via R2 GET su `anac/{anno}/awards.parquet` filtrato
 * per CF stazione appaltante (lookup IPA → ISTAT).
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ComuneDetail {
  istat_code: string;
  denominazione: string;
  codice_fiscale: string | null;
  codice_ipa: string | null;
}

interface ComuniBundle {
  comuni: Record<string, ComuneDetail>;
}

export const comuneContratti: ToolDefinition = {
  description:
    "[v0.2 — STUB] Lista dettagliata dei contratti pubblici (CIG, OCDS) per un comune. ATTENZIONE: ETL ANAC OCDS non ancora implementato — l'endpoint ritorna sempre {results: [], _stub: true} indipendentemente dai filtri. Per dati ANAC aggregati (count totale aggiudicazioni, importo cumulato, top CPV, date min/max) USA `comune_dashboard` sezione `anac` che è popolata. La lista per CIG con filtri per anno, importo, fornitore (P.IVA), RUP (CF) e CPV sarà disponibile in v0.2. Fonte prevista: dati.anticorruzione.it/opendata, licenza CC-BY 4.0.",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre del comune",
      },
      anno: {
        type: "integer",
        minimum: 2018,
        maximum: 2030,
        description: "Anno di aggiudicazione (default: tutti)",
      },
      importo_min: { type: "number", description: "Importo minimo in euro" },
      importo_max: { type: "number", description: "Importo massimo in euro" },
      fornitore_piva: { type: "string", pattern: "^\\d{11}$", description: "P.IVA dell'aggiudicatario" },
      rup_cf: { type: "string", description: "Codice fiscale del RUP" },
      cpv: { type: "string", description: "Codice CPV (categoria merceologica)" },
      page: { type: "integer", minimum: 1, default: 1 },
      page_size: { type: "integer", minimum: 1, maximum: 100, default: 25 },
    },
    required: ["istat_code"],
    additionalProperties: false,
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    const istatCode = args.istat_code as string;
    const page = Number(args.page ?? 1);
    const pageSize = Number(args.page_size ?? 25);

    // Verify the comune exists in our anagrafica
    const bundle = await fetchR2Json<ComuniBundle>(env, "lookup/comuni-bundle.json");
    if (!bundle || !bundle.comuni) {
      return {
        error: "comuni_bundle_not_found",
        hint: "Run anagrafica ETL first",
      };
    }
    const detail = bundle.comuni[istatCode];
    if (!detail) {
      return {
        error: "comune_not_found",
        istat_code: istatCode,
      };
    }

    // STUB: would query anac/{anno}/awards.parquet filtered by detail.codice_fiscale
    return {
      comune: {
        istat_code: detail.istat_code,
        denominazione: detail.denominazione,
        codice_fiscale_ente: detail.codice_fiscale,
      },
      filters: {
        anno: args.anno ?? null,
        importo_min: args.importo_min ?? null,
        importo_max: args.importo_max ?? null,
        fornitore_piva: args.fornitore_piva ?? null,
        rup_cf: args.rup_cf ?? null,
        cpv: args.cpv ?? null,
      },
      pagination: {
        page,
        page_size: pageSize,
        total_count: null,
        total_pages: null,
      },
      summary: {
        count: null,
        importo_totale: null,
        top_fornitori: null,
        top_cpv: null,
      },
      results: [],
      _stub: true,
      _stub_reason: "ETL ANAC OCDS not yet implemented. See DESIGN.md § 2.1 / Roadmap v0.2.",
      _data_source: "https://dati.anticorruzione.it/opendata",
      _license: "CC-BY 4.0",
    };
  },
};
