"""
Cruscotto Italia - Detector automatico Fenomeno 2 (AGE non pubblica catasto
in alcune zone urbane).

Algoritmo grid-based: per ogni comune in intersezione anncsu_full + catasto_full,
costruisce una griglia 200m × 200m e identifica celle con civici ANNCSU densi
(>=50) ma senza alcuna particella catastale.

Comune classificato "Fenomeno 2" se ha >=3 celle anomale.

Output: /var/www/cruscotto-italia/data/catasto_anomalies.json
Uso: ticket istituzionale AgID->AGE + disclaimer popup frontend + data journalism

Eseguibile dopo la pipeline ETL semestrale catasto_age.
"""
import json, gzip, math, sys, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

DATA = Path('/var/www/cruscotto-italia/data')
ANNCSU_DIR = DATA / 'anncsu_full'
CATASTO_DIR = DATA / 'catasto_full'
OUT_FILE = DATA / 'catasto_anomalies.json'

CELL_METERS = 200
THRESHOLD_CIVICI = 50      # min civici per cella anomala
THRESHOLD_CELLS = 3        # min celle anomale per classificare comune

def detect_one(istat):
    """Detect Fenomeno 2 per UN comune. Ritorna dict o None se OK / non testabile."""
    a_path = ANNCSU_DIR / f'{istat}.json'
    p_path = CATASTO_DIR / f'{istat}_ple.geojson.gz'
    
    if not a_path.exists() or not p_path.exists():
        return None  # non testabile (mono-only)
    
    try:
        civici = json.load(open(a_path)).get('punti', [])
        particelle = json.load(gzip.open(p_path)).get('features', [])
    except Exception as e:
        return {'istat': istat, 'error': str(e)[:80]}
    
    if not civici or not particelle:
        return None
    
    all_lon = [c['lon'] for c in civici if 'lon' in c]
    all_lat = [c['lat'] for c in civici if 'lat' in c]
    for feat in particelle:
        g = feat['geometry']
        coords = g['coordinates'][0] if g['type']=='Polygon' else g['coordinates'][0][0]
        if isinstance(coords[0][0], list): coords = coords[0]
        for pt in coords:
            all_lon.append(pt[0]); all_lat.append(pt[1])
    
    if not all_lon or not all_lat:
        return None
    
    lon_min, lon_max = min(all_lon), max(all_lon)
    lat_min, lat_max = min(all_lat), max(all_lat)
    mid_lat = (lat_min + lat_max) / 2
    CELL_LAT = CELL_METERS / 111000
    CELL_LON = CELL_METERS / (111000 * math.cos(math.radians(mid_lat)))
    
    def cell_idx(lat, lon):
        return (int((lat-lat_min)/CELL_LAT), int((lon-lon_min)/CELL_LON))
    
    civ_count = {}
    for c in civici:
        lat, lon = c.get('lat'), c.get('lon')
        if lat is None or lon is None: continue
        k = cell_idx(lat, lon)
        civ_count[k] = civ_count.get(k, 0) + 1
    
    parc_cells = set()
    for feat in particelle:
        g = feat['geometry']
        coords = g['coordinates'][0] if g['type']=='Polygon' else g['coordinates'][0][0]
        if isinstance(coords[0][0], list): coords = coords[0]
        for pt in coords:
            parc_cells.add(cell_idx(pt[1], pt[0]))
    
    anomale = [(k, n) for k, n in civ_count.items() if n >= THRESHOLD_CIVICI and k not in parc_cells]
    if len(anomale) < THRESHOLD_CELLS:
        return None  # comune OK
    
    # Anomalia rilevata: estrai bbox + cluster di celle adiacenti
    bboxes = []
    for (i, j), n in sorted(anomale, key=lambda x: -x[1])[:10]:  # top 10 per civici
        bboxes.append({
            'cell': [i, j],
            'civici': n,
            'bbox': [
                round(lat_min + i * CELL_LAT, 5),
                round(lon_min + j * CELL_LON, 5),
                round(lat_min + (i+1) * CELL_LAT, 5),
                round(lon_min + (j+1) * CELL_LON, 5),
            ]
        })
    
    return {
        'istat': istat,
        'civici_totali': len(civici),
        'particelle_totali': len(particelle),
        'celle_anomale': len(anomale),
        'civici_scoperti': sum(n for _, n in anomale),
        'pct_civici_scoperti': round(100 * sum(n for _, n in anomale) / len(civici), 2),
        'top_celle': bboxes,
    }

def main():
    print('=== Detector Fenomeno 2 - Cruscotto Italia ===')
    
    # Intersezione anncsu_full + catasto_full
    anncsu_ids = set(p.stem for p in ANNCSU_DIR.glob('*.json'))
    catasto_ids = set(p.name.replace('_map.geojson.gz','') for p in CATASTO_DIR.glob('*_map.geojson.gz'))
    testable = sorted(anncsu_ids & catasto_ids)
    print(f'Comuni testabili (anncsu_full ∩ catasto_full): {len(testable)}')
    
    # Carica mapping ISTAT -> nome dal file istat-names.json
    names_path = Path('/var/www/cruscotto-italia/frontend/istat-names.json')
    names = {}
    if names_path.exists():
        names = json.load(open(names_path))
    
    t0 = time.time()
    results = []
    
    with ProcessPoolExecutor(max_workers=13) as ex:
        futures = {ex.submit(detect_one, istat): istat for istat in testable}
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res:
                res['nome'] = names.get(res['istat'], res['istat'])
                results.append(res)
            if done % 500 == 0:
                print(f'  ...elaborati {done}/{len(testable)} ({len(results)} anomalie)')
    
    elapsed = time.time() - t0
    print(f'Elaborazione completata in {elapsed:.1f}s')
    print(f'Comuni con Fenomeno 2 rilevato: {len(results)}')
    
    # Ordina per civici scoperti desc
    results.sort(key=lambda x: -x.get('civici_scoperti', 0))
    
    output = {
        'generated_at': date.today().isoformat(),
        'method': f'grid {CELL_METERS}m, civici>={THRESHOLD_CIVICI} senza particelle, comuni con >={THRESHOLD_CELLS} celle anomale',
        'totale_testati': len(testable),
        'totale_anomalie': len(results),
        'comuni': results,
    }
    
    with open(OUT_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'Output: {OUT_FILE}')
    print(f'\nTop 10 comuni con Fenomeno 2:')
    for r in results[:10]:
        print(f'  {r["istat"]} {r["nome"]:20s}: {r["celle_anomale"]:3d} celle, {r["civici_scoperti"]:>5} civici scoperti ({r["pct_civici_scoperti"]:.1f}%)')

if __name__ == '__main__':
    main()
