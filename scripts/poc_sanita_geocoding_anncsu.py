"""POC: tentativo di geocoding farmacie MdS coord-droppate via ANNCSU full.

Obiettivo: capire se vale la pena rifare l'ETL per geocodare le coord
sbagliate via ANNCSU. Usiamo le farmacie di Roma droppate dal filtro
centroide (~29 record) e tentiamo match su anncsu_full/058091.json.

Output atteso: match rate. Se >70%, vale la pena.

Run:
  cd /home/ubuntu/cruscotto-italia
  python3 scripts/poc_sanita_geocoding_anncsu.py 058091
"""

import json
import re
import sys
import urllib.request
import gzip
from pathlib import Path

# ---------------------------------------------------------------------------
# Normalizzazione indirizzo
# ---------------------------------------------------------------------------

# Prefissi indirizzo da rimuovere (lista canonica + abbreviazioni)
PREFISSI = [
    r"\bviale\b", r"\bvialetto\b",
    r"\bvia\b",
    r"\bpiazza\b", r"\bpiazzale\b", r"\bpiazzetta\b",
    r"\bcorso\b",
    r"\blargo\b",
    r"\bvicolo\b",
    r"\blungotevere\b", r"\blungomare\b", r"\blungarno\b",
    r"\bsalita\b", r"\bdiscesa\b",
    r"\bstrada\b", r"\bstradone\b", r"\bstradella\b",
    r"\bsentiero\b",
    r"\btravers[ae]\b",
    r"\bv\.le\b", r"\bv\.\b",
    r"\bp\.zza\b", r"\bp\.le\b", r"\bp\.\b",
    r"\bc\.so\b",
    r"\bl\.go\b",
]
RE_PREFISSI = re.compile("|".join(PREFISSI), re.IGNORECASE)

# Pattern civico: numero + opzionale esponente lettera/slash
RE_CIVICO = re.compile(r"\b(\d{1,4})\s*([a-zA-Z]|\/[a-zA-Z0-9]+)?\b\s*$")

# Tokens generici da rimuovere
RE_PUNTI = re.compile(r"[\.\,\;\:]+")
RE_SPAZI = re.compile(r"\s+")


def normalize_odonimo(s: str) -> str:
    """Normalizza nome strada per matching."""
    if not s:
        return ""
    s = s.strip().lower()
    s = RE_PREFISSI.sub("", s)
    s = RE_PUNTI.sub(" ", s)
    s = RE_SPAZI.sub(" ", s).strip()
    return s


def extract_civico(indirizzo: str) -> tuple[str, str | None, str | None]:
    """Estrae (odonimo_clean, civico, esp) da indirizzo MdS.

    Esempi:
      'VIA SERAFINO BELFANTI 1' -> ('serafino belfanti', '1', None)
      'PIAZZA SAN PIETRO 12/A'  -> ('san pietro', '12', '/A')
      'VIA ROMA 45B'            -> ('roma', '45', 'B')
    """
    if not indirizzo:
        return "", None, None
    s = indirizzo.strip()
    m = RE_CIVICO.search(s)
    civico, esp = None, None
    if m:
        civico = m.group(1)
        esp = m.group(2)
        s = s[: m.start()].strip()
    odonimo = normalize_odonimo(s)
    return odonimo, civico, esp


def try_swap_name(odonimo: str) -> str:
    """Inverte ordine token (gestisce 'BELFANTI SERAFINO' vs 'SERAFINO BELFANTI').

    Solo se 2 token. Per nomi più lunghi non tento (rumoroso).
    """
    toks = odonimo.split()
    if len(toks) == 2:
        return " ".join(reversed(toks))
    return odonimo


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_sanita_shard(istat: str) -> dict:
    p = Path(f"output/sanita_mds/shards/{istat}.json")
    if not p.exists():
        sys.exit(f"Manca {p}. Lancia prima: python3 -m etl.sources.sanita_mds --target=local")
    return json.loads(p.read_text())


def fetch_anncsu_full(istat: str) -> dict:
    """Scarica anncsu_full/<istat>.json da R2 via Worker."""
    url = f"https://cruscotto-italia-mcp.piersoftckan.biz/data/anncsu_full/{istat}.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "poc-sanita-geocoding/0.1",
        "Accept-Encoding": "gzip",
    })
    print(f"  fetching {url}...")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def build_anncsu_index(anncsu: dict) -> dict:
    """Indice: {odonimo_normalizzato: [(civ, esp, lat, lon), ...]}.

    ANNCSU full shard schema (compact): { punti: [{lat, lon, odo, civ, esp, ...}] }
    """
    idx: dict[str, list[tuple]] = {}
    punti = anncsu.get("punti", [])
    print(f"  ANNCSU punti totali: {len(punti)}")
    for p in punti:
        if p.get("lat") is None or p.get("lon") is None:
            continue
        odo = normalize_odonimo(p.get("odo", ""))
        if not odo:
            continue
        civ = str(p.get("civ", "")) if p.get("civ") is not None else ""
        esp = p.get("esp")
        idx.setdefault(odo, []).append((civ, esp, p["lat"], p["lon"]))
    print(f"  ANNCSU odonimi unici: {len(idx)}")
    return idx


