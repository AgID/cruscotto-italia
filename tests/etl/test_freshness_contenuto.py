"""Test dei controlli di contenuto del freshness check (05/10/2026).

Il guasto tipico trovato nell'audit: status ok, ETL che gira, dato fermo.
Questi controlli guardano il PERIODO DEL DATO e devono scattare.
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[2] / "scripts" / "etl" / "freshness_check.py"


@pytest.fixture
def fc(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("freshness_check", _P)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    return mod


def _scrivi(base, rel, dati):
    f = base / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(dati))


def _ora(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_agcom_soglia_170(fc, tmp_path):
    _scrivi(tmp_path, "agcom_bbmap/077014.json", {"_data_period": "31/12/2025"})
    assert fc.controlli_contenuto("agcom_bbmap", _ora("2026-06-19")) == []
    assert fc.controlli_contenuto("agcom_bbmap", _ora("2026-06-20"))


def test_anac_finestra_corta(fc, tmp_path):
    _scrivi(tmp_path, "lookup/anac-aggregato.json", {"_period_files": ["2026-03-awards"]})
    assert fc.controlli_contenuto("anac", _ora("2026-10-05"))
    mesi = [f"2025-{m:02d}-awards" for m in range(1, 12)] + ["2026-03-awards"]
    _scrivi(tmp_path, "lookup/anac-aggregato.json", {"_period_files": mesi})
    assert fc.controlli_contenuto("anac", _ora("2026-10-05")) == []


def test_bdap_eta_download(fc, tmp_path):
    _scrivi(tmp_path, "bdap/dettaglio/077014.json", {"_data_download": "2026-10-05"})
    assert fc.controlli_contenuto("bdap", _ora("2026-11-10")) == []
    assert fc.controlli_contenuto("bdap", _ora("2026-11-20"))


def test_turismo_anno_minimo(fc, tmp_path):
    _scrivi(tmp_path, "turismo/077014.json", {"capacita_comune": {"anno": 2024}})
    assert fc.controlli_contenuto("istat_turismo", _ora("2026-10-05")) == []
    assert fc.controlli_contenuto("istat_turismo", _ora("2027-01-15"))


def test_campo_mancante_non_rompe(fc, tmp_path):
    _scrivi(tmp_path, "turismo/077014.json", {"altro": 1})
    out = fc.controlli_contenuto("istat_turismo", _ora("2026-10-05"))
    assert out and "fallito" in out[0]


def test_aria_anno_minimo(fc, tmp_path):
    _scrivi(tmp_path, "aria/075035.json", {"_anno_dati": 2022})
    assert fc.controlli_contenuto("aria", _ora("2026-10-05"))          # il guasto di oggi
    _scrivi(tmp_path, "aria/075035.json", {"_anno_dati": 2024})
    assert fc.controlli_contenuto("aria", _ora("2026-10-05")) == []
