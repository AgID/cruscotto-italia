"""Fasce di concentrazione dell'Annuario ISPRA (aria.py), stesse classi di range_y."""
from etl.sources.aria import fascia_da_media


def test_fasce():
    assert fascia_da_media("pm10", 20.7) == "(20;30]"
    assert fascia_da_media("pm10", 15) == "(0;15]"          # estremo destro incluso
    assert fascia_da_media("pm10", 41) == "(40;Inf]"
    assert fascia_da_media("pm25", 12) == "(10;15]"
    assert fascia_da_media("no2", 5) == "(0;10]"
    assert fascia_da_media("no2", None) == ""
