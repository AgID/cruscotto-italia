/**
 * DuckDB-WASM bootstrap.
 *
 * NOTA: l'integrazione DuckDB-WASM nel Worker richiede attenzione perché:
 * - WASM bundle è ~30 MB (oltre il limite Worker Free, 10 MB)
 * - Serve Cloudflare Workers Paid plan ($5/mese) per WASM module > 10MB
 *
 * Alternative considerate (vedi DESIGN.md § 1.3):
 * 1. DuckDB-WASM diretto (richiede paid plan) — preferito per latenza
 * 2. R2 + range reads + parser Parquet JS leggero (es. parquetjs-lite) — più complesso
 * 3. Sidecar Cloudflare Container con DuckDB nativo — overkill per MVP
 *
 * Per il MVP iniziale (v0.1) implementiamo lo stub: tutte le funzioni
 * lanciano "NotImplemented" e i tool ritornano dati mock dai fixtures.
 * Si attiverà il vero DuckDB-WASM in v0.2 sotto paid plan.
 */

import type { Env } from "../index.js";

export interface QueryResult {
  rows: Record<string, unknown>[];
  rowCount: number;
  schema: { name: string; type: string }[];
}

/**
 * Esegue una query SQL su un Parquet R2.
 * STUB: ritorna NotImplemented finché non attiviamo DuckDB-WASM.
 */
export async function queryParquet(
  _env: Env,
  _r2Key: string,
  _sql: string,
  _params?: unknown[]
): Promise<QueryResult> {
  throw new Error(
    "queryParquet not implemented yet. " +
      "DuckDB-WASM integration scheduled for v0.2. " +
      "See worker/src/lib/duck.ts for context."
  );
}

/**
 * Ritorna URL pre-firmato (R2 public bucket o signed) per un Parquet.
 * Usato per leggere via HTTP range reads dal frontend.
 */
export async function r2PublicUrl(_env: Env, key: string): Promise<string> {
  // Per il MVP usiamo R2 public bucket. Custom domain configurabile.
  return `https://data.cruscotto-italia.piersoft.it/${key}`;
}
