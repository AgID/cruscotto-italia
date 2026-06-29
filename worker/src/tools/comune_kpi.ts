/**
 * Tool: comune_kpi
 *
 * Restituisce ~55 KPI sintetici di un comune italiano in una risposta
 * leggera (~2.5KB / ~620 token). Pensato come tool "primo contatto"
 * per agent AI: minimal token cost, schema stabile, copertura ampia.
 *
 * Usa per:
 *   - Ricerche puntuali su singoli KPI (es. "popolazione di Bari",
 *     "n. distributori a Milano", "reddito medio Lecce")
 *   - Confronti tra N comuni (es. "Verona vs Bari su PNRR")
 *   - Ranking ("top 5 comuni per banda larga FTTH")
 *
 * NON usa per:
 *   - Dettaglio mappe (civici ANNCSU, punti ricarica EV)
 *   - Top liste (CPV ANAC, settori BDAP, missioni PNRR)
 *   - Time series (SIOPE per mese, demografia piramide età)
 *   - Vista completa singolo-comune → usa comune_dashboard
 *
 * Sorgente: dashboard/<istat>.json su R2, campo kpi_summary
 * (pre-calcolato da etl/sources/dashboard.py compute_kpi_summary()).
 *
 * Fallback: se lo shard è vecchio e non ha kpi_summary, ritorna errore
 * con hint per rigenerare via ETL. Niente fallback "calcolo live"
 * per due ragioni:
 *   1) Il dashboard è grande (1MB), il fallback annullerebbe il
 *      vantaggio prestazionale di questo tool
 *   2) Il calcolo live duplicherebbe logica già in dashboard.py
 *
 * Schema output: gruppi tematici (anagrafica, demografia, redditi,
 * scuole, ANAC, BDAP, PNRR, SIOPE, immobili PA, ambiente, aria,
 * turismo, veicoli, banda larga, ricarica EV, carburanti, civici
 * ANNCSU, RUNTS, sanità). Solo scalari (numeri + stringhe brevi),
 * null espliciti per dato mancante.
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";
import { validateIstatCode, validateDenominazione } from "../lib/validate.js";

interface DashboardShardWithKpi {
  _etl_version: string;
  _generated_at: string;
  _missing?: string[];
  kpi_summary?: Record<string, unknown>;
  anagrafica?: {
    istat_code: string;
    denominazione: string;
    [k: string]: unknown;
  };
}

interface ComuniBundle {
  _etl_version: string;
  comuni: Record<string, {
    istat_code: string;
    denominazione: string;
    [k: string]: unknown;
  }>;
}

export const comuneKpi: ToolDefinition = {
  description:
    "KPI sintetici di un comune italiano (~55 indicatori chiave, risposta ~2.5KB / ~620 token). Usa per: ricerche puntuali ('popolazione di Bari', 'reddito medio Lecce'), confronti tra comuni ('Verona vs Bari su PNRR'), ranking ('top comuni per banda larga'). Include gruppi tematici: anagrafica, demografia, istruzione, lavoro, redditi MEF, scuole MIUR, contratti ANAC, opere BDAP, PNRR, spese SIOPE, patrimonio immobiliare PA, ambiente (ISPRA suolo/rifiuti), qualità aria, turismo, veicoli ACI, banda larga AGCOM, ricarica EV, carburanti, civici ANNCSU, terzo settore RUNTS, sanità (farmacie/ospedali), imprese e addetti (ASIA UL), pendolarismo, morfologia (CNR-IRPI HR-DTM 5m: elevazione, pendenze, esposizione, geomorfologia, irraggiamento solare — se disponibile nello shard). Importi finanziari sono anche in euro per abitante per facilitare confronti. NON include: dettaglio mappe, top liste (es. categorie ANAC, settori BDAP), time series. Per quelli usa comune_dashboard. Richiede istat_code 6 cifre (es. '075035' Lecce). Se hai solo nome, chiama prima search_comune.",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre (es. '075035' per Lecce)",
      },
      denominazione: {
        type: "string",
        minLength: 2,
        description: "Nome del comune (case-insensitive). Risolto via comuni-bundle.",
      },
    },
    additionalProperties: false,
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    // Validazione vincolante CERT-AgID (paper 2026-04): no fallback,
    // ogni parametro verificato prima dell'uso. Lancia Error se invalido.
    const istatCode = args.istat_code !== undefined
      ? validateIstatCode(args.istat_code)
      : undefined;
    const denominazione = args.denominazione !== undefined
      ? validateDenominazione(args.denominazione)
      : undefined;

    if (!istatCode && !denominazione) {
      throw new Error("Either 'istat_code' or 'denominazione' is required");
    }

    // 1. Risoluzione istat_code se non fornito (stesso pattern di comune_dashboard)
    let resolvedIstat: string | undefined = istatCode;
    if (!resolvedIstat && denominazione) {
      const bundle = await fetchR2Json<ComuniBundle>(
        env, "lookup/comuni-bundle.json"
      );
      if (!bundle?.comuni) {
        return { error: "comuni_bundle_not_found" };
      }
      const target = denominazione.toLowerCase().trim();
      const match = Object.values(bundle.comuni).find(
        (c) => c.denominazione.toLowerCase() === target,
      );
      if (!match) {
        return {
          error: "comune_not_found",
          searched: { denominazione },
        };
      }
      resolvedIstat = match.istat_code;
      // Validazione finale: anche l'ISTAT risolto dal bundle deve passare
      // il pattern (difensivo: protegge anche da bundle corrotto).
      validateIstatCode(resolvedIstat, "resolved_istat_code");
    }

    // 2. Fetch del dashboard shard
    const shardKey = `dashboard/${resolvedIstat}.json`;
    const shard = await fetchR2Json<DashboardShardWithKpi>(
      env, shardKey, { useKvCache: false }
    );

    if (!shard) {
      return {
        error: "dashboard_shard_not_found",
        istat_code: resolvedIstat,
        hint:
          "Lo shard non e' disponibile per questo comune. Potrebbe essere un comune molto recente non ancora processato dall'ETL, oppure un errore di codice ISTAT.",
      };
    }

    // 3. Estrai kpi_summary
    if (!shard.kpi_summary) {
      return {
        error: "kpi_summary_not_yet_generated",
        istat_code: resolvedIstat,
        hint:
          "Il campo kpi_summary non e' ancora presente in questo shard. Lo shard e' stato generato da una versione di dashboard.py precedente all'introduzione del summary. Sara' disponibile al prossimo run dell'ETL (job notturno 04:00 UTC). Nel frattempo usa comune_dashboard per il dato completo.",
        shard_generated_at: shard._generated_at,
        shard_etl_version: shard._etl_version,
      };
    }

    // 4. Ritorna solo il summary + metadati minimi
    return {
      _generated_at: shard._generated_at,
      _etl_version: shard._etl_version,
      _missing: shard._missing || [],
      ...shard.kpi_summary,
    };
  },
};
