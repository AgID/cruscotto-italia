import json
from intent_engine import _punto_civico, _sezione_censimento, _valore_sezione

ISTAT = "077014"
ODO = "VIA LUPO PROTOSPATA"
CIV = "53"

print(f"Civico: {ODO} {CIV}  (istat {ISTAT})")
p = _punto_civico(ISTAT, ODO, CIV)
print("PUNTO:", json.dumps(p, ensure_ascii=False) if p else None)

if p:
    s = _sezione_censimento(ISTAT, p["lat"], p["lon"])
    print("SEZIONE (geocoding):", json.dumps(s, ensure_ascii=False) if s else None)
    sez = (s or {}).get("sezione")
    if sez not in (None, ""):
        print(f"\nValori reali per la sezione {sez}:")
        for campo in ("laureati", "diplomati", "popolazione"):
            v = _valore_sezione(ISTAT, sez, campo)
            print(f"  {campo:12s}: {json.dumps(v, ensure_ascii=False)}")
