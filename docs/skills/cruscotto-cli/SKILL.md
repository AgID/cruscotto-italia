---
name: cruscotto-cli
version: 0.1.3
description: Interroga via CLI i dati aperti di Cruscotto Italia (AgID) per tutti i comuni italiani, leggendo direttamente gli shard JSON statici senza passare dal server MCP. Usa questa skill quando servono dati ufficiali su un comune italiano - popolazione, demografia, censimento 2021, redditi, veicoli, scuole, sanita', opere pubbliche, cassa SIOPE (pagamenti e incassi), appalti, PNRR, turismo, qualita' dell'aria, morfologia, beni culturali, civici e toponomastica, carburanti, banda larga, terzo settore, immobili pubblici, pendolarismo, meteo - oppure quando si citano fonti come ISTAT, BDAP, SIOPE, ANAC, ISPRA, MEF, MIUR, ACI, ANNCSU, AGCOM, MIMIT, RUNTS, MiC, Ministero della Salute. Copre 7896 comuni, 31 sezioni tematiche piu' tre archivi estesi (beni culturali, civici, censimento per sezione). Preferibile all'MCP quando serve una query mirata su pochi campi, un confronto tra comuni o un ranking, perche' filtra i dati prima di caricarli in contesto ed evita di leggere shard da centinaia di KB.
license: CC-BY-4.0
---

# Cruscotto Italia - CLI

Interrogazione degli shard JSON di Cruscotto Italia (AgID). Nessuna dipendenza esterna: solo `python3` stdlib.

## Configurazione

```bash
# default: HTTPS pubblico (funziona ovunque)
export CRUSCOTTO_BASE=https://cruscotto-italia.dati.gov.it/data

# sulla VM AgID: filesystem locale, zero rete
export CRUSCOTTO_BASE=/var/www/cruscotto-italia/data
```

Opzionali: `CRUSCOTTO_CACHE` (default `/tmp/cruscotto-cache`), `CRUSCOTTO_TTL` (86400 s), `CRUSCOTTO_CAP` (20000 byte di output massimo).

Protezione del server, da non modificare per aggirare un blocco: `CRUSCOTTO_MIN_INTERVAL` (0.5 s fra le richieste), `CRUSCOTTO_MAX_COMUNI` (12 comuni distinti), `CRUSCOTTO_WINDOW` (600 s di finestra).

## Uso

Tutti i comandi accettano il nome del comune o il codice ISTAT a 6 cifre. In caso di omonimia lo script elenca i candidati e si ferma.

```bash
python3 scripts/cruscotto.py <comando> ...
```

### Percorso consigliato

1. `kpi` per la sintesi (costo minimo)
2. `sezioni` per vedere cosa esiste e quanto pesa
3. `sez` o `q` per il dettaglio mirato

```bash
python3 scripts/cruscotto.py find matera            # nome -> codice ISTAT
python3 scripts/cruscotto.py kpi Matera             # anagrafica + kpi_summary
python3 scripts/cruscotto.py sezioni 077014         # elenco sezioni + peso in byte
python3 scripts/cruscotto.py sez 077014 aria        # una sezione intera
python3 scripts/cruscotto.py sez 077014 opere --top 5   # sezione pesante, liste troncate
python3 scripts/cruscotto.py q 077014 redditi.anni[0]   # path dot-notation
```

### Archivi estesi

```bash
# beni culturali (MiC ArCo + Cultural-ON)
python3 scripts/cruscotto.py full 077014 beni --kpi
python3 scripts/cruscotto.py full 077014 beni --categoria chiesa --top 10
python3 scripts/cruscotto.py full 077014 beni --find "archivio" --desc

# civici e toponomastica (ANNCSU)
python3 scripts/cruscotto.py full 077014 anncsu --kpi
python3 scripts/cruscotto.py full 077014 anncsu --via "via lucana" --civico 53

# censimento 2021 per sezione (119 variabili)
python3 scripts/cruscotto.py full 077014 censimento --kpi
python3 scripts/cruscotto.py full 077014 censimento --sez 388
python3 scripts/cruscotto.py full 077014 censimento --var ST19 --den ST1 --min-den 30 --top 10
python3 scripts/cruscotto.py vars stranieri         # cerca nel dizionario delle variabili
```

## Regole d'uso

