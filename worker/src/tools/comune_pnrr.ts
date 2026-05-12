/**
 * Tool: comune_pnrr
 *
 * Progetti PNRR del comune (in qualità di Soggetto Attuatore).
 *
 * Fonte: Italia Domani / Sistema ReGiS (Presidenza del Consiglio).
 * Match: Codice Fiscale del Soggetto Attuatore -> denominazione comune.
 *
 * Mostra TUTTI i progetti dove il comune è soggetto attuatore diretto:
 * - 43 progetti per Lecce (€47.7M PNRR)
 * - 284 progetti per Roma Capitale (€1.019B PNRR)
 * - 83 progetti per Milano (€681M PNRR)
 *
 * NON include progetti realizzati nel territorio comunale da altri enti
 * (Regioni, Ministeri, GSE, Aziende Sanitarie, Soprintendenze): il dataset
 * Italia Domani non contiene un campo "comune destinatario" affidabile.
 *
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ProgettoPnrr {
  cup: string;
  titolo: string;
  missione: string;
  missione_descrizione: string;
  componente: string;
  componente_descrizione: string;
  submisura: string;
  submisura_descrizione: string;
  finanziamento_pnrr: number;
  finanziamento_totale: number;
  stato_avanzamento: string;
  fase_iter: string;
  stato_fase_iter: string;
  data_inizio_prevista: string | null;
  data_inizio_effettiva: string | null;
  data_fine_prevista: string | null;
  data_fine_effettiva: string | null;
  soggetto_attuatore: string;
  settore: string;
  natura: string;
}

interface PerMissione {
  missione: string;
  descrizione: string;
  n_progetti: number;
  tot_pnrr: number;
  tot_globale: number;
}

interface PnrrShard {
  codice_istat: string;
  kpi: {
    n_progetti: number;
    totale_finanziamento_pnrr: number;
    totale_finanziamento_globale: number;
    n_concluso: number;
    n_in_corso: number;
    n_altro: number;
    n_missioni_distinte: number;
    missioni_principali: string[];
  };
  per_missione: PerMissione[];
  progetti: ProgettoPnrr[];
  fonte: string;
  fonte_url: string;
  data_estrazione: string | null;
}

const CACHE_TTL = 60 * 60;

export const comunePnrr: ToolDefinition = {
  description:
    "Progetti PNRR (Piano Nazionale di Ripresa e Resilienza) di un comune italiano in qualità di Soggetto Attuatore. Restituisce tutti i progetti finanziati con fondi PNRR realizzati direttamente dal comune, con dettaglio per missione (M1-M7), componente, submisura, finanziamento PNRR e totale, stato di avanzamento (Concluso, In Corso), fase iter di progetto, date previste ed effettive, soggetto attuatore, settore e natura. Fonte: Italia Domani / Sistema ReGiS. NON include progetti realizzati sul territorio comunale da altri enti (Regioni, Ministeri, GSE, Aziende Sanitarie, Università): il dataset di origine non contiene mappatura affidabile progetto-territorio comunale.",
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

    const cacheKey = `pnrr:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    const shard = await fetchR2Json<PnrrShard>(
      env,
      `pnrr/${istatCode}.json`
    );
    if (!shard) {
      return { error: "pnrr_not_found", istat_code: istatCode };
    }

    const result = {
      _source: "Italia Domani - Sistema ReGiS",
      _note:
        "Progetti PNRR dove il comune è 'Soggetto Attuatore'. Non include progetti realizzati sul territorio da altri enti (Regioni, Ministeri, GSE, ecc.).",
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
