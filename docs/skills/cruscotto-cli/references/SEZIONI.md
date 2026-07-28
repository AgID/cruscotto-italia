# Sezioni del dashboard shard

Ogni comune ha uno shard `dashboard/<istat>.json` con 31 chiavi top-level.
I pesi indicati sono quelli di Matera (077014, 59k abitanti): scalano con la dimensione del comune.

## Chiavi di servizio

| Chiave | Contenuto |
|---|---|
| `_etl_version` | versione della pipeline ETL |
| `_generated_at` | timestamp di generazione dello shard |
| `_missing` | elenco delle fonti senza dati per questo comune |

## Sezioni leggere (sotto i 10 KB)

| Chiave | Fonte | Contenuto | Peso |
|---|---|---|---|
| `anagrafica` | ISTAT + IPA | denominazione, provincia, regione, codice fiscale, codice catastale, codice IPA | 0.3 KB |
| `meteo` | ItaliaMeteo | stazione di riferimento e dati sintetici | 0.4 KB |
| `morfologia` | CNR-IRPI (HR-DTM 5m) | quota, pendenza, esposizione, classi altimetriche | 0.7 KB |
| `agcom_bbmap` | AGCOM | copertura banda larga e FTTH | 0.9 KB |
| `profilo` | ISTAT | profilo sintetico del comune | 1.2 KB |
| `pendolarismo` | ISTAT (Censimento 2021) | flussi casa-lavoro e casa-studio | 1.2 KB |
| `censimento` | ISTAT (Variabili censuarie 2023 su Basi Territoriali 2021) | KPI censuari aggregati a livello comunale | 1.3 KB |
| `veicoli` | ACI | parco veicolare, classi Euro, alimentazioni | 1.5 KB |
| `turismo` | ISTAT | capacita' ricettiva e movimento turistico | 1.5 KB |
| `territorio` | ISPRA + DPC | consumo di suolo, dissesto idrogeologico, rifiuti, classificazione sismica | 4.1 KB |
| `aria` | ISPRA SNPA | stazioni di monitoraggio, PM10, PM2.5, NO2, limiti di legge e OMS | 4.1 KB |
| `demografia` | ISTAT POSAS | popolazione per eta' e sesso, serie storica | 5.0 KB |
| `redditi` | MEF | redditi dichiarati per fascia, patrimonio immobiliare | 6.9 KB |
| `carburanti` | MIMIT | impianti di distribuzione e prezzi | 8.1 KB |

## Sezioni medie (10-30 KB)

| Chiave | Fonte | Contenuto | Peso |
|---|---|---|---|
| `asia` | ISTAT ASIA | imprese attive per settore e classe dimensionale | 11.6 KB |
| `sanita_mds` | Ministero della Salute | farmacie, parafarmacie, strutture sanitarie con coordinate | 12.0 KB |
| `beni_culturali` | MiC (ArCo + Cultural-ON) | sintesi dei luoghi della cultura | 14.6 KB |
| `pun` | GSE | punti di ricarica per veicoli elettrici | 19.2 KB |
| `pnrr` | ReGiS / OpenPNRR | progetti PNRR con importi e stato di avanzamento | 24.4 KB |

## Sezioni pesanti (dump vietato, richiedono `--top` o `q`)

| Chiave | Fonte | Contenuto | Peso |
|---|---|---|---|
| `scuole` | MIUR | anagrafe scolastica, edifici, alunni | 31.0 KB |
| `runts` | RUNTS | enti del terzo settore | 48.8 KB |
| `immobili_pa` | MEF (Patrimonio PA) | immobili di proprieta' pubblica | 56.4 KB |
| `siope` | MEF-RGS SIOPE (siope.it) | pagamenti e incassi per codice gestionale, per anno, con saldo di cassa | 104 KB |
| `opere` | BDAP-MOP | progetti di opere pubbliche, CUP, costi, finanziamenti | 104.3 KB |
| `anncsu` | ANNCSU | strade e numeri civici (sintesi) | 117.8 KB |

## Sezioni derivate

