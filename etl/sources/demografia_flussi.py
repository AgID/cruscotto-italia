"""ETL Dinamica demografica ISTAT D7B - bilancio demografico mensile.

Fonte: ISTAT, applicazione D7B "Bilancio demografico mensile e popolazione
residente per sesso". Fonte anagrafica dichiarata: ANPR.
Download bulk annuale da https://demo.istat.it/data/d7b/D7B{anno}.csv.zip

Questo ETL NON crea shard: arricchisce quelli gia' prodotti da
etl.sources.demografia aggiungendo la sezione "dinamica". Un comune senza
shard demografia viene saltato (la fotografia POSAS e' il dato primario).

Contenuto esposto (aggregati ANNUALI, non mensili):
- nati, morti, saldo naturale
- saldo migratorio interno, saldo migratorio con l'estero
- popolazione a inizio e fine di ciascun anno

DUE TRAPPOLE DEL TRACCIATO, entrambe verificate sui file reali:

1. La colonna Sesso ha TRE valori: Maschi, Femmine e Totale. Sommare tutte
   le righe senza filtrare RADDOPPIA ogni valore.
2. La colonna Mese non va da 1 a 12. Esistono anche il mese 13 (rettifiche
   e variazioni territoriali) e il mese 15 (riepilogo annuale). In quelle
   righe Nati vivi e Morti sono VUOTI mentre la popolazione e' valorizzata:
   un cast diretto esplode, e sommarle falsa i totali.

Filtro corretto: Sesso = 'Totale' AND Mese fra 1 e 12.
Prova: con il filtro sbagliato Lecce 2024 risulta a 1094 nati e 2228 morti
(natalita' 11,6 per mille, implausibile). Con il filtro corretto: 547 e 1114.

NOTA sulla popolazione: "Popolazione fine periodo" di un anno NON coincide
con "Popolazione inizio periodo" dell'anno successivo, per effetto delle
rettifiche post-censuarie applicate a inizio anno (Roma 2021: fine 2764589,
inizio 2022 2749031). I due valori vanno letti DENTRO il singolo anno, dove
il bilancio chiude, e non concatenati in una serie continua. Per la serie di
stock si usa POSAS (sezione serie_storica), che al 1 gennaio coincide
esattamente con la popolazione di inizio periodo del D7B.

Usage:
  python -m etl.sources.demografia_flussi
  python -m etl.sources.demografia_flussi --anni 6
  python -m etl.sources.demografia_flussi --csv-dir /path/con/csv/gia/estratti
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb
import requests
import structlog

log = structlog.get_logger()

D7B_URL_TEMPLATE = "https://demo.istat.it/data/d7b/D7B{year}.csv.zip"

# Anni indietro rispetto a quello corrente entro cui cercare le annate
# pubblicate. Il D7B esce a chiusura d'anno con ritardo: al 2026 l'ultima
# annata disponibile e' il 2024.
LOOKBACK = 9


def pull_d7b_anno(workdir: Path, year: int) -> Path | None:
    """Scarica ed estrae il bulk D7B di un anno. None se l'anno non esiste.

    Un anno mancante non e' un errore fatale: e' un punto in meno nella serie.
    Ogni annata viene estratta in una sottodirectory dedicata, altrimenti il
    glob raccoglierebbe il CSV di un'altra annata.
    """
    extract_dir = workdir / str(year)
    extract_dir.mkdir(parents=True, exist_ok=True)

    gia_estratti = list(extract_dir.glob("*.csv"))
    if gia_estratti:
        log.info("d7b_csv_riusato", year=year, path=str(gia_estratti[0]))
        return gia_estratti[0]

    url = D7B_URL_TEMPLATE.format(year=year)
    try:
        head = requests.head(url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        log.warning("d7b_head_failed", year=year, error=str(e))
        return None
    if head.status_code != 200:
        log.info("d7b_anno_non_disponibile", year=year,
                 status=head.status_code)
        return None

    zip_path = extract_dir / f"D7B{year}.csv.zip"
    resp = requests.get(url, timeout=600, stream=True)
    resp.raise_for_status()
    bytes_written = 0
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            bytes_written += len(chunk)
    log.info("d7b_zip_saved", year=year, bytes=bytes_written)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    zip_path.unlink(missing_ok=True)

    csv_files = list(extract_dir.glob("*.csv"))
    if not csv_files:
        log.warning("d7b_csv_assente", year=year)
        return None
    log.info("d7b_csv_estratto", year=year, path=str(csv_files[0]),
             bytes=csv_files[0].stat().st_size)
    return csv_files[0]


def anni_disponibili(anno_max: int | None = None,
                     lookback: int = LOOKBACK) -> list[int]:
    """Elenca le annate D7B effettivamente pubblicate, dalla piu' recente.

    Non si cablano gli anni: la disponibilita' cambia nel tempo e un URL
    cablato diventa un 404 silenzioso.
    """
    top = anno_max or datetime.date.today().year
    trovati: list[int] = []
    for year in range(top, top - lookback - 1, -1):
        url = D7B_URL_TEMPLATE.format(year=year)
        try:
            head = requests.head(url, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            log.warning("d7b_probe_failed", year=year, error=str(e))
            continue
        if head.status_code == 200:
            trovati.append(year)
    log.info("d7b_anni_disponibili", anni=trovati)
    return trovati


def aggrega_anno(csv_path: Path, anno: int) -> dict[str, dict]:
    """Aggrega il bilancio mensile in totali annuali per comune.

    Il filtro Sesso='Totale' AND Mese fra 1 e 12 e' la parte critica: senza,
    i valori raddoppiano e le righe di rettifica falsano i conteggi.
    TRY_CAST restituisce NULL sui campi vuoti dei mesi 13 e 15 invece di
    sollevare, e SUM ignora i NULL.
    """
    con = duckdb.connect()
    try:
        rows = con.execute(f"""
            SELECT
                "Codice comune" AS istat,
                SUM(TRY_CAST("Nati vivi" AS INTEGER))                    AS nati,
                SUM(TRY_CAST("Morti" AS INTEGER))                        AS morti,
                SUM(TRY_CAST("Saldo naturale" AS INTEGER))               AS saldo_nat,
                SUM(TRY_CAST("Saldo migratorio interno" AS INTEGER))     AS saldo_int,
                SUM(TRY_CAST("Saldo migratorio con l'estero" AS INTEGER)) AS saldo_est,
                MAX(CASE WHEN TRY_CAST("Mese" AS INTEGER) = 1
                         THEN TRY_CAST("Popolazione inizio periodo" AS INTEGER) END) AS pop_ini,
                MAX(CASE WHEN TRY_CAST("Mese" AS INTEGER) = 12
                         THEN TRY_CAST("Popolazione fine periodo" AS INTEGER) END)   AS pop_fin
            FROM read_csv(
                '{csv_path}',
                delim=';',
                header=true,
                quote='"',
                ignore_errors=true,
                all_varchar=true
            )
            WHERE "Sesso" = 'Totale'
              AND TRY_CAST("Mese" AS INTEGER) BETWEEN 1 AND 12
              AND "Codice comune" IS NOT NULL
            GROUP BY 1
        """).fetchall()
    finally:
        con.close()

    out: dict[str, dict] = {}
    for istat, nati, morti, s_nat, s_int, s_est, p_ini, p_fin in rows:
        out[istat] = {
            "anno": anno,
            "nati": int(nati or 0),
            "morti": int(morti or 0),
            "saldo_naturale": int(s_nat or 0),
            "saldo_migratorio_interno": int(s_int or 0),
            "saldo_migratorio_estero": int(s_est or 0),
            "popolazione_inizio": int(p_ini) if p_ini is not None else None,
            "popolazione_fine": int(p_fin) if p_fin is not None else None,
        }
    return out


def scrivi_dinamica(shard_dir: Path, per_comune: dict[str, list],
                    anno_ultimo: int) -> tuple[int, int]:
    """Innesta la sezione dinamica negli shard demografia esistenti.

    Non crea shard nuovi: un comune presente nel D7B ma senza shard POSAS
    e' quasi sempre un comune soppresso per fusione, e non va resuscitato.
    """
    n_scritti = 0
    n_saltati = 0
    for istat, anni in per_comune.items():
        path = shard_dir / f"{istat}.json"
        if not path.exists():
            n_saltati += 1
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("shard_illeggibile", istat=istat, error=str(e))
            n_saltati += 1
            continue

        anni_ord = sorted(anni, key=lambda x: x["anno"])
        d["dinamica"] = {
            "_fonte": ("ISTAT D7B - Bilancio demografico mensile "
                       "(fonte anagrafica ANPR)"),
            "_nota": ("aggregati annuali; popolazione inizio e fine si "
                      "riferiscono al singolo anno e non vanno concatenate "
                      "tra anni diversi"),
            "_anno_ultimo": anni_ord[-1]["anno"],
            "anni": anni_ord,
        }
        d["_anno_dati_dinamica"] = anno_ultimo

        path.write_text(
            json.dumps(d, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        n_scritti += 1
    return n_scritti, n_saltati


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL Dinamica demografica ISTAT D7B")
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato.")
    parser.add_argument("--outdir", type=Path,
                        default=Path("/var/www/cruscotto-italia/data"))
    parser.add_argument("--workdir", type=Path, default=None,
                        help="Directory di lavoro per ZIP+CSV (default: tempdir).")
    parser.add_argument("--anni", type=int, default=6,
                        help="Numero massimo di annate da esporre (default 6).")
    parser.add_argument("--anno-max", type=int, default=None,
                        help="Anno piu' recente da cui iniziare la ricerca.")
    parser.add_argument("--csv-dir", type=Path, default=None,
                        help="Usa i CSV gia' estratti qui, senza scaricare.")
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    shard_dir = args.outdir / "demografia"
    if not shard_dir.is_dir():
        log.error("shard_dir_assente", path=str(shard_dir),
                  hint="eseguire prima etl.sources.demografia")
        return 1

    workdir = args.workdir or Path(
        tempfile.mkdtemp(prefix="cruscotto-demografia-d7b-"))
    workdir.mkdir(parents=True, exist_ok=True)

    anni = anni_disponibili(args.anno_max)
    if not anni:
        log.error("nessun_anno_d7b_disponibile")
        return 1
    anni = sorted(anni)[-args.anni:]
    log.info("etl_start", anni=anni, outdir=str(args.outdir),
             workdir=str(workdir))

    per_comune: dict[str, list] = {}
    for i, anno in enumerate(anni, 1):
        step = f"[{i}/{len(anni)}]"
        log.info("d7b_anno_inizio", step=step, anno=anno)

        if args.csv_dir:
            candidati = list(args.csv_dir.glob(f"*{anno}*.csv"))
            csv_path = candidati[0] if candidati else None
        else:
            csv_path = pull_d7b_anno(workdir, anno)

        if csv_path is None:
            log.warning("d7b_anno_saltato", step=step, anno=anno)
            continue

        aggregati = aggrega_anno(csv_path, anno)
        for istat, blocco in aggregati.items():
            per_comune.setdefault(istat, []).append(blocco)
        log.info("d7b_anno_ok", step=step, anno=anno, comuni=len(aggregati))

        if not args.csv_dir:
            # 40 MB per annata: senza rimozione il workdir arriva a 240 MB
            csv_path.unlink(missing_ok=True)

    if not per_comune:
        log.error("nessun_dato_aggregato")
        return 1

    anno_ultimo = max(anni)
    n_scritti, n_saltati = scrivi_dinamica(shard_dir, per_comune, anno_ultimo)
    log.info("etl_done", comuni_con_dinamica=n_scritti,
             comuni_senza_shard=n_saltati, anni=anni, anno_ultimo=anno_ultimo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
