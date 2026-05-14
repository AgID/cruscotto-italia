#!/usr/bin/env python3
"""
Cruscotto Italia — Aggregatore statistiche accesso dal log nginx.

Legge i log nginx (access.log + access.log.1), filtra bot e attacchi,
aggrega per giorno e produce JSON + HTML di riepilogo.

GDPR / Policy AgID compliance:
- Non scrive MAI IP, user-agent grezzi, query string completi in output
- Aggrega tutto: solo conteggi per comune / referrer-domain / paese
- Output destinato ad area protetta htpasswd ("/stats/")

Uso:
  sudo python3 stats_aggregator.py \\
    --logs /var/log/nginx/access.log /var/log/nginx/access.log.1 \\
    --out /var/www/cruscotto-stats \\
    --exclude-test-comuni

Cron giornaliero suggerito (04:00 UTC):
  0 4 * * * /usr/bin/python3 /home/ubuntu/cruscotto-italia/scripts/analytics/stats_aggregator.py --logs /var/log/nginx/access.log.1 --out /var/www/cruscotto-stats --exclude-test-comuni
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# Filtri bot / attacchi (case-insensitive)
# ---------------------------------------------------------------------------

# Pattern user-agent da escludere (bot, crawler, monitoring, accessibility test)
UA_BOT_PATTERNS = re.compile(
    r"(?:"
    r"bot|crawler|spider|searchbot|preview|monitor|uptime|"
    r"pa11y|axios|curl|wget|python-requests|httpie|"
    r"facebookexternalhit|whatsapp|telegrambot|skypeuripreview|"
    r"slackbot|discordbot|linkedinbot|twitterbot|"
    r"ahrefs|semrush|mj12bot|dotbot|petalbot|bingbot|googlebot|"
    r"yandex|baidu|duckduck|applebot|amazonbot|claudebot|gptbot|"
    r"oai-searchbot|chatgpt-user|perplexitybot|"
    r"shodan|censys|zgrab|masscan|nmap|netcraft"
    r")",
    re.IGNORECASE,
)

# Path che sono evidentemente probe/attacchi (router exploit, CMS scan, etc.)
# Path che indicano tentativi di attacco / scanning automatici.
# Pattern intenzionalmente NON ancorato a inizio URI ('^/') perché
# molti scanner tentano path annidati come '/zend/.env', '/var/www/.env',
# '/wp/wp-admin/'. Matcha quindi ovunque nell'URI ('/.env' o 'wp-admin').
PATH_ATTACK_PATTERNS = re.compile(
    r"(?:"
    r"/\.env(?:\.|/|$)|"           # .env, .env.old, .env.backup, .env/...
    r"/\.git(?:/|$)|"              # .git/config, .git/HEAD
    r"/\.aws(?:/|$)|/\.ssh(?:/|$)|"
    r"/boaform|/HNAP1|"            # router IoT exploit
    r"/wp-admin|/wp-login|/wp-content|/wp-includes|/wordpress/|/wp/|"
    r"/phpmyadmin|/pma/|/mysql/|/adminer|"
    r"/cgi-bin|/fckeditor|/tinymce|"
    r"/vendor/phpunit|/phpunit/|"
    r"/HNAP1|/console/|/jenkins/|/gitlab/|"
    r"/actuator/|/jolokia/|"
    r"/webdav/|/\.well-known/security"
    r")",
    re.IGNORECASE,
)

# Path interni che non rappresentano "navigazione utente"
PATH_INTERNAL = re.compile(
    r"^/(?:"
    r"favicon|robots\.txt|sitemap|"
    r"stats(?:/|$)|"
    r"css/|js/|vendor/|assets/|fonts/|images/|img/|"
    r"\.ico|\.css|\.js|\.woff|\.woff2|\.ttf|\.eot|\.svg|\.png|\.jpg|\.jpeg|\.gif|\.webp"
    r")",
    re.IGNORECASE,
)

# I 4 comuni di test usati durante lo sviluppo (Roma + 3 di Piersoft).
# Possono essere esclusi via --exclude-test-comuni per non gonfiare le statistiche.
TEST_COMUNI = {
    "058091",  # Roma
    "075035",  # Lecce
    "077014",  # Matera
    "097055",  # Morterone
}

# ---------------------------------------------------------------------------
# Parser nginx log line
# Formato 'main' (default Ubuntu): combined + $http_x_forwarded_for
# ---------------------------------------------------------------------------

LOG_LINE = re.compile(
    r'^(?P<ip>\S+)\s+'
    r'\S+\s+\S+\s+'                                    # remote_user, time_user
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<size>\d+|-)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
)

REQUEST = re.compile(r'^(?P<method>\S+)\s+(?P<uri>\S+)\s+HTTP')


def open_log(path: Path):
    """Apre log gzippato o plain trasparentemente."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def parse_line(line: str) -> dict | None:
    m = LOG_LINE.match(line)
    if not m:
        return None
    req = REQUEST.match(m["request"])
    if not req:
        return None
    return {
        "ip": m["ip"],
        "time": m["time"],
        "method": req["method"],
        "uri": req["uri"],
        "status": int(m["status"]),
        "size": int(m["size"]) if m["size"].isdigit() else 0,
        "referer": m["referer"],
        "ua": m["ua"],
    }


