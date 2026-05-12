/**
 * Tool: comune_profilo
 *
 * Profilo del comune dal Censimento permanente ISTAT.
 * Legge shard pre-aggregato R2 'profilo/<istat>.json'.
 *
 * 5 sezioni:
 *  - istruzione (anno 2024, fascia 25-64): % terziario, diploma, max media
 *  - lavoro     (anno 2024, fascia 25-64): tasso occupazione/disoccupazione/attivita
 *  - famiglie   (anno 2024): numero famiglie, dim. media
 *  - mobilita   (anno 2019, ULTIMO disponibile per pendolarismo): pendolari fuori comune,
 *               split lavoro/studio. UI deve mostrare badge "dato 2019".
 *  - cittadinanza (anno 2024): % stranieri
 *
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ProfiloShard {
  codice_istat: string;
  istruzione: {
    anno: number;
    pop_riferimento_25_64: number | null;
    terziario_n: number | null;
    terziario_pct: number | null;
    diploma_oltre_n: number | null;
    diploma_oltre_pct: number | null;
    max_media_n: number | null;
    max_media_pct: number | null;
    dettaglio: {
      nessun_titolo: number | null;
      elementare: number | null;
      media: number | null;
      diploma: number | null;
      laurea_triennale: number | null;
      laurea_magistrale_dottorato: number | null;
    };
  };
  lavoro: {
    anno: number;
    pop_riferimento_25_64: number | null;
    occupati_n: number | null;
    in_cerca_n: number | null;
    forze_lavoro_n: number | null;
    tasso_occupazione: number | null;
    tasso_disoccupazione: number | null;
    tasso_attivita: number | null;
  };
  famiglie: {
    anno: number;
    n_famiglie: number | null;
    pop_in_famiglia: number | null;
    pop_in_convivenza: number | null;
    dim_media_famiglia: number | null;
  };
  mobilita: {
    anno: number;
    _warning: string;
    pendolari_totale_n: number | null;
    fuori_comune_n: number | null;
    fuori_comune_pct: number | null;
    per_lavoro_n: number | null;
    per_lavoro_pct: number | null;
    per_studio_n: number | null;
    per_studio_pct: number | null;
  };
  cittadinanza: {
    anno: number;
    pop_totale_n: number | null;
    italiani_n: number | null;
    stranieri_n: number | null;
    stranieri_pct: number | null;
  };
  fonte: string;
  fonte_url: string;
}

const CACHE_TTL = 60 * 60;

export const comuneProfilo: ToolDefinition = {
  description:
    "Profilo socio-demografico di un comune italiano dal Censimento permanente ISTAT. Restituisce 5 sezioni: istruzione (% laureati, diplomati nella fascia 25-64), lavoro (tassi di occupazione/disoccupazione/attivita 25-64), famiglie (numero famiglie, dimensione media), mobilita (pendolarismo per lavoro/studio, dati 2019), cittadinanza (% stranieri). Anno di riferimento 2024 per tutte le sezioni tranne mobilita (2019, ultimo disponibile).",
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

    const cacheKey = `profilo:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    const shard = await fetchR2Json<ProfiloShard>(
      env,
      `profilo/${istatCode}.json`
    );
    if (!shard) {
      return { error: "profilo_not_found", istat_code: istatCode };
    }

    const result = {
      _source: "ISTAT - Censimento permanente",
      _riferimento_anno: "2024 (mobilita: 2019)",
      _note:
        "Dati aggregati per fascia 25-64 anni per istruzione e lavoro. Dato di mobilita aggiornato all'ultimo censimento permanente con questo dettaglio (2019). Comuni piccoli (<2000 abitanti) possono avere alcuni indicatori non disponibili per protezione statistica ISTAT.",
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
