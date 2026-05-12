/**
 * Tool: comune_territorio
 *
 * Profilo ambientale del comune: consumo di suolo, rischio idrogeologico
 * (frane + alluvioni), gestione dei rifiuti urbani.
 *
 * Tre fonti ISPRA federate in un unico shard:
 *   1. ISPRA Consumo di Suolo (Rapporto SNPA 2025) — XLSX
 *      Stock 2024 + serie storica annuale 2006→2024 (incrementi netti + ripristini)
 *   2. ISPRA IdroGEO PIR — API REST
 *      Pericolosità da frane (P1-P4 + AA) e alluvioni (P1-P3)
 *      Esposti: popolazione, famiglie, edifici, imprese, beni culturali
 *   3. ISPRA Catasto Nazionale Rifiuti — CSV per anno
 *      Serie storica 2010→2024 (% RD, RU totale, RD totale, kg/abitante)
 *      Gestione aggregazioni territoriali (capofila / membro)
 *
 * Esempi:
 *   - Lecce 075035: 14.83% suolo consumato, 70.39% RD, 1% pop a rischio frane
 *   - Roma  058091: 23.7% suolo consumato, 48.03% RD, 30.396 ha cementificati
 *   - Aliano 077002: 1.66% suolo, 26.57% RD, 7.7% pop a rischio frane elevate
 *
 * Per i comuni membri di un'Unione (raccolta rifiuti consolidata), il blocco
 * `rifiuti.ultimo` è null e `rifiuti._aggregato` indica l'Unione di appartenenza.
 *
 * Licenza: tutte le fonti CC-BY 4.0
 * Cache KV 1h.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface SuoloIntervallo {
  intervallo: string;
  netto_ha: number | null;
  ripristino_ha: number | null;
}

interface SuoloBlock {
  stock_2024: { ha: number | null; pct: number | null };
  serie_storica: SuoloIntervallo[];
}

interface RischioBlock {
  _disclaimer: string;
  alluvioni: Record<string, number | null>;
  frane: Record<string, number | null>;
  _demografia_idrogeo: Record<string, number | null>;
}

interface RifiutiAnno {
  anno: number;
  rd_pct: number | null;
  ru_t: number | null;
  rd_t: number | null;
  kg_ab: number | null;
}

interface RifiutiUltimo {
  popolazione: number | null;
  ru_t: number | null;
  rd_t: number | null;
  rd_pct: number | null;
  kg_ab: number | null;
}

interface RifiutiBlock {
  ultimo_anno: number | null;
  ultimo: RifiutiUltimo | null;
  serie_storica: RifiutiAnno[];
  _aggregato: string | null;
  _aggregato_ruolo: "capofila" | "membro" | null;
}

interface GeoBlock {
  ar_kmq: number | null;
  osmid: number | null;
  extent: [[number, number], [number, number]] | null;
}

interface TerritorioKpi {
  ar_kmq: number | null;
  suolo_consumato_2024_pct: number | null;
  incremento_2024_ha: number | null;
  popolazione_frane_p3p4_pct: number | null;
  rd_pct_ultimo_anno: number | null;
  rd_ultimo_anno: number | null;
  kg_per_abitante_ultimo_anno: number | null;
}

interface TerritorioShard {
  istat_code: string;
  denominazione: string;
  provincia: string;
  regione: string;
  kpi: TerritorioKpi;
  suolo?: SuoloBlock;
  rischio_idrogeologico?: RischioBlock;
  rifiuti?: RifiutiBlock;
  geo?: GeoBlock;
}

const CACHE_TTL = 60 * 60;

export const comuneTerritorio: ToolDefinition = {
  description:
    "Profilo ambientale di un comune italiano: consumo di suolo, rischio idrogeologico, rifiuti urbani. Combina tre fonti ISPRA: (1) Consumo di Suolo (Rapporto SNPA 2025) con stock 2024 e serie storica 2006-2024; (2) IdroGEO PIR con pericolosità frane (P1-P4+AA) e alluvioni (P1-P3) e popolazione/famiglie/edifici/imprese/beni culturali esposti; (3) Catasto Nazionale Rifiuti con serie storica 2010-2024 di % raccolta differenziata, RU totale, RD totale, kg per abitante. Per i comuni membri di Unioni con gestione rifiuti consolidata, indica l'aggregazione di appartenenza. Tutte le fonti hanno licenza CC-BY 4.0. NOTA: non include i dati di qualità dell'aria (PM10/PM2.5/NO2 della rete SNPA), che sono in uno shard separato accessibile via comune_dashboard sezione aria (604 comuni con stazione).",
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

    const cacheKey = `territorio:${istatCode}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    const shard = await fetchR2Json<TerritorioShard>(
      env,
      `territorio/${istatCode}.json`
    );
    if (!shard) {
      return { error: "territorio_not_found", istat_code: istatCode };
    }

    const result = {
      _source:
        "ISPRA Consumo di Suolo (Rapporto SNPA 2025) + ISPRA IdroGEO PIR + ISPRA Catasto Nazionale Rifiuti",
      _license: "CC-BY 4.0",
      _note:
        "Suolo: dato comunale annuale ISPRA. Rischio idrogeologico: Mosaicatura ISPRA v5.0 (2020) + Censimento ISTAT 2021. Rifiuti: per i comuni membri di Unioni con raccolta consolidata, il dato proprio è null e il campo _aggregato indica l'Unione di appartenenza.",
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
