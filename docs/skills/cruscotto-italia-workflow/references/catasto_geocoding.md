# Geocoding catastale inverso — da civico/coordinata a foglio e particella

Questa procedura risponde a domande del tipo:

> "A quale foglio e particella catastale appartiene Via Vittorio Emanuele II, 29 a Lecce?"
> "Qual è la particella alle coordinate 40.3525, 18.1698?"

**Tutto il calcolo è LOCALE**: civici (ANNCSU) e catasto (Agenzia delle Entrate)
sono entrambi serviti da Cruscotto Italia. **Non interrogare MAI servizi esterni**
(WMS/WFS AGE, geoportale cartografico, Catasto online): non servono e non vanno usati.
Il dato è già tutto qui.

**Base URL degli endpoint `/data/...`**: `https://cruscotto-italia.dati.gov.it`
(il sito, NON l'host del connettore MCP `*.workers.dev`). Es: il file particelle di Lecce è
`https://cruscotto-italia.dati.gov.it/data/catasto_full/075035_ple.geojson.gz`.
Pubblici, same-origin, senza autenticazione.

---

## Come scaricare i file (gerarchia di accesso — LEGGERE PRIMA)

Il calcolo (point-in-polygon) lo fa SEMPRE l'agente, mai il server: i file `/data/...`
sono puro file-serving statico. Esistono due modi di scaricarli, da provare **in
quest'ordine**:

1. **Container (bash/python)** — via preferita. Se il container ha accesso di rete in
   uscita, scarica con `curl`/`requests` ed esegui il PIP in Python (snippet sotto).
2. **Widget browser (artifact HTML)** — **fallback quando il container NON ha rete in
   uscita** (capita su alcune sessioni/piani: `curl` fallisce con "domain not in
   allowlist"). In questo caso **NON arrenderti e NON costruire URL verso WMS/WFS o il
   geoportale AGE**: costruisci invece un artifact HTML che fa `fetch` + gunzip + PIP
   **nel browser dell'utente**. Funziona perché gli endpoint `/data/...` rispondono con
   header **`Access-Control-Allow-Origin: *`** (CORS aperto, verificato): una `fetch`
   GET semplice cross-origin è quindi permessa. Snippet JS pronto in fondo a questo file.

**Regola d'oro**: non comporre mai a mano il codice foglio/particella. Scarica le
geometrie e **leggi `NATIONALCADASTRALREFERENCE` dalla feature** che contiene il punto.
Il codice foglio reale può contenere la **sezione catastale** (es. Palermo `0025D0`, non
`002500`): costruirlo a mano fallisce, leggerlo dalla feature no.

### Snippet download (container)

```bash
curl -sS -o ple.geojson.gz \
  "https://cruscotto-italia.dati.gov.it/data/catasto_full/082053_ple.geojson.gz"
```
```python
import gzip, json, urllib.request
url = "https://cruscotto-italia.dati.gov.it/data/catasto_full/082053_ple.geojson.gz"
with urllib.request.urlopen(url) as r:
    fc = json.loads(gzip.decompress(r.read()))
```

---

## Cosa è possibile e cosa no (leggere prima di promettere risultati)

Il civico ANNCSU è georeferenziato sul **fronte strada** (metodo "Ortofoto" nella
maggioranza dei casi), non sul tetto dell'edificio. Di conseguenza la coordinata di
un civico, quasi sempre, **cade dentro la particella-STRADA**, non dentro la
particella edificata.

Quindi la promessa corretta da fare all'utente è **B1**:

- **Foglio**: determinabile con certezza (il civico cade nel foglio giusto).
- **Particella esatta dell'immobile**: NON garantita al 100%. Si riportano le
  **particelle edificate adiacenti** al punto, ordinate per distanza, come candidate.

Non promettere "la particella esatta": riporta foglio (certo) + particelle candidate
(adiacenti, con distanza). È onesto e quasi sempre identifica correttamente l'immobile.

---

## Algoritmo (validato)

### 1. Civico → coordinate
Usa `anncsu_civico_search` (o `/data/anncsu_full/<istat>.json`) per ottenere lat/lon
del civico. Se ci sono più civici con lo stesso numero (es. "29" e "29/G"), elaborali
tutti o chiedi disambiguazione.

### 2. Scarica le particelle del comune
- **Comune monolitico** (tutti tranne Roma): `GET /data/catasto_full/<istat>_ple.geojson.gz`
  (≤ 20 MB). Decomprimi gzip → FeatureCollection di Polygon.
- **Roma (058091)**: è l'UNICO comune in modalità split (file troppo grande). Qui NON
  esiste il monolitico: devi prima sapere il foglio. Se non lo conosci, usa prima i
  **fogli** `GET /data/catasto_full/058091_map.geojson.gz` (CadastralZoning, leggero)
  per il point-in-polygon sul foglio, ricavi `<FFFF00>`, poi scarichi solo quel foglio
  `GET /data/catasto_full/058091_ple/H501_<FFFF00>.geojson.gz`.

### 3. Point-in-polygon (ray casting, nessuna libreria esterna)
Trova la feature la cui geometria contiene il punto (lon, lat). Attenzione: GeoJSON usa
ordine `[lon, lat]`.

```python
def point_in_ring(x, y, ring):
    inside = False; n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def point_in_polygon(x, y, geom):
    if geom['type'] == 'Polygon':
        rings = geom['coordinates']
    elif geom['type'] == 'MultiPolygon':
        rings = [r for poly in geom['coordinates'] for r in poly]
    else:
        return False
    if not rings or not point_in_ring(x, y, rings[0]):
        return False
    return not any(point_in_ring(x, y, h) for h in rings[1:])  # esclude i buchi
```

### 4. Interpreta NATIONALCADASTRALREFERENCE (DUE formati nello stesso comune)
La property `NATIONALCADASTRALREFERENCE` ha due formati conviventi:

- con underscore: `E506_0259A0.1`  → BELFIORE=`E506`, foglio=`0259A0`, particella=`1`
- senza underscore: `E506A026200.1` → BELFIORE=`E506`, foglio=`A026200`, particella=`1`

```python
def parse_ref(ref):
    pre, part = ref.split('.', 1)
    if '_' in pre:
        bel, fog = pre.split('_', 1)
    else:
        bel, fog = pre[:4], pre[4:]
    return bel, fog, part
```

Le sedi stradali/acque hanno particella che inizia con `STRADA` o `ACQUA`
(es. `E506_0259F0.STRADA569`): NON sono immobili.

### 5. Se il match è una STRADA/ACQUA → particelle edificate adiacenti
Cerca, **nello stesso foglio**, le particelle non-strada più vicine al punto,
misurando la distanza minima dal **bordo** del poligono (non dal centroide).

```python
import math
def min_dist_m(x, y, geom):  # distanza minima punto-vertici, in metri approssimati
    if geom['type'] == 'Polygon':
        rings = geom['coordinates']
    else:
        rings = [r for poly in geom['coordinates'] for r in poly]
    best = float('inf')
    for ring in rings:
        for px, py in ring:
            d = math.hypot((px - x) * math.cos(math.radians(y)), py - y) * 111000
            best = min(best, d)
    return best
```

Filtra le feature dello stesso `foglio`, escludi `STRADA`/`ACQUA`, ordina per
`min_dist_m`, riporta le prime 3-5 come candidate con la loro distanza.

---

## Esempio reale validato: Via Vittorio Emanuele II, 29 — Lecce

1. `anncsu_civico_search` → civico "29" a `lat 40.352556, lon 18.169816`.
2. `GET /data/catasto_full/075035_ple.geojson.gz` (8.3 MB, monolitico, 68.426 particelle).
3. Point-in-polygon → match `E506_0259F0.STRADA569` → è la **sede stradale** del foglio **0259**.
4. Particelle edificate adiacenti nel foglio 0259 (distanza dal bordo):
   particella **1648** (~3 m), **1649** (~3 m), 730 (~12 m), 1650 (~15 m).

Risposta corretta all'utente:
> Il civico 29 di Via Vittorio Emanuele II (Lecce) ricade nel **foglio 0259**
> (BELFIORE E506). La coordinata cade sul fronte strada; le particelle edificate
> immediatamente adiacenti sono la **1648** e la **1649** (~3 m), quindi quasi
> certamente l'immobile corrisponde a una di queste.

---

## Esempio reale validato: Via Ammiraglio Paolo Thaon De Revel, 22 — Palermo

Caso diverso da Lecce: qui il punto cade **dentro** una particella edificata (non sul
fronte strada), quindi la particella è certa. Mostra anche un comune con **sezione
catastale** nel codice foglio.

1. `anncsu_civico_search(istat 082053, odonimo "Thaon De Revel", civico "22")` → due
   punti (civico 22 e 22/B), entrambi georef "Catasto":
   `lon 13.360452, lat 38.14539` e `lon 13.360499, lat 38.145844`.
2. `GET /data/catasto_full/082053_ple.geojson.gz` (13.4 MB monolitico, 135.101 particelle).
3. Point-in-polygon: **entrambi** i punti cadono in `G273_0025D0.2365`.
4. `parse_ref` → BELFIORE `G273`, foglio `0025D0` (foglio 25, sezione D), particella `2365`.

Risposta corretta all'utente:
> Via Ammiraglio Paolo Thaon De Revel 22 (Palermo) ricade nel **foglio 25, sezione D**
> (`0025D0`), particella **2365** — BELFIORE `G273`. Riferimento catastale completo:
> `G273_0025D0.2365`. Il civico è georeferenziato su Catasto e cade dentro la particella,
> quindi il dato è certo.

---

## Appendice: versione JavaScript per il widget browser

Usa questo nell'artifact HTML **solo** quando il container non ha rete in uscita. Il
gunzip lato browser usa `DecompressionStream('gzip')` (nativo, nessuna libreria). La
logica PIP è identica a quella Python.

```javascript
async function fetchParticelle(url) {
  const resp = await fetch(url);                       // CORS aperto: GET semplice ok
  const stream = resp.body.pipeThrough(new DecompressionStream('gzip'));
  const txt = await new Response(stream).text();
  return JSON.parse(txt);                              // FeatureCollection
}

function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function pointInPolygon(x, y, geom) {                  // x=lon, y=lat
  const polys = geom.type === 'Polygon' ? [geom.coordinates]
              : geom.type === 'MultiPolygon' ? geom.coordinates : [];
  return polys.some(rings =>
    pointInRing(x, y, rings[0]) && !rings.slice(1).some(h => pointInRing(x, y, h)));
}
function parseRef(ref) {                                // "G273_0025D0.2365"
  const [pre, part] = ref.split('.');
  const [bel, fog] = pre.includes('_') ? pre.split('_') : [pre.slice(0, 4), pre.slice(4)];
  return { bel, fog, part };
}
```

---

## Cosa NON fare

- ❌ Non interrogare WMS/WFS o il geoportale cartografico AGE: il dato è locale e completo.
- ❌ Non promettere "la particella esatta" come fosse certa: il civico è sul fronte strada.
- ❌ Non usare il centroide per il ranking di vicinanza: usa la distanza dal bordo.
- ❌ Non dimenticare Roma: è l'unico comune split (niente `_ple.geojson.gz` monolitico).
- ❌ Se `curl`/`requests` falliscono perché il container non ha rete in uscita, NON
  arrenderti e NON dire che il dato non è raggiungibile: passa al **widget browser**
  (gli endpoint hanno CORS `*`, la fetch lato client funziona).
