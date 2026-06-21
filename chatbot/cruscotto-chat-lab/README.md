# Cruscotto Italia — Chat Lab (prototipo)

Chatbot AI conversazionale **on-premise** dedicato ai dati di Cruscotto Italia,
vincolato al perimetro skill + connettore MCP interno. Nessun servizio AI esterno.

## Architettura

Tre strati, deterministici tranne il modello:

- **Frontend** (`static/index.html`): SPA statica, streaming SSE, rendering via
  `textContent` (zero XSS), cronologia cappata. Nessuna AI nel browser.
- **Backend orchestratore** (`app.py`): FastAPI. Loop iterativo di tool calling
  via Ollama. Il modello *propone* le tool call, il backend le *valida ed esegue*.
- **LLM**: `qwen3:32b` su GPU L40S via Ollama (stack SIMBA). Unico componente
  non deterministico. `think:false`, temperature 0.1, num_ctx 16384.

Tool esposti: `search_comune`, `comune_kpi`, `anncsu_civico_search` (MCP Worker AgID)
+ `catasto_geocode` (funzione locale point-in-polygon su shard AGE).

## Controlli di sicurezza e affidabilità (verificati su campo 12/06/2026)

| Rischio | Difesa | Tipo |
|---|---|---|
| Fuori perimetro / jailbreak / deriva multi-turno | system prompt vincolante ri-ancorato | prompt |
| Codice ISTAT inventato a memoria | validazione contro lista ufficiale `istat-names.json` | deterministico |
| Comune sbagliato cross-turno | sanity check distanza punto/particelle > 2 km | deterministico |
| Narrazione del tool senza esecuzione | backstop: nudge e nuovo round | deterministico |
| Mancata disambiguazione via/piazza omonime | regola odonimo + richiesta all'utente | prompt |
| Numeri allucinati da JSON annidato | tool result KPI appiattito `chiave: valore` | deterministico |
| Numeri inventati su campi assenti | validatore numerico + rigenerazione forzata | deterministico |

Il validatore numerico (`verifica_numeri`) estrae ogni cifra significativa dalla
risposta e la confronta con i numeri presenti nei tool result del turno; se una
cifra non ha riscontro, forza una singola rigenerazione che usa solo dati reali.

## Avvio

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./start.sh   # risolve l'IP del container ollama, espone su 127.0.0.1:3010
```

In produzione: dietro nginx con basic auth dedicata (`location ^~ /chat-lab/`,
`proxy_buffering off` per SSE). Modello tenuto caldo con `keep_alive`.

## Stato

Prototipo sperimentale per test di fattibilità. NON in produzione.
TODO: guardrail tematico 3b (difesa in profondità, fase 2), rate limiting,
systemd, validatore esteso ai tool non-KPI.
