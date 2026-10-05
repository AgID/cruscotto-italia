"""Test ANAC (05/10/2026): conversione in streaming dei bulk OCDS (3-5 GB)
e deduplica degli affidamenti ripetuti tra mesi."""
import json

import duckdb

from etl.sources import anac as A


def _pacchetto(n):
    rel = [{"ocid": f"o{i}", "id": f"r{i}", "buyer": {"id": "CF1", "name": "Comune è X"},
            "tender": {"mainProcurementCategory": "works", "procurementMethodDetails": "aperta"},
            "parties": [{"id": "x" * 50}],
            "awards": [{"id": f"a{i}", "status": "active", "date": "2025-10-01",
                        "value": {"amount": 10 * i, "currency": "EUR"},
                        "items": [{"classification": {"id": "45000000", "description": "Lavori"},
                                   "description": 'lavori { con graffe ] e "virgolette"'}]}]}
           for i in range(n)]
    return {"version": "1.1", "publishedDate": "2026-04-08", "releases": rel}


def test_json_a_righe_blocchi_minuscoli(tmp_path):
    src = tmp_path / "m.json"
    src.write_text(json.dumps(_pacchetto(25), indent=2, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "m.righe.json"
    n = A.json_a_righe(src, out, blocco=7)        # oggetti sempre a cavallo dei blocchi
    righe = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert n == 25 and len(righe) == 25
    assert righe[3]["r"]["awards"][0]["value"]["amount"] == 30
    assert "parties" not in righe[0]["r"]                       # campi non usati scartati
    assert righe[0]["r"]["buyer"]["name"] == "Comune è X"
    assert not list(tmp_path.glob("*.part"))


def test_dedup_affidamenti_tra_mesi(tmp_path):
    con = duckdb.connect()
    cols = ("ocid VARCHAR, release_id VARCHAR, buyer_cf VARCHAR, buyer_name VARCHAR, category VARCHAR, "
            "procurement_method VARCHAR, award_id VARCHAR, award_status VARCHAR, award_date VARCHAR, "
            "award_amount DOUBLE, award_currency VARCHAR, cpv_code VARCHAR, cpv_desc VARCHAR, item_description VARCHAR")
    def r(o, rel, a, dt, imp):
        return (o, rel, "CF1", "X", "works", "open", a, "active", dt, imp, "EUR", "45000000", "Lavori", "x")
    base = [r("o1", "r1", "a1", "2025-10-01", 100.0), r("o2", "r2", "a2", "2025-10-05", 50.0)]
    for nome, righe in (("2025-10", base), ("2025-11", base + [r("o1", "r9", "a1", "2025-11-20", 120.0)])):
        con.execute(f"CREATE OR REPLACE TABLE t ({cols})")
        con.executemany(f"INSERT INTO t VALUES ({','.join('?' * 14)})", righe)
        con.execute(f"COPY t TO '{tmp_path}/{nome}-awards.parquet' (FORMAT PARQUET)")
    agg = json.load(open(A.aggregate_anac(sorted(tmp_path.glob("*.parquet")), tmp_path / "out")))
    b = (agg.get("data") or agg)["CF1"]
    assert b.get("count_total", b.get("count")) == 2      # due affidamenti, non cinque
    assert b["importo_totale"] == 170.0                    # versione piu recente di a1 (120) + 50
