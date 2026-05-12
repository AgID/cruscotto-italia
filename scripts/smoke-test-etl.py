#!/usr/bin/env python3
"""smoke-test-etl.py — Smoke test dei 18 ETL di Cruscotto Italia.

Lancia ogni ETL con scope minimo (limit/regioni/anni) per verificare in pochi
minuti che parsino correttamente, scrivano shard ben formati su disco, e non
crashino. NESSUN upload R2 (--target=local su tutti).

Output:
- /tmp/cruscotto-smoke/<source>/   shard JSON prodotti
- /tmp/cruscotto-smoke/_logs/<source>.log  stdout+stderr completo
- /tmp/cruscotto-smoke/REPORT.md   tabella riassuntiva
- /tmp/cruscotto-smoke/results.json  risultati machine-readable

Uso:
    cd /home/ubuntu/cruscotto-italia
    source .venv/bin/activate     # attivo il venv
    python3 smoke-test-etl.py     # tutti gli ETL
    python3 smoke-test-etl.py aria scuole         # solo alcuni
    python3 smoke-test-etl.py --tier fast         # solo tier veloce
    python3 smoke-test-etl.py --dry               # mostra piano senza eseguire
    python3 smoke-test-etl.py --clean             # cancella output precedente
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

BASE_OUT = Path("/tmp/cruscotto-smoke")
LOG_DIR = BASE_OUT / "_logs"

# Piano ETL: name, tier, args_extra, timeout_s
# --target=local e --outdir/--output-dir sono aggiunti automaticamente.
ETL_PLAN = [
    # --------- FAST ---------
    ("anagrafica",     "fast",       [],                                                 60),
    ("bdap_mop",       "fast",       [],                                                 60),
    ("bdap_siope",     "fast",       ["--year", "2026"],                                 90),

    # --------- MEDIUM ---------
    ("demografia",     "medium",     [],                                                300),
    ("istat_profilo",  "medium",     [],                                                300),
    ("istat_turismo",  "medium",     [],                                                300),
    ("territorio",     "medium",     ["--skip-idrogeo", "--skip-rifiuti"],              300),
    ("scuole",         "medium",     [],                                                360),
    ("pnrr_progetti",  "medium",     [],                                                360),
    ("anac",           "medium",     ["--months", "202604"],                            360),
    ("immobili_pa",    "medium",     ["--regione", "VAL"],                              360),
    ("siope",          "medium",     ["--regioni", "02", "--anni", "2026"],             420),
    ("anncsu",         "medium",     ["--regioni", "02"],                               420),

    # --------- SLOW ---------
    ("bdap",           "slow",       [],                                                900),
    ("aria",           "slow",       [],                                                900),
    ("veicoli",        "slow",       [],                                                900),
    ("redditi",        "slow",       ["--anni", "2022", "--limit", "100"],              600),

    # --------- AGGREGATOR (legge da R2 in lettura) ---------
    ("dashboard",      "aggregator", ["--limit", "50"],                                 420),
]

# Alcuni ETL usano --outdir invece di --output-dir
OUTDIR_FLAG = {
    "anncsu":      "--outdir",
    "immobili_pa": "--outdir",
    "redditi":     "--outdir",
    "scuole":      "--outdir",
}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

C = {
    "reset": "\033[0m",
    "bold":  "\033[1m",
    "dim":   "\033[2m",
    "green": "\033[32m",
    "red":   "\033[31m",
    "yell":  "\033[33m",
    "cyan":  "\033[36m",
}


def log(msg: str = ""):
    print(msg, flush=True)


def fmt_time(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m, r = divmod(s, 60)
    return f"{int(m)}m{int(r):02d}s"


def run_one(name: str, tier: str, extra_args: list, timeout_s: int) -> dict:
    """Esegue un singolo ETL con timeout. Ritorna dict risultati."""
    out_dir = BASE_OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"

    outdir_flag = OUTDIR_FLAG.get(name, "--output-dir")
    cmd = [
        sys.executable, "-m", f"etl.sources.{name}",
        "--target", "local",
        outdir_flag, str(out_dir),
    ] + extra_args

    log(f"{C['cyan']}[{tier:10s}]{C['reset']} {C['bold']}{name:18s}{C['reset']} "
        f"timeout={timeout_s}s  args: {' '.join(cmd[3:])}")

    t0 = time.time()
    status = "?"
    exit_code = None
    err_excerpt = ""

    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                timeout=timeout_s, cwd=Path.cwd(),
            )
        exit_code = proc.returncode
        if exit_code == 0:
            status = "OK"
        else:
            status = "FAIL"
            try:
                tail = log_path.read_text().splitlines()[-5:]
                err_excerpt = " | ".join(l.strip()[:90] for l in tail if l.strip())
            except Exception:
                pass
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
        exit_code = -1
    except Exception as e:
        status = "ERROR"
        err_excerpt = str(e)[:120]

    elapsed = time.time() - t0

    # Conta file prodotti
    try:
        files = list(out_dir.rglob("*.json"))
        n_files = len(files)
        total_bytes = sum(f.stat().st_size for f in files)
    except Exception:
        n_files, total_bytes = 0, 0

    # Sample
    sample = None
    try:
        first = next(iter(out_dir.rglob("*.json")), None)
        if first:
            sample = first.name
    except Exception:
        pass

    # Print outcome
    col = {"OK": C["green"], "TIMEOUT": C["yell"]}.get(status, C["red"])
    log(f"   {col}{status:8s}{C['reset']} elapsed={fmt_time(elapsed)}  "
        f"files={n_files}  size={total_bytes/1024:.0f}KB  sample={sample}")
    if err_excerpt:
        log(f"   {C['dim']}tail: {err_excerpt[:200]}{C['reset']}")

    return {
        "name": name,
        "tier": tier,
        "status": status,
        "exit_code": exit_code,
        "elapsed_s": round(elapsed, 1),
        "n_files": n_files,
        "total_kb": round(total_bytes / 1024, 1),
        "sample_file": sample,
        "err_excerpt": err_excerpt,
        "log_path": str(log_path),
        "cmd": cmd,
    }


def write_report(results: list) -> Path:
    """Scrive REPORT.md con tabella + dettagli fail."""
    report = BASE_OUT / "REPORT.md"
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_timeout = sum(1 for r in results if r["status"] == "TIMEOUT")
    n_err = sum(1 for r in results if r["status"] == "ERROR")
    total_time = sum(r["elapsed_s"] for r in results)

    with open(report, "w") as f:
        f.write("# Smoke test ETL Cruscotto Italia\n\n")
        f.write(f"- Eseguiti: **{len(results)}**\n")
        f.write(f"- OK: **{n_ok}** | FAIL: **{n_fail}** | TIMEOUT: **{n_timeout}** | ERROR: **{n_err}**\n")
        f.write(f"- Tempo totale: **{fmt_time(total_time)}**\n")
        f.write(f"- Output: `{BASE_OUT}`\n")
        f.write(f"- Log: `{LOG_DIR}`\n\n")

        f.write("| ETL | Tier | Status | Time | Files | Size | Sample |\n")
        f.write("|---|---|---|---:|---:|---:|---|\n")
        for r in results:
            f.write(f"| `{r['name']}` | {r['tier']} | **{r['status']}** | "
                    f"{fmt_time(r['elapsed_s'])} | {r['n_files']} | "
                    f"{r['total_kb']:.0f}KB | `{r['sample_file'] or '-'}` |\n")

        fails = [r for r in results if r["status"] != "OK"]
        if fails:
            f.write("\n## Fail / Timeout details\n\n")
            for r in fails:
                f.write(f"### `{r['name']}` — {r['status']}\n\n")
                f.write(f"- exit_code: `{r['exit_code']}`\n")
                f.write(f"- elapsed: `{fmt_time(r['elapsed_s'])}`\n")
                f.write(f"- log: `{r['log_path']}`\n")
                f.write(f"- cmd: `{' '.join(r['cmd'])}`\n")
                if r["err_excerpt"]:
                    f.write(f"- tail: `{r['err_excerpt']}`\n")
                f.write("\n")

    return report


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Smoke test ETL Cruscotto Italia")
    p.add_argument("filter", nargs="*",
                   help="Solo questi ETL (es. 'aria scuole'). Vuoto = tutti.")
    p.add_argument("--tier", choices=["fast", "medium", "slow", "aggregator"],
                   help="Solo questo tier")
    p.add_argument("--clean", action="store_true",
                   help="Cancella /tmp/cruscotto-smoke/ prima di partire")
    p.add_argument("--dry", action="store_true",
                   help="Stampa il piano senza eseguire")
    args = p.parse_args()

    # Filtri
    plan = ETL_PLAN
    if args.filter:
        plan = [x for x in plan if x[0] in args.filter]
    if args.tier:
        plan = [x for x in plan if x[1] == args.tier]
    if not plan:
        log(f"{C['red']}Nessun ETL selezionato dai filtri{C['reset']}")
        sys.exit(2)

    # Pre-check: nella repo giusta?
    if not (Path.cwd() / "etl" / "sources").is_dir():
        log(f"{C['red']}ERRORE: lancia questo script dalla root della repo{C['reset']}")
        log(f"    cd /home/ubuntu/cruscotto-italia && python3 smoke-test-etl.py")
        sys.exit(2)

    # Setup output dir
    if args.clean and BASE_OUT.exists():
        log(f"{C['yell']}Pulisco {BASE_OUT} ...{C['reset']}")
        shutil.rmtree(BASE_OUT)
    BASE_OUT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Avviso credenziali R2
    r2_keys = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    missing = [k for k in r2_keys if not os.environ.get(k)]
    if missing:
        log(f"{C['yell']}!! Credenziali R2 mancanti in env: {missing}{C['reset']}")
        log(f"   Gli ETL che leggono cache raw da R2 (bdap, redditi, siope, anac,"
            f" anncsu, immobili_pa) e dashboard probabilmente falliranno.")
        log(f"   Tutti gli ETL scrivono comunque solo su disco (target=local).")
        log()

    # Piano
    total_timeout = sum(x[3] for x in plan)
    log(f"{C['bold']}Piano: {len(plan)} ETL, "
        f"timeout cumulativo massimo {fmt_time(total_timeout)}{C['reset']}")
    if args.dry:
        for name, tier, extra, t in plan:
            log(f"  [{tier:10s}] {name:18s} timeout={t}s extra={extra}")
        return

    log()
    results = []
    t_start = time.time()
    try:
        for name, tier, extra, timeout_s in plan:
            r = run_one(name, tier, extra, timeout_s)
            results.append(r)
            # Salvataggio incrementale
            json.dump(results, open(BASE_OUT / "results.json", "w"), indent=2)
    except KeyboardInterrupt:
        log(f"\n{C['yell']}Interrotto dall'utente. Scrivo report parziale.{C['reset']}")

    total_elapsed = time.time() - t_start
    log()
    log(f"{C['bold']}=== FINE ==={C['reset']}")
    log(f"Tempo totale: {fmt_time(total_elapsed)}")

    report_path = write_report(results)
    log(f"Report:   {report_path}")
    log(f"Output:   {BASE_OUT}/<source>/")
    log(f"Log:      {LOG_DIR}/")

    fails = sum(1 for r in results if r["status"] != "OK")
    if fails:
        log(f"\n{C['red']}{fails}/{len(results)} ETL non OK. Vedi REPORT.md{C['reset']}")
    else:
        log(f"\n{C['green']}Tutti gli ETL OK!{C['reset']}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
