/**
 * outputSchema di mcp_info. Fixture: tests/worker/fixtures/info_*.json
 */
export const mcpInfoOutputSchema: Record<string, unknown> = {
  type: "object",
  description: "Metadati del server: versione, protocollo, build verificabile, catalogo delle fonti (URL canonico, licenza, dataset) e freschezza ETL per sorgente.",
  required: ["service", "version", "sources"],
  properties: {
    service: { type: "string" },
    version: { type: "string", description: "Versione del worker MCP" },
    protocol: { type: "string" },
    build: {
      type: ["object", "null"],
      properties: {
        worker_tree: { type: ["string", "null"], description: "Tree hash git di worker/ (git rev-parse <commit>:worker sul repo pubblico)" },
        time: { type: ["string", "null"] },
        source: { type: ["string", "null"] },
        verify: { type: ["string", "null"] },
      },
      additionalProperties: true,
    },
    datasets: { type: ["number", "null"] },
    institutions: { type: ["number", "null"] },
    municipalities: { type: ["number", "null"] },
    sources: {
      type: "object",
      description: "Catalogo statico delle fonti: chiave = identificativo sorgente",
      additionalProperties: {
        type: "object",
        properties: {
          canonical: { type: ["string", "null"], description: "URL canonico della fonte" },
          license: { type: ["string", "null"] },
          rights_holder: { type: ["string", "null"] },
          note: { type: ["string", "null"] },
          datasets: { type: ["array", "null"], items: { type: "string" } },
        },
        additionalProperties: true,
      },
    },
    manifest: {
      type: ["object", "null"],
      description: "Freschezza ETL (sintesi del manifest; dettaglio completo in /data/manifest.json)",
      properties: {
        generated_at: { type: ["string", "null"] },
        etl_version: { type: ["string", "null"] },
        full_manifest: { type: ["string", "null"] },
        warning: { type: ["string", "null"] },
        sources: {
          type: "object",
          additionalProperties: {
            type: "object",
            properties: {
              last_run: { type: ["string", "null"], description: "Ultimo run ETL della sorgente (ISO 8601)" },
              status: { type: ["string", "null"] },
              n_files: { type: ["number", "null"] },
              total_bytes: { type: ["number", "null"] },
            },
            additionalProperties: true,
          },
        },
      },
      additionalProperties: true,
    },
    generated_at: { type: ["string", "null"], description: "Istante della risposta" },
  },
  additionalProperties: true,
};
