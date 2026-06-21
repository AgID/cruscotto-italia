import sys, time, asyncio
import app_v2
from app_v2 import _SUGGERIMENTI

MODELLO = sys.argv[1] if len(sys.argv) > 1 else "llama3.1:8b"
app_v2.MODEL = MODELLO  # override: vale per estrazione (r.342) e verbalizzazione (r.187/710)

COMUNE = "Matera"
SKIP = {"carburanti", "farmacie", "pnrr", "siope", "scuole", "ricarica_ev", "demografia_dettaglio"}

async def main():
    print(f"### MODELLO: {MODELLO} ###")
    tot = ko = 0
    tempi = []
    for sez, tmpl in _SUGGERIMENTI.items():
        if sez in SKIP:
            continue
        for t in tmpl:
            q = t.replace("{c}", COMUNE)
            tot += 1
            t0 = time.monotonic()
            try:
                r = await app_v2.pipeline(q, lang="it")
            except Exception as e:
                dt = time.monotonic() - t0
                tempi.append(dt)
                print(f"KO [ERR] {sez} | {q} | {type(e).__name__} {e} | {dt:.1f}s")
                ko += 1
                continue
            dt = time.monotonic() - t0
            tempi.append(dt)
            valido = bool(r.get("valido"))
            if r.get("multi"):
                ops = [it.get("operazione") for it in (r.get("intenti") or [])]
            else:
                ops = [(r.get("intento") or {}).get("operazione")]
            nonsup = any(o == "non_supportata" for o in ops)
            ok = valido and not nonsup
            if not ok:
                ko += 1
            tag = "OK " if ok else "KO "
            causa = "" if ok else ("[estrazione]" if nonsup else "[verbalizz/check]")
            print(f"{tag} {sez} | {q} | ops={ops} valido={valido} {causa} | {dt:.1f}s")
    n = len(tempi) or 1
    print(f"--- {MODELLO}: TOTALE {tot} | problemi {ko} | "
          f"tempo medio {sum(tempi)/n:.1f}s | totale {sum(tempi):.0f}s ---")

asyncio.run(main())

