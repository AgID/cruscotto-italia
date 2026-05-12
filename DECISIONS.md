# Decisioni di progetto

Questo documento registra le decisioni prese sui punti aperti di [`DESIGN.md` § 9](DESIGN.md#9-open-questions). Sono **default ragionevoli** modificabili in futuro senza grandi rotture.

| # | Domanda DESIGN.md | Decisione | Razionale | Reversibile? |
|---|-------------------|-----------|-----------|--------------|
| 1 | Frontend hosting? | Cloudflare Pages | Edge cache integrata + stesso account del Worker | Sì (basta cambio DNS) |
| 2 | Auth opzionale? | No al MVP, Cloudflare Turnstile per anti-abuse | Free tier coperto da rate-limit IP a 60 rpm | Sì |
| 3 | Sotto-soglia ANAC SmartCIG? | **No al MVP** | Volume enorme + valore informativo basso, dataset separato | Sì (aggiungere `etl/sources/anac_smartcig.py`) |
| 4 | Formato export? | CSV + JSON (no Excel) | CSV universale, JSON per i dev | Sì |
| 5 | i18n? | Solo IT al MVP, EN in v1.x | Audience primaria italiana, EN dopo per OCDS internazionale | Sì |
| 6 | Domain? | `cruscotto-italia.piersoft.it` | Mantieni autorialità del progetto | Sì |
| 7 | Licenza codice? | AGPL-3.0 | Coerente con i tuoi altri progetti, copyleft strong | Difficile (cambio licenza richiede consenso contributor) |
| 8 | Deploy MCP? | `cruscotto-italia-mcp.piersoft.workers.dev` (alias `mcp.cruscotto-italia.piersoft.it`) | Custom domain pulito, leggibile | Sì |
| 9 | Test data nel repo? | Sì, snapshot mini (50-100 righe per fonte) in `tests/fixtures/` | CI veloce + esempi di dev | Sì |

## Decisioni aggiuntive emerse

| # | Tema | Decisione | Note |
|---|------|-----------|------|
| 10 | Versioning frontend | `cruscotto-italia-vYYYY.MM.DD.N.html` | Pattern Piersoft consolidato |
| 11 | Versioning Worker | SemVer (`0.1.0`, `0.2.0`, ...) in `package.json` | Standard npm |
| 12 | DuckDB engine | DuckDB-WASM nel Worker | Senza spin-up server, esegue Parquet HTTP range reads |
| 13 | Cache strategia | Cloudflare KV con TTL 1h per query frequenti | KV ha 100k ops/giorno gratis |
| 14 | Logging | Cloudflare Workers Analytics + Sentry (free tier) | Errori critici → email |
| 15 | ETL log | GitHub Actions logs + R2 manifest aggiornato a fine run | Self-documenting |
| 16 | Schema migrations | Cartella `etl/migrations/NNNN-description.py` | Sequenziale, idempotenti |
| 17 | Naming Parquet | `<source>/<entity>/<partition>.parquet` | Es. `anac/awards/2024.parquet` |
| 18 | Compression Parquet | ZSTD level 3 | Bilanciamento ratio/velocità |

## Come modificare

Apri una PR che modifica questo file e linka l'issue/discussion che motiva il cambio. Le decisioni con `Reversibile? = Difficile` richiedono majority dei contributor.
