import asyncio
import app_v2

DOMANDE = [
    "Quante farmacie per abitante a Matera?",
    "Indice di vecchiaia di Matera",
    "Qual e' l'indice di vecchiaia a Matera?",
]

async def main():
    for q in DOMANDE:
        r = await app_v2.pipeline(q, lang="it")
        if r.get("multi"):
            ops = [it.get("operazione") for it in (r.get("intenti") or [])]
        else:
            ops = [(r.get("intento") or {}).get("operazione")]
        print("Q:", q)
        print("  valido=", r.get("valido"), "| ops=", ops, "| fonte=", r.get("fonte_risposta"))
        print("  check=", r.get("check_numerico"))
        print("  risposta:", (r.get("risposta") or "")[:300])
        print("---")

asyncio.run(main())