| Chiave | Contenuto |
|---|---|
| `anac` | ANAC: appalti e contratti, aggregati per CPV |
| `bdap_kpi` | indicatori sintetici di bilancio da BDAP |
| `kpi_summary` | cruscotto sintetico trasversale a tutte le fonti |

## Archivi estesi (fuori dal dashboard)

Accessibili solo con il comando `full`, sempre con filtro obbligatorio.

| Archivio | Percorso | Contenuto | Peso |
|---|---|---|---|
| `beni` | `beni_culturali_full/<istat>.json` | 215 luoghi per Matera: denominazione, categoria, coordinate, descrizione | 184 KB |
| `anncsu` | `anncsu_full/<istat>.json` | 17.720 civici per Matera: odonimo, civico, coordinate, metodo di georeferenziazione | 1.8 MB |
| `censimento` | `censimento_full/<istat>.geojson` | 623 sezioni di censimento per Matera, 127 variabili ciascuna | 1.3 MB |

Non esposto: `catasto_full` (geometrie catastali, 7.2 MB compressi) - il point-in-polygon resta lato client per vincolo CERT-AgID.

---

# Denominatori leciti per le variabili censuarie

Registro verificato sui dati: in ogni riga la somma delle parti coincide con il totale.
Il comando `full <comune> censimento --var X --den Y` accetta solo le coppie qui elencate.

| Variabili | Denominatore | Significato |
|---|---|---|
| `P2`, `P3` | `P1` | maschi e femmine sul totale |
| `P14`-`P29` | `P1` | fasce d'eta' quinquennali sul totale |
| `P30`-`P45` | `P2` | fasce d'eta' maschi |
| `P67`-`P82` | `P3` | fasce d'eta' femmine |
| `P83` | `P1` | popolazione 9+ sul totale |
| `P84` | `P2` | popolazione 9+ maschi |
| `P85` | `P3` | popolazione 9+ femmine |
| `P86`-`P90` | `P83` | titolo di studio sulla popolazione 9 anni e piu' |
| `P91`-`P95` | `P84` | titolo di studio maschi |
| `P96`-`P100` | `P85` | titolo di studio femmine |
| `ST1` | `P1` | stranieri sulla popolazione |
| `ST2`, `ST2_B`, `ST3`-`ST5`, `ST16`, `ST19`, `ST22`-`ST24` | `ST1` | ripartizioni degli stranieri |
| `ST17`, `ST18` | `ST16` | stranieri UE per sesso |
| `ST20`, `ST21` | `ST19` | stranieri extra-UE per sesso |
| `ST25`-`ST27` | `ST2` | stranieri maschi per fascia d'eta' |
| `ST28`-`ST30` | `ST2_B` | stranieri femmine per fascia d'eta' |
| `ST31` | `ST23` | tasso di occupazione stranieri 15-64 |
| `ST32` | `ST26` | occupati stranieri maschi |
| `ST33` | `ST29` | occupati stranieri femmine |
| `IT10` | `IT2` | tasso di occupazione italiani 15-64 |
| `IT11` | `IT5` | occupati italiani maschi |
| `IT12` | `IT8` | occupati italiani femmine |
| `PF3`-`PF8` | `PF1` | famiglie per numero di componenti |
| `A2`, `A3` | `A8` | abitazioni occupate e vuote sul totale |

## Senza denominatore lecito

Queste variabili non hanno un totale di riferimento fra le 127 disponibili:

- `P101`-`P103` (occupati 15-64): la base sarebbe la popolazione 15-64, che non esiste come variabile singola (andrebbe ricostruita come `IT2 + ST23`)
- `IT1`-`IT9` (italiani per fascia d'eta'): non esiste una variabile "italiani totali" (sarebbe `P1 - ST1`)
- `NA1` (automobili di proprieta'): non esiste un totale veicoli fra le variabili censuarie

Per queste il comando rifiuta il rapporto. Il flag `--force-den` lo consente comunque, ma l'output riporta un avviso esplicito di comparabilita' non garantita.
