/**
 * Tool: comune_opere_dettaglio
 *
 * Restituisce TUTTI i progetti/CUP del comune (filtrati al 2025) per il
 * tab Opere filtrabile. Legge shard pre-aggregato R2 'bdap/dettaglio/<istat>.json'.
 *
 * Filtro temporale: progetti con Data Inizio Validità >= 2025-01-01 OPPURE
 * stato = 'ATTIVO' (armonizzazione con SIOPE 2025).
 *
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";
import { validateIstatCode } from "../lib/validate.js";

interface Progetto {
  cup: string;
  descrizione: string | null;
  stato: string;
  settore: string | null;
  sottosettore: string | null;
  natura: string | null;
  data_inizio: string | null;
  data_fine: string | null;
  costo_eff: number;
  costo_prev: number;
  fin_statali: number;
  fin_europei: number;
  fin_enti_terr: number;
  fin_privati: number;
  fin_altri: number;
}

interface Shard {
  _filter: string;
  istat_code: string;
  codice_fiscale: string;
  n_progetti: number;
  progetti: Progetto[];
}

interface ComuniBundle {
  comuni: Record<string, {
    istat_code: string;
    denominazione: string;
    codice_fiscale: string | null;
  }>;
}

const CACHE_TTL = 60 * 60; // 1h

export const comuneOpereDettaglio: ToolDefinition = {
  description:
    "Dettaglio completo opere pubbliche di un comune (BDAP MOP). Restituisce TUTTI i progetti CUP filtrati al 2025 (data inizio >= 2025-01-01 oppure stato ATTIVO), con descrizione, finanziamenti dettagliati, settore. Per il tab Opere filtrabile.",
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
    const istatCode = validateIstatCode(args.istat_code);

    // Cache KV
    const cacheKey = `bdap_dettaglio:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    // Risolvi anagrafica per arricchire output
    const bundle = await fetchR2Json<ComuniBundle>(env, "lookup/comuni-bundle.json");
    const detail = bundle?.comuni[istatCode];
    if (!detail) {
      return { error: "comune_not_found", istat_code: istatCode };
    }

    // Leggi shard
    const shard = await fetchR2Json<Shard>(env, `bdap/dettaglio/${istatCode}.json`);
    if (!shard?.progetti) {
      return {
        anagrafica: detail,
        n_progetti: 0,
        progetti: [],
        _filter: "only_2025",
        _note: "Nessun progetto BDAP trovato per questo comune (potrebbe essere senza CUP)",
      };
    }

    // KPI aggregati per la strip in alto
    const totale_eff = shard.progetti.reduce((s, p) => s + (p.costo_eff || 0), 0);
    const totale_prev = shard.progetti.reduce((s, p) => s + (p.costo_prev || 0), 0);
    const stati: Record<string, number> = {};
    const settori: Record<string, number> = {};
    for (const p of shard.progetti) {
      stati[p.stato] = (stati[p.stato] || 0) + 1;
      if (p.settore) settori[p.settore] = (settori[p.settore] || 0) + 1;
    }

    const result = {
      anagrafica: {
        istat_code: detail.istat_code,
        denominazione: detail.denominazione,
        codice_fiscale: detail.codice_fiscale,
      },
      _source: "BDAP MOP - Progetti Opere Pubbliche - dettaglio per comune",
      _filter: shard._filter,
      _note: "Filtro 2025: progetti con data inizio >= 2025-01-01 OPPURE stato ATTIVO",
      n_progetti: shard.n_progetti,
      totale_costo_eff: totale_eff,
      totale_costo_prev: totale_prev,
      distribuzione_stati: stati,
      distribuzione_settori: settori,
      progetti: shard.progetti,
    };

    // Cache
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
