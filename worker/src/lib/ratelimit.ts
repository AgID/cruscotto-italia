/**
 * Rate limit basato sul binding nativo Cloudflare Rate Limiting.
 * Limite: N richieste/minuto per IP (configurato in wrangler.toml [[ratelimits]]).
 *
 * Vantaggi vs KV-based:
 *  - Non consuma quota KV PUT (1000/giorno su Free tier)
 *  - Sliding window + sincronizzazione cross-region async
 *  - Latenza zero (counters cached locally)
 */
import type { Env } from "../index.js";

export async function rateLimit(req: Request, env: Env): Promise<Response | null> {
  const ip =
    req.headers.get("CF-Connecting-IP") ||
    req.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
    "unknown";

  const limit = parseInt(env.RATE_LIMIT_RPM ?? "60", 10);

  try {
    const { success } = await env.MCP_RATE_LIMITER.limit({ key: ip });

    if (!success) {
      return new Response(
        JSON.stringify({
          error: "rate_limited",
          message: `Limit ${limit}/min exceeded`,
          retry_after_seconds: 60,
        }),
        {
          status: 429,
          headers: {
            "Content-Type": "application/json",
            "Retry-After": "60",
            "X-RateLimit-Limit": String(limit),
            "X-RateLimit-Remaining": "0",
          },
        }
      );
    }
  } catch (err) {
    /* Binding non disponibile o errore: fallback open (servizio continua) */
    console.error("Rate limiter error:", err);
  }

  return null;
}
