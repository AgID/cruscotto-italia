/**
 * Tool: anncsu_civico_search
 *
 * Cerca numeri civici puntuali ANNCSU (Archivio Nazionale Numeri Civici e
 * Strade Urbane, Agenzia delle Entrate + ISTAT, HVD UE 2023/138) all'interno
 * di un comune, con filtri opzionali su odonimo (substring match) e civico
 * (esatto). Restituisce un sottoinsieme limitato per evitare di buttare
 * centinaia di migliaia di civici nel context LLM (Roma ~516k, Milano ~280k).
 *
 * Use case tipici:
 *   - "Quote altimetriche di Via dei Mille a Lecce" → odonimo="Via dei Mille"
 *   - "Esiste il civico 5 in Piazza San Marco a Venezia?" → odonimo, civico
 *   - "Quanti civici ha Via Roma a Matera?" → odonimo, limit=500
 *
 * Per il dataset completo (>500 risultati o nessun filtro su comuni grandi),
 * usare l'endpoint REST diretto GET /data/anncsu_full/<istat>.json.
 *
 * Cache KV 24h sulla coppia (istat, query_hash). Lo shard upstream e' gia'
 * pesante (Roma 50MB), quindi vale la pena cachare le query piu' comuni.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";
import {
  validateIstatCode,
  validateOdonimo,
  validateCivico,
  validateLimit,
} from "../lib/validate.js";

interface Civico {
  lat: number;
  lon: number;
  odo: string;     // odonimo (nome strada) UPPERCASE
  civ: string;     // numero civico come stringa
  esp: string;     // esponente / subcivico (es. "A", "B")
  quota: number | null;  // altitudine in metri
  met: number;     // metodo georef: 1=GPS, 3=catasto, 4=altro, 5=...
}

interface FullShard {
  _etl_version: string;
  _source: string;
  _snapshot_date: string;
  _generated_at: string;
  _full: boolean;
  kpi: Record<string, unknown>;
  punti: Civico[];
}

interface ComuniBundle {
  comuni: Record<string, {
    istat_code: string;
    denominazione: string;
    codice_fiscale: string | null;
  }>;
}

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 500;
const CACHE_TTL = 24 * 60 * 60; // 24h, come l'endpoint REST sottostante

// Etichette metodo georeferenziazione (allineate a etl/sources/anncsu.py)
const METODO_LABEL: Record<number, string> = {
  1: "GPS",
  2: "Cartografia",
  3: "Catasto",
  4: "Ortofoto",
  5: "Altra sorgente",
};

function normalizeOdo(s: string): string {
  // ANNCSU usa odonimi in UPPERCASE senza accenti compositi. Sostituiamo
  // apostrofi tipografici e normalizziamo spazi multipli.
  return s
    .toUpperCase()
    .replace(/[\u2019\u2018`']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export const anncsuCivicoSearch: ToolDefinition = {
  description:
    "Cerca numeri civici puntuali ANNCSU (Archivio Nazionale Numeri Civici e Strade Urbane - Agenzia delle Entrate + ISTAT, Open Data UE 2023/138 HVD) in un comune con filtri su odonimo (substring) e/o civico (esatto). Restituisce coordinate geografiche, quota altimetrica, metodo di georeferenziazione (GPS/Catasto/Ortofoto). Default limit 50, max 500. Use case: 'quote di Via X', 'civico Y esiste a Z', 'quanti civici ha Piazza W'. Per il dataset completo del comune (Roma 515.815 civici, Milano ~280k) usare l'endpoint REST GET /data/anncsu_full/<istat>.json. Copertura ~5.387 comuni: quelli con sufficiente pct_geo_ref. Lo shard 'anncsu' di comune_dashboard ha gia' KPI aggregati + 1000 punti sample - usa questo tool solo per query puntuali piu' specifiche. Le coordinate (lon/lat) restituite si possono incrociare con le particelle catastali (GET /data/catasto_full/<istat>_ple.geojson.gz, point-in-polygon lato client) per ricavare foglio e particella: vedi le instructions del server.",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre del comune (es. '075035' per Lecce). Obbligatorio.",
      },
      odonimo: {
        type: "string",
        description: "Substring del nome strada (case-insensitive, accenti opzionali). Es. 'Roma' matcha 'Via Roma', 'Piazza Roma', ecc. Lascia vuoto per non filtrare per nome.",
      },
      civico: {
        type: "string",
        description: "Numero civico esatto (es. '5' o '12'). Match esatto sulla stringa. Usa insieme a 'odonimo' per query puntuali precise.",
      },
      limit: {
        type: "integer",
        minimum: 1,
        maximum: MAX_LIMIT,
        description: `Numero massimo di risultati (default ${DEFAULT_LIMIT}, max ${MAX_LIMIT}). Se vengono trovati piu' risultati, _truncated: true.`,
      },
    },
    required: ["istat_code"],
    additionalProperties: false,
  },
  handler: async (args, env: Env) => {
    // Validazione vincolante CERT-AgID (paper 2026-04, raccomandazione 1).
    const istatCode = validateIstatCode(args.istat_code);
    const odonimoRaw = validateOdonimo(args.odonimo ?? "");
    const civicoRaw = validateCivico(args.civico ?? "");
    const limit = validateLimit(args.limit, 1, MAX_LIMIT, DEFAULT_LIMIT);

    // Risolvi anagrafica (anche se per ANNCSU non serve per il filtro,
    // serve per arricchire output e validare l'esistenza del comune)
    const bundle = await fetchR2Json<ComuniBundle>(env, "lookup/comuni-bundle.json");
    const detail = bundle?.comuni[istatCode];
    if (!detail) {
      return { error: "comune_not_found", istat_code: istatCode };
    }

    // Cache key: hash della query, no canonical JSON per semplicita'
    const odoNorm = normalizeOdo(odonimoRaw);
    const civNorm = civicoRaw.trim();
    const cacheKey = `anncsu_search:${istatCode}:${odoNorm}:${civNorm}:${limit}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    // Fetch dello shard full. Puo' essere assente (comune senza geo-ref
    // sufficiente, ~2.500 comuni su 7.918 hanno shard full).
    const shard = await fetchR2Json<FullShard>(env, `anncsu_full/${istatCode}.json`);
    if (!shard?.punti) {
      const result = {
        anagrafica: {
          istat_code: detail.istat_code,
          denominazione: detail.denominazione,
        },
        _source: "ANNCSU - Agenzia delle Entrate + ISTAT (Open Data UE 2023/138 HVD)",
        _note: "Comune non disponibile nello shard ANNCSU full: pct_geo_ref insufficiente o non ancora processato. Solo ~5.387 comuni su 7.918 hanno lo shard completo.",
        count: 0,
        results: [],
      };
      try {
        await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL });
      } catch (e) { /* ignore */ }
      return result;
    }

    // Filter in-memory (Roma worst case ~516k punti, comunque <100ms su Worker)
    let filtered: Civico[];
    if (!odoNorm && !civNorm) {
      // Nessun filtro: prendi i primi `limit` come slice rappresentativo,
      // ma flag esplicitamente _truncated perche' non e' un risultato completo.
      filtered = shard.punti.slice(0, limit);
    } else {
      filtered = shard.punti.filter(p => {
        if (odoNorm && !p.odo.includes(odoNorm)) return false;
        if (civNorm && p.civ !== civNorm) return false;
        return true;
      });
    }

    const totalMatches = filtered.length;
    const truncated = totalMatches > limit || (!odoNorm && !civNorm && shard.punti.length > limit);
    const sliced = filtered.slice(0, limit);

    // Arricchimento output: aggiungi label metodo umana
    const results = sliced.map(p => ({
      lat: p.lat,
      lon: p.lon,
      odonimo: p.odo,
      civico: p.civ,
      esponente: p.esp || null,
      quota_m: p.quota,
      metodo: p.met,
      metodo_label: METODO_LABEL[p.met] || `Codice ${p.met}`,
    }));

    const result = {
      anagrafica: {
        istat_code: detail.istat_code,
        denominazione: detail.denominazione,
      },
      _source: "ANNCSU - Agenzia delle Entrate + ISTAT (Open Data UE 2023/138 HVD)",
      _snapshot_date: shard._snapshot_date,
      query: {
        odonimo: odonimoRaw || null,
        civico: civicoRaw || null,
        limit,
      },
      count: totalMatches,
      _truncated: truncated,
      _note: truncated
        ? `Risultati troncati a ${limit}. Per il dataset completo del comune usa l'endpoint REST GET /data/anncsu_full/${istatCode}.json (file: ${shard.punti.length} civici totali).`
        : undefined,
      _total_civici_comune: shard.punti.length,
      results,
    };

    try {
      await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL });
    } catch (e) { /* KV put limit; cache opzionale */ }

    return result;
  },
};
