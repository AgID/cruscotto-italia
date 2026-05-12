/**
 * Tool: comune_demografia
 *
 * Demografia ISTAT POSAS per un comune al 1 gennaio 2026.
 * Legge shard pre-aggregato R2 'demografia/<istat>.json'.
 *
 * Restituisce: KPI (popolazione, % giovani/adulti/anziani, eta media,
 * indice vecchiaia/dipendenza) + matrice eta x sesso per piramide.
 *
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface DemografiaShard {
  istat_code: string;
  comune: string;
  popolazione_totale: number;
  maschi: number;
  femmine: number;
  pct_maschi: number;
  pct_femmine: number;
  fasce_eta: {
    "0_14": { n: number; pct: number };
    "15_64": { n: number; pct: number };
    "65_piu": { n: number; pct: number };
    "85_piu": { n: number; pct: number };
  };
  eta_media: number;
  indice_vecchiaia: number | null;
  indice_dipendenza: number | null;
  piramide: { eta: number; m: number; f: number; tot: number }[];
}

const CACHE_TTL = 60 * 60;

export const comuneDemografia: ToolDefinition = {
  description:
    "Demografia di un comune italiano al 1 gennaio 2026 (stima ISTAT POSAS). Restituisce popolazione totale, distribuzione M/F, fasce di eta (0-14, 15-64, 65+, 85+), eta media, indice di vecchiaia, indice di dipendenza, e matrice 0-100 anni per genere (per piramide demografica).",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre (es. '075035' per Lecce)",
      },
    },
    required: ["istat_code"],
    additionalProperties: false,
  },
  handler: async (args, env: Env) => {
    const istatCode = args.istat_code as string;

    const cacheKey = `demografia:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    const shard = await fetchR2Json<DemografiaShard>(
      env,
      `demografia/${istatCode}.json`
    );
    if (!shard?.popolazione_totale) {
      return { error: "demografia_not_found", istat_code: istatCode };
    }

    const result = {
      _source: "ISTAT POSAS - Popolazione residente per eta e sesso",
      _riferimento: "1 gennaio 2026 (stima ISTAT)",
      ...shard,
    };

    try {


      await env.CACHE.put(cacheKey, JSON.stringify(result), {
      expirationTtl: CACHE_TTL,
    });


    } catch (e) {


      // KV put limit exceeded? Ignoriamo: la cache e' opzionale.


    }

    return result;
  },
};
