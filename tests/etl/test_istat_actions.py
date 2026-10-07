"""Pipeline ISTAT su GitHub Actions: partizione, unione, --solo-cache."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from etl.sources import istat_turismo as T

_spec = importlib.util.spec_from_file_location(
    "istat_actions", Path(__file__).resolve().parents[2] / "scripts/etl/istat_actions.py")
A = importlib.util.module_from_spec(_spec)
sys.modules["istat_actions"] = A
_spec.loader.exec_module(A)

CODICI = [f"{i:06d}" for i in range(1, 101)]


def test_parti_coprono_tutti_i_comuni_una_volta(monkeypatch, tmp_path):
    visti = []
    monkeypatch.setattr(A.T, "codici_comuni", lambda: CODICI)
    monkeypatch.setattr(A, "scarica_a_blocchi",
                        lambda base, chiave, a0, a1, codici, out, **kw: visti.extend(codici))
    for parte in range(1, A.PARTI["incidenti"] + 1):
        A.cmd_scarica(SimpleNamespace(dataset="incidenti", parte=parte,
                                      anno_cap=2025, anno_parco=2024, anno_inc=2024,
                                      out=str(tmp_path)))
    assert sorted(visti) == CODICI


def _scrivi_parti(d: Path, anno_cap: int, anno_fl: int, meno: int = 0):
    for nome, spec in A.specifiche(anno_cap, 2025, 2025).items():
        for k in range(1, A.PARTI[nome] + 1 - meno):
            p = d / f"parte-{k}" / f"{spec['file']}.parte{k:02d}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"H1,H2\nr{k},1\nr{k},2\n", encoding="utf-8")
    (d / "prov").mkdir()
    (d / "prov" / f"flussi_{anno_fl}.csv").write_text("H\nx\n", encoding="utf-8")
    (d / "prov" / "itter107.xml").write_text("<x/>", encoding="utf-8")


def test_unisci_una_intestazione_e_manifest(tmp_path):
    _scrivi_parti(tmp_path / "parti", 2025, 2025)
    A.cmd_unisci(SimpleNamespace(dir=str(tmp_path / "parti"), out=str(tmp_path / "raw"),
                                 anno_cap=2025, anno_fl=2025,
                                 anno_parco=2025, anno_inc=2025))
    m = json.loads((tmp_path / "raw/manifest.json").read_text())
    assert m["anno_cap"] == 2025
    assert {f["nome"] for f in m["file"]} >= {"capacita_2025.csv", "flussi_2025.csv", "itter107.xml"}
    cap = (tmp_path / "raw/capacita_2025.csv").read_text().splitlines()
    assert cap[0] == "H1,H2" and cap.count("H1,H2") == 1
    assert len(cap) == 1 + 2 * A.PARTI["capacita"]
    assert all(f["righe"] == 2 * A.PARTI[n] for n, f in zip(A.PARTI, m["file"]))


def test_unisci_rifiuta_parti_mancanti(tmp_path):
    _scrivi_parti(tmp_path / "parti", 2025, 2025, meno=1)
    with pytest.raises(SystemExit):
        A.cmd_unisci(SimpleNamespace(dir=str(tmp_path / "parti"), out=str(tmp_path / "raw"),
                                     anno_cap=2025, anno_fl=2025,
                                     anno_parco=2025, anno_inc=2025))


def test_turismo_anni_da_cache(tmp_path):
    for n in ("capacita_2024.csv", "capacita_2025.csv", "flussi_2025.csv", "itter107.xml"):
        (tmp_path / n).write_text("x")
    T.anni_da_cache(tmp_path)
    assert (T.ANNO_CAP, T.ANNO_FL) == (2025, 2025)
    assert T.DATAFLOWS[0]["year_start"] == 2025


def test_turismo_solo_cache_senza_file(tmp_path):
    with pytest.raises(SystemExit):
        T.anni_da_cache(tmp_path)


def test_veicoli_solo_cache_non_scarica(monkeypatch, tmp_path):
    from etl.sources import veicoli as V
    monkeypatch.setattr(V, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(V, "SOLO_CACHE", True)
    monkeypatch.setattr(V, "scarica_a_blocchi", lambda *a, **k: pytest.fail("rete"))
    with pytest.raises(FileNotFoundError):
        V.fetch_istat_parco(anno=2024)
    with pytest.raises(FileNotFoundError):
        V.fetch_istat_incidenti(anni=[2020, 2024])


def test_incidenti_finestra_mobile():
    spec = A.specifiche(2025, 2025, 2025)
    assert spec["incidenti"]["anni"] == (2021, 2025)
    assert spec["incidenti"]["file"] == "istat_41_983_incidenti_2021_2025.csv"
    assert spec["parco"]["file"] == "istat_41_993_parco_2025.csv"


def test_veicoli_anni_da_cache(monkeypatch, tmp_path):
    from etl.sources import veicoli as V
    for n in ("istat_41_993_parco_2024.csv", "istat_41_993_parco_2025.csv",
              "istat_41_983_incidenti_2020_2024.csv", "istat_41_983_incidenti_2021_2025.csv"):
        (tmp_path / n).write_text("x")
    monkeypatch.setattr(V, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(V, "ANNO_PARCO", V.ANNO_PARCO)
    monkeypatch.setattr(V, "ANNI_INCIDENTI", V.ANNI_INCIDENTI)
    V.anni_da_cache()
    assert V.ANNO_PARCO == 2025
    assert V.ANNI_INCIDENTI == [2021, 2022, 2023, 2024, 2025]
