/**
 * outputSchema di search_comune. Fixture: tests/worker/fixtures/search_*.json
 */
export const searchComuneOutputSchema: Record<string, unknown> = {
  type: "object",
  description: "Comuni che corrispondono alla query (prefisso prima, poi sottostringa), con codice ISTAT da usare nei tool comune_*.",
  required: ["count", "results"],
  properties: {
    count: { type: "number", description: "Numero di risultati restituiti (<= limit)" },
    query: { type: "string", description: "Query normalizzata (minuscola, trim)" },
    total_in_index: { type: "number", description: "Comuni presenti nell'indice" },
    warning: { type: "string" },
    results: {
      type: "array",
      items: {
        type: "object",
        required: ["istat_code", "denominazione"],
        properties: {
          istat_code: { type: "string", description: "Codice ISTAT 6 cifre" },
          denominazione: { type: "string" },
          provincia: { type: ["string", "null"], description: "Sigla provincia" },
          regione: { type: ["string", "null"] },
          codice_ipa: { type: ["string", "null"], description: "Codice IPA dell'ente (es. c_e506)" },
          codice_fiscale: { type: ["string", "null"] },
        },
        additionalProperties: true,
      },
    },
  },
  additionalProperties: true,
};
