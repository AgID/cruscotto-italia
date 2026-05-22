/**
 * Tool: censimento_sezione_search
 *
 * Ricerca puntuale o ranking sulle 119 variabili raw del Censimento
 * Permanente 2021 ISTAT (Basi Territoriali + Variabili censuarie) per
 * singola sezione di censimento del comune.
 *
 * Distinto da comune_dashboard.censimento (KPI aggregati comune-level):
 * questo tool espone il dato grezzo a livello di sezione di censimento,
 * con le 119 vars (P1, P2, ..., ST33, PF1-PF8, A2/A3/A8, E3, IT*).
 *
 * Due modalita':
 *   - lookup (con sez_id):  ritorna 1 sezione con tutte le 119 vars raw.
 *   - ranking (con var_name): ordina le sezioni del comune per il valore
 *     di una variabile (o per rapporto var_name/denominator_var se set),
 *     top N risultati.
 *
 * Use case tipici:
 *   - "Quale sezione di Lecce ha la popolazione piu' alta?"
 *     -> var_name=P1, top=5
 *   - "Sezione con piu' stranieri Extra-UE in proporzione a Roma?"
 *     -> var_name=ST19, denominator_var=ST1, top=10
 *   - "Variabili censuarie complete della sezione 23456012?"
 *     -> sez_id=23456012
 *   - "Sezioni di Milano col tasso piu' alto di abitazioni vuote?"
 *     -> var_name=A3, denominator_var=A8, top=10, min_pop=100
 *
 * Storage: il geojson per comune e' su nginx VM AgID statico
 * (DATA_BASE_URL/censimento_full/<istat>.geojson). Cache 24h locale.
 *
 * Caveat:
 *   - 33% delle sezioni nazionali sono 'no_vars' (aree non residenziali:
 *     parchi, zone industriali, infrastrutture). Vengono escluse
 *     automaticamente dal ranking.
 *   - Centroide calcolato come media aritmetica dei vertici del poligono
 *     (approssimato, NON baricentro geometrico).
 *   - Aggiornamento decennale: prossimo Censimento 2031.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";
import { validateIstatCode, validateLimit } from "../lib/validate.js";

interface ComuniBundle {
  comuni: Record<string, { istat_code: string; denominazione: string }>;
}

interface FeatureProperties {
  id: number;          // sez_id
  sez?: string | number;
  tipo_loc?: number;
  loc_id?: number;
  area_mq: number;
  vars: Record<string, number>;  // 0-119 keys
}

interface Feature {
  type: "Feature";
  properties: FeatureProperties;
  geometry: {
    type: "Polygon" | "MultiPolygon";
    coordinates: number[][][] | number[][][][];
  };
}

interface CensimentoFC {
  type: "FeatureCollection";
  _source: string;
  _istat: number;
  _n_sezioni: number;
  features: Feature[];
}

const DEFAULT_TOP = 10;
const MAX_TOP = 50;
const CACHE_TTL = 24 * 60 * 60; // 24h

// Whitelist delle variabili. Il geojson dichiara P*, IT*, ST*, PF*, A*, E*.
// Pattern minimal: alfa + digits, 1-4 char totali.
const VAR_NAME_PATTERN = /^[A-Z]{1,2}\d{1,3}$/;

function validateVarName(value: unknown, paramName = "var_name"): string {
  if (value === undefined || value === null) {
    throw new Error(`${paramName} obbligatorio in modalita' ranking`);
  }
  if (typeof value !== "string") {
    throw new Error(`${paramName} deve essere stringa`);
  }
  const v = value.trim().toUpperCase();
  if (!VAR_NAME_PATTERN.test(v)) {
    throw new Error(
      `${paramName}='${value}' non valido. Atteso codice tipo P1, ST19, A8 (alfa + digits, 1-4 char totali)`
    );
  }
  return v;
}

function validateSezId(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") {
    throw new Error("sez_id deve essere stringa o numero");
  }
  const s = String(value).trim();
  if (!/^\d{1,12}$/.test(s)) {
    throw new Error(`sez_id='${value}' non valido. Atteso intero positivo`);
  }
  return s;
}

/**
 * Centroide approssimato: media aritmetica dei vertici del primo ring
 * del Polygon (o del primo Polygon del MultiPolygon). Va bene per
 * visualizzazione cartografica; per analisi spaziali precise usare turf.js.
 */
