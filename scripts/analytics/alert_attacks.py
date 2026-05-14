#!/usr/bin/env python3
"""
Alert Telegram quando il volume di attacchi cresce oltre una soglia.

Confronta il counter hits_attack di stats.json con la lettura precedente
(stato persistito in /var/lib/cruscotto-alerts/state.json). Se la delta
oraria supera la soglia configurata, invia notifica Telegram con dettagli
investigativi (top IP, top path).

Variabili d'ambiente richieste:
  TELEGRAM_BOT_TOKEN    Token bot @BotFather
  TELEGRAM_CHAT_ID      Chat ID destinatario (numero, può essere negativo)

Variabili opzionali:
  ALERT_THRESHOLD       Soglia minima delta oraria (default: 50)
  ALERT_STATE_DIR       Dove persistere state.json (default: /var/lib/cruscotto-alerts)

Uso:
  python3 alert_attacks.py --stats /var/www/cruscotto-stats/stats.json \\
                           --log /var/log/nginx/access.log

Idempotente: se delta sotto soglia o se già notificato negli ultimi N min, esce
senza inviare (cron-safe).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Riusa la logica del parser
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats_aggregator import is_attack, parse_line  # noqa: E402


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def telegram_send(token: str, chat_id: str, text: str) -> bool:
    """Invia messaggio Telegram. Ritorna True se OK, False su errore."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = Request(url, data=data, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        print(f"✗ Telegram error: {e}", file=sys.stderr)
        return False


def collect_attack_details(log_path: Path, max_hours: int = 1,
                            top_n: int = 5) -> dict:
    """
    Scorre il log nginx delle ultime max_hours ore e raccoglie:
    - Top IP attaccanti
    - Top path attaccati
    - Conteggio totale attacchi nel window
    """
    if not log_path.exists():
        return {"top_ips": [], "top_paths": [], "total": 0}

    cutoff_ts = time.time() - (max_hours * 3600)
    by_ip: Counter = Counter()
    by_path: Counter = Counter()
    total = 0

    try:
        with open(log_path, "rb") as f:
            # Tail veloce: ultimi ~5 MB del log
            size = os.fstat(f.fileno()).st_size
            if size > 5 * 1024 * 1024:
                f.seek(-5 * 1024 * 1024, 2)
                f.readline()  # skip partial line
            # else: leggo dall'inizio (log piccolo, tipicamente post-logrotate)
            for raw in f:
                try:
                    line = raw.decode("utf-8", errors="ignore")
                except Exception:
                    continue
                ev = parse_line(line)
                if not ev:
                    continue
                if not is_attack(ev["uri"]):
                    continue
                # parse timestamp (formato nginx: 14/May/2026:11:57:12 +0000)
                try:
                    # ev["time"] è già senza quadre, es: "14/May/2026:11:57:12 +0000"
                    # struct_time non gestisce timezone, prendo solo i primi 20 char
                    t = time.strptime(ev["time"][:20], "%d/%b/%Y:%H:%M:%S")
                    ts = time.mktime(t)  # locale; approssimazione accettabile per cutoff 1h
                except Exception:
                    continue
                if ts < cutoff_ts:
                    continue
                total += 1
                by_ip[ev["ip"]] += 1
                by_path[ev["uri"]] += 1
    except OSError:
        return {"top_ips": [], "top_paths": [], "total": 0}

    return {
        "total": total,
        "top_ips": [(ip, n) for ip, n in by_ip.most_common(top_n)],
        "top_paths": [(p, n) for p, n in by_path.most_common(top_n)],
    }


