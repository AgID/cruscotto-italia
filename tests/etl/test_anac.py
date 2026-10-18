"""Test ANAC BDNCP (19/10/2026): finestra, deduplica per CIG, filtri sulle
date, unione con i CIG, esclusione degli importi anomali (spesa SIOPE)."""
import csv
import io
import json
import zipfile

from etl.sources import anac as A

COL_AGG = ["cig", "data_aggiudicazione_definitiva", "data_comunicazione_esito",
           "importo_aggiudicazione", "id_aggiudicazione"]
COL_CIG = ["cig", "cf_amministrazione_appaltante", "denominazione_amministrazione_appaltante",
           "cod_cpv", "descrizione_cpv", "oggetto_lotto", "flag_prevalente"]


def _zip(path, colonne, righe):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)
    w.writerow(colonne)
    w.writerows(righe)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.name.replace(".zip", ".csv"), "﻿" + buf.getvalue())


def _ambiente(tmp_path):
    arch, data = tmp_path / "arch", tmp_path / "data"
    for d in ("aggiudicazioni", "cig", "cig_annuali"):
        (arch / d).mkdir(parents=True)
    (data / "lookup").mkdir(parents=True)
    (data / "siope").mkdir()
    (data / "lookup" / "comuni-index.json").write_text(json.dumps([
        {"i": "075035", "n": "Lecce", "cf": "80008510754", "p": 95000},
        {"i": "097055", "n": "Morterone", "cf": "00559990130", "p": 30},
    ]))
    (data / "siope" / "075035.json").write_text(json.dumps({"per_anno": {
        "2025": {"parziale": False, "totale_anno": 300_000_000.0},
        "2026": {"parziale": True, "totale_anno": 1.0}}}))
    # storico: X1 vecchia versione, X2 data impossibile, X5 fuori finestra
    _zip(arch / "aggiudicazioni" / "aggiudicazioni_csv_20261019.zip", COL_AGG, [
        ["X1", "2025-11-10", "2025-11-12", "100", "1"],
        ["X2", "7600-05-01", "2025-11-12", "999", "2"],
        ["X5", "2025-06-01", "2025-06-05", "50", "5"],
    ])
    # mensili: X1 rettificata (vince), X3 anomala per Lecce, X4 Morterone
    _zip(arch / "aggiudicazioni" / "20260901-aggiudicazioni_csv.zip", COL_AGG, [
        ["X3", "2026-08-20", "2026-08-25", "5000000000", "-3"],
    ])
    _zip(arch / "aggiudicazioni" / "20261001-aggiudicazioni_csv.zip", COL_AGG, [
        ["X1", "2025-11-10", "2026-09-02", "120", "-1"],
        ["X4", "2026-09-30", "2026-09-30", "10", "4"],
        ["X6", "2026-09-15", "2026-09-16", "40", "6"],
    ])
    _zip(arch / "cig_annuali" / "cig_csv_2025_11.zip", COL_CIG, [
        ["X1", "80008510754", "COMUNE DI LECCE", "45000000-7", "LAVORI", "strada", "1"],
    ])
    _zip(arch / "cig" / "20261001-cig_csv.zip", COL_CIG, [
        ["X3", "80008510754", "COMUNE DI LECCE", "30199770-8", "BUONI PASTO", "buoni pasto", "1"],
        ["X4", "00559990130", "COMUNE DI MORTERONE", "45000000-7", "LAVORI", "muro", "0"],
        ["X4", "00559990130", "COMUNE DI MORTERONE", "71000000-8", "INGEGNERIA", "muro", "1"],
        ["X6", "99999999999", "ENTE NON COMUNE", "45000000-7", "LAVORI", "altro", "1"],
    ])
    return arch, data


def test_finestra():
    assert A.finestra("202610")[:2] == ("2025-10-01", "2026-09-30")
    assert A.finestra("202601")[:2] == ("2025-01-01", "2025-12-31")
    da, a, elenco = A.finestra("202610", fine="2026-02", mesi=3)
    assert (da, a, elenco) == ("2025-12-01", "2026-02-28", ["2025-12", "2026-01", "2026-02"])


def test_etl_completo(tmp_path):
    arch, data = _ambiente(tmp_path)
    out = tmp_path / "out"
    rc = A.main(["--archivio", str(arch), "--datadir", str(data), "--outdir", str(out)])
    assert rc == 0
    doc = json.loads((out / "lookup" / "anac-aggregato.json").read_text())
    assert doc["_period_files"][0] == "2025-10" and doc["_period_files"][-1] == "2026-09"
    d = doc["data"]
    assert set(d) == {"80008510754", "00559990130"}           # ente non comune escluso
    lecce = d["80008510754"]
    assert lecce["count"] == 1 and lecce["importo_totale"] == 120.0   # rettifica, data 7600 scartata
    assert lecce["count_anomalie"] == 1 and lecce["importo_anomalie"] == 5e9
    assert lecce["anomalie"][0]["cig"] == "X3"
    assert lecce["riferimento_anomalie"]["anno"] == "2025"          # anno completo, non il parziale
    mort = d["00559990130"]
    assert mort["count"] == 1 and mort["last_award_date"] == "2026-09-30"
    assert mort["top_cpv"][0]["code"] == "71000000-8"              # riga prevalente
    assert mort["riferimento_anomalie"]["tipo"] == "euro_per_abitante"  # niente SIOPE
    assert not list((out / "lookup").glob("*.part"))


def test_argomenti_ocds_accettati(tmp_path):
    arch, data = _ambiente(tmp_path)
    rc = A.main(["--archivio", str(arch), "--datadir", str(data), "--outdir", str(tmp_path / "o"),
                 "--years", "2026"])
    assert rc == 0


def test_archivio_vuoto_fallisce(tmp_path):
    (tmp_path / "vuoto").mkdir()
    assert A.main(["--archivio", str(tmp_path / "vuoto"), "--outdir", str(tmp_path / "o")]) == 1
