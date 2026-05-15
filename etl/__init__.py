"""Cruscotto Italia ETL package.

Modules:
    sources/    - one file per data source. 19 dataset / 15 istituzioni:
                  anac, bdap, siope, pnrr_progetti, demografia, istat_profilo,
                  istat_turismo, territorio, aria, scuole, veicoli, redditi,
                  immobili_pa, anncsu, sanita_mds, pun, agcom_bbmap,
                  carburanti, runts, anagrafica, dashboard.
    lib/        - shared helpers (r2 client, duckdb wrapper, manifest)
"""

__version__ = "0.1.0"
