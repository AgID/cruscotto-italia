# Documento di progetto — Chatbot Cruscotto Italia (`cruscotto-chat-lab`)

**Oggetto:** chatbot conversazionale di Cruscotto Italia
**Asset:** `https://cruscotto-italia.dati.gov.it/chatbot/` — IP `89.97.159.17`
**Ambiente:** VM AgID on-premise (GPU NVIDIA L40S); modello linguistico eseguito localmente
**Stato:** pre-go-live pubblico — l'accesso al chatbot è attualmente protetto da Basic Auth a livello di reverse proxy
**A cura di:** Francesco Piero Paolicelli — sviluppo e gestione Cruscotto Italia
**Destinatari:** CERT-AgID; Antonio Romano (AgID, referente privacy); Antonio Rotundo (AgID, service owner)
**Data:** 21/06/2026

> Documento descrittivo a supporto della richiesta di Vulnerability Assessment del chatbot. Non contiene segreti (chiavi, credenziali, parametri di configurazione sensibili) né dettagli sfruttabili come mappa di evasione dei controlli.

---

## 1. Descrizione del servizio

Il chatbot è l'interfaccia conversazionale di Cruscotto Italia: risponde in linguaggio naturale (italiano e inglese) a domande su **dati pubblici aggregati per comune** (popolazione, censimento, opere PNRR/BDAP, ambiente ISPRA, sismica DPC, imprese, scuole, veicoli, farmacie e ospedali, beni culturali, e altre fonti istituzionali), per i ~7.900 comuni italiani.

L'ambito è **strettamente di dominio**: il servizio è progettato per fornire esclusivamente dati presenti nelle fonti aggregate dalla piattaforma e non per generare contenuti liberi fuori contesto.

---

## 2. Architettura tecnica

Il chatbot adotta una pipeline a **tre stadi** con una netta separazione tra comprensione del linguaggio e produzione dei dati:

1. **Estrazione dell'intento (LLM):** un modello linguistico converte la domanda dell'utente in un **intento strutturato** (sezione, campo, dimensioni, comune). Il modello *non* produce i valori numerici della risposta.
2. **Motore deterministico (Python):** un motore applicativo esegue l'intento, recupera i dati dalle fonti locali ed effettua tutti i calcoli. Tutti i numeri provengono da questo stadio.
3. **Verbalizzazione (LLM) + consegna (SSE):** l'LLM riformula in linguaggio naturale il risultato **già calcolato** dal motore; la risposta è trasmessa in streaming via Server-Sent Events.

Il principio cardine è che **i numeri non sono mai affidati al modello linguistico**: l'LLM interpreta e verbalizza, il codice Python esegue e calcola. Un controllo dedicato (`check_numerico`) verifica che i valori presentati corrispondano a quelli prodotti dal motore.

**Modello linguistico on-premise.** Il modello (qwen3:32b via Ollama) è eseguito **localmente** sulla VM AgID con GPU NVIDIA L40S. Nessun dato della conversazione è inviato a servizi terzi o esterni.

**Stack.** Applicazione Python (FastAPI) gestita da systemd, esposta unicamente su loopback; reverse proxy nginx come unico punto di ingresso pubblico. La fonte dati è costituita da shard locali per-fonte sul filesystem della VM.

---

## 3. Superficie esposta

L'accesso pubblico avviene esclusivamente attraverso il reverse proxy, sotto il path `/chatbot/`. Gli endpoint applicativi sono:

- **`/api/chat`** — endpoint conversazionale. Il contratto di produzione è basato su streaming SSE, soggetto ai controlli di concorrenza e anti-flood descritti al §4.
- **`/api/pow`** — emissione delle sfide di proof-of-work (vedi §4).

Esiste un **contratto legacy** (richiesta non-streaming) usato unicamente dagli strumenti di test interni: è **ristretto al solo accesso da loopback** e viene rifiutato quando la richiesta proviene dal reverse proxy, senza raggiungere il modello linguistico.

L'applicazione è eseguita su loopback e non è raggiungibile direttamente dall'esterno; ogni richiesta transita per il reverse proxy.

