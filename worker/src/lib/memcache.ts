/**
 * Cache in memoria per-isolate per i risultati dei tool di ricerca.
 * Sostituisce il KV (27/11/2026): le put in KV a ogni miss superavano il
 * limite gratuito di 1.000 put/giorno e riempivano lo storage (508 MB).
 * Best-effort: ogni isolate ha la sua cache, che sparisce al riciclo.
 */
const MAX_ENTRIES = 200;
const store = new Map<string, { exp: number; val: unknown }>();

export function memGet<T = unknown>(key: string): T | null {
  const e = store.get(key);
  if (!e) return null;
  if (e.exp < Date.now()) { store.delete(key); return null; }
  store.delete(key); store.set(key, e); // LRU: sposta in coda
  return e.val as T;
}

export function memPut(key: string, val: unknown, ttlSeconds: number): void {
  if (store.has(key)) store.delete(key);
  store.set(key, { exp: Date.now() + ttlSeconds * 1000, val });
  while (store.size > MAX_ENTRIES) {
    const oldest = store.keys().next().value;
    if (oldest === undefined) break;
    store.delete(oldest);
  }
}
