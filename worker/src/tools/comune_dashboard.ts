/**
 * Tool: comune_dashboard
 *
 * Single-fetch endpoint che restituisce TUTTI i dati di un comune in una
 * sola chiamata MCP. Sostituisce le 6+ chiamate separate (overview,
 * demografia, profilo, turismo, pnrr, territorio, opere, contratti, spese)
 * usate dal frontend al primo load di /comune.html.
 *
 * Sorgente: dashboard/<istat>.json su R2, generato da etl/sources/dashboard.py
 * che accorpa:
 *   - lookup/comuni-bundle.json[<istat>] (anagrafica)
 *   - demografia/<istat>.json
 *   - profilo/<istat>.json
 *   - turismo/<istat>.json
 *   - pnrr/<istat>.json
 *   - territorio/<istat>.json
 *   - bdap/dettaglio/<istat>.json    (opere)
 *   - siope/<istat>.json             (spese SIOPE pre-calcolate)
 *   - immobili_pa/<istat>.json     (MEF DE - Beni Immobili Pubblici 2022)
 *   - lookup/anac-aggregato.json[<cf>] (contratti)
 *
 * Schema output (passa-attraverso del file R2):
 *   {
 *     "_etl_version": "0.1.0",
 *     "_generated_at": "ISO-8601",
 *     "_missing": ["lista shard non disponibili per questo comune"],
 *     "anagrafica":  { ... },
 *     "demografia":  { ... } | null,
 *     "profilo":     { ... } | null,
 *     "turismo":     { ... } | null,
 *     "pnrr":        { ... } | null,
 *     "territorio":  { ... } | null,
 *     "opere":       { ... } | null,
 *     "siope":       { ... } | null,    // schema v0.2.0 multi-anno:
 *                                         //   { _etl_version: "0.2.0",
 *                                         //     anni_disponibili: [2025, 2026],
 *                                         //     anno_default: 2025,
 *                                         //     per_anno: {
 *                                         //       "2025": { totale_anno, n_voci, voci, mesi_disponibili,
 *                                         //                 ultimo_mese, parziale: false, ... },
 *                                         //       "2026": { ..., parziale: true }
 *                                         //     }
 *                                         //   }
 *     "anac":        { ... } | null,
 *     "immobili_pa": { ... } | null    // MEF DE - Beni Immobili Pubblici 2022:
 *                                       //   { anno_rilevazione: 2022,
 *                                       //     kpi: { n_totale, n_fabbricati, n_terreni,
 *                                       //            pct_geo_referenziati,
 *                                       //            pct_vincolo_qualsiasi, pct_vincolo_culturale,
 *                                       //            pct_uso_terzi, superficie_totale_mq,
 *                                       //            mix_categoria, mix_natura },
 *                                       //     punti: [{ lat, lon, cat, tipo, sup,
 *                                       //               vincolo, uso_terzi }, ...]
 *                                       //               // capped a 500 punti per comune
 *                                       //               // (sampling stratificato per categoria)
 *                                       //   }
 *   }
 *
 * NB sulla cache:
 *   - KV cache disabilitata: shard grandi (Roma ~984 KB raw) ridurrebbero il
 *     vantaggio per via di JSON.parse lato worker; R2 con CF cache layer
 *     copre comunque i casi caldi.
 *   - Aggiornamento dati: l'ETL dashboard rigenera i file su R2 quando uno
 *     degli shard sorgente cambia. Per ora ricostruito on-demand via GitHub
 *     Actions (TODO multi-cadence).
 *
 * Input alternativi:
 *   - istat_code (preferito, 6 cifre)
 *   - denominazione (case-insensitive, fallback)
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ComuneAnagrafica {
  istat_code: string;
  denominazione: string;
  provincia: string;
  regione: string;
  codice_ipa: string | null;
  codice_fiscale: string | null;
  nome_categoria: string | null;
  codice_catastale: string | null;
  kpi: Record<string, unknown>;
}

interface ComuniBundle {
  _etl_version: string;
  comuni: Record<string, ComuneAnagrafica>;
}

interface DashboardShard {
  _etl_version: string;
  _generated_at: string;
  _missing: string[];
  anagrafica: ComuneAnagrafica;
  demografia: unknown | null;
  profilo: unknown | null;
  turismo: unknown | null;
  pnrr: unknown | null;
  territorio: unknown | null;
  opere: unknown | null;
  siope: unknown | null;
  anac: unknown | null;
}

export const comuneDashboard: ToolDefinition = {
  description:
    "Vista completa di un comune italiano in una sola chiamata: anagrafica, demografia, profilo censimento, turismo, progetti PNRR, territorio (ISPRA Suolo/IdroGEO/Rifiuti), qualità dell'aria (ISPRA SNPA: PM10/PM2.5/NO2 con stazioni), opere pubbliche (BDAP-MOP), spese (SIOPE multi-anno con per_anno e anno_default), contratti (ANAC), scuole (MIUR), veicoli e incidenti (ISTAT 41_993 parco PRA per classe Euro + ISTAT 41_983 incidenti stradali con morti/feriti + ACI LOD nuove iscrizioni per alimentazione), redditi e fisco (MEF Dipartimento delle Finanze: dichiarazioni IRPEF su base comunale a.i. 2020-2024 con numero contribuenti, reddito medio, distribuzione per 8 fasce di reddito, tipologie dipendente/pensione/autonomo/fabbricati, addizionale comunale e imposta netta media). Tool da preferire per qualsiasi domanda generale su un comune ('mostrami Bergamo', 'dati di Milano'). Richiede istat_code (6 cifre, es. '075035'). Se hai solo il nome, chiama prima search_comune per ottenerlo. Accetta anche denominazione ma è meno affidabile sui casi di omonimia/fusione. Include anche la sezione immobili_pa con beni immobili pubblici detenuti dalle PA (MEF DE 2022, dichiarazioni al 31/12/2022): KPI aggregati (fabbricati/terreni, vincolo culturale, uso a terzi, superficie totale, mix categoria) e fino a 500 punti georeferenziati con tipologia e categoria semantica.",
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
    const istatCode = args.istat_code as string | undefined;
    const denominazione = args.denominazione as string | undefined;

    if (!istatCode && !denominazione) {
      throw new Error("Either 'istat_code' or 'denominazione' is required");
    }

    // 1. Risoluzione istat_code se non fornito
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
    }

    // 2. Fetch del dashboard shard (no KV cache: file grandi)
    const shardKey = `dashboard/${resolvedIstat}.json`;
    const shard = await fetchR2Json<DashboardShard>(
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

    return shard;
  },
};
