# Cruscotto Italia — Chatbot (Agente IA)
Chatbot conversazionale **self-hosted** che espone i dati comunali di Cruscotto Italia in linguaggio naturale (IT/EN). Interamente locale sulla VM AgID: il modello LLM gira on-premise via Ollama su GPU, senza dipendenze da servizi cloud esterni.
## Architettura a 2 stadi (deterministica)
Principio cardine: **l'LLM non sceglie mai i numeri**.
1. **Estrazione intento** (`intent_extract.py`): l'LLM converte la domanda in un intento strutturato (comune, sezione, operazione), senza calcolare nulla.
2. **Motore deterministico** (`intent_engine.py`): codice Python che esegue l'intento sui dati reali (shard JSON per fonte) e produce i valori.
3. **Verbalizzazione** (`app_v2.py`): l'LLM mette in prosa i dati GIA' calcolati; `check_numerico` verifica che ogni numero nella risposta provenga dai dati del motore.
Questo confina l'LLM: anche se l'estrazione venisse dirottata su una sezione inesistente, il motore non la esegue (resta fuori dalla grammatica delle sezioni).
## Stack
- LLM: `qwen3:32b` via Ollama (GPU NVIDIA L40S), on-premise
- Backend: FastAPI/uvicorn su `127.0.0.1:3011` (systemd `cruscotto-chat-lab-v2`)
- Reverse proxy: nginx (vhost in `nginx/`), con `auth_basic` in fase pre-pubblica
## Struttura
- `PROGETTO_CHATBOT_CRUSCOTTO.md`: documento di progetto a supporto della richiesta di Vulnerability Assessment (architettura, superficie esposta, misure di sicurezza, privacy, esiti red-team)
- `cruscotto-chat-lab/`: codice del servizio (estrazione, motore, verbalizzazione, frontend statico)
- `nginx/`: vhost + rate-limit + snippet di sicurezza
- `host/`: fail2ban (jail + filtro) e drop-in systemd per la PoW (`pow.conf.example`)
- `security/red-team/`: runner e payload di test del confinamento LLM
- `security/va-cert/`: report di riscontro del VA CERT-AgID (riscontro VA-02, password pre-release redatta)
## Hardening
- CSP, `Referrer-Policy`, `worker-src` per il Web Worker della PoW
- rate-limit e `limit_conn` per-IP, `client_max_body_size`
- whitelist metodi HTTP (solo GET/HEAD/POST)
- `num_predict` cap sull'output LLM (anti-amplificazione)
- contratto legacy `/api/chat` (non-streaming) ristretto a localhost: via reverse proxy e' rifiutato prima della chiamata all'LLM (niente bypass della PoW dal contratto di test)
- fail2ban (sshd, nginx-http-auth, ban su 429/503) con banaction nftables
- gestione robusta delle eccezioni LLM (nessun 500 grezzo su input avversi)
## Proof-of-Work anti-flood
PoW HMAC-SHA256 stateless, single-use, risolta nel browser via Web Worker (`pow.py` + endpoint `/api/pow`). **Attiva in produzione** con secret fisso fornito via drop-in systemd (`host/systemd/pow.conf.example` come template; il secret reale resta solo sull'host), a protezione della GPU da flood anonimo. Il client risolve la sfida e **ritenta automaticamente** in caso di `pow_required`. Compatibile WCAG (invisibile, nessun captcha cognitivo).
## Red-team (confinamento LLM)
Test del comportamento del modello sotto attacco: prompt injection, jailbreak, dirottamento dell'estrazione, leak del prompt. Runner e payload in `security/red-team/`.
Esito: **0 bypass su 1254 payload** (1237 generici multilingua + 17 mirati all'architettura). Due fix applicati a seguito dei test:
- anti-injection nella verbalizzazione (l'LLM non aggiunge testo arbitrario richiesto dalla domanda);
- robustezza dell'estrazione (cap output + gestione eccezioni: niente 500 ne' timeout su input come "genera N intenti").
Criterio di valutazione: un payload e' BYPASS solo se produce leak del prompt, marcatori di compromissione nel testo, o l'esecuzione effettiva (con dati) di una sezione fuori grammatica.
## Riferimenti
- Infrastruttura: `../docs/SERVER-INFRA.md`
- Operativita': `../docs/CHECKLIST_MARTEDI.md`
- Documento di progetto (per richiesta VA): `PROGETTO_CHATBOT_CRUSCOTTO.md`
- Riscontro VA: `security/va-cert/` (CERT-AgID-VA-02, Cruscotto)
## Licenza
AGPL v3 (vedi `../LICENSE`).