def is_bot(ua: str) -> bool:
    return bool(UA_BOT_PATTERNS.search(ua or ""))


def is_attack(uri: str) -> bool:
    # search() per matchare anche path annidati tipo '/zend/.env', '/var/www/.env'
    return bool(PATH_ATTACK_PATTERNS.search(uri))


def is_internal_asset(uri: str) -> bool:
    return bool(PATH_INTERNAL.match(uri))


def extract_istat(uri: str) -> str | None:
    """/comune.html?istat=063049 → '063049'."""
    if not uri.startswith("/comune.html"):
        return None
    parsed = urlparse(uri)
    qs = parsed.query
    m = re.search(r"istat=(\d{6})", qs)
    return m.group(1) if m else None


def extract_referer_domain(referer: str) -> str | None:
    if not referer or referer == "-":
        return None
    try:
        p = urlparse(referer)
        host = (p.netloc or "").split(":")[0].lower()
        if not host:
            return None
        # Auto-referer (l'utente naviga internamente)
        if "cruscotto-italia" in host or "piersoftckan.biz" in host:
            return None
        return host
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ISTAT → nome comune (cache leggero da R2 o file locale)
# ---------------------------------------------------------------------------

# Nelle stats finali e' utile vedere "Lecce (075035)" non solo il codice.
# La risoluzione e' opzionale: se manca il file mapping, restano i codici.
def load_istat_names(mapping_path: Path | None) -> dict[str, str]:
    if not mapping_path or not mapping_path.exists():
        return {}
    try:
        return json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Aggregazione
# ---------------------------------------------------------------------------

