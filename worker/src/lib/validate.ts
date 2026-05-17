/**
 * Validazione vincolante input MCP — conforme alle raccomandazioni
 * CERT-AgID "Analisi di sicurezza su implementazioni MCP open source"
 * (paper aprile 2026, raccomandazione 1: Validazione vincolante).
 *
 * I JSON Schema dichiarati negli inputSchema dei tool sono SOLO
 * documentazione client-side: non vengono enforced dal dispatcher
 * JSON-RPC (mcp.ts). Per evitare SSRF, path traversal e simili
 * (CERT-AgID raccomandazione 2: Allowlist restrittive), ogni
 * handler DEVE validare runtime gli argomenti via le funzioni
 * di questo modulo PRIMA di usarli per costruire key R2 o filtri.
 *
 * Ogni funzione getta un Error a messaggio chiaro se la validazione
 * fallisce, MAI un fallback silenzioso (CERT-AgID raccomandazione 1:
 * "eliminare ogni logica di riserva (fallback) che possa aggirare i
 * controlli in caso di errore").
 */

/** Codice ISTAT comune: esattamente 6 cifre, no leading/trailing space. */
const ISTAT_RE = /^\d{6}$/;

/** Denominazione comune: caratteri leciti italiani, max 80 char. */
const DENOMINAZIONE_RE = /^[A-Za-zÀ-ÿ0-9' \-,.()/]{1,80}$/;

/** Query libera search_comune: alfanumerico + spazi/accenti, 3-50 char. */
const QUERY_RE = /^[A-Za-zÀ-ÿ0-9' \-,.]{3,50}$/;

/** Odonimo ANNCSU: nome strada, max 120 char. */
const ODONIMO_RE = /^[A-Za-zÀ-ÿ0-9' \-,.()/]{1,120}$/;

/** Numero civico: alfanumerico breve (es. "5", "12/A", "Sn"), max 15 char. */
const CIVICO_RE = /^[A-Za-z0-9/ \-]{1,15}$/;

/** ID OpenAI MCP fetch: deve essere un ISTAT (6 cifre). */
const FETCH_ID_RE = /^\d{6}$/;

/**
 * Valida codice ISTAT (6 cifre stringa). Lancia Error se invalido.
 * @throws Error con codice JSON-RPC suggerito -32602 (invalid params).
 */
export function validateIstatCode(value: unknown, paramName = "istat_code"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  if (!ISTAT_RE.test(value)) {
    throw new Error(
      `Parameter '${paramName}' must match pattern ${ISTAT_RE.source} (6 digits). ` +
      `Got: '${value.slice(0, 20)}${value.length > 20 ? "..." : ""}'`
    );
  }
  return value;
}

/** Valida denominazione comune. Lancia Error se invalida. */
export function validateDenominazione(value: unknown, paramName = "denominazione"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  const trimmed = value.trim();
  if (!DENOMINAZIONE_RE.test(trimmed)) {
    throw new Error(
      `Parameter '${paramName}' must match pattern (alphanumeric + accents + ' - , . ( ) /, max 80 char). ` +
      `Got: '${trimmed.slice(0, 20)}${trimmed.length > 20 ? "..." : ""}'`
    );
  }
  // Anti path-traversal (difensivo, anche se '/' non è nella regex di denominazione)
  if (trimmed.includes("..") || trimmed.includes("//")) {
    throw new Error(
      `Parameter '${paramName}' contains forbidden sequence.`
    );
  }
  return trimmed;
}

/** Valida query libera per search_comune. */
export function validateQuery(value: unknown, paramName = "query"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  const trimmed = value.trim();
  if (!QUERY_RE.test(trimmed)) {
    throw new Error(
      `Parameter '${paramName}' must match pattern (alphanumeric + accents + ' - , ., 3-50 char). ` +
      `Got: '${trimmed.slice(0, 20)}${trimmed.length > 20 ? "..." : ""}'`
    );
  }
  return trimmed;
}

/** Valida odonimo (nome strada) ANNCSU. */
export function validateOdonimo(value: unknown, paramName = "odonimo"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) return ""; // odonimo opzionale, vuoto = no filtro
  if (!ODONIMO_RE.test(trimmed)) {
    throw new Error(
      `Parameter '${paramName}' must match pattern (alphanumeric + accents + ' - , . ( ) /, max 120 char). ` +
      `Got: '${trimmed.slice(0, 30)}${trimmed.length > 30 ? "..." : ""}'`
    );
  }
  // Anti path-traversal: rifiuta sequenze '..' anche se i caratteri singoli
  // '.' e '/' sono ammessi nella regex per nomi legittimi come 'S. Antonio'
  // o 'V. Tiziano / V. Caravaggio'.
  if (trimmed.includes("..") || trimmed.includes("//")) {
    throw new Error(
      `Parameter '${paramName}' contains forbidden sequence ('..' or '//').`
    );
  }
  return trimmed;
}

/** Valida numero civico (es. "5", "12/A"). */
export function validateCivico(value: unknown, paramName = "civico"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) return ""; // civico opzionale, vuoto = no filtro
  if (!CIVICO_RE.test(trimmed)) {
    throw new Error(
      `Parameter '${paramName}' must match pattern (alphanumeric + '/' + '-' + space, max 15 char). ` +
      `Got: '${trimmed.slice(0, 20)}${trimmed.length > 20 ? "..." : ""}'`
    );
  }
  return trimmed;
}

/** Valida limit (intero positivo entro bound). */
export function validateLimit(
  value: unknown,
  min: number,
  max: number,
  defaultValue: number,
  paramName = "limit"
): number {
  if (value === undefined || value === null) return defaultValue;
  const n = Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) {
    throw new Error(`Parameter '${paramName}' must be an integer, got ${typeof value}`);
  }
  if (n < min || n > max) {
    throw new Error(`Parameter '${paramName}' must be in range [${min}, ${max}], got ${n}`);
  }
  return n;
}

/** Valida ID per il tool `fetch` (compat OpenAI): deve essere un ISTAT. */
export function validateFetchId(value: unknown, paramName = "id"): string {
  if (typeof value !== "string") {
    throw new Error(`Parameter '${paramName}' must be a string, got ${typeof value}`);
  }
  if (!FETCH_ID_RE.test(value)) {
    throw new Error(
      `Parameter '${paramName}' must be a 6-digit ISTAT code. Got: '${value.slice(0, 20)}'`
    );
  }
  return value;
}
