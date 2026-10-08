"""Test di etl/lib/codici.py (migrazione ai codici ISTAT vigenti, 08/10/2026)."""
import json

import pytest

from etl.lib import codici

_TABELLA = {
    "_conteggi": {"ricodifica": 2, "incorporazione": 1, "fusione": 2},
    "variazioni": {
        "090003": {"nuovo": "112001", "tipo": "ricodifica", "decorrenza": "2026-01-01",
                   "denominazione": "Alghero", "denominazione_nuovo": "Alghero", "nota": ""},
        "092009": {"nuovo": "118006", "tipo": "ricodifica", "decorrenza": "2026-01-01",
                   "denominazione": "Cagliari", "denominazione_nuovo": "Cagliari", "nota": ""},
        "018082": {"nuovo": "018094", "tipo": "incorporazione", "decorrenza": "2026-01-31",
                   "denominazione": "Lirio", "denominazione_nuovo": "Montalto Pavese", "nota": ""},
        "024027": {"nuovo": "024129", "tipo": "fusione", "decorrenza": "2026-02-21",
                   "denominazione": "Castegnero", "denominazione_nuovo": "Castegnero Nanto", "nota": ""},
        "024071": {"nuovo": "024129", "tipo": "fusione", "decorrenza": "2026-02-21",
                   "denominazione": "Nanto", "denominazione_nuovo": "Castegnero Nanto", "nota": ""},
    },
}


@pytest.fixture
def tabella(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOKUP_DIR", str(tmp_path))
    (tmp_path / codici.NOME_FILE).write_text(json.dumps(_TABELLA))
    codici.carica(ricarica=True)
    yield tmp_path
    codici._cache = codici._cache_path = None


def test_codice_vigente(tabella):
    assert codici.codice_vigente("090003") == "112001"       # ricodifica
    assert codici.codice_vigente(90003) == "112001"          # int senza zeri
    assert codici.codice_vigente("90003.0") == "112001"      # float da pandas
    assert codici.codice_vigente("075035") == "075035"       # invariato
    assert codici.codice_vigente("024027") is None           # fusione
    assert codici.codice_vigente("018082") is None           # incorporazione
    assert codici.codice_vigente("112001") == "112001"       # gia' nuovo


def test_codice_destinazione(tabella):
    assert codici.codice_destinazione("024071") == "024129"
    assert codici.codice_destinazione("018082") == "018094"
    assert codici.codice_destinazione("092009") == "118006"
    assert codici.codice_destinazione("077014") == "077014"


def test_soppresso_e_variazione(tabella):
    assert codici.e_soppresso("024027")
    assert not codici.e_soppresso("090003")
    assert not codici.e_soppresso("075035")
    assert codici.variazione("075035") is None
    assert codici.variazione("092009")["denominazione"] == "Cagliari"


def test_rimappa_chiavi(tabella):
    out, r = codici.rimappa_chiavi({"090003": 1, "024027": 2, "024071": 3, "075035": 4})
    assert out == {"112001": 1, "075035": 4}
    assert r == {"ricodificati": 1, "scartati_soppressi": ["024027", "024071"], "invariati": 1}


def test_rimappa_collisione(tabella):
    with pytest.raises(ValueError, match="collisione su 112001"):
        codici.rimappa_chiavi({"090003": 1, "112001": 2})


def test_codice_non_valido(tabella):
    for bad in ("abc", "1234567", ""):
        with pytest.raises(ValueError):
            codici.normalizza(bad)


def test_tabella_assente(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOKUP_DIR", str(tmp_path))
    codici._cache = codici._cache_path = None
    with pytest.raises(codici.TabellaVariazioniError, match="assente"):
        codici.codice_vigente("075035")


@pytest.mark.parametrize("guasto", [
    {"variazioni": {}},
    {"variazioni": {"090003": {"nuovo": "112001", "tipo": "boh"}}},
    {"variazioni": {"090003": {"nuovo": "12", "tipo": "ricodifica"}}},
    {"variazioni": {"090003": {"nuovo": "092009", "tipo": "ricodifica"},
                    "092009": {"nuovo": "118006", "tipo": "ricodifica"}}},
    {"_conteggi": {"ricodifica": 5},
     "variazioni": {"090003": {"nuovo": "112001", "tipo": "ricodifica"}}},
])
def test_tabella_incoerente(tmp_path, monkeypatch, guasto):
    monkeypatch.setenv("LOOKUP_DIR", str(tmp_path))
    (tmp_path / codici.NOME_FILE).write_text(json.dumps(guasto))
    codici._cache = codici._cache_path = None
    with pytest.raises(codici.TabellaVariazioniError):
        codici.carica()
