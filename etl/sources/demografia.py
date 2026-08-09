"""ETL Demografia ISTAT POSAS - matrice eta x sesso per comune.

Fonte: ISTAT POSAS (Popolazione e situazione anagrafica).
Auto-download bulk ZIP da https://demo.istat.it/data/posas/POSAS_{YEAR}_it_Comuni.zip
con auto-detect dell'anno disponibile piu' recente (parte dall'anno
corrente +1 e scende fino a trovare lo ZIP pubblicato). E' possibile
forzare l'anno con --year.

Schema CSV upstream: codice_istat, comune, eta, maschi, femmine, totale
        (eta=999 e' la riga di totale comune)

Output:
- demografia/<istat>.json per ogni comune (~3-5 KB)
  Contiene: KPI aggregati + matrice eta x sesso per piramide

KPI calcolati:
- popolazione_totale, maschi_totale, femmine_totale
- pct_0_14 (giovanissimi), pct_15_64 (eta lavorativa), pct_65_piu (anziani)
- pct_85_piu (grandi anziani)
- indice_vecchiaia = (65+) / (0-14) * 100
- indice_dipendenza = (0-14 + 65+) / (15-64) * 100
- eta_media (ponderata)

Usage:
  python -m etl.sources.demografia
  python -m etl.sources.demografia --year=2027  # forza anno
  python -m etl.sources.demografia --csv=/path/to.csv  # bypass download
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb
import requests
import structlog

from etl.lib import manifest
from etl.lib.shard_io import preserva_sezioni_esistenti, write_shard_preserving

log = structlog.get_logger()

POSAS_URL_TEMPLATE = "https://demo.istat.it/data/posas/POSAS_{year}_it_Comuni.zip"


def pull_posas_auto_year(workdir: Path,
                         forced_year: int | None = None) -> tuple[Path, int]:
    """Scarica e estrae il bulk POSAS ISTAT, con auto-detect dell'anno.

    ISTAT pubblica POSAS_<YEAR>_it_Comuni.zip dove YEAR si riferisce alla
    popolazione al 1 gennaio dell'anno indicato. Il file dell'anno corrente
    viene tipicamente pubblicato in primavera/estate. Per coprire i casi di
    pubblicazione in ritardo o anticipata, la funzione prova in sequenza
    [anno_corrente+1, anno_corrente, anno_corrente-1] e si ferma al primo
    URL che restituisce 200. Con --year la sequenza e' bypassata.

    Args:
        workdir: directory di lavoro per ZIP + estrazione
        forced_year: se non None, scarica solo questo anno (fail se 404)

    Returns:
        tuple (csv_path, year_used)
    """
    workdir.mkdir(parents=True, exist_ok=True)
    extract_dir = workdir / "extracted"
    extract_dir.mkdir(exist_ok=True)

    current_year = datetime.date.today().year
    if forced_year is not None:
        candidates = [forced_year]
    else:
        # Prova prima anno+1 (es. POSAS_2027 pubblicato a inizio 2026 e' raro
        # ma possibile), poi anno corrente, poi anno-1 come fallback robusto.
        candidates = [current_year + 1, current_year, current_year - 1]

    last_status: int | None = None
    last_url: str | None = None
    for year in candidates:
        url = POSAS_URL_TEMPLATE.format(year=year)
        log.info("posas_try_year", year=year, url=url)
        try:
            head = requests.head(url, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            log.warning("posas_head_failed", year=year, error=str(e))
            continue
        last_status, last_url = head.status_code, url
        if head.status_code != 200:
            log.info("posas_year_not_found", year=year, status=head.status_code)
            continue
        # Trovato: download + extract
        zip_path = workdir / f"POSAS_{year}_it_Comuni.zip"
        log.info("posas_downloading", year=year, url=url)
        resp = requests.get(url, timeout=300, stream=True)
        resp.raise_for_status()
        bytes_written = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                bytes_written += len(chunk)
        log.info("posas_zip_saved", year=year, bytes=bytes_written,
                 path=str(zip_path))
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        csv_files = list(extract_dir.glob(f"POSAS_{year}_it_Comuni.csv"))
        if not csv_files:
            # Fallback: qualsiasi CSV nell'estratto
            csv_files = list(extract_dir.glob("*.csv"))
        if not csv_files:
            raise RuntimeError(
                f"POSAS_{year} ZIP estratto ma nessun CSV trovato in {extract_dir}"
            )
        csv_path = csv_files[0]
        log.info("posas_csv_extracted", year=year, path=str(csv_path),
                 bytes=csv_path.stat().st_size)
        return csv_path, year

    # Tutti gli anni candidati hanno fallito
    raise RuntimeError(
        f"Nessun POSAS scaricabile tra {candidates}. "
        f"Ultimo URL provato: {last_url} (status: {last_status})"
    )


def titolo_posas(csv_path: Path) -> str:
    """Legge la riga 1 del CSV POSAS, che e' un titolo e non l'header.

    Il titolo dichiara anno di riferimento e, per le annate non ancora
    definitive, la parola "stima". I file dal 2024 in poi hanno il BOM:
    utf-8-sig lo gestisce e resta corretto anche sui file che non ce l'hanno.
    """
    with open(csv_path, encoding="utf-8-sig") as f:
        return f.readline().strip().strip('"')


def anno_da_titolo(titolo: str) -> int | None:
    """Estrae l'anno di riferimento dal titolo POSAS (prima occorrenza 19xx/20xx)."""
    m = re.search(r"(?:19|20)\d{2}", titolo)
    return int(m.group(0)) if m else None


