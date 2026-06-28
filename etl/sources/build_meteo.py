#!/usr/bin/env python3
"""
ETL ItaliaMeteo ICON-2I → shard meteo per comune
Fonte: https://meteohub.agenziaitaliameteo.it/nwp/ICON-2I_SURFACE_PRESSURE_LEVELS/
Licenza: CC BY 4.0 | HVD Meteorologici (Reg. EU 2023/138)
Cron: 2x/giorno 03:30 e 15:30 CEST (corse 00 UTC e 12 UTC)
"""
import cfgrib, numpy as np, json, os, time, tempfile, urllib.request
import glob
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

BASE_NWP  = "https://meteohub.agenziaitaliameteo.it/nwp/ICON-2I_SURFACE_PRESSURE_LEVELS"
COORDS    = "/var/www/cruscotto-italia/data/istat-coords.json"
OUT_DIR   = "/var/www/cruscotto-italia/data/meteo"
WORKERS   = 4
GRIB_VARS = ["T_2M","TOT_PREC","RELHUM","U_10M","V_10M","VMAX_10M","CLCT","H_SNOW","WW"]

WW_DESC = {
    0:"Cielo sereno", 1:"Prevalentemente sereno", 2:"Parzialmente nuvoloso",
    3:"Nuvoloso", 4:"Nebbia", 5:"Nebbia ghiacciante",
    51:"Pioviggine leggera", 52:"Pioviggine moderata", 53:"Pioviggine forte",
    61:"Pioggia leggera", 62:"Pioggia moderata", 63:"Pioggia forte",
    71:"Neve leggera", 72:"Neve moderata", 73:"Neve forte",
    80:"Rovesci leggeri", 81:"Rovesci moderati", 82:"Rovesci forti",
    85:"Rovesci di neve leggeri", 86:"Rovesci di neve moderati",
    95:"Temporale", 96:"Temporale con grandine", 99:"Temporale forte",
}

def ww_label(code):
    c = int(round(code))
    if c in WW_DESC: return WW_DESC[c]
    if c <= 3:  return "Sereno/poco nuvoloso"
    if c <= 49: return "Nebbia"
    if c <= 59: return "Pioviggine"
    if c <= 69: return "Pioggia"
    if c <= 79: return "Neve"
    if c <= 94: return "Rovesci"
    return "Temporale"

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for k,v in attrs:
                if k == 'href' and not v.startswith('../'): self.links.append(v.rstrip('/'))

def get_latest_run():
    p = LinkParser()
    with urllib.request.urlopen(f"{BASE_NWP}/", timeout=15) as r: p.feed(r.read().decode())
    runs = sorted(l for l in p.links if l[:10].isdigit())
    return runs[-1]

def get_grib_url(run, varname):
    p = LinkParser()
    with urllib.request.urlopen(f"{BASE_NWP}/{run}/{varname}/", timeout=10) as r: p.feed(r.read().decode())
    fname = next(l for l in p.links if l.endswith('.grib'))
    return f"{BASE_NWP}/{run}/{varname}/{fname}"

def download_grib(run, varname):
    url  = get_grib_url(run, varname)
    tmp  = tempfile.mktemp(suffix=f"_{varname}.grib")
    urllib.request.urlretrieve(url, tmp)
    ds   = cfgrib.open_dataset(tmp)
    key  = list(ds.data_vars)[0]
    data = ds[key].values.copy()
    meta = {'lats': ds.latitude.values, 'lons': ds.longitude.values,
            'valid_times': ds['valid_time'].values, 'units': ds[key].attrs.get('units','?')}
    ds.close()
    os.unlink(tmp)
    for _idx in glob.glob(tmp + ".*.idx"):
        try:
            os.unlink(_idx)
        except OSError:
            pass
    print(f"  ✓ {varname:12s} ({data.nbytes/1024/1024:.0f}MB) units={meta['units']}", flush=True)
    return varname, data, meta

