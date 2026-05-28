#!/usr/bin/env python3
"""
Cruscotto Italia - Catasto AGE pipeline (parallel).
"""
import argparse, json, gzip, os, sys, time, shutil, subprocess, zipfile, re
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# --- Config
ZIP_DIR        = Path('/home/ubuntu/catasto_test')
WORK_DIR       = Path('/home/ubuntu/catasto_test/work_extract')
OUTPUT_DIR     = Path('/var/www/cruscotto-italia/data/catasto_full')
LOOKUP_FILE    = Path('/home/ubuntu/catasto_test/lookup_belfiore_to_istat.json')
MONO_MAX_BYTES = 20 * 1024 * 1024   # 20 MB → soglia monolitico
LOG_FILE       = Path('/tmp/catasto_build.log')
DEFAULT_WORKERS = 13                # 16 core - 3 per OS/nginx

# --- Log helpers
def log(msg, *, level='INFO'):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {level:5s} {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

def fmt_size(b):
    for u in ['B','KB','MB','GB']:
        if b < 1024: return f'{b:.1f}{u}'
        b /= 1024
    return f'{b:.1f}TB'

# --- Lookup (caricato in ogni worker via init)
_LOOKUP = None
def _worker_init(lookup_path):
    global _LOOKUP
    with open(lookup_path) as f:
        _LOOKUP = json.load(f)

# --- Conversion helpers (run in worker)
def gml_to_geojson(gml_path, layer, out_path):
    cmd = ['ogr2ogr', '-f', 'GeoJSON', '-t_srs', 'EPSG:4326',
           str(out_path), str(gml_path), layer]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, 0
    if res.returncode != 0:
        return False, 0
    try:
        with open(out_path) as f: d = json.load(f)
        return True, len(d.get('features', []))
    except Exception:
        return False, 0

def write_compact_gz(geojson_path, gz_path):
    with open(geojson_path) as f:
        d = json.load(f)
    raw = json.dumps(d, separators=(',', ':')).encode('utf-8')
    with gzip.open(gz_path, 'wb', compresslevel=9) as fout:
        fout.write(raw)
    return os.path.getsize(gz_path)

def split_ple_by_foglio(ple_geojson_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(ple_geojson_path) as f:
        d = json.load(f)
    buckets = defaultdict(list)
    for feat in d['features']:
        ref = feat.get('properties', {}).get('NATIONALCADASTRALREFERENCE', '')
        if '.' in ref:
            buckets[ref.split('.')[0]].append(feat)
    total = 0
    for fid, feats in buckets.items():
        fc = {"type": "FeatureCollection", "features": feats}
        raw = json.dumps(fc, separators=(',', ':')).encode('utf-8')
        out = out_dir / f'{fid}.geojson.gz'
        with gzip.open(out, 'wb', compresslevel=9) as fout:
            fout.write(raw)
        total += os.path.getsize(out)
    return len(buckets), total

# --- Worker entry: processa 1 zip comune (path su disco)
def process_one(args):
    """args = (belfiore, c_zip_path_str, force) → dict"""
    belfiore, c_zip_path_str, force = args
    c_zip_path = Path(c_zip_path_str)
    info = _LOOKUP.get(belfiore)
    if not info:
        return {'belfiore': belfiore, 'status': 'no_lookup'}
    istat = info['istat']
    name = info['name']
    map_out  = OUTPUT_DIR / f'{istat}_map.geojson.gz'
    ple_dir  = OUTPUT_DIR / f'{istat}_ple'
    ple_mono = OUTPUT_DIR / f'{istat}_ple.geojson.gz'

    # Idempotenza
    if not force and map_out.exists() and map_out.stat().st_size > 0:
        try: c_zip_path.unlink()
        except: pass
        return {'belfiore': belfiore, 'istat': istat, 'name': name, 'status': 'skip'}

    t0 = time.time()
    pid = os.getpid()
    extract_dir = Path(f'/tmp/catasto_w/{pid}_{belfiore}')
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    try:
        with zipfile.ZipFile(c_zip_path) as zf:
            zf.extractall(extract_dir)

        gml_files = list(extract_dir.glob('*.gml'))
        map_gml = next((g for g in gml_files if g.name.endswith('_map.gml')), None)
        ple_gml = next((g for g in gml_files if g.name.endswith('_ple.gml')), None)
        if not map_gml or not ple_gml:
            return {'belfiore': belfiore, 'istat': istat, 'name': name, 'status': 'gml_missing'}

        map_geojson = extract_dir / 'map.geojson'
        ple_geojson = extract_dir / 'ple.geojson'
        ok1, n_fogli = gml_to_geojson(map_gml, 'CadastralZoning', map_geojson)
        ok2, n_ple   = gml_to_geojson(ple_gml, 'CadastralParcel', ple_geojson)
        if not (ok1 and ok2):
            return {'belfiore': belfiore, 'istat': istat, 'name': name, 'status': 'ogr_fail'}

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        size_map = write_compact_gz(map_geojson, map_out)
        size_mono = write_compact_gz(ple_geojson, ple_mono)
        if size_mono > MONO_MAX_BYTES:
            ple_mono.unlink()
            size_mono = 0
        if ple_dir.exists():
            shutil.rmtree(ple_dir)
        n_fogli_split, size_split = split_ple_by_foglio(ple_geojson, ple_dir)

        # chown www-data
        try:
            shutil.chown(map_out, user='www-data', group='www-data')
            if ple_mono.exists():
                shutil.chown(ple_mono, user='www-data', group='www-data')
            shutil.chown(ple_dir, user='www-data', group='www-data')
            for p in ple_dir.iterdir():
                shutil.chown(p, user='www-data', group='www-data')
        except (PermissionError, LookupError):
            pass

        return {
            'belfiore': belfiore, 'istat': istat, 'name': name, 'status': 'ok',
            'fogli': n_fogli, 'particelle': n_ple, 'fogli_split': n_fogli_split,
            'size_map': size_map, 'size_mono': size_mono, 'size_split': size_split,
            'mode': 'monolitico+split' if size_mono > 0 else 'chunking',
            'dt': time.time() - t0,
        }
    except Exception as e:
        return {'belfiore': belfiore, 'istat': istat, 'name': name, 'status': f'exception:{e}'}
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        try: c_zip_path.unlink()
        except: pass

# --- Main: estrae comune ZIPs su /tmp, sottomette al pool
def process_region_parallel(region_name, *, only_belfiore=None, force=False, workers=DEFAULT_WORKERS):
    region_zip = ZIP_DIR / f'{region_name}.zip'
    if not region_zip.exists():
        log(f'❌ {region_zip} non esiste.', level='ERROR')
        return None

    log(f'=== REGIONE {region_name} (pool={workers}) ===')
    staging = Path(f'/tmp/catasto_staging/{region_name}')
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # Estraggo tutti i comune-zip su /tmp/staging (parallelo CPU-bound trascurabile)
    log(f'  Estrazione provincie/comuni in staging...')
    t_st = time.time()
    tasks = []  # [(belfiore, c_zip_path, force)]
    with zipfile.ZipFile(region_zip) as rzf:
        provincie = [n for n in rzf.namelist() if n.endswith('.zip')]
        for prov_name in provincie:
            prov_zip_path = staging / prov_name
            with rzf.open(prov_name) as src, open(prov_zip_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            with zipfile.ZipFile(prov_zip_path) as pzf:
                for c_name in pzf.namelist():
                    if not c_name.endswith('.zip'): continue
                    m = re.match(r'^([A-Z]\d{3})_', c_name)
                    if not m: continue
                    belfiore = m.group(1)
                    if only_belfiore and belfiore != only_belfiore: continue
                    c_zip_path = staging / c_name
                    with pzf.open(c_name) as src, open(c_zip_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    tasks.append((belfiore, str(c_zip_path), force))
            prov_zip_path.unlink()
    log(f'  → {len(tasks)} comuni in staging ({time.time()-t_st:.1f}s)')

    # Pool worker
    stats = defaultdict(int)
    t_start = time.time()
    completed = 0
    total = len(tasks)
    
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(LOOKUP_FILE,)) as pool:
        futures = {pool.submit(process_one, t): t for t in tasks}
        for fut in as_completed(futures):
            r = fut.result()
            stats[r['status']] += 1
            completed += 1
            if r['status'] == 'ok':
                log(f'  [{completed}/{total}] ✓ {r["belfiore"]} {r["name"]:30s} → {r["fogli"]:4d}f {r["particelle"]:6d}p '
                    f'mono={fmt_size(r["size_mono"]) if r["size_mono"] else "—":>7s} '
                    f'split={fmt_size(r["size_split"]):>7s} ({r["dt"]:.1f}s)')
            elif r['status'] == 'skip':
                if completed % 50 == 0:
                    log(f'  [{completed}/{total}] (skip in corso, OK={stats["ok"]} skip={stats["skip"]})')
            else:
                log(f'  [{completed}/{total}] ✗ {r["belfiore"]} {r.get("name","?")}: {r["status"]}', level='WARN')

    shutil.rmtree(staging, ignore_errors=True)
    elapsed = time.time() - t_start
    log(f'=== {region_name} DONE in {elapsed/60:.1f} min (ok:{stats["ok"]} skip:{stats["skip"]} fail:{total - stats["ok"] - stats["skip"]}) ===')
    return {'region': region_name, 'stats': dict(stats), 'elapsed': elapsed}

# --- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('region', help='Regione o ALL')
    ap.add_argument('--belfiore', help='1 solo comune (es. F052)')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    args = ap.parse_args()
    force = args.force or bool(args.belfiore)

    log(f'==== BUILD CATASTO PARALLEL start region={args.region} belfiore={args.belfiore} force={force} workers={args.workers} ====')

    if args.region == 'ALL':
        # Trovo tutti gli ZIP regionali (sono i .zip > 50MB con NOME tutto UPPER e senza pattern BELFIORE+_)
        regions = sorted(
            p.stem for p in ZIP_DIR.glob('*.zip')
            if p.stat().st_size > 50*1024*1024
            and re.match(r'^[A-Z\-]+$', p.stem)
            and len(p.stem) > 3
        )
        log(f'Regioni: {regions}')
    else:
        regions = [args.region]

    t0 = time.time()
    Path('/tmp/catasto_w').mkdir(exist_ok=True)
    Path('/tmp/catasto_staging').mkdir(exist_ok=True)
    for r in regions:
        process_region_parallel(r, only_belfiore=args.belfiore, force=force, workers=args.workers)
    log(f'==== TOTALE: {(time.time()-t0)/60:.1f} min ====')

if __name__ == '__main__':
    main()