- **Sezioni pesanti** (`anncsu`, `opere`, `siope`, `immobili_pa`, `runts`, `scuole`): `sez` rifiuta il dump e richiede `--top N`, oppure usa `q` con un path.
- **Archivi `full`**: un filtro e' sempre obbligatorio (`--kpi` e' il piu' economico). Non esiste il dump completo.
- **Output troncato**: superati i 20 KB l'output viene tagliato con avviso. Non e' un errore: restringi la query.
- **Ranking percentuali** (`--var` con `--den`): senza `--min-den` la classifica e' dominata da sezioni con denominatore minimo (es. 2 stranieri su 2 = 100%). Usa `--min-den 30` sui comuni medio-grandi, valori piu' bassi sui comuni piccoli. Lo script segnala quante sezioni ha scartato.
- **Denominatori validati**: lo script accetta solo rapporti fra variabili effettivamente comparabili, secondo un registro esplicito verificato sui dati. La base corretta non e' sempre `P1`: il titolo di studio (`P86`-`P90`) si rapporta a `P83` (popolazione 9 anni e piu'), non alla popolazione totale. Un rapporto fuori registro viene rifiutato con l'indicazione del denominatore corretto. Alcune variabili (`P101`-`P103` occupati, `IT1`-`IT9` italiani, `E3` edifici) non hanno alcun denominatore lecito fra le 119 variabili. `--force-den` aggira il controllo ma marca l'output con un avviso: usalo solo se la comparabilita' e' stata verificata altrove.
- **Variabili censuarie**: i codici ISTAT non sono parlanti. Usa `vars` per cercare per descrizione. Attenzione: la terminologia e' quella ISTAT (es. "terziario", non "laureati").
- **Sezioni senza dati**: circa un terzo delle sezioni di censimento e' non residenziale (parchi, aree industriali) e viene escluso automaticamente dai ranking.

## Uso responsabile del server

- La skill interroga un server pubblico con un limite di richieste attivo: superarlo fa **bloccare l'indirizzo IP per un'ora**.
- **Non esistono dati aggregati** per regione, provincia o area nazionale: la skill lavora comune per comune. Medie territoriali, classifiche regionali e simili **non vanno ricostruite** scaricando tutti i comuni di un territorio.
- Se l'utente chiede un aggregato territoriale, rispondi che il dato non e' disponibile in questa forma. Non stimarlo e non calcolarlo con uno scaricamento di massa.
- **Non proporre approfondimenti che richiederebbero dati inesistenti**, ad esempio «confronto con la media regionale»: suggerire un'analisi impossibile porta poi a tentare lo scaricamento di massa.
- I confronti fra pochi comuni indicati dall'utente sono l'uso previsto e non pongono alcun problema.
- Lo script impone da se' un intervallo minimo fra le richieste e si ferma oltre un certo numero di comuni distinti in dieci minuti. Se il blocco scatta, **riferiscilo all'utente**: non alzare `CRUSCOTTO_MAX_COMUNI` e non aggirare il limite in altro modo.

## Risoluzione problemi

- **`CERTIFICATE_VERIFY_FAILED`**: l'installazione di Python non trova i certificati delle autorita'. Non e' un problema del server. Nell'ordine: su macOS con Python da python.org eseguire `Install Certificates.command` nella cartella `/Applications/Python 3.x/`; oppure `pip3 install certifi`, che lo script usa automaticamente se presente; oppure, dietro proxy aziendale, `export SSL_CERT_FILE=/percorso/ca.pem`.
  **Non disattivare la verifica del certificato.** `PYTHONHTTPSVERIFY=0`, `ssl.CERT_NONE` e simili non sono soluzioni ammesse: i dati sono pubblici, ma il canale va comunque autenticato. Se il problema non si risolve, riferirlo all'utente invece di aggirarlo.
- **`HTTP 404`**: il comune non ha dati per quella fonte, oppure il codice ISTAT e' errato. Verifica con `find`.
- **Non costruire URL a mano.** I percorsi degli shard non vanno indovinati: usa i comandi della skill, che li compongono correttamente. Se un comando non copre quello che serve, dillo invece di improvvisare una chiamata HTTP.
- **Output troncato**: non e' un errore. Restringi la query con un filtro, `--top N` o il comando `q`.

## Limiti noti

- Il catasto (`catasto_full`) non e' esposto: sono geometrie pure, il point-in-polygon resta lato client per vincolo CERT-AgID.
- Gli aggregati nazionali ANAC e BDAP (indicizzati per codice fiscale, oltre 10 MB) non sono coperti: niente ranking nazionali.
- Questa skill non sostituisce il server MCP `cruscotto-italia-mcp`, che resta il canale per i client AI esterni.

## Riferimenti

- `references/SEZIONI.md` - cosa contiene ogni sezione, con la fonte istituzionale
- `references/censimento_vars.json` - le 119 variabili censuarie con la descrizione
