"""ETL ISTAT - Matrice pendolarismo per lavoro 2021.

Fonte: ISTAT esploradati.istat.it (file ZIP download diretto, NON SDMX API).
Licenza: CC BY 3.0 IT (standard ISTAT).
URL: https://esploradati.istat.it/databrowser/DWL/PERMPOP/MATPEN/matrix_pendoLAVORO_2021.zip
Edizione: Censimento permanente 2021 (pubbl. 2/10/2025, nota metodologica 3/11/2025).

NOTA: ISTAT 2021 distribuisce SOLO matrice "per lavoro" (no studio).

Strategia: 1 sola chiamata HTTP, no chunking, no SDMX, no rate-limit.
L'IP Aruba e' bannato da esploradati.istat.it (ban host-based confermato
2026-05-16). Esecuzione obbligatoria via GitHub-hosted runner.

Output: data/pendolarismo/<istat>.json (1 file per comune, ~5-10 KB cad).
Totale 7896 file × ~7 KB = ~55 MB raw, ~10 MB nel repo git (pack-compresso).

Schema shard:
{
  "_etl_version": "0.1.0",
  "_source": "ISTAT - Matrice pendolarismo per lavoro 2021",
  "_source_url": "https://esploradati.istat.it/databrowser/...",
  "_license": "CC BY 3.0 IT",
  "_anno_rilevazione": 2021,
  "_motivo_spostamento": "lavoro",
  "_generated_at": "ISO-8601",
  "kpi": {
    "uscenti_totali": int,            // residenti che lavorano fuori comune
    "entranti_totali": int,           // lavoratori provenienti da altri comuni
    "saldo_netto": int,               // entranti - uscenti
    "auto_contenimento_pct": float,   // % residenti che lavorano nel comune
    "n_destinazioni": int,            // comuni diversi raggiunti dagli uscenti
    "n_origini": int,                 // comuni diversi di provenienza entranti
    "top_destinazioni": [             // top 10 destinazioni per # pendolari
      {"istat": "075052", "count": 1782}, ...
    ],
    "top_origini": [                  // top 10 origini per # pendolari
      {"istat": "075008", "count": 1690}, ...
    ]
  }
}
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import structlog

from etl.lib import manifest

log = structlog.get_logger()

# ═════════════════════════════════════════════════════════════════════════
# Costanti
# ═════════════════════════════════════════════════════════════════════════

SOURCE_URL = (
    "https://esploradati.istat.it/databrowser/DWL/PERMPOP/MATPEN/"
    "matrix_pendoLAVORO_2021.zip"
)
SOURCE_LABEL = "ISTAT - Matrice pendolarismo per lavoro 2021"
LICENSE = "CC BY 3.0 IT"
ANNO = 2021
MOTIVO = "lavoro"
ETL_VERSION = "0.1.0"

UA = "CruscottoItalia-ETL/1.0 (+https://cruscotto-italia.dati.gov.it)"

CACHE_DIR = Path("/tmp/cruscotto_pendolarismo")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TOP_N = 10  # numero di destinazioni/origini più frequenti nello shard


# ═════════════════════════════════════════════════════════════════════════
# FASE 1 — Download ZIP
# ═════════════════════════════════════════════════════════════════════════

def download_zip(force: bool = False) -> Path:
    """Scarica matrix_pendoLAVORO_2021.zip in CACHE_DIR."""
    out = CACHE_DIR / "matrix_pendoLAVORO_2021.zip"
    if out.exists() and not force:
        log.info("pendolarismo_zip_cached", path=str(out), size=out.stat().st_size)
        return out

    log.info("pendolarismo_zip_download_start", url=SOURCE_URL)
    t0 = time.time()
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    elapsed = time.time() - t0

    if len(data) < 100_000:
        snippet = data[:200].decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"ZIP troppo piccolo ({len(data)} bytes), forse errore HTTP: {snippet}"
        )
    if data[:2] != b"PK":
        raise RuntimeError(
            f"Non e' un ZIP valido (magic bytes: {data[:4].hex()})"
        )

    out.write_bytes(data)
    log.info("pendolarismo_zip_downloaded",
             size_mb=round(len(data) / 1024 / 1024, 1),
             elapsed_s=round(elapsed, 1))
    return out


# ═════════════════════════════════════════════════════════════════════════
# FASE 2 — Parsing ZIP → lista record OD
# ═════════════════════════════════════════════════════════════════════════

def parse_zip(zip_path: Path) -> list[dict]:
    """Estrae e parsa i record dal ZIP.

    Schema ATTESO (da validare al primo run con tracing):
    Probabilmente fixed-width o tab-separated. Documentazione 2011 (matrice
    pendolarismo) usava fixed-width 16 colonne. Per 2021 (Censimento permanente)
    il formato e' presumibilmente piu' semplice (CSV o TSV con header).

    Strategia robusta: ispeziona il primo file txt del ZIP, prova autodetect
    delimitatore e ritorna lista di dict normalizzati:
      [{"istat_res": "075035", "istat_dest": "075052", "count": 1782}, ...]
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        log.info("pendolarismo_zip_contents", n_files=len(names), files=names[:5])

        # Trova il file dati principale (txt/csv più grande)
        data_files = [n for n in names if n.lower().endswith((".txt", ".csv"))]
        if not data_files:
            raise RuntimeError(f"Nessun .txt/.csv nel ZIP. Files: {names}")

        # Prendi il piu' grande (sicuramente non e' un readme)
        infos = [zf.getinfo(n) for n in data_files]
        infos.sort(key=lambda i: i.file_size, reverse=True)
        main_file = infos[0]
        log.info("pendolarismo_main_file",
                 name=main_file.filename,
                 size_mb=round(main_file.file_size / 1024 / 1024, 1))

        with zf.open(main_file) as f:
            raw_bytes = f.read()

    # Autodetect encoding (ISTAT spesso usa ISO-8859-1 o UTF-8)
    for enc in ("utf-8", "iso-8859-1", "cp1252"):
        try:
            content = raw_bytes.decode(enc)
            log.info("pendolarismo_encoding_detected", encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("Impossibile decodificare il file")

    lines = content.splitlines()
    log.info("pendolarismo_lines_total", n=len(lines))

    if not lines:
        return []

    # Sniff: prima riga è header?
    first = lines[0]
    log.info("pendolarismo_first_line_sample", line=first[:200])

    # Autodetect separatore
    delim = None
    for d in [";", ",", "\t", "|"]:
        if first.count(d) >= 3:
            delim = d
            break

    records: list[dict] = []

    if delim:
        # Formato delimitato (CSV/TSV)
        log.info("pendolarismo_format_delimited", delimiter=repr(delim))
        records = _parse_delimited(lines, delim)
    else:
        # Formato fixed-width (come matrice 2011)
        log.info("pendolarismo_format_fixed_width")
        records = _parse_fixed_width(lines)

    log.info("pendolarismo_records_parsed", n=len(records))
    return records


def _parse_delimited(lines: list[str], delim: str) -> list[dict]:
    """Parsa file delimitato con autodetect campi.

    Cerca colonne per: codice istat residenza/origine, codice istat destinazione,
    conteggio individui. Schema 2021 ipotetico:
      ITTER107_RES  ITTER107_DEST  SEXISTAT1  AGE  TIME_PERIOD  OBS_VALUE
    oppure:
      cod_res  cod_dest  sesso  numero
    """
    import csv

    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
    rows = list(reader)
    if len(rows) < 2:
        return []

    header = [h.strip().upper() for h in rows[0]]
    log.info("pendolarismo_header", cols=header)

    # Cerca indici colonne chiave
    idx_res = idx_dest = idx_val = None
    for i, h in enumerate(header):
        # PRIORITY: cerca prima PROCOM_RES/LAV (schema 2021 confermato)
        # poi fallback su nomi semantici
        if idx_res is None and h in ("PROCOM_RES", "ITTER107_RES"):
            idx_res = i
        elif idx_dest is None and h in ("PROCOM_LAV", "PROCOM_DEST", "ITTER107_DEST"):
            idx_dest = i
        elif idx_val is None and h in ("PENDOLARI", "OBS_VALUE", "VALUE"):
            idx_val = i
        elif idx_res is None and any(k in h for k in [
            "RESIDENZA", "COD_RES", "ORIGINE", "COMUNE_RES",
        ]):
            idx_res = i
        elif idx_dest is None and any(k in h for k in [
            "DESTINAZIONE", "COD_DEST", "COMUNE_DEST", "LOCAL_AREA",
        ]):
            idx_dest = i
        elif idx_val is None and any(k in h for k in [
            "STIMA", "NUMERO", "INDIVIDUI",
        ]):
            idx_val = i

    if None in (idx_res, idx_dest, idx_val):
        log.error("pendolarismo_columns_not_found",
                  idx_res=idx_res, idx_dest=idx_dest, idx_val=idx_val,
                  header=header)
        raise RuntimeError(
            f"Impossibile identificare colonne residenza/destinazione/valore. "
            f"Header: {header}"
        )

    log.info("pendolarismo_columns_mapped",
             res=header[idx_res], dest=header[idx_dest], val=header[idx_val])

    records = []
    skipped = 0
    for row in rows[1:]:
        if len(row) <= max(idx_res, idx_dest, idx_val):
            skipped += 1
            continue
        try:
            istat_res = _normalize_istat(row[idx_res].strip())
            istat_dest = _normalize_istat(row[idx_dest].strip())
            val_raw = row[idx_val].strip().replace(",", ".")
            count = int(float(val_raw))
            if count <= 0:
                continue
            if not istat_res or not istat_dest:
                skipped += 1
                continue
            records.append({
                "istat_res": istat_res,
                "istat_dest": istat_dest,
                "count": count,
            })
        except (ValueError, IndexError):
            skipped += 1
            continue

    if skipped:
        log.warning("pendolarismo_rows_skipped", n=skipped)
    return records


def _parse_fixed_width(lines: list[str]) -> list[dict]:
    """Parsa formato fixed-width stile matrice 2011.

    Schema 2011 (riferimento, da adattare se 2021 differisce):
      pos 1:   tipo record (S=totali)
      pos 2:   tipo residenza (1=famiglia, 2=convivenza)
      pos 3-4: prov_residenza (XX)
      pos 5-7: com_residenza (XXX)
      pos 8:   sesso (1=M, 2=F)
      pos 9:   motivo (1=studio, 2=lavoro)
      pos 10:  luogo (1=stesso comune, 2=altro IT, 3=estero)
      pos 11-12: prov_dest (XX)
      pos 13-15: com_dest (XXX)
      ...

    Per 2021 (solo lavoro) probabilmente piu' semplice. Ispeziono per
    individuare lunghezze e parsare in modo permissivo.
    """
    sample = [l for l in lines[:20] if l.strip()]
    if not sample:
        return []
    log.info("pendolarismo_fw_sample", first_line=sample[0][:120],
             line_len=len(sample[0]) if sample else 0)
    raise RuntimeError(
        "Formato fixed-width detected ma parser specifico 2021 non ancora "
        "implementato. Necessario ispezionare il file. Sample line: "
        f"{sample[0][:200]!r}"
    )


def _normalize_istat(code: str) -> str:
    """Normalizza codice ISTAT a 6 digit string."""
    code = code.strip()
    if not code:
        return ""
    # Rimuovi prefissi tipo "IT" o "ITC1" se presenti
    if code.startswith(("IT", "it")):
        # Codici NUTS sono diversi, scarta
        if len(code) <= 6:
            return ""
    # Solo cifre
    digits = "".join(c for c in code if c.isdigit())
    if len(digits) == 6:
        return digits
    if len(digits) == 5:
        return "0" + digits
    if len(digits) == 4:
        return "00" + digits
    return digits  # ritorna comunque, sara' scartato a valle se invalido


# ═════════════════════════════════════════════════════════════════════════
# FASE 3 — Aggregazione per comune
# ═════════════════════════════════════════════════════════════════════════

def aggregate(records: list[dict]) -> dict[str, dict]:
    """Aggrega i record OD per comune.

    Ritorna dict {istat: {kpi...}} pronto per essere serializzato in shard.
    """
    # Indici: uscenti[res] = {dest: count}, entranti[dest] = {res: count}
    uscenti: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    entranti: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    auto: dict[str, int] = defaultdict(int)  # quando res == dest
    all_comuni: set[str] = set()

    for r in records:
        res, dest, count = r["istat_res"], r["istat_dest"], r["count"]
        all_comuni.add(res)
        all_comuni.add(dest)
        if res == dest:
            auto[res] += count
        else:
            uscenti[res][dest] += count
            entranti[dest][res] += count

    log.info("pendolarismo_aggregated",
             n_comuni=len(all_comuni),
             n_uscenti=sum(sum(v.values()) for v in uscenti.values()),
             n_entranti=sum(sum(v.values()) for v in entranti.values()),
             n_auto=sum(auto.values()))

    out: dict[str, dict] = {}
    for istat in sorted(all_comuni):
        usc_dict = uscenti.get(istat, {})
        ent_dict = entranti.get(istat, {})
        usc_tot = sum(usc_dict.values())
        ent_tot = sum(ent_dict.values())
        auto_count = auto.get(istat, 0)
        residenti_attivi = usc_tot + auto_count  # tot residenti che lavorano
        auto_pct = (
            round(auto_count / residenti_attivi * 100, 1)
            if residenti_attivi > 0 else 0.0
        )

        top_dest = sorted(usc_dict.items(), key=lambda x: -x[1])[:TOP_N]
        top_orig = sorted(ent_dict.items(), key=lambda x: -x[1])[:TOP_N]

        out[istat] = {
            "uscenti_totali": usc_tot,
            "entranti_totali": ent_tot,
            "saldo_netto": ent_tot - usc_tot,
            "auto_contenimento_pct": auto_pct,
            "n_destinazioni": len(usc_dict),
            "n_origini": len(ent_dict),
            "top_destinazioni": [
                {"istat": d, "count": c} for d, c in top_dest
            ],
            "top_origini": [
                {"istat": o, "count": c} for o, c in top_orig
            ],
        }
    return out


# ═════════════════════════════════════════════════════════════════════════
# FASE 4 — Scrittura shard
# ═════════════════════════════════════════════════════════════════════════

def write_shards(kpi_per_comune: dict[str, dict],
                 output_dir: Path,
                 limit: set[str] | None = None) -> int:
    """Scrive 1 file JSON per comune in output_dir/<istat>.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    written = 0
    for istat, kpi in sorted(kpi_per_comune.items()):
        if limit and istat not in limit:
            continue
        # Filtra ISTAT non a 6 digit
        if not (len(istat) == 6 and istat.isdigit()):
            continue
        shard = {
            "_etl_version": ETL_VERSION,
            "_source": SOURCE_LABEL,
            "_source_url": SOURCE_URL,
            "_license": LICENSE,
            "_anno_rilevazione": ANNO,
            "_motivo_spostamento": MOTIVO,
            "_generated_at": now,
            "kpi": kpi,
        }
        out_path = output_dir / f"{istat}.json"
        out_path.write_text(json.dumps(shard, ensure_ascii=False, separators=(",", ":")))
        written += 1

    log.info("pendolarismo_shards_written", n=written, dir=str(output_dir))
    return written


# ═════════════════════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="ETL ISTAT Matrice pendolarismo per lavoro 2021",
    )
    ap.add_argument(
        "--target",
        choices=["local", "local-repo"],
        default="local",
        help="local: /var/www/cruscotto-italia/data/pendolarismo/ ; "
             "local-repo: data/pendolarismo/ (per commit via CI)",
    )
    ap.add_argument(
        "--outdir", default=None,
        help="Override directory di output",
    )
    ap.add_argument(
        "--force-download", action="store_true",
        help="Riscarica ZIP anche se presente in cache",
    )
    ap.add_argument(
        "--limit", default="",
        help="Smoke test: comma-separated ISTAT (es. 075035,058091)",
    )
    args = ap.parse_args()

    if args.outdir:
        output_dir = Path(args.outdir)
    elif args.target == "local-repo":
        output_dir = Path("data/pendolarismo")
    else:
        output_dir = Path("/var/www/cruscotto-italia/data/pendolarismo")

    limit_set = (
        {c.strip() for c in args.limit.split(",") if c.strip()}
        if args.limit else None
    )

    log.info("pendolarismo_etl_start",
             target=args.target, outdir=str(output_dir),
             limit=sorted(limit_set) if limit_set else None)
    t_start = time.time()

    # FASE 1: download
    zip_path = download_zip(force=args.force_download)

    # FASE 2: parse
    records = parse_zip(zip_path)
    if not records:
        log.error("pendolarismo_no_records")
        return 1

    # FASE 3: aggregate
    kpi_per_comune = aggregate(records)

    # FASE 4: write shards
    n_written = write_shards(kpi_per_comune, output_dir, limit=limit_set)

    # Manifest update best-effort
    try:
        files = [{"name": f.name,
                  "size": f.stat().st_size,
                  "key": f"pendolarismo/{f.name}"}
                 for f in sorted(output_dir.glob("*.json"))]
        manifest.update_source("pendolarismo", files, status="ok")
        log.info("pendolarismo_manifest_updated", n_files=len(files))
    except Exception as e:
        log.warning("pendolarismo_manifest_update_skipped", err=str(e))

    elapsed = time.time() - t_start
    log.info("pendolarismo_etl_done",
             shards_written=n_written, elapsed_s=round(elapsed, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