def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)

    print("[1/5] Centroidi...", flush=True)
    coords     = json.load(open(COORDS))
    istat_list = list(coords.keys())
    lat_arr    = np.array([coords[i][0] for i in istat_list])
    lon_arr    = np.array([coords[i][1] for i in istat_list])
    print(f"      {len(istat_list)} comuni", flush=True)

    print("[2/5] Ultima corsa ICON-2I...", flush=True)
    run    = get_latest_run()
    run_dt = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    print(f"      Corsa: {run} ({run_dt.strftime('%d/%m/%Y %H UTC')})", flush=True)

    print(f"[3/5] Download {len(GRIB_VARS)} variabili (parallel={WORKERS})...", flush=True)
    t_dl   = time.time()
    grids  = {}
    meta_r = None
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(download_grib, run, v): v for v in GRIB_VARS}
        for fut in as_completed(futures):
            varname, data, meta = fut.result()
            grids[varname] = data
            if meta_r is None: meta_r = meta
    print(f"      Totale download: {time.time()-t_dl:.1f}s", flush=True)

    print("[4/5] Step corrente...", flush=True)
    lats        = meta_r['lats']
    lons        = meta_r['lons']
    vtimes      = meta_r['valid_times'].astype('datetime64[s]').astype(datetime)
    now_utc     = datetime.now(timezone.utc).replace(tzinfo=None)
    step_now    = int(np.argmin([abs((vt - now_utc).total_seconds()) for vt in vtimes]))
    step_24h    = min(step_now + 24, len(vtimes) - 1)
    valid_str   = vtimes[step_now].strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"      step={step_now} → {valid_str} | +24h=step{step_24h}", flush=True)

    print(f"[5/5] Estrazione vettorizzata + scrittura shard...", flush=True)
    t_ex = time.time()

    # Indici nearest-neighbour (tutti i comuni in una sola operazione)
    ilats = np.argmin(np.abs(lats[:,None] - lat_arr[None,:]), axis=0)
    ilons = np.argmin(np.abs(lons[:,None] - lon_arr[None,:]), axis=0)

    t2m_now   = grids['T_2M'][step_now,  ilats, ilons] - 273.15
    t2m_max24 = grids['T_2M'][step_now:step_24h, ilats, ilons].max(axis=0) - 273.15
    t2m_min24 = grids['T_2M'][step_now:step_24h, ilats, ilons].min(axis=0) - 273.15
    prec_24h  = np.maximum(0, grids['TOT_PREC'][step_24h, ilats, ilons]
                              - grids['TOT_PREC'][step_now, ilats, ilons])
    relhum    = grids['RELHUM'][step_now, ilats, ilons]
    u10       = grids['U_10M'][step_now, ilats, ilons]
    v10       = grids['V_10M'][step_now, ilats, ilons]
    wind_kmh  = np.sqrt(u10**2 + v10**2) * 3.6
    wind_dir  = (270 - np.degrees(np.arctan2(v10, u10))) % 360
    raffica   = grids['VMAX_10M'][step_now:step_24h, ilats, ilons].max(axis=0) * 3.6
    nuv       = grids['CLCT'][step_now, ilats, ilons]
    neve_cm   = grids['H_SNOW'][step_now, ilats, ilons] * 100
    ww_vals   = grids['WW'][step_now, ilats, ilons]

    for i, istat in enumerate(istat_list):
        shard = {
            "fonte"              : "ItaliaMeteo ICON-2I",
            "licenza"            : "CC BY 4.0",
            "run_utc"            : run,
            "valid_time_utc"     : valid_str,
            "t2m_c"              : round(float(t2m_now[i]),   1),
            "t2m_max24h_c"       : round(float(t2m_max24[i]), 1),
            "t2m_min24h_c"       : round(float(t2m_min24[i]), 1),
            "prec_24h_mm"        : round(float(prec_24h[i]),  1),
            "umidita_pct"        : round(float(relhum[i])),
            "vento_kmh"          : round(float(wind_kmh[i]),  1),
            "vento_dir_deg"      : round(float(wind_dir[i])),
            "raffica_max24h_kmh" : round(float(raffica[i]),   1),
            "nuvolosita_pct"     : round(float(nuv[i])),
            "neve_cm"            : round(float(neve_cm[i]),   1),
            "ww"                 : int(round(float(ww_vals[i]))),
            "ww_desc"            : ww_label(float(ww_vals[i])),
        }
        with open(f"{OUT_DIR}/{istat}.json", "w") as f:
            json.dump(shard, f, ensure_ascii=False)

    elapsed = time.time() - t0
    print(f"      Scritti {len(istat_list)} shard in {time.time()-t_ex:.1f}s", flush=True)
    print(f"\n✓ ETL completato in {elapsed:.1f}s totali", flush=True)
    print("\nVerifica esempi:", flush=True)
    for istat, nome in [("075035","Lecce"),("077014","Matera"),("058091","Roma")]:
        d = json.load(open(f"{OUT_DIR}/{istat}.json"))
        print(f"  {nome}: {d['t2m_c']}°C | max={d['t2m_max24h_c']}°C | "
              f"prec={d['prec_24h_mm']}mm | vento={d['vento_kmh']}km/h | {d['ww_desc']}", flush=True)

if __name__ == "__main__":
    main()
