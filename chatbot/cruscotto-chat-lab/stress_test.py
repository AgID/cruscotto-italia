import asyncio
from intent_extract import estrai_intento

OLLAMA = "http://172.18.0.8:11434"
MODELS = ["qwen3:32b", "llama3.1:8b"]

# (domanda_in_linguaggio_libero, contesto_prec)  -- ctx vuoto = domanda isolata
CASI = [
    ("ma quanti vecchi ci stanno a Lecce?", ""),
    ("quanta gente abita a Morterone", ""),
    ("l'aria com'è messa a Milano?", ""),
    ("fammi vedere le farmacie di Roma", ""),
    ("quante macchine ci sono a Torino", ""),
    ("soldi spesi per i rifiuti a Bari", ""),
    ("imprese a Morterone", ""),
    ("il reddito a Potenza", ""),
    ("quanti turisti vanno a Lecce", ""),
    ("appalti pubblici a Napoli", ""),
    ("la fibra a Matera com'è?", ""),
    ("scuole superiori a Lecce", ""),
    ("incidenti a Roma negli ultimi anni", ""),
    ("quanti stranieri a Prato", ""),
    ("comune di Lecce codice catastale", ""),
    ("quanto è inquinata l'aria a Taranto", ""),
    ("qunti abitnti a Lecce", ""),                                   # refusi
    ("e per i rifiuti?", "quanto spende Lecce per la carta"),        # follow-up: cambia voce
    ("e a Bari?", "quanti abitanti ha Lecce"),                       # follow-up: cambia comune
    ("e gli ospedali?", "fammi vedere le farmacie di Lecce"),        # follow-up: cambia campo
]

def sig(intenti):
    if not intenti:
        return "(none)"
    it = intenti[0]
    return f"{it.get('sezione')}/{it.get('operazione')}/c={it.get('campo')}/com={it.get('comune')}"

async def main():
    diff = 0
    print(f"{'':2}{'#':>2}  {'DOMANDA':38} | {'qwen (gold)':42} | llama")
    print("-" * 120)
    for k, (q, ctx) in enumerate(CASI, 1):
        res = {}
        for m in MODELS:
            try:
                intenti, _ = await estrai_intento(q, OLLAMA, m, ctx)
                res[m] = sig(intenti)
            except Exception as e:
                res[m] = f"ERR {type(e).__name__}"
        g, l = res["qwen3:32b"], res["llama3.1:8b"]
        mark = "  " if g == l else "DIFF"
        if g != l:
            diff += 1
        print(f"{mark[:2]}{k:>2}  {q[:38]:38} | {g:42} | {l}")
        if ctx:
            print(f"        [ctx: {ctx}]")
    print("-" * 120)
    print(f"--- divergenze llama vs qwen: {diff}/{len(CASI)} ---")

asyncio.run(main())