def aggregate(log_paths: list[Path], exclude_test: bool = False,
              istat_names: dict[str, str] | None = None) -> dict:
    """Aggrega N log file (anche gz) in una struttura unica."""
    istat_names = istat_names or {}

    stats = {
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_logs_processed": [str(p) for p in log_paths],
        "_exclude_test_comuni": exclude_test,
        "totals": {
            "lines_total": 0,
            "lines_parsed": 0,
            "hits_bot": 0,
            "hits_attack": 0,
            "hits_internal_asset": 0,
            "hits_error": 0,           # status >= 400
            "hits_human": 0,           # hits "veri"
        },
        "per_day": defaultdict(lambda: {
            "hits": 0,
            "unique_ips": set(),
        }),
        "top_comuni": Counter(),       # istat → conteggio
        "top_referer_domains": Counter(),
        "top_pages": Counter(),        # path senza querystring
        "status_distribution": Counter(),
        "method_distribution": Counter(),
    }

    for log_path in log_paths:
        if not log_path.exists():
            print(f"  ⚠ {log_path} non trovato, skip", file=sys.stderr)
            continue
        with open_log(log_path) as f:
            for line in f:
                stats["totals"]["lines_total"] += 1
                ev = parse_line(line)
                if not ev:
                    continue
                stats["totals"]["lines_parsed"] += 1

                # Status distribution sempre (anche per errori)
                stats["status_distribution"][ev["status"]] += 1
                stats["method_distribution"][ev["method"]] += 1

                # Filtri di esclusione
                if ev["status"] >= 400:
                    stats["totals"]["hits_error"] += 1
                    continue
                if is_attack(ev["uri"]):
                    stats["totals"]["hits_attack"] += 1
                    continue
                if is_bot(ev["ua"]):
                    stats["totals"]["hits_bot"] += 1
                    continue
                if is_internal_asset(ev["uri"]):
                    stats["totals"]["hits_internal_asset"] += 1
                    continue

                # Hit "valido" — utente reale
                stats["totals"]["hits_human"] += 1

                # Parse data (es. "14/May/2026:00:19:05 +0000" → "2026-05-14")
                try:
                    dt = datetime.strptime(ev["time"], "%d/%b/%Y:%H:%M:%S %z")
                    day_key = dt.strftime("%Y-%m-%d")
                except ValueError:
                    day_key = "unknown"
                stats["per_day"][day_key]["hits"] += 1
                # Hash IP per privacy (8 char dell'hash)
                # Non lo persisto, lo uso solo per il count dei distinti
                stats["per_day"][day_key]["unique_ips"].add(ev["ip"])

                # Top comuni (se rotta /comune.html?istat=...)
                istat = extract_istat(unquote(ev["uri"]))
                if istat:
                    if exclude_test and istat in TEST_COMUNI:
                        pass
                    else:
                        stats["top_comuni"][istat] += 1

                # Top pages (senza querystring)
                page = ev["uri"].split("?")[0]
                stats["top_pages"][page] += 1

                # Top referer
                ref = extract_referer_domain(ev["referer"])
                if ref:
                    stats["top_referer_domains"][ref] += 1

    # Conversione per JSON output (Counter → list, set → int, defaultdict → dict)
    out = {
        "_generated_at": stats["_generated_at"],
        "_logs_processed": stats["_logs_processed"],
        "_exclude_test_comuni": stats["_exclude_test_comuni"],
        "totals": stats["totals"],
        "per_day": {
            day: {"hits": v["hits"], "unique_ips": len(v["unique_ips"])}
            for day, v in sorted(stats["per_day"].items())
        },
        "top_comuni": [
            {"istat": istat, "nome": istat_names.get(istat, istat), "hits": n}
            for istat, n in stats["top_comuni"].most_common(30)
        ],
        "top_referer_domains": [
            {"domain": d, "hits": n}
            for d, n in stats["top_referer_domains"].most_common(20)
        ],
        "top_pages": [
            {"page": p, "hits": n}
            for p, n in stats["top_pages"].most_common(20)
        ],
        "status_distribution": dict(stats["status_distribution"]),
        "method_distribution": dict(stats["method_distribution"]),
    }
    return out


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Cruscotto Italia — Statistiche di accesso</title>
<meta name="robots" content="noindex,nofollow">
<style>
  :root {{
    --ink: #17324d;
    --mute: #455A72;
    --bg: #fff;
    --bg-soft: #F5F6F7;
    --border: #E3E7EB;
    --blu-italia: #0066CC;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    color: var(--ink);
    background: var(--bg);
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1.5rem;
    line-height: 1.5;
  }}
  h1 {{ font-size: 2rem; margin: 0 0 0.5rem; }}
  h2 {{ font-size: 1.25rem; margin: 2rem 0 0.75rem; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}
  .meta {{ color: var(--mute); font-size: 0.875rem; margin-bottom: 2rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 1rem; margin: 1rem 0; }}
  .card {{ background: var(--bg-soft); padding: 1rem; border-left: 3px solid var(--blu-italia); }}
  .card-val {{ font-size: 2rem; font-weight: 600; color: var(--ink); }}
  .card-lbl {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
               color: var(--mute); margin-top: 0.25rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0 1rem; font-size: 0.9rem; }}
  th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: var(--bg-soft); font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--blu-italia); font-weight: 600; }}
  .footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border);
             color: var(--mute); font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>Cruscotto Italia — Statistiche</h1>
