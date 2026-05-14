#!/usr/bin/env python3
"""
Cruscotto Italia — Fetcher analytics MCP da Cloudflare KV.

Pesca i contatori scritti dal Worker MCP nel KV namespace CACHE
(chiavi `analytics:YYYY-MM-DD:<tool>:<istat>:<client>`) e produce
un JSON aggregato consumato poi da stats_aggregator.py.

Conformità privacy AgID:
- I contatori KV sono già aggregati anonimi (no IP, no UA grezzo)
- Lo script non aggiunge PII; solo somma e organizza

Variabili d'ambiente richieste:
  CF_ACCOUNT_ID    Account ID Cloudflare (visibile nel dashboard)
  CF_KV_NAMESPACE  KV namespace ID (per CACHE: 9251e463afc3406b83f81e555a6e12b7)
  CF_API_TOKEN     Token con permesso "Workers KV Storage:Read"

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


def aggregate(keys_values: dict[str, str], istat_names: dict[str, str],
              days_window: int) -> dict:
    """
    Aggrega le coppie key→value in statistiche.
    Key format: `analytics:YYYY-MM-DD:<tool>:<istat>:<client>`
    """
    cutoff = (date.today() - timedelta(days=days_window)).isoformat()

    by_tool: dict[str, int] = defaultdict(int)
    by_comune: dict[str, int] = defaultdict(int)
    by_client: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    by_tool_comune: dict[tuple[str, str], int] = defaultdict(int)
    total = 0

    skipped_malformed = 0
    skipped_old = 0

    for k, v in keys_values.items():
        # k = "analytics:YYYY-MM-DD:tool:istat:client"
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
        if istat != "_":
            by_comune[istat] += n
            by_tool_comune[(tool, istat)] += n

    return {
        "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "_days_window": days_window,
        "_period_start": cutoff,
        "_skipped_malformed": skipped_malformed,
        "_skipped_old": skipped_old,
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
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True,
                    help="Directory output (scrive mcp_stats.json)")
    ap.add_argument("--istat-names",
                    help="JSON mapping {istat: nome} (opzionale)")
    ap.add_argument("--days", type=int, default=30,
                    help="Numero di giorni recenti da aggregare (default: 30)")
    ap.add_argument("--prefix", default="analytics:",
                    help="Prefix delle chiavi KV da leggere (default: analytics:)")
    args = ap.parse_args()

    account_id = os.environ.get("CF_ACCOUNT_ID")
    namespace_id = os.environ.get("CF_KV_NAMESPACE")
    token = os.environ.get("CF_API_TOKEN")

    missing = [k for k, v in (
        ("CF_ACCOUNT_ID", account_id),
        ("CF_KV_NAMESPACE", namespace_id),
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

    print(f"→ Listo keys con prefix '{args.prefix}'...")
    try:
        keys = list_keys(account_id, namespace_id, token, args.prefix)
    except (HTTPError, URLError, RuntimeError) as e:
        print(f"✗ Errore lista keys: {e}", file=sys.stderr)
        return 1
    print(f"✓ Keys trovate: {len(keys):,}")

    if not keys:
        # Scrivi comunque output vuoto per non rompere il consumer
        empty = {
            "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "_days_window": args.days,
            "totals": {"calls": 0, "distinct_tools": 0,
                       "distinct_comuni": 0, "distinct_clients": 0},
            "by_tool": [], "by_comune": [], "by_client": [], "by_day": [],
        }
        (out_dir / "mcp_stats.json").write_text(
            json.dumps(empty, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Output vuoto scritto in {out_dir / 'mcp_stats.json'}")
        return 0

    print(f"→ Leggo valori in bulk (batch 100)...")
    try:
        values = get_bulk_values(account_id, namespace_id, token, keys)
    except (HTTPError, URLError, RuntimeError) as e:
        print(f"✗ Errore bulk get: {e}", file=sys.stderr)
        return 1
    print(f"✓ Valori letti: {len(values):,}")

    print(f"→ Aggrego (window {args.days} giorni)...")
    stats = aggregate(values, istat_names, args.days)

    out_file = out_dir / "mcp_stats.json"
    out_file.write_text(json.dumps(stats, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"✓ Tool calls totali: {stats['totals']['calls']:,}")
    print(f"✓ Tool distinti: {stats['totals']['distinct_tools']}")
    print(f"✓ Comuni distinti: {stats['totals']['distinct_comuni']}")
    print(f"✓ Client distinti: {stats['totals']['distinct_clients']}")
    print(f"✓ Output: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
