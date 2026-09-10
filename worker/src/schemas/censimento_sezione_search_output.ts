/**
 * outputSchema di censimento_sezione_search. Fixture: tests/worker/fixtures/cens_*.json
 * Due modalita' (campo `mode`): "lookup" -> `risultato` (una sezione),
 * "ranking" -> `risultati` (top N) + contatori. Comune senza shard -> count 0.
 */
const SEZIONE = {
  type: "object",
  required: ["sez_id"],
  properties: {
    sez_id: { type: "string", description: "SEZ21_ID nazionale della sezione di censimento" },
    lat: { type: ["number", "null"], description: "Centroide approssimato (media vertici), WGS84" },
    lon: { type: ["number", "null"] },
    area_kmq: { type: ["number", "null"] },
    vars: {
      type: "object",
      description: "Variabili censuarie ISTAT 2023 (P1 popolazione, ST1 stranieri, PF1 famiglie, A8 abitazioni, ...): codice -> valore numerico",
      additionalProperties: { type: ["number", "null"] },
    },
    _no_vars: { type: ["boolean", "null"], description: "true se la sezione e' non residenziale (nessuna variabile)" },
    _computed: { type: ["number", "null"], description: "Valore usato per il ranking (var_name, o var_name/denominator_var)" },
  },
  additionalProperties: true,
};

export const censimentoSezioneSearchOutputSchema: Record<string, unknown> = {
  type: "object",
  description: "Sezioni di censimento ISTAT 2023 del comune: lookup di una sezione (mode=lookup, campo risultato) o ranking per variabile (mode=ranking, campo risultati).",
  required: ["anagrafica"],
  properties: {
    anagrafica: {
      type: "object",
      required: ["istat_code", "denominazione"],
      properties: { istat_code: { type: "string" }, denominazione: { type: "string" } },
      additionalProperties: true,
    },
    _source: { type: "string" },
    _license: { type: ["string", "null"] },
    _note: { type: ["string", "null"] },
    mode: { type: ["string", "null"], enum: ["lookup", "ranking", null] },
    sez_id: { type: ["string", "null"], description: "(lookup) sezione richiesta" },
    risultato: { ...SEZIONE, type: ["object", "null"], description: "(lookup) la sezione trovata" },
    query: {
      type: ["object", "null"],
      description: "(ranking) parametri applicati",
      properties: {
        var_name: { type: "string" },
        denominator_var: { type: ["string", "null"] },
        order: { type: ["string", "null"] },
        top: { type: ["number", "null"] },
        min_pop: { type: ["number", "null"] },
      },
      additionalProperties: true,
    },
    n_sezioni_totali: { type: ["number", "null"] },
    n_sezioni_con_dati: { type: ["number", "null"] },
    n_sezioni_no_vars: { type: ["number", "null"], description: "Sezioni non residenziali escluse dal ranking" },
    n_sezioni_qualificate: { type: ["number", "null"], description: "Sezioni che superano min_pop" },
    _computed_label: { type: ["string", "null"], description: "Etichetta della metrica di ranking" },
    count: { type: ["number", "null"] },
    risultati: { type: ["array", "null"], description: "(ranking) top N sezioni ordinate", items: SEZIONE },
  },
  additionalProperties: true,
};