def find_match(indirizzo: str, anncsu_idx: dict) -> dict | None:
    """Tenta match contro indice ANNCSU. Ritorna (lat, lon, strategy) o None."""
    odo, civ, esp = extract_civico(indirizzo)
    if not odo:
        return None

    # Strategia 1: match esatto odonimo + civico
    cands = anncsu_idx.get(odo, [])
    if not cands:
        # Strategia 2: swap nome/cognome (BELFANTI SERAFINO vs SERAFINO BELFANTI)
        swapped = try_swap_name(odo)
        if swapped != odo:
            cands = anncsu_idx.get(swapped, [])
            if cands:
                odo = swapped

    if not cands:
        return None

    # Match civico esatto
    if civ:
        for c_civ, c_esp, lat, lon in cands:
            if c_civ == civ and (c_esp or "") == (esp or ""):
                return {"lat": lat, "lon": lon, "strategy": "odo_civ_exact",
                        "odo_match": odo}
        # Match solo civico (ignora esponente)
        for c_civ, c_esp, lat, lon in cands:
            if c_civ == civ:
                return {"lat": lat, "lon": lon, "strategy": "odo_civ_no_esp",
                        "odo_match": odo}

    # Match solo odonimo: fallback al primo civico della strada
    lat, lon = cands[0][2], cands[0][3]
    return {"lat": lat, "lon": lon, "strategy": "odo_only",
            "odo_match": odo, "n_civici_strada": len(cands)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(istat: str):
    print(f"=== POC geocoding ANNCSU per ISTAT {istat} ===")
    print()

    print("[1/3] Carico shard sanita_mds locale...")
    sanita = load_sanita_shard(istat)
    farm = sanita.get("farmacie", {}).get("punti", [])
    pf   = sanita.get("parafarmacie", {}).get("punti", [])
    dropped_farm = [p for p in farm if p.get("coord_dropped")]
    dropped_pf   = [p for p in pf   if p.get("coord_dropped")]
    nocoord_farm = [p for p in farm if p.get("lat") is None and not p.get("coord_dropped")]
    print(f"  Farmacie tot: {len(farm)}")
    print(f"  Parafarmacie tot: {len(pf)}")
    print(f"  Farmacie con coord_dropped: {len(dropped_farm)}")
    print(f"  Parafarmacie con coord_dropped: {len(dropped_pf)}")
    print(f"  Farmacie senza coord upstream (no dropped): {len(nocoord_farm)}")
    print()

    print("[2/3] Scarico ANNCSU full da R2...")
    try:
        anncsu = fetch_anncsu_full(istat)
    except Exception as e:
        print(f"  ERRORE: {e}")
        print(f"  Forse il comune non ha shard full ANNCSU?")
        return
    idx = build_anncsu_index(anncsu)
    print()

    print("[3/3] Tento matching su punti droppati + no-coord...")
    targets = [("FARMACIA_dropped", p) for p in dropped_farm] + \
              [("PARAFARM_dropped", p) for p in dropped_pf] + \
              [("FARMACIA_nocoord", p) for p in nocoord_farm[:20]]  # limit 20

    stats = {"odo_civ_exact": 0, "odo_civ_no_esp": 0, "odo_only": 0, "no_match": 0}
    examples = []
    for tag, p in targets:
        m = find_match(p.get("indirizzo", ""), idx)
        if m is None:
            stats["no_match"] += 1
            if len(examples) < 8:
                examples.append((tag, p.get("nome", "?")[:35],
                                p.get("indirizzo", "?"), None))
        else:
            stats[m["strategy"]] += 1
            if len(examples) < 8:
                examples.append((tag, p.get("nome", "?")[:35],
                                p.get("indirizzo", "?"), m))

    print()
    print("=== RISULTATI ===")
    total = len(targets)
    matched = total - stats["no_match"]
    print(f"  Tentati: {total}")
    print(f"  Matched: {matched} ({100*matched/total:.1f}%)")
    print(f"    odo_civ_exact:  {stats['odo_civ_exact']}")
    print(f"    odo_civ_no_esp: {stats['odo_civ_no_esp']}")
    print(f"    odo_only:       {stats['odo_only']}  (fallback, civico mancato)")
    print(f"  No match:         {stats['no_match']}")
    print()
    print("=== ESEMPI ===")
    for tag, nome, indirizzo, m in examples:
        if m is None:
            print(f"  [{tag}] {nome:35s} | '{indirizzo}'")
            print(f"      -> NO MATCH")
        else:
            print(f"  [{tag}] {nome:35s} | '{indirizzo}'")
            print(f"      -> {m['strategy']:20s} lat={m['lat']:.4f} lon={m['lon']:.4f}  (anncsu odo: '{m['odo_match']}')")


if __name__ == "__main__":
    istat = sys.argv[1] if len(sys.argv) > 1 else "058091"
    main(istat)