def format_alert(delta: int, threshold: int, current: int,
                 details: dict, hours: int) -> str:
    """Formatta messaggio Telegram in HTML."""
    lines = [
        f"🚨 <b>Cruscotto Italia — Picco attacchi</b>",
        f"",
        f"Delta {hours}h: <b>+{delta}</b> attacchi (soglia: {threshold})",
        f"Totale oggi: {current}",
        f"",
    ]
    if details["top_ips"]:
        lines.append(f"<b>Top IP attaccanti (ultime {hours}h):</b>")
        for ip, n in details["top_ips"]:
            lines.append(f"  • <code>{ip}</code> — {n} hit")
        lines.append("")
    if details["top_paths"]:
        lines.append(f"<b>Top path tentati:</b>")
        for p, n in details["top_paths"]:
            # Telegram HTML: scape minimo
            safe_p = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if len(safe_p) > 60:
                safe_p = safe_p[:60] + "…"
            lines.append(f"  • <code>{safe_p}</code> — {n}")
        lines.append("")
    lines.append(f"🔗 https://cruscotto-italia.piersoftckan.biz/stats/")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats", type=Path,
                    default=Path("/var/www/cruscotto-stats/stats.json"),
                    help="Path al stats.json prodotto dall'aggregator")
    ap.add_argument("--log", type=Path,
                    default=Path("/var/log/nginx/access.log"),
                    help="Log nginx da cui estrarre dettagli attacchi")
    ap.add_argument("--threshold", type=int,
                    default=int(os.environ.get("ALERT_THRESHOLD", "50")),
                    help="Soglia minima delta per inviare alert (default: 50)")
    ap.add_argument("--cooldown-min", type=int, default=55,
                    help="Minuti minimi tra alert consecutivi (default: 55)")
    ap.add_argument("--state-dir", type=Path,
                    default=Path(os.environ.get("ALERT_STATE_DIR",
                                                "/var/lib/cruscotto-alerts")),
                    help="Dir per state.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="Non invia Telegram, stampa solo cosa farebbe")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run:
        if not token or not chat_id:
            print("✗ TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID mancanti", file=sys.stderr)
            return 2

    if not args.stats.exists():
        print(f"✗ Stats file non trovato: {args.stats}", file=sys.stderr)
        return 1

    try:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"✗ Stats parse error: {e}", file=sys.stderr)
        return 1

    current = stats.get("totals", {}).get("hits_attack", 0)
    state_path = args.state_dir / "state.json"
    state = load_state(state_path)
    prev = state.get("last_hits_attack", 0)
    last_alert_ts = state.get("last_alert_ts", 0)

    delta = current - prev

    # Reset gestione (es. logrotate ha reso current < prev)
    if current < prev:
        print(f"→ Reset detected (current={current} < prev={prev}). "
              f"Aggiorno baseline senza alert.")
        state["last_hits_attack"] = current
        save_state(state_path, state)
        return 0

    print(f"→ hits_attack: prev={prev} current={current} delta={delta} "
          f"threshold={args.threshold}")

    if delta < args.threshold:
        print(f"✓ Sotto soglia, nessun alert.")
        state["last_hits_attack"] = current
        save_state(state_path, state)
        return 0

    # Cooldown: se ho già notificato di recente, skip
    now = time.time()
    elapsed_min = (now - last_alert_ts) / 60
    if elapsed_min < args.cooldown_min:
        print(f"⏸  Cooldown attivo ({elapsed_min:.0f}/{args.cooldown_min} min). "
              f"Aggiorno baseline senza alert.")
        state["last_hits_attack"] = current
        save_state(state_path, state)
        return 0

    # Sopra soglia + cooldown OK: raccolgo dettagli e invio
    # Finestra dettagli: 6h (più larga della delta-soglia oraria, così
    # cattura comunque IP/path anche se gli attacchi sono spalmati).
    print(f"→ Estraggo dettagli dal log {args.log} (ultime 6h)...")
    details = collect_attack_details(args.log, max_hours=6)
    msg = format_alert(delta, args.threshold, current, details, hours=6)

    print("\n" + msg + "\n")

    if args.dry_run:
        print("[DRY-RUN] Telegram NON inviato.")
        return 0

    if telegram_send(token, chat_id, msg):
        print("✓ Telegram inviato.")
        state["last_hits_attack"] = current
        state["last_alert_ts"] = now
        save_state(state_path, state)
        return 0
    else:
        print("✗ Telegram fallito, baseline NON aggiornata.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
