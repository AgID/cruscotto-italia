/**
 * outputSchema di anncsu_civico_search. Fixture: tests/worker/fixtures/anncsu_*.json
 * Due forme: comune con shard (query, _snapshot_date, results) e comune senza
 * shard (count 0, _note esplicativa). Entrambe conformi.
 */
export const anncsuCivicoSearchOutputSchema: Record<string, unknown> = {
  type: "object",
  description: "Civici ANNCSU georeferenziati del comune che soddisfano i filtri odonimo/civico. count e' il totale dei match, results e' troncato a limit (_truncated=true).",
  required: ["anagrafica", "count", "results"],
  properties: {
    anagrafica: {
      type: "object",
      required: ["istat_code", "denominazione"],
      properties: {
        istat_code: { type: "string" },
        denominazione: { type: "string" },
      },
      additionalProperties: true,
    },
    _source: { type: "string" },
    _snapshot_date: { type: ["string", "null"], description: "Data dello snapshot ANNCSU (YYYY-MM-DD)" },
    _note: { type: ["string", "null"] },
    _truncated: { type: ["boolean", "null"] },
    _total_civici_comune: { type: ["number", "null"], description: "Civici totali nello shard del comune" },
    query: {
      type: ["object", "null"],
      properties: {
        odonimo: { type: ["string", "null"] },
        civico: { type: ["string", "null"] },
        limit: { type: ["number", "null"] },
      },
      additionalProperties: true,
    },
    count: { type: "number", description: "Match totali (puo' superare results.length)" },
    results: {
      type: "array",
      items: {
        type: "object",
        required: ["lat", "lon", "odonimo", "civico"],
        properties: {
          lat: { type: "number", description: "Latitudine WGS84" },
          lon: { type: "number", description: "Longitudine WGS84" },
          odonimo: { type: "string" },
          civico: { type: "string" },
          esponente: { type: ["string", "null"], description: "Esponente del civico (es. 'A' in 12/A)" },
          quota_m: { type: ["number", "null"], description: "Quota altimetrica in metri" },
          metodo: { type: ["number", "null"], description: "Codice metodo di georeferenziazione" },
          metodo_label: { type: ["string", "null"], description: "Etichetta del metodo (GPS, Catasto, Ortofoto, ...)" },
        },
        additionalProperties: true,
      },
    },
  },
  additionalProperties: true,
};
