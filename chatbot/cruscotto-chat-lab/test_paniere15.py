import asyncio
import app_v2
from app_v2 import _SUGGERIMENTI

COMUNE = "Matera"
SKIP = {"carburanti", "farmacie", "pnrr", "siope", "scuole", "ricarica_ev", "demografia_dettaglio"}

async def main():
    tot = ko = 0
    for sez, tmpl in _SUGGERIMENTI.items():
        if sez in SKIP:
            continue
        for t in tmpl:
            q = t.replace("{c}", COMUNE)
            tot += 1
            try:
                r = await app_v2.pipeline(q, lang="it")
            except Exception as e:
                print("KO [ERR]", sez, "|", q, "|", type(e).__name__, e)
                ko += 1
                continue
            valido = bool(r.get("valido"))
            if r.get("multi"):
                ops = [it.get("operazione") for it in (r.get("intenti") or [])]
            else:
                ops = [(r.get("intento") or {}).get("operazione")]
            nonsup = any(o == "non_supportata" for o in ops)
            ok = valido and not nonsup
            if not ok:
                ko += 1
            print(("OK " if ok else "KO "), sez, "|", q, "| ops=", ops, "| valido=", valido)
    print(f"--- TOTALE {tot} | problemi {ko} ---")

asyncio.run(main())