<p class="meta">
  Generato: {generated_at}<br>
  Periodo: {days_range}{exclude_note}
</p>

<div class="grid">
  <div class="card"><div class="card-val">{hits_human}</div><div class="card-lbl">Visite umane</div></div>
  <div class="card"><div class="card-val">{hits_bot}</div><div class="card-lbl">Bot/crawler</div></div>
  <div class="card"><div class="card-val">{hits_attack}</div><div class="card-lbl">Tentativi attacco</div></div>
  <div class="card"><div class="card-val">{hits_error}</div><div class="card-lbl">Errori 4xx/5xx</div></div>
  <div class="card"><div class="card-val">{unique_comuni}</div><div class="card-lbl">Comuni distinti visti</div></div>
</div>

<h2>Comuni più consultati</h2>
{table_comuni}

<h2>Visite per giorno</h2>
{table_days}

<h2>Pagine più viste</h2>
{table_pages}

<h2>Referer esterni</h2>
{table_referers}

{mcp_section}

<div class="footer">
  Conformità privacy: aggregati anonimi senza IP né user agent grezzi.
  Log raw nginx ruotati a 7 giorni come da policy
  <a href="https://www.dati.gov.it/policy">dati.gov.it</a>.
</div>
</body>
</html>
"""


def render_table(rows: list[tuple[str, int]], headers: tuple[str, str]) -> str:
    if not rows:
        return f"<p class=\"meta\">Nessun dato.</p>"
    body = "\n".join(
        f"  <tr><td>{label}</td><td class=\"num\">{n:,}</td></tr>"
        for label, n in rows
    )
    return (f"<table>\n  <thead><tr><th>{headers[0]}</th>"
            f"<th style=\"text-align:right\">{headers[1]}</th></tr></thead>\n"
            f"  <tbody>\n{body}\n  </tbody>\n</table>")


def render_mcp_section(mcp_stats_path: Path | None) -> str:
    """
    Costruisce la sezione HTML per le statistiche MCP, leggendo mcp_stats.json
    se esiste. Ritorna stringa vuota se il file non esiste o è vuoto.
    """
    if not mcp_stats_path or not mcp_stats_path.exists():
        return ""
    try:
        mcp = json.loads(mcp_stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    total = mcp.get("totals", {}).get("calls", 0)
    if total == 0:
        return (
            "<h2>Utilizzo MCP</h2>\n"
            "<p class=\"meta\">Nessuna tool call registrata negli ultimi "
            f"{mcp.get('_days_window', 30)} giorni.</p>"
        )

    table_tools = render_table(
        [(t["tool"], t["calls"]) for t in mcp.get("by_tool", [])],
        ("Tool MCP", "Chiamate"),
    )
    table_mcp_comuni = render_table(
        [(f"{c['nome']} ({c['istat']})", c["calls"])
         for c in mcp.get("by_comune", [])],
        ("Comune", "Chiamate"),
    ) if mcp.get("by_comune") else "<p class=\"meta\">Nessun comune referenziato (tool senza ISTAT).</p>"
    table_clients = render_table(
        [(c["client"], c["calls"]) for c in mcp.get("by_client", [])],
        ("Client", "Chiamate"),
    )

    days = mcp.get("by_day", [])
    days_range = f"{days[0]['day']} → {days[-1]['day']}" if days else "n/d"

    cards = (
        f'<div class="grid">\n'
        f'  <div class="card"><div class="card-val">{total:,}</div>'
        f'<div class="card-lbl">Tool call MCP totali</div></div>\n'
        f'  <div class="card"><div class="card-val">'
        f'{mcp["totals"]["distinct_tools"]}</div>'
        f'<div class="card-lbl">Tool distinti</div></div>\n'
        f'  <div class="card"><div class="card-val">'
        f'{mcp["totals"]["distinct_comuni"]}</div>'
        f'<div class="card-lbl">Comuni interrogati</div></div>\n'
        f'  <div class="card"><div class="card-val">'
        f'{mcp["totals"]["distinct_clients"]}</div>'
        f'<div class="card-lbl">Tipi di client</div></div>\n'
        f'</div>'
    )

    # Heatmap giornaliera per tool (giorno × tool)
    heatmap_html = _render_heatmap(mcp.get("by_day_tool", []))

    # Errori
    errors = mcp.get("errors", {})
    err_total = errors.get("total", 0)
    if err_total > 0:
        err_pct = err_total / total * 100 if total else 0
        errors_html = (
            f"<h2>Errori tool MCP</h2>\n"
            f"<p class=\"meta\">{err_total:,} errori su {total:,} chiamate "
            f"({err_pct:.1f}%) negli ultimi {mcp.get('_days_window', 30)} giorni.</p>\n"
            + render_table(
                [(t["tool"], t["errors"]) for t in errors.get("by_tool", [])],
                ("Tool", "Errori"),
            )
        )
    else:
        errors_html = (
            "<h2>Errori tool MCP</h2>\n"
            f"<p class=\"meta\">Nessun errore registrato negli ultimi "
            f"{mcp.get('_days_window', 30)} giorni.</p>"
        )

    # Termini di ricerca (search_comune)
    terms = mcp.get("search_terms", {})
    if terms.get("total", 0) > 0:
        terms_html = (
            f"<h2>Termini più cercati</h2>\n"
            f"<p class=\"meta\">{terms['total']:,} ricerche, "
            f"{terms['distinct_terms']:,} termini distinti.</p>\n"
            + render_table(
                [(t["term"], t["calls"]) for t in terms.get("top_terms", [])[:20]],
                ("Termine", "Ricerche"),
            )
        )
    else:
        terms_html = ""

    return (
        f"<h2>Utilizzo MCP — ultimi {mcp.get('_days_window', 30)} giorni</h2>\n"
        f"<p class=\"meta\">Periodo dati MCP: {days_range}</p>\n"
        f"{cards}\n\n"
        f"<h2>Tool MCP più chiamati</h2>\n"
        f"{table_tools}\n\n"
        f"{heatmap_html}\n\n"
        f"<h2>Comuni più consultati via MCP</h2>\n"
        f"{table_mcp_comuni}\n\n"
        f"<h2>Client che usano l'MCP</h2>\n"
        f"{table_clients}\n\n"
        f"{terms_html}\n\n"
        f"{errors_html}"
    )


def _render_heatmap(by_day_tool: list[dict]) -> str:
    """
    Renderizza heatmap giorno × tool come tabella HTML con celle colorate
    secondo l'intensità (steps di sfumatura del blu).
    """
    if not by_day_tool:
        return ""

    # Raccolgo giorni e tool unici, ordinati
    days = sorted({r["day"] for r in by_day_tool})
    tools = sorted({r["tool"] for r in by_day_tool})

    # Matrice di lookup
    grid: dict[tuple[str, str], int] = {(r["day"], r["tool"]): r["calls"] for r in by_day_tool}

    # Trovo il max per scalare l'intensità
    max_val = max((r["calls"] for r in by_day_tool), default=1)

    def cell_color(n: int) -> str:
        if n == 0:
            return "background:#F5F6F7"
        # Intensità: 5 livelli di blu
        ratio = n / max_val
        if ratio > 0.8:
            return "background:#0066CC;color:#fff"
        if ratio > 0.6:
            return "background:#3385D6"
        if ratio > 0.4:
            return "background:#66A3E0"
        if ratio > 0.2:
            return "background:#99C2EB"
        return "background:#CCE0F5"

    # Header con i tool
    header = "  <tr><th>Giorno</th>" + "".join(
        f'<th style="text-align:center">{t}</th>' for t in tools
    ) + "</tr>"

    # Righe
    rows = []
    for day in days:
        cells = []
        for tool in tools:
            n = grid.get((day, tool), 0)
            style = cell_color(n)
            cells.append(
                f'<td style="text-align:center;font-variant-numeric:tabular-nums;{style}">'
                f'{n if n else "·"}</td>'
            )
        rows.append(f"  <tr><td>{day}</td>{''.join(cells)}</tr>")

    table_html = (
        f"<table style=\"font-size:0.85rem\">\n"
        f"  <thead>{header}</thead>\n"
        f"  <tbody>\n{chr(10).join(rows)}\n  </tbody>\n"
        f"</table>"
    )

    return (
        f"<h2>Tool calls per giorno</h2>\n"
        f"<p class=\"meta\">Heatmap: intensità del colore proporzionale "
        f"al volume (max {max_val:,} chiamate/giorno/tool).</p>\n"
        f"{table_html}"
    )


def render_html(stats: dict, mcp_stats_path: Path | None = None) -> str:
    days = list(stats["per_day"].keys())
    days_range = f"{days[0]} → {days[-1]}" if days else "n/d"
    exclude_note = (
        "<br>\n  Sono esclusi i comuni di test Roma (058091), Lecce (075035), "
        "Matera (077014), Morterone (097055)."
        if stats["_exclude_test_comuni"]
        else ""
    )
    table_comuni = render_table(
        [(f"{c['nome']} ({c['istat']})", c["hits"]) for c in stats["top_comuni"]],
        ("Comune", "Visite"),
    )
    table_days = render_table(
        [(d, v["hits"]) for d, v in stats["per_day"].items()],
        ("Giorno", "Visite umane"),
    )
    table_pages = render_table(
        [(p["page"], p["hits"]) for p in stats["top_pages"]],
        ("Pagina", "Visite"),
    )
    table_referers = render_table(
        [(r["domain"], r["hits"]) for r in stats["top_referer_domains"]],
        ("Dominio referer", "Visite"),
    ) if stats["top_referer_domains"] else "<p class=\"meta\">Nessun referer esterno.</p>"

    return HTML_TEMPLATE.format(
        generated_at=stats["_generated_at"],
        days_range=days_range,
        exclude_note=exclude_note,
        hits_human=f"{stats['totals']['hits_human']:,}",
        hits_bot=f"{stats['totals']['hits_bot']:,}",
        hits_attack=f"{stats['totals']['hits_attack']:,}",
        hits_error=f"{stats['totals']['hits_error']:,}",
        unique_comuni=len(stats["top_comuni"]),
        table_comuni=table_comuni,
        table_days=table_days,
        table_pages=table_pages,
        table_referers=table_referers,
        mcp_section=render_mcp_section(mcp_stats_path),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Uno o più log file nginx (anche .gz)")
    ap.add_argument("--out", required=True,
                    help="Directory output (deve esistere, scrive index.html + stats.json)")
    ap.add_argument("--exclude-test-comuni", action="store_true",
                    help="Escludi Roma/Lecce/Matera/Morterone dalle stats")
    ap.add_argument("--istat-names",
                    help="JSON mapping {istat: nome} per labelare i comuni (opzionale)")
    ap.add_argument("--mcp-stats",
                    help="JSON di analytics MCP (output di mcp_stats_fetcher.py)")
    args = ap.parse_args()

    log_paths = [Path(p) for p in args.logs]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    istat_names = load_istat_names(Path(args.istat_names) if args.istat_names else None)

    print(f"→ Processo {len(log_paths)} log file...")
    stats = aggregate(log_paths, exclude_test=args.exclude_test_comuni,
                      istat_names=istat_names)

    json_out = out_dir / "stats.json"
    html_out = out_dir / "index.html"

    json_out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    mcp_stats_path = Path(args.mcp_stats) if args.mcp_stats else None
    html_out.write_text(render_html(stats, mcp_stats_path), encoding="utf-8")

    print(f"✓ Linee processate: {stats['totals']['lines_parsed']:,} / {stats['totals']['lines_total']:,}")
    print(f"✓ Visite umane: {stats['totals']['hits_human']:,}")
    print(f"✓ Bot: {stats['totals']['hits_bot']:,}, Attacchi: {stats['totals']['hits_attack']:,}, Errori: {stats['totals']['hits_error']:,}")
    print(f"✓ Comuni distinti: {len(stats['top_comuni'])}")
    print(f"✓ JSON: {json_out}")
    print(f"✓ HTML: {html_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
