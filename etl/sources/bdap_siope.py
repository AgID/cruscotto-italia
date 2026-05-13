"""ETL OpenBDAP SIOPE.

Pattern dataset: spd_rnd_{spe|ent|liq}_sio_reg{NN}_01_{anno}
- spe: Spese
- ent: Entrate
- liq: Liquidità

Strategia: per anno corrente, pull di 20 regioni x {spe, ent} = 40 dataset.
Concat in un singolo Parquet `siope_{tipo}_{anno}.parquet`.

STATUS: stub v0.1. Da implementare in v0.4.

Vedi DESIGN.md § 2.2 (SIOPE è dentro BDAP).
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL BDAP SIOPE — STUB v0.1")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--target", choices=["local", "r2"], default="local")
    args = parser.parse_args()

    print(f"BDAP SIOPE ETL stub: year={args.year}, target={args.target}")
    print("Not yet implemented. See DESIGN.md § 2.2.")
    print("Roadmap: v0.4 (settimana 7-8).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