---

## 4. Misure di sicurezza

**Confinamento di dominio.** La separazione comprensione/esecuzione (§2) garantisce che la risposta finale sia costruita su dati del motore e non su testo libero del modello. Le domande fuori dominio non producono dati: il motore non esegue intenti al di fuori della propria grammatica.

**Robustezza anti-prompt-injection.** L'architettura a due stadi limita strutturalmente l'impatto di tentativi di manipolazione: lo stadio di verbalizzazione contiene una regola esplicita che ignora istruzioni volte ad aggiungere, ripetere o terminare la risposta con testo arbitrario; il controllo `check_numerico` impedisce l'alterazione dei valori. Lo stadio di estrazione dell'intento gestisce in modo controllato input anomali, timeout e tentativi di amplificazione, degradando a un rifiuto pulito.

**Anti-flood a protezione della GPU.** L'endpoint conversazionale richiede la risoluzione di un **proof-of-work** (HMAC-SHA256, difficoltà configurabile, sfida a uso singolo con scadenza) calcolato dal client prima di impegnare il modello. La misura innalza il costo di un flood automatizzato verso una risorsa computazionalmente onerosa (GPU), senza ostacolare l'uso legittimo.

**Controllo della concorrenza.** Il backend serve **una sola elaborazione alla volta** verso il modello, con una coda FIFO per le richieste in attesa: questo evita la saturazione della GPU e rende prevedibile il carico. È disponibile un'interruzione esplicita lato utente (Stop) che rilascia le risorse server-side.

**Hardening del reverse proxy.** Gli aspetti di trasporto e di superficie HTTP (CSP, restrizione dei metodi, normalizzazione delle risposte, gestione delle directory) sono trattati a livello di reverse proxy; gli interventi corrispondenti sono documentati nel report di riscontro del VA-02 della piattaforma (cfr. §7).

**Accesso pre-go-live.** In fase di pre-rilascio l'intero chatbot è protetto da Basic Auth; le credenziali amministrative dei pannelli statistici sono separate da quelle di blindatura del sito.

---

## 5. Dati trattati e privacy

- Il chatbot espone **esclusivamente dati pubblici aggregati** per comune, già pubblicati dalla piattaforma a partire da fonti istituzionali. Non tratta dati personali degli utenti.
- Le conversazioni **non sono persistite** lato server come profili utente; non sono inviate a terzi.
- Le metriche di utilizzo sono **anonime**: nei punti dato analitici non sono inclusi indirizzi IP.

---

## 6. Test di sicurezza interni

Prima della richiesta di VA è stata eseguita una campagna di **red-team** interna sull'endpoint conversazionale, con un corpus di **1.254 payload** (generici e mirati: prompt-injection, tentativi di evasione del dominio, richieste di esecuzione di istruzioni arbitrarie, sollecitazioni fuori ambito).

Esito: **0 bypass** del confinamento di dominio. La campagna ha inoltre prodotto due irrobustimenti applicativi (regola anti-injection nello stadio di verbalizzazione; gestione di timeout/anomalie nello stadio di estrazione dell'intento), entrambi verificati.

Gli script e i payload della campagna sono versionati nel repository (vedi §7).

---

## 7. Limiti noti

- **Query di intersezione cross-sezione** non supportate: a fronte di una richiesta che combina più sezioni, il sistema esegue gli intenti separatamente e ne affianca i risultati, senza join.
- I dati della fonte **ANAC** sono trattati in forma **aggregata**, non a livello di singolo record.

---

## 8. Riferimenti

- **Snapshot del codice e documentazione di sicurezza:** `chatbot/` in `AgID/cruscotto-italia-internal` (repository privato AgID).
- **Campagna red-team:** `chatbot/security/red-team/`.
- **Report di riscontro VA-02 della piattaforma** (include l'hardening del reverse proxy che copre anche il chatbot): `chatbot/security/va-cert/`.
- **Asset correlato:** worker MCP `cruscotto-italia-mcp.agid.workers.dev` (sorgente dati della piattaforma), oggetto del VA-02.
