"""ETL OpenBDAP MOP (Monitoraggio Opere Pubbliche).

Strategy: pull mensile del CSV unificato "Progetti Opere Pubbliche MOP - Totale"
(esiste dal nov 2024). Fallback per regione se il totale fallisce.

STATUS: stub v0.1. Da implementare in v0.3.

Plan:
    1. CKAN package_search su BDAP per "MOP Totale"
    2. Get the dataset UUID via package_list workaround (bug DKAN sul count)
    3. Download CSV via /api/3/datastore/dump/{UUID}.csv
    4. Trasforma in Parquet
    5. Index inverso per CUP, CF ente, codice ISTAT comune

Vedi DESIGN.md § 2.2.
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL BDAP MOP — STUB v0.1")
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    args = parser.parse_args()

    print(f"BDAP MOP ETL stub: target={args.target}")
    print("Not yet implemented. See DESIGN.md § 2.2.")
    print("Roadmap: v0.3 (settimana 5-6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
