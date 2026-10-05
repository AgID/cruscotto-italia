"""Test dell'helper SDMX ISTAT (etl/lib/istat_sdmx.py).

Protezioni introdotte il 05/10/2026 dopo il blocco IP della VM: limite di
5 query/minuto, blocchi da 35 codici, nessun nuovo tentativo sui 4xx,
download riprendibile. Rete sempre simulata.
"""
import pytest
import requests

from etl.lib import istat_sdmx as I


class _Risposta:
    def __init__(self, testo="", stato=200):
        self.status_code = stato
        self.content = testo.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture(autouse=True)
def _senza_attese(monkeypatch, tmp_path):
    monkeypatch.setattr(I.time, "sleep", lambda s: None)
    monkeypatch.setattr(I, "_FILE_TURNO", tmp_path / "turno")


def test_chiave_con_codici():
    assert I.chiave_con_codici("A..........", ["075035", "058091"]) == "A.075035+058091........."
    assert I.chiave_con_codici("A..VEHICFLEET.", ["075035"]) == "A.075035.VEHICFLEET."
    with pytest.raises(ValueError):
        I.chiave_con_codici("A.IT..", ["075035"])


def test_blocco_predefinito_35():
    import inspect
    assert inspect.signature(I.scarica_a_blocchi).parameters["blocco"].default == 35


def _fake(registro, guasti=()):
    def get(url, **k):
        codici = url.split("/A.")[1].split(".")[0]
        registro.append(codici)
        if any(codici.startswith(g) for g in guasti):
            raise requests.ConnectionError("rete giu")
        righe = "\n".join(f"{c},1" for c in codici.split("+"))
        return _Risposta("REF_AREA,OBS_VALUE\n" + righe + "\n")
    return get


def test_ripresa_dopo_interruzione(monkeypatch, tmp_path):
    out = tmp_path / "x.csv"
    registro = []
    monkeypatch.setattr(I.requests, "get", _fake(registro, guasti=("c3",)))
    with pytest.raises(requests.ConnectionError):
        I.scarica_a_blocchi("B", "A...", 2024, 2024, ["c1", "c2", "c3", "c4", "c5"], out, blocco=2)
    assert not out.exists()
    registro.clear()
    monkeypatch.setattr(I.requests, "get", _fake(registro))
    I.scarica_a_blocchi("B", "A...", 2024, 2024, ["c1", "c2", "c3", "c4", "c5"], out, blocco=2)
    assert registro == ["c3+c4", "c5"]            # il primo blocco e riusato
    testo = out.read_text()
    assert testo.count("REF_AREA") == 1 and testo.count("\n") == 6
    assert not list(tmp_path.glob("x.csv.blocchi-*"))


def test_errore_4xx_senza_nuovi_tentativi(monkeypatch, tmp_path):
    chiamate = []
    monkeypatch.setattr(I.requests, "get", lambda url, **k: chiamate.append(url) or _Risposta(stato=400))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        I.scarica_a_blocchi("B", "A...", 2024, 2024, ["c1"], tmp_path / "y.csv")
    assert len(chiamate) == 1
    assert not (tmp_path / "y.csv").exists()


def test_limitatore_intervallo_minimo(monkeypatch):
    istanti = []
    monkeypatch.setattr(I.time, "sleep", lambda s: istanti.append(("sleep", s)))
    monkeypatch.setattr(I, "INTERVALLO_MIN_S", 13.0)
    I.attendi_turno_istat()
    I.attendi_turno_istat()                       # subito dopo: deve attendere
    attese = [s for tipo, s in istanti if tipo == "sleep"]
    assert attese and attese[-1] > 12.0
