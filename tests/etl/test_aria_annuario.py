"""Fasce di concentrazione dell'Annuario ISPRA (aria.py), stesse classi di range_y."""
from etl.sources.aria import fascia_da_media


def test_fasce():
    assert fascia_da_media("pm10", 20.7) == "(20;30]"
    assert fascia_da_media("pm10", 15) == "(0;15]"          # estremo destro incluso
    assert fascia_da_media("pm10", 41) == "(40;Inf]"
    assert fascia_da_media("pm25", 12) == "(10;15]"
    assert fascia_da_media("no2", 5) == "(0;10]"
    assert fascia_da_media("no2", None) == ""


def test_parser_formato_snpa(tmp_path):
    """Tabella SNPA preliminare: nota in riga 0, intestazione in riga 1,
    nomi con trattino basso, superamenti 'della soglia di 50'."""
    import openpyxl

    from etl.sources.aria import parse_annuario
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["MATERIALE PARTICOLATO (PM10)\nI dati riportati sono preliminari"])
    ws.append(["id_regione", "id_provincia", "id_comune", "station_eu_code", "Regione", "Provincia",
               "Comune", "nome_stazione", "tipo_zona", "tipo_stazione",
               "Valore medio annuo [µg/m³]\n(Valore limite annuale: 40 µg/m³)",
               "Giorni di superamento della soglia di  50 µg/m³\n(valore limite giornaliero: max 35 superamenti)"])
    ws.append([16, 75, 16075035, "IT9999A", "Puglia", "Lecce", "Lecce", "Lecce - Test", "URBANA", "TRAFFICO", 18.5, 4])
    ws.append([16, 75, 16075035, "IT0000Z", "Puglia", "Lecce", "Lecce", "Senza coordinate", "URBANA", "FONDO", 10, 0])
    f = tmp_path / "snpa.xlsx"; wb.save(f)
    righe = parse_annuario(f, "pm10", 2025, {"IT9999A": (40.35, 18.17)})
    assert len(righe) == 1                                  # stazione senza coordinate scartata
    r = righe[0]
    assert (r["istat_code"], r["anno"], r["media_yy"], r["sup50"]) == ("075035", 2025, 18.5, 4)
    assert (r["tipo_combinato"], r["fascia"]) == ("UT", "(15;20]")