def is_stima(titolo: str) -> bool:
    """True se il titolo dichiara il dato come stima (anno non ancora chiuso)."""
    return "stima" in titolo.lower()


def pull_posas_anno(workdir: Path, year: int) -> Path | None:
    """Scarica ed estrae il bulk POSAS di un anno specifico.

    A differenza di pull_posas_auto_year NON solleva se l'anno non esiste:
    ritorna None. Nella serie storica un anno mancante non e' un errore fatale,
    e' solo un punto in meno.

    Ogni anno viene estratto in una sottodirectory dedicata: estrarre annate
    diverse nella stessa cartella farebbe raccogliere il CSV sbagliato.
    """
    extract_dir = workdir / "serie" / str(year)
    extract_dir.mkdir(parents=True, exist_ok=True)

    gia_estratti = list(extract_dir.glob("*.csv"))
    if gia_estratti:
        log.info("serie_csv_riusato", year=year, path=str(gia_estratti[0]))
        return gia_estratti[0]

    url = POSAS_URL_TEMPLATE.format(year=year)
    try:
        head = requests.head(url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        log.warning("serie_head_failed", year=year, error=str(e))
        return None
    if head.status_code != 200:
        log.info("serie_anno_non_disponibile", year=year,
                 status=head.status_code)
        return None

    zip_path = workdir / "serie" / f"POSAS_{year}_it_Comuni.zip"
    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()
    bytes_written = 0
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            bytes_written += len(chunk)
    log.info("serie_zip_saved", year=year, bytes=bytes_written)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    csv_files = list(extract_dir.glob("*.csv"))
    if not csv_files:
        log.warning("serie_csv_assente", year=year)
        return None
    return csv_files[0]


def leggi_totali_posas(csv_path: Path) -> dict[str, dict]:
    """Estrae i soli totali comunali dal CSV POSAS (riga Eta=999).

    ATTENZIONE: la riga con Eta=999 e' il totale gia' calcolato da ISTAT.
    Sommare tutte le righe di un comune SENZA escluderla raddoppia i valori.
    Qui si legge direttamente quella riga, che e' l'operazione inversa e
    altrettanto valida (verificato: coincide con la somma 0-100).

    La lettura e' per NOME di colonna: il tracciato POSAS cambia tra le annate
    (20 colonne fino al 2025 con lo stato civile, 6 dal 2026), ma i nomi
    "Totale maschi" / "Totale femmine" / "Totale" sono presenti in entrambi.
    """
    con = duckdb.connect()
    try:
        rows = con.execute(f"""
            SELECT
                "Codice comune" AS istat,
                CAST("Totale maschi" AS INTEGER) AS m,
                CAST("Totale femmine" AS INTEGER) AS f,
                CAST("Totale" AS INTEGER) AS tot
            FROM read_csv(
                '{csv_path}',
                delim=';',
                header=true,
                skip=1,
                quote='"',
                ignore_errors=true,
                all_varchar=false
            )
            WHERE CAST("Età" AS INTEGER) = 999
              AND "Codice comune" IS NOT NULL
        """).fetchall()
    finally:
        con.close()
    return {
        r[0]: {"maschi": r[1] or 0, "femmine": r[2] or 0,
               "popolazione": r[3] or 0}
        for r in rows
    }


def build_serie_storica(workdir: Path, anno_ultimo: int, n_anni: int,
                        csv_ultimo: Path | None = None) -> dict[str, list]:
    """Serie storica della popolazione residente al 1 gennaio, per comune.

    Il numero di comuni cambia nel tempo (fusioni e istituzioni): un comune
    puo' non essere presente in tutte le annate. La serie di ogni comune
    contiene solo i punti realmente disponibili, MAI zeri di riempimento
    (uno zero e un dato mancante non sono la stessa cosa).

    Il CSV dell'anno piu' recente e' gia' stato scaricato dal flusso
    principale: viene riusato invece di riscaricarlo.
    """
    anni = list(range(anno_ultimo - n_anni + 1, anno_ultimo + 1))
    serie: dict[str, list] = {}
    for i, anno in enumerate(anni, 1):
        step = f"[{i}/{len(anni)}]"
        log.info("serie_anno_inizio", step=step, anno=anno)
        if anno == anno_ultimo and csv_ultimo is not None:
            csv_path = csv_ultimo
        else:
            csv_path = pull_posas_anno(workdir, anno)
        if csv_path is None:
            log.warning("serie_anno_saltato", step=step, anno=anno)
            continue
        stima = is_stima(titolo_posas(csv_path))
        totali = leggi_totali_posas(csv_path)
        for istat, d in totali.items():
            serie.setdefault(istat, []).append({
                "anno": anno,
                "popolazione": d["popolazione"],
                "maschi": d["maschi"],
                "femmine": d["femmine"],
                "stima": stima,
            })
        log.info("serie_anno_ok", step=step, anno=anno,
                 comuni=len(totali), stima=stima)

    for istat in serie:
        serie[istat].sort(key=lambda p: p["anno"])
    return serie


def build_demografia_shards(csv_path: Path, output_dir: Path,
                            anno: int | None = None,
                            stima: bool | None = None,
                            serie: dict[str, list] | None = None) -> Path:
    """Genera 1 file JSON per comune con matrice eta x sesso + KPI + serie."""
    titolo = titolo_posas(csv_path)
    if anno is None:
        anno = anno_da_titolo(titolo) or datetime.date.today().year
    if stima is None:
        stima = is_stima(titolo)
    riferimento = f"1 gennaio {anno}" + (" (stima)" if stima else "")
    log.info("demografia_riferimento", anno=anno, stima=stima,
             riferimento=riferimento)
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = output_dir / "demografia"
    shard_dir.mkdir(parents=True, exist_ok=True)

    log.info("demografia_loading_csv", path=str(csv_path))
    con = duckdb.connect()

    # POSAS ha riga 1 con titolo, riga 2 header reale
    con.execute(f"""
        CREATE TABLE pop AS
        SELECT
            "Codice comune" AS istat,
            "Comune" AS comune,
            CAST("Età" AS INTEGER) AS eta,
            CAST("Totale maschi" AS INTEGER) AS m,
            CAST("Totale femmine" AS INTEGER) AS f,
            CAST("Totale" AS INTEGER) AS tot
        FROM read_csv(
            '{csv_path}',
            delim=';',
            header=true,
            skip=1,
            quote='"',
            ignore_errors=true,
            all_varchar=false
        )
    """)

    n_total = con.execute("SELECT COUNT(*) FROM pop").fetchone()[0]
    n_comuni = con.execute("SELECT COUNT(DISTINCT istat) FROM pop").fetchone()[0]
    log.info("demografia_loaded", rows=n_total, comuni=n_comuni)

    # Per ogni comune, costruisci la matrice eta -> {m, f, tot} per eta 0-100
    # + estrai riga totale (eta=999)
    rows = con.execute("""
        SELECT istat, comune, eta, m, f, tot
        FROM pop
        WHERE istat IS NOT NULL
        ORDER BY istat, eta
    """).fetchall()

    # Raggruppa per istat
    by_istat: dict[str, dict] = {}
    for istat, comune, eta, m, f, tot in rows:
        if istat not in by_istat:
            by_istat[istat] = {
                "istat_code": istat,
                "comune": comune,
                "matrice": {},  # {eta: {m, f, tot}}
                "totale_riga": None,  # riga eta=999
            }
        if eta == 999:
            by_istat[istat]["totale_riga"] = {"m": m or 0, "f": f or 0, "tot": tot or 0}
        else:
            by_istat[istat]["matrice"][eta] = {"m": m or 0, "f": f or 0, "tot": tot or 0}

    # Calcola KPI per ogni comune
    n_written = 0
    total_bytes = 0
    for istat, d in by_istat.items():
        m_tot = d["totale_riga"]["m"] if d["totale_riga"] else 0
        f_tot = d["totale_riga"]["f"] if d["totale_riga"] else 0
        pop_tot = d["totale_riga"]["tot"] if d["totale_riga"] else 0

        if pop_tot == 0:
            log.warning("demografia_skip_empty", istat=istat)
            continue

        # Fasce d'eta
        matrice = d["matrice"]
        pop_0_14 = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(0, 15))
        pop_15_64 = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(15, 65))
        pop_65_piu = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(65, 101))
        pop_85_piu = sum(matrice.get(e, {"tot": 0})["tot"] for e in range(85, 101))

        # Eta media ponderata: somma(eta * pop) / pop_tot
        eta_pop_sum = sum(e * matrice.get(e, {"tot": 0})["tot"] for e in range(0, 101))
        eta_media = round(eta_pop_sum / pop_tot, 1) if pop_tot else 0

        # Indici
        indice_vecchiaia = round(pop_65_piu / pop_0_14 * 100, 1) if pop_0_14 else None
        indice_dipendenza = round(
            (pop_0_14 + pop_65_piu) / pop_15_64 * 100, 1
        ) if pop_15_64 else None

        # Costruisci array piramide: [{eta, m, f, tot}, ...]
        piramide = [
            {
                "eta": e,
                "m": matrice.get(e, {"m": 0})["m"],
                "f": matrice.get(e, {"f": 0})["f"],
                "tot": matrice.get(e, {"tot": 0})["tot"],
            }
            for e in range(0, 101)
        ]

        payload = {
            "_etl_version": "0.3.0",
            "_source": "ISTAT POSAS - Popolazione residente per eta e sesso",
            "_riferimento": riferimento,
            "_anno_riferimento": anno,
            "_stima": stima,
            "istat_code": istat,
            "comune": d["comune"],
            "popolazione_totale": pop_tot,
            "maschi": m_tot,
            "femmine": f_tot,
            "pct_maschi": round(m_tot / pop_tot * 100, 1),
            "pct_femmine": round(f_tot / pop_tot * 100, 1),
            "fasce_eta": {
                "0_14": {"n": pop_0_14, "pct": round(pop_0_14 / pop_tot * 100, 1)},
                "15_64": {"n": pop_15_64, "pct": round(pop_15_64 / pop_tot * 100, 1)},
                "65_piu": {"n": pop_65_piu, "pct": round(pop_65_piu / pop_tot * 100, 1)},
                "85_piu": {"n": pop_85_piu, "pct": round(pop_85_piu / pop_tot * 100, 1)},
            },
            "eta_media": eta_media,
            "indice_vecchiaia": indice_vecchiaia,
            "indice_dipendenza": indice_dipendenza,
            "piramide": piramide,
        }

        punti = (serie or {}).get(istat)
        if punti:
            payload["serie_storica"] = {
                "_fonte": "ISTAT POSAS",
                "_nota": "popolazione residente al 1 gennaio di ciascun anno",
                "punti": punti,
            }

        shard_path = shard_dir / f"{istat}.json"
        # La sezione dinamica e' di competenza dell'ETL D7B, non di questo:
        # va riportata dallo shard esistente, altrimenti questo giro la
        # cancella. NON va pero' marcata _stale_: quella marcatura significa
        # "fetch fallito, dato vecchio", mentre qui si tratta semplicemente di
        # un'altra fonte con un'altra cadenza, il cui dato puo' essere
        # freschissimo.
        if shard_path.exists():
            try:
                vecchio = json.loads(shard_path.read_text(encoding="utf-8"))
            except Exception:
                vecchio = {}
            preserva_sezioni_esistenti(
                payload, vecchio, ["dinamica"],
                meta_map={"dinamica": "_anno_dati_dinamica"},
            )
            payload.pop("_stale_dinamica", None)

        write_shard_preserving(
            shard_path,
            payload,
            indent=None,
            separators=(",", ":"),
        )
        n_written += 1
        total_bytes += shard_path.stat().st_size

    log.info("demografia_shards_done",
             n_shards=n_written,
             total_bytes=total_bytes,
             avg_bytes=total_bytes // max(1, n_written))
    return shard_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL Demografia ISTAT POSAS")
    # --target tenuto per retrocompat workflow esistenti, ma solo 'local' e' supportato
    parser.add_argument("--target", choices=["local"], default="local",
                        help="Solo 'local' supportato (R2 rimosso dall'infrastruttura AgID)")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Bypassa il download e usa questo CSV locale.")
    parser.add_argument("--year", type=int, default=None,
                        help="Forza un anno specifico per il download POSAS.")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="Directory di lavoro per ZIP+CSV (default: tempdir).")
    parser.add_argument("--outdir", type=Path, default=Path("/var/www/cruscotto-italia/data"))
    parser.add_argument("--anni-serie", type=int, default=5,
                        help="Numero di annate POSAS nella serie storica (default 5).")
    parser.add_argument("--no-serie", action="store_true",
                        help="Salta la serie storica (solo fotografia corrente).")
    args = parser.parse_args()

    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ])

    output_dir = args.outdir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Risoluzione CSV: priorità --csv esplicito, altrimenti auto-download.
    csv_path: Path
    year_used: int | None = None
    if args.csv is not None:
        if not args.csv.exists():
            log.error("csv_not_found", path=str(args.csv))
            return 1
        csv_path = args.csv
        log.info("posas_csv_from_arg", path=str(csv_path))
    else:
        workdir = args.workdir or (
            Path(tempfile.mkdtemp(prefix="cruscotto-demografia-posas-"))
        )
        try:
            csv_path, year_used = pull_posas_auto_year(workdir,
                                                       forced_year=args.year)
        except Exception as e:
            log.exception("posas_download_failed", error=str(e))
            return 1

    log.info("etl_start", output_dir=str(output_dir),
             csv=str(csv_path), year=year_used)

    try:
        titolo = titolo_posas(csv_path)
        anno_rif = (year_used or anno_da_titolo(titolo)
                    or datetime.date.today().year)
        stima_rif = is_stima(titolo)
        log.info("posas_titolo", titolo=titolo, anno=anno_rif, stima=stima_rif)

        serie = None
        if not args.no_serie and args.anni_serie > 1:
            serie_workdir = args.workdir or Path(
                tempfile.mkdtemp(prefix="cruscotto-demografia-serie-")
            )
            serie = build_serie_storica(serie_workdir, anno_rif,
                                        args.anni_serie, csv_ultimo=csv_path)
            log.info("serie_storica_pronta", comuni=len(serie))

        shard_dir = build_demografia_shards(csv_path, output_dir,
                                            anno=anno_rif, stima=stima_rif,
                                            serie=serie)

        # Manifest update (best-effort)
        shard_count = len(list(shard_dir.glob("*.json")))
        try:
            manifest.update_source(
                "demografia",
                [{"key": "demografia/*", "count": shard_count}],
                status="ok",
            )
            log.info("manifest_updated", count=shard_count)
        except Exception as e:
            log.warning("manifest_update_skipped", error=str(e))

        log.info("etl_done", year=year_used, comuni=shard_count)
        return 0
    except Exception as e:
        log.exception("etl_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
