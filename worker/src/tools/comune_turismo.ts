/**
 * Tool: comune_turismo
 *
 * Turismo del comune dal dataflow ISTAT capacita ricettiva (TUR_1) +
 * flussi turistici provinciali (TUR_7).
 *
 * Legge shard pre-aggregato R2 'turismo/<istat>.json'.
 *
 * 2 sezioni:
 *  - capacita_comune (anno 2024, COMUNALE):
 *      strutture, letti, camere, indice turisticita per 100 ab,
 *      breakdown alberghi 1-5 stelle + extra-alberghiero
 *      (B&B, agriturismi, camping, case in affitto, ostelli, etc.)
 *  - flussi_provincia (anno 2024, PROVINCIALE NUTS3):
 *      arrivi/presenze italiani+stranieri, permanenza media,
 *      % turisti stranieri.
 *      ISTAT NON pubblica flussi a livello comunale.
 *      UI deve mostrare badge "dato provinciale".
 *
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface CategoriaAlloggio {
  strutture: number | null;
  letti: number | null;
}

interface TurismoShard {
  codice_istat: string;
  capacita_comune: {
    anno: number;
    totale_strutture: number | null;
    totale_letti: number | null;
    totale_camere: number | null;
    indice_turisticita_per_100ab: number | null;
    popolazione_riferimento: number | null;
    alberghi: {
      totale_strutture: number | null;
      totale_letti: number | null;
      stelle_5: CategoriaAlloggio;
      stelle_4: CategoriaAlloggio;
      stelle_3: CategoriaAlloggio;
      stelle_2: CategoriaAlloggio;
      stelle_1: CategoriaAlloggio;
      residence: CategoriaAlloggio;
    };
    extra_alberghiero: {
      totale_strutture: number | null;
      totale_letti: number | null;
      bnb: CategoriaAlloggio;
      case_in_affitto: CategoriaAlloggio;
      camping_villaggi: CategoriaAlloggio;
      agriturismi: CategoriaAlloggio;
      ostelli: CategoriaAlloggio;
      case_per_ferie: CategoriaAlloggio;
      rifugi_montagna: CategoriaAlloggio;
      altri_extra: CategoriaAlloggio;
    };
  };
  flussi_provincia: {
    anno: number;
    _warning: string;
    provincia_nuts3: string;
    provincia_nome: string;
    arrivi_totali: number | null;
    arrivi_italiani: number | null;
    arrivi_stranieri: number | null;
    presenze_totali: number | null;
    presenze_italiane: number | null;
    presenze_straniere: number | null;
    permanenza_media: number | null;
    stranieri_pct: number | null;
  };
  fonte: string;
  fonte_url: string;
}

const CACHE_TTL = 60 * 60;

export const comuneTurismo: ToolDefinition = {
  description:
    "Turismo di un comune italiano. Capacita ricettiva a livello comunale (alberghi 1-5 stelle, B&B, camping, agriturismi, ostelli, ecc.) con indice di turisticita (letti per 100 abitanti). Inoltre flussi turistici a livello PROVINCIALE NUTS3 (ISTAT non pubblica i flussi per singolo comune): arrivi e presenze totali e per provenienza (italiani/stranieri), permanenza media, percentuale turisti stranieri. Anno di riferimento 2024.",
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

    const cacheKey = `turismo:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    const shard = await fetchR2Json<TurismoShard>(
      env,
      `turismo/${istatCode}.json`
    );
    if (!shard) {
      return { error: "turismo_not_found", istat_code: istatCode };
    }

    const result = {
      _source: "ISTAT - Capacita ed esercizio degli esercizi ricettivi",
      _riferimento_anno: "2024",
      _note:
        "Capacita ricettiva a livello comunale (TUR_1). Flussi turistici (arrivi, presenze, permanenza media) a livello provinciale NUTS3 (TUR_7): ISTAT non pubblica i flussi turistici per singolo comune.",
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