function polygonCentroid(geom: Feature["geometry"]): { lat: number; lon: number } | null {
  try {
    let ring: number[][];
    if (geom.type === "Polygon") {
      ring = (geom.coordinates as number[][][])[0];
    } else if (geom.type === "MultiPolygon") {
      ring = (geom.coordinates as number[][][][])[0][0];
    } else {
      return null;
    }
    if (!ring || ring.length === 0) return null;
    let sumLon = 0;
    let sumLat = 0;
    let n = 0;
    // Salta l'ultimo vertice se chiude il poligono (= primo)
    const limit = ring.length > 1 &&
                  ring[0][0] === ring[ring.length - 1][0] &&
                  ring[0][1] === ring[ring.length - 1][1]
                ? ring.length - 1 : ring.length;
    for (let i = 0; i < limit; i++) {
      sumLon += ring[i][0];
      sumLat += ring[i][1];
      n++;
    }
    if (n === 0) return null;
    return {
      lat: Math.round((sumLat / n) * 1e6) / 1e6,
      lon: Math.round((sumLon / n) * 1e6) / 1e6,
    };
  } catch {
    return null;
  }
}

export const censimentoSezioneSearch: ToolDefinition = {
  description:
    "Ricerca o ranking sulle 119 variabili censuarie raw del Censimento Permanente 2021 ISTAT (Basi Territoriali + Variabili censuarie, CC BY 3.0 IT) per singola sezione di censimento di un comune italiano. Distinto da comune_dashboard.censimento (KPI aggregati comune-level): qui il dato grezzo a livello sub-comunale, una sezione di censimento per record. Due modalita': (1) LOOKUP - passa sez_id (es. 750350001012) per ottenere le 119 vars raw + area + centroide di quella sezione; (2) RANKING - passa var_name (es. 'P1' popolazione, 'ST19' stranieri extra-UE, 'A3' abitazioni vuote, 'PF1' famiglie) per ottenere la top N sezioni ordinate per quel valore. Opzionalmente denominator_var per ranking percentuale (es. var_name='ST19' denominator_var='ST1' = % extra-UE su totale stranieri). Variabili chiave: P1 pop totale, P2/P3 maschi/femmine, P14-P29 fasce eta' 5anni (totale), P30-P45 maschi, P46-P61 femmine, P62-P82 stato civile, P86-P90 titolo di studio 9+ (nessuno/elementare/media/diploma/terziario per sesso), P101-P103 occupati 15-64 (tot/M/F), IT1-IT12 italiani per fascia eta', ST1 stranieri tot, ST16 UE, ST19 Extra-UE, ST3-ST5 stranieri per fascia eta', ST20-ST33 stranieri occupati/UE/extra dettagliati, PF1 famiglie tot, PF3-PF8 famiglie 1-6+ componenti, A2/A3/A8 abitazioni occupate/vuote/totali, E3 edifici residenziali. Le 33% sezioni 'no_vars' (aree non residenziali: parchi, zone industriali, infrastrutture) escluse automaticamente da ranking. Centroide approssimato (media vertici). Aggiornamento decennale (prossimo 2031).",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre del comune (es. '075035' per Lecce). Obbligatorio.",
      },
      sez_id: {
        type: "string",
        description:
          "ID univoco nazionale della sezione di censimento (SEZ21_ID, 1-12 cifre). Se valorizzato attiva la modalita' LOOKUP: ritorna 1 sola sezione con le 119 vars complete. Mutualmente esclusivo con var_name.",
      },
      var_name: {
        type: "string",
        description:
          "Codice variabile censuaria ISTAT da usare per il ranking (es. 'P1', 'ST19', 'A8'). Richiesto in modalita' RANKING (sez_id non passato).",
      },
      denominator_var: {
        type: "string",
        description:
          "Codice variabile censuaria da usare come denominatore per ranking percentuale (es. var_name='ST19' denominator_var='ST1' -> % extra-UE su stranieri). Se non passato il ranking e' sul valore assoluto.",
      },
      order: {
        type: "string",
        enum: ["desc", "asc"],
        description:
          "Direzione del ranking: 'desc' (default) sezioni con valore piu' alto, 'asc' valore piu' basso.",
      },
      top: {
        type: "integer",
        minimum: 1,
        maximum: MAX_TOP,
        description: `Numero massimo di sezioni da restituire in ranking (default ${DEFAULT_TOP}, max ${MAX_TOP}). Ignorato in lookup.`,
      },
      min_pop: {
        type: "integer",
        minimum: 0,
        description:
          "Esclude dal ranking le sezioni con P1 (popolazione totale) inferiore a questa soglia. Default 0 (nessun filtro). Utile per evitare rumore su sezioni minuscole (es. min_pop=100 filtra le sezioni con meno di 100 abitanti).",
      },
    },
    required: ["istat_code"],
    additionalProperties: false,
  },
  handler: async (args, env: Env) => {
    // === Validazione input ===
    const istatCode = validateIstatCode(args.istat_code);

    // Modalita': sez_id presente => lookup, altrimenti ranking
    const isLookup = args.sez_id !== undefined && args.sez_id !== null && args.sez_id !== "";

    let sezId = "";
    let varName = "";
    let denomVar = "";
    let order: "desc" | "asc" = "desc";
    let top = DEFAULT_TOP;
    let minPop = 0;

    if (isLookup) {
      sezId = validateSezId(args.sez_id);
    } else {
      varName = validateVarName(args.var_name);
      if (args.denominator_var !== undefined && args.denominator_var !== null && args.denominator_var !== "") {
        denomVar = validateVarName(args.denominator_var, "denominator_var");
      }
      if (args.order === "asc") order = "asc";
      top = validateLimit(args.top, 1, MAX_TOP, DEFAULT_TOP);
      if (typeof args.min_pop === "number" && args.min_pop >= 0) {
        minPop = Math.floor(args.min_pop);
      }
    }

    // === Risolvi anagrafica comune ===
    const bundle = await fetchR2Json<ComuniBundle>(env, "lookup/comuni-bundle.json");
    const detail = bundle?.comuni[istatCode];
    if (!detail) {
      return { error: "comune_not_found", istat_code: istatCode };
    }

    // === Cache key ===
    const cacheKey = isLookup
      ? `censimento_sez:${istatCode}:lookup:${sezId}`
      : `censimento_sez:${istatCode}:rank:${varName}:${denomVar}:${order}:${top}:${minPop}`;
    const cached = await env.CACHE.get(cacheKey, "json");
    if (cached) return cached;

    // === Fetch geojson da nginx VM (DATA_BASE_URL/censimento_full/<istat>.geojson) ===
    const fc = await fetchR2Json<CensimentoFC>(env, `censimento_full/${istatCode}.geojson`);
    if (!fc?.features) {
      const result = {
        anagrafica: { istat_code: detail.istat_code, denominazione: detail.denominazione },
        _source: "ISTAT Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021",
        _note: "Shard censimento_full non disponibile per questo comune.",
        count: 0,
        risultati: [],
      };
      try { await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL }); } catch {}
      return result;
    }

    const nTotali = fc.features.length;
    const conDati = fc.features.filter(f => Object.keys(f.properties.vars ?? {}).length > 0);
    const nConDati = conDati.length;

    // === Modalita' LOOKUP ===
    if (isLookup) {
      const target = fc.features.find(f => String(f.properties.id) === sezId);
      if (!target) {
        const result = {
          anagrafica: { istat_code: detail.istat_code, denominazione: detail.denominazione },
          _source: "ISTAT Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021",
          mode: "lookup",
          sez_id: sezId,
          error: "sez_id_not_found",
          _note: `Sezione ${sezId} non trovata nel comune ${detail.denominazione} (${istatCode}). Comune ha ${nTotali} sezioni totali.`,
        };
        try { await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL }); } catch {}
        return result;
      }
      const centroid = polygonCentroid(target.geometry);
      const result = {
        anagrafica: { istat_code: detail.istat_code, denominazione: detail.denominazione },
        _source: "ISTAT Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021",
        _license: "CC-BY 3.0 IT",
        mode: "lookup",
        sez_id: sezId,
        risultato: {
          sez_id: String(target.properties.id),
          lat: centroid?.lat ?? null,
          lon: centroid?.lon ?? null,
          area_kmq: target.properties.area_mq
            ? Math.round((target.properties.area_mq / 1e6) * 1e6) / 1e6
            : null,
          vars: target.properties.vars ?? {},
          _no_vars: !target.properties.vars || Object.keys(target.properties.vars).length === 0,
        },
      };
      try { await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL }); } catch {}
      return result;
    }

    // === Modalita' RANKING ===
    // Calcola valore di ordinamento per ogni sezione con dati
    interface Scored {
      feat: Feature;
      value: number;
    }
    const scored: Scored[] = [];
    for (const f of conDati) {
      const vars = f.properties.vars;
      if (!vars) continue;
      // Filtro min_pop su P1
      if (minPop > 0) {
        const p1 = vars["P1"] ?? 0;
        if (p1 < minPop) continue;
      }
      const num = vars[varName];
      if (num === undefined || num === null) continue;
      let v: number;
      if (denomVar) {
        const den = vars[denomVar];
        if (den === undefined || den === null || den === 0) continue;
        v = (num / den) * 100;
      } else {
        v = num;
      }
      if (!Number.isFinite(v)) continue;
      scored.push({ feat: f, value: v });
    }

    // Ordina
    scored.sort((a, b) => order === "desc" ? b.value - a.value : a.value - b.value);

    // Prendi top N
    const sliced = scored.slice(0, top);

    const risultati = sliced.map(s => {
      const centroid = polygonCentroid(s.feat.geometry);
      return {
        sez_id: String(s.feat.properties.id),
        lat: centroid?.lat ?? null,
        lon: centroid?.lon ?? null,
        area_kmq: s.feat.properties.area_mq
          ? Math.round((s.feat.properties.area_mq / 1e6) * 1e6) / 1e6
          : null,
        vars: s.feat.properties.vars ?? {},
        _computed: Math.round(s.value * 1e4) / 1e4,
      };
    });

    const result = {
      anagrafica: { istat_code: detail.istat_code, denominazione: detail.denominazione },
      _source: "ISTAT Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021",
      _license: "CC-BY 3.0 IT",
      mode: "ranking",
      query: {
        var_name: varName,
        denominator_var: denomVar || null,
        order,
        top,
        min_pop: minPop,
      },
      n_sezioni_totali: nTotali,
      n_sezioni_con_dati: nConDati,
      n_sezioni_no_vars: nTotali - nConDati,
      n_sezioni_qualificate: scored.length,
      _computed_label: denomVar
        ? `(${varName} / ${denomVar}) * 100`
        : varName,
      risultati,
      _note: scored.length === 0
        ? `Nessuna sezione qualificata. Possibili cause: variabile '${varName}' non presente nei dati ISTAT per questo comune, min_pop ${minPop} troppo alto, o denominatore '${denomVar}' sempre 0.`
        : undefined,
    };

    try { await env.CACHE.put(cacheKey, JSON.stringify(result), { expirationTtl: CACHE_TTL }); } catch {}
    return result;
  },
};
