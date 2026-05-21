/**
 * Helper per leggere oggetti R2 con caching in KV.
 *
 * Pattern:
 *   - Prima check KV (chiave 'r2cache:<key>')
 *   - Se miss, GET da R2, parse, store in KV con TTL
 *   - Return parsed value
 *
 * Per oggetti > 25 MB (limite KV) il caching va disabilitato esplicitamente.
 */

import type { Env } from "../index.js";

const KV_PREFIX = "r2cache:";
const DEFAULT_TTL_SECONDS = 60; // 60s: bilanciato per propagare update ETL/cron veloce

export async function fetchR2Json<T = unknown>(
  env: Env,
  r2Key: string,
  options: { ttl?: number; useKvCache?: boolean } = {}
): Promise<T | null> {
  const { ttl = DEFAULT_TTL_SECONDS, useKvCache = true } = options;
  const cacheKey = `${KV_PREFIX}${r2Key}`;

  // 1. Try KV cache
  if (useKvCache) {
    try {
      const cached = await env.CACHE.get(cacheKey, "json");
      if (cached !== null) {
        return cached as T;
      }
    } catch {
      /* cache miss is fine */
    }
  }

  // 2. Fetch from DATA_BASE_URL (B1: HTTPS instead of R2 binding)
  // Cache CF edge: TTL 60s allineato a DEFAULT_TTL_SECONDS. Bilancia carico
  // backend (1 fetch/min/shard popolare) e freshness post-ETL (max 60s di lag).
  const url = `${env.DATA_BASE_URL}/${r2Key}`;
  const r = await fetch(url, {
    cf: { cacheTtl: 60, cacheEverything: true },
    headers: env.DATA_BASIC_AUTH ? { "Authorization": `Basic ${env.DATA_BASIC_AUTH}` } : {},
  });
  if (r.status === 404) {
    return null;
  }
  if (!r.ok) {
    throw new Error(`fetch ${r.status} on ${r2Key}`);
  }
  const text = await r.text();
  let parsed: T;
  try {
    parsed = JSON.parse(text) as T;
  } catch (e) {
    throw new Error(`Failed to parse JSON from R2 key '${r2Key}': ${String(e)}`);
  }

  // 3. Store in KV (best-effort, fire-and-forget if too big)
  if (useKvCache) {
    try {
      // KV value limit is 25 MB. Skip cache if too big.
      if (text.length < 24 * 1024 * 1024) {
        await env.CACHE.put(cacheKey, text, { expirationTtl: ttl });
      }
    } catch {
      /* don't fail the request if cache write fails */
    }
  }

  return parsed;
}
