#!/usr/bin/env python3
"""
Cruscotto Italia — Fetcher analytics MCP da Cloudflare Analytics Engine.

Pesca i contatori scritti dal Worker MCP nel KV namespace CACHE
(chiavi `analytics:YYYY-MM-DD:<tool>:<istat>:<client>`) e produce
un JSON aggregato consumato poi da stats_aggregator.py.

Conformità privacy AgID:
- I contatori KV sono già aggregati anonimi (no IP, no UA grezzo)
- Lo script non aggiunge PII; solo somma e organizza

Variabili d'ambiente richieste:
  CF_ACCOUNT_ID    Account ID Cloudflare (visibile nel dashboard)
  CF_AE_DATASET    Dataset Analytics Engine (default: cruscotto_mcp_analytics)
  CF_API_TOKEN     Token con permesso "Account Analytics:Read"

Uso:
  python3 mcp_stats_fetcher.py --out /var/www/cruscotto-stats \\
    --istat-names /var/www/cruscotto-stats/istat-names.json \\
    --days 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

CF_API_BASE = "https://api.cloudflare.com/client/v4"


def cf_get(path: str, token: str, params: dict | None = None) -> dict:
    """GET su Cloudflare API, ritorna il dict result."""
    url = f"{CF_API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        raise RuntimeError(f"CF API error: {data.get('errors')}")
    return data


def list_keys(account_id: str, namespace_id: str, token: str, prefix: str) -> list[str]:
    """Lista tutte le keys con un prefix (paginata)."""
    keys = []
    cursor = None
    while True:
        params = {"prefix": prefix, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        path = f"/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/keys"
        data = cf_get(path, token, params=params)
        keys.extend(k["name"] for k in data.get("result", []))
        cursor = data.get("result_info", {}).get("cursor")
        if not cursor:
            break
    return keys


def get_value(account_id: str, namespace_id: str, token: str, key: str) -> str | None:
    """Legge il valore di una key. None se non esiste."""
    path = f"/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{key}"
    url = f"{CF_API_BASE}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def get_bulk_values(account_id: str, namespace_id: str, token: str,
                     keys: list[str]) -> dict[str, str]:
    """
    Legge in bulk fino a 100 key per richiesta via endpoint /bulk/get.
    Molto più efficiente del get singolo (1 round-trip ogni 100 chiavi).
    """
    if not keys:
        return {}
    result: dict[str, str] = {}
    for i in range(0, len(keys), 100):
        batch = keys[i:i + 100]
        path = f"/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/bulk/get"
        url = f"{CF_API_BASE}{path}"
        body = json.dumps({"keys": batch, "type": "text"}).encode("utf-8")
        req = Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success"):
            raise RuntimeError(f"CF API bulk error: {data.get('errors')}")
        values = data.get("result", {}).get("values", {})
        for k, v in values.items():
            if v is not None:
                result[k] = v
    return result


def aggregate_main(keys_values: dict[str, str], istat_names: dict[str, str],
                   days_window: int) -> dict:
    """
    Aggrega i counter principali (prefix 'analytics:').
    Key format: `analytics:YYYY-MM-DD:<tool>:<istat>:<client>`
    """
    cutoff = (date.today() - timedelta(days=days_window)).isoformat()

    by_tool: dict[str, int] = defaultdict(int)
    by_comune: dict[str, int] = defaultdict(int)
    by_client: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    # Heatmap: matrice giorno × tool
    by_day_tool: dict[tuple[str, str], int] = defaultdict(int)
    total = 0

    skipped_malformed = 0
    skipped_old = 0

    for k, v in keys_values.items():
        parts = k.split(":", 4)
        if len(parts) != 5 or parts[0] != "analytics":
            skipped_malformed += 1
            continue
        _, day, tool, istat, client = parts
        if day < cutoff:
            skipped_old += 1
            continue
        try:
            n = int(v)
        except (ValueError, TypeError):
            skipped_malformed += 1
            continue
        total += n
        by_tool[tool] += n
        by_client[client] += n
        by_day[day] += n
        by_day_tool[(day, tool)] += n
        if istat != "_":
            by_comune[istat] += n

    return {
        "totals": {
            "calls": total,
            "distinct_tools": len(by_tool),
            "distinct_comuni": len(by_comune),
            "distinct_clients": len(by_client),
        },
        "by_tool": [
            {"tool": t, "calls": n}
            for t, n in sorted(by_tool.items(), key=lambda x: -x[1])
        ],
        "by_comune": [
            {
                "istat": istat,
                "nome": istat_names.get(istat, istat),
                "calls": n,
            }
            for istat, n in sorted(by_comune.items(), key=lambda x: -x[1])[:30]
        ],
        "by_client": [
            {"client": c, "calls": n}
            for c, n in sorted(by_client.items(), key=lambda x: -x[1])
        ],
        "by_day": [
            {"day": d, "calls": n}
            for d, n in sorted(by_day.items())
        ],
        "by_day_tool": [
            {"day": d, "tool": t, "calls": n}
            for (d, t), n in sorted(by_day_tool.items())
        ],
        "_skipped_malformed": skipped_malformed,
        "_skipped_old": skipped_old,
    }


def aggregate_errors(keys_values: dict[str, str], days_window: int) -> dict:
    """
    Aggrega i counter degli errori (prefix 'analytics-err:').
    Key format: `analytics-err:YYYY-MM-DD:<tool>:<client>`
    """
    cutoff = (date.today() - timedelta(days=days_window)).isoformat()
    by_tool: dict[str, int] = defaultdict(int)
    by_client: dict[str, int] = defaultdict(int)
    total = 0

    for k, v in keys_values.items():
        parts = k.split(":", 3)
        if len(parts) != 4 or parts[0] != "analytics-err":
            continue
        _, day, tool, client = parts
        if day < cutoff:
            continue
        try:
            n = int(v)
        except (ValueError, TypeError):
            continue
        total += n
        by_tool[tool] += n
        by_client[client] += n

    return {
        "total": total,
        "by_tool": [
            {"tool": t, "errors": n}
            for t, n in sorted(by_tool.items(), key=lambda x: -x[1])
        ],
        "by_client": [
            {"client": c, "errors": n}
            for c, n in sorted(by_client.items(), key=lambda x: -x[1])
        ],
    }


def aggregate_terms(keys_values: dict[str, str], days_window: int,
                    limit: int = 30) -> dict:
    """
    Aggrega i termini di ricerca (prefix 'analytics-term:').
    Key format: `analytics-term:YYYY-MM-DD:<slug>`
    """
    cutoff = (date.today() - timedelta(days=days_window)).isoformat()
    by_term: dict[str, int] = defaultdict(int)
    total = 0

    for k, v in keys_values.items():
        parts = k.split(":", 2)
        if len(parts) != 3 or parts[0] != "analytics-term":
            continue
        _, day, slug = parts
        if day < cutoff:
            continue
        try:
            n = int(v)
        except (ValueError, TypeError):
            continue
        total += n
        by_term[slug] += n

    return {
        "total": total,
        "distinct_terms": len(by_term),
        "top_terms": [
            {"term": t, "calls": n}
            for t, n in sorted(by_term.items(), key=lambda x: -x[1])[:limit]
        ],
    }


def fetch_prefix(account_id: str, namespace_id: str, token: str,
                 prefix: str) -> dict[str, str]:
    """Lista + bulk get di tutte le chiavi con il prefix dato."""
    keys = list_keys(account_id, namespace_id, token, prefix)
    if not keys:
        return {}
    return get_bulk_values(account_id, namespace_id, token, keys)


def ae_sql(account_id: str, token: str, sql: str) -> list[dict]:
    """POST SQL sull'endpoint Analytics Engine, ritorna le righe (data)."""
    url = f"{CF_API_BASE}/accounts/{account_id}/analytics_engine/sql"
    req = Request(url, data=sql.encode("utf-8"), method="POST",
                  headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("data", [])


def fetch_ae_counters(account_id: str, token: str, dataset: str,
                      days: int) -> tuple[dict, dict, dict]:
    """Ricostruisce da AE i 3 dizionari {chiave_KV: conteggio} del vecchio KV.

    Mapping (blobs del writeDataPoint: [tool, istat, client, status, term]):
      main : blob4='ok'              -> analytics:DAY:tool:istat:client
      err  : blob4='error'           -> analytics-err:DAY:tool:client
      term : blob1='search_comune'   -> analytics-term:DAY:slug
    SUM(_sample_interval * double1) compensa l'eventuale sampling AE.
    """
    win = f"WHERE timestamp > NOW() - INTERVAL '{int(days)}' DAY"

    main_kv = {}
    for r in ae_sql(account_id, token, f"""
        SELECT toDate(timestamp) AS day, blob1 AS tool, blob2 AS istat,
               blob3 AS client, SUM(_sample_interval * double1) AS n
        FROM {dataset} {win} AND blob4 = 'ok'
        GROUP BY day, tool, istat, client FORMAT JSON"""):
        main_kv[f"analytics:{r['day']}:{r['tool']}:{r['istat']}:{r['client']}"] = str(int(float(r["n"])))

    err_kv = {}
    for r in ae_sql(account_id, token, f"""
        SELECT toDate(timestamp) AS day, blob1 AS tool, blob3 AS client,
               SUM(_sample_interval * double1) AS n
        FROM {dataset} {win} AND blob4 = 'error'
        GROUP BY day, tool, client FORMAT JSON"""):
        err_kv[f"analytics-err:{r['day']}:{r['tool']}:{r['client']}"] = str(int(float(r["n"])))

    term_kv = {}
    for r in ae_sql(account_id, token, f"""
        SELECT toDate(timestamp) AS day, blob5 AS term,
               SUM(_sample_interval * double1) AS n
        FROM {dataset} {win} AND blob1 = 'search_comune' AND blob5 <> ''
        GROUP BY day, term FORMAT JSON"""):
        term_kv[f"analytics-term:{r['day']}:{r['term']}"] = str(int(float(r["n"])))

    return main_kv, err_kv, term_kv



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True,
                    help="Directory output (scrive mcp_stats.json)")
    ap.add_argument("--istat-names",
                    help="JSON mapping {istat: nome} (opzionale)")
    ap.add_argument("--days", type=int, default=30,
                    help="Numero di giorni recenti da aggregare (default: 30)")
    args = ap.parse_args()

    account_id = os.environ.get("CF_ACCOUNT_ID")
    token = os.environ.get("CF_API_TOKEN")
    dataset = os.environ.get("CF_AE_DATASET", "cruscotto_mcp_analytics")

    missing = [k for k, v in (
        ("CF_ACCOUNT_ID", account_id),
        ("CF_API_TOKEN", token),
    ) if not v]
    if missing:
        print(f"✗ Variabili d'ambiente mancanti: {', '.join(missing)}",
              file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    istat_names: dict[str, str] = {}
    if args.istat_names:
        p = Path(args.istat_names)
        if p.exists():
            istat_names = json.loads(p.read_text(encoding="utf-8"))

    try:
        print(f"→ Query Analytics Engine '{dataset}' (window {args.days}gg)...")
        main_kv, err_kv, term_kv = fetch_ae_counters(account_id, token, dataset, args.days)
        print(f"  ✓ main: {len(main_kv):,} — err: {len(err_kv):,} — term: {len(term_kv):,} chiavi")
    except (HTTPError, URLError, RuntimeError) as e:
        print(f"✗ Errore fetch: {e}", file=sys.stderr)
        return 1

    print(f"→ Aggrego (window {args.days} giorni)...")
    main_stats = aggregate_main(main_kv, istat_names, args.days)
    errors = aggregate_errors(err_kv, args.days)
    terms = aggregate_terms(term_kv, args.days)

    cutoff = (date.today() - timedelta(days=args.days)).isoformat()

    stats = {
        "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "_days_window": args.days,
        "_period_start": cutoff,
        **main_stats,
        "errors": errors,
        "search_terms": terms,
    }

    out_file = out_dir / "mcp_stats.json"
    out_file.write_text(json.dumps(stats, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"✓ Tool calls totali: {stats['totals']['calls']:,}")
    print(f"✓ Tool distinti: {stats['totals']['distinct_tools']}")
    print(f"✓ Comuni distinti: {stats['totals']['distinct_comuni']}")
    print(f"✓ Client distinti: {stats['totals']['distinct_clients']}")
    print(f"✓ Errori totali: {errors['total']:,}")
    print(f"✓ Termini ricerca distinti: {terms['distinct_terms']:,} ({terms['total']:,} ricerche)")
    print(f"✓ Output: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
