# Licenze delle fonti dati

Cruscotto Italia aggrega dati pubblicati da diverse istituzioni, ciascuna con la propria licenza. Il **codice** del progetto è AGPL-3.0, ma i **dati** mantengono le licenze originali.

| Fonte | Licenza | URL ufficiale |
|-------|---------|---------------|
| ANAC OCDS-IT | CC-BY 4.0 | https://dati.anticorruzione.it/opendata/licenza |
| OpenBDAP (incl. SIOPE) | IODL 2.0 | https://bdap-opendata.rgs.mef.gov.it/content/note-legali |
| OpenCoesione | CC-BY 4.0 | https://opencoesione.gov.it/it/note-legali/ |
| ISTAT | CC-BY 3.0 IT | https://www.istat.it/note-legali/ |
| IPA (AgID) | CC-BY 4.0 | https://indicepa.gov.it/ipa-portale/condizioni-utilizzo |
| dati.gov.it | varia per dataset | https://www.dati.gov.it/it/note-legali |

## Attribuzione

Ogni vista del frontend deve mostrare un badge "Fonte: ANAC / OpenBDAP / ..." cliccabile che porta al dataset originale.

Esempio nel footer di una tabella contratti:
> Fonte: [ANAC — Portale Open Data BDNCP](https://dati.anticorruzione.it/opendata/) — Licenza CC-BY 4.0

## Compatibilità

Tutte le licenze elencate sono **compatibili tra loro per uso aggregato** purché si fornisca attribuzione. CC-BY 4.0 è la più diffusa; IODL 2.0 (Italian Open Data License) è equivalente.

## Cosa NON è incluso

- Dati personali (RUP, persone fisiche): pubblicati solo aggregati o con CF mascherato dopo le prime 6 cifre
- Dati sotto-soglia individuali (SmartCIG): esclusi al MVP
- Dati amministrativi sensibili (es. esiti negativi gare): non pubblicati dalle fonti
