
#!/usr/bin/env python3
"""
validate.py — Validazione automatica degli asset morfologici di un comune.

Controlla file attesi, CRS, plausibilità statistica dei KPI, coerenza geometrica
e (se fornita) la superficie ISTAT ufficiale. Pensato per girare su 7918 comuni
in CI: exit code 0 = OK, 1 = warning, 2 = errori bloccanti.

Uso:
  python validate.py --dir out/077003
  python validate.py --dir out/077003 --istat-area-csv superfici_istat.csv
  # CSV: righe "ISTAT;superficie_kmq" (es. 077003;392.09)
"""
import os, sys, json, argparse, csv

OK, WARN, FAIL = "OK", "WARN", "FAIL"
EXPECT_CRS = "EPSG:6875"

class Report:
    def __init__(self): self.rows=[]
    def add(self, level, check, msg): self.rows.append((level, check, msg))
    def worst(self):
        if any(r[0]==FAIL for r in self.rows): return 2
        if any(r[0]==WARN for r in self.rows): return 1
        return 0
    def show(self, istat):
        ic={OK:"\u2713", WARN:"!", FAIL:"\u2717"}
        print(f"\n=== Validazione {istat} ===")
        for lv,ck,ms in self.rows:
            print(f"  [{ic[lv]}] {ck:22s} {ms}")
        rc=self.worst()
        print(f"  -> esito: {'OK' if rc==0 else 'WARNING' if rc==1 else 'FAIL'}")
        return rc

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="cartella out/<istat>")
    ap.add_argument("--istat-area-csv", default=None)
    ap.add_argument("--area-tol", type=float, default=8.0, help="tolleranza %% superficie ISTAT")
    a=ap.parse_args()
    R=Report()
    d=a.dir.rstrip("/")
    istat=os.path.basename(d)

    # 1. file attesi
    need=[f"{istat}_stats.json","bounds.json","terrain.png"]
    for f in need:
        R.add(OK if os.path.exists(os.path.join(d,f)) else FAIL, "file", f+(" presente" if os.path.exists(os.path.join(d,f)) else " MANCANTE"))
    texdir=os.path.join(d,"tex")
    ntex=len([x for x in os.listdir(texdir) if x.endswith(".png")]) if os.path.isdir(texdir) else 0
    R.add(OK if ntex>=3 else WARN, "texture", f"{ntex} texture trovate")

    # 2. stats plausibili
    sp=os.path.join(d,f"{istat}_stats.json")
    S={}
    if os.path.exists(sp):
        S=json.load(open(sp))
        def chk(cond, ck, ms, lvl=FAIL): R.add(OK if cond else lvl, ck, ms)
        chk(S.get("elev_min",-9999) < S.get("elev_max",-9999), "elevazione", f"{S.get('elev_min')}–{S.get('elev_max')} m")
        chk(-10 <= S.get("elev_min",0) <= 5000 and S.get("elev_max",0) <= 5000, "elev_range", "entro -10..5000 m")
        chk(0 <= S.get("slope_mean",-1) <= 90 and 0 <= S.get("slope_max",-1) <= 90, "pendenza", f"media {S.get('slope_mean')}° max {S.get('slope_max')}°")
        chk(S.get("slope_gt35_pct",0) <= S.get("slope_gt15_pct",0)+0.01, "pend_soglie", ">35% ≤ >15%")
        gm=S.get("geomorph",{})
        tot=round(sum(gm.values()),1) if gm else 0
        chk(abs(tot-100) <= 1.5, "geomorph_somma", f"Σ forme = {tot}%")
        ad=S.get("aspect_dist",{})
        adt=round(sum(ad.values()),1) if ad else 0
        chk(abs(adt-100) <= 1.5, "esposiz_somma", f"Σ settori = {adt}%")
        chk(S.get("tri_mean",-1) >= 0, "tri", f"medio {S.get('tri_mean')}")
        if "solar_mean" in S:
            sm=S["solar_mean"]
            chk(300 <= sm <= 2800, "solare", f"{sm} kWh/m²·anno (plausibile)", WARN)
        else:
            R.add(WARN, "solare", "assente (r.sun non eseguito)")
        # area > 0
        chk(S.get("area_km2",0) > 0, "area", f"{S.get('area_km2')} km²")

    # 3. CRS dei GeoTIFF derivati
    cogdir=os.path.join(d,"cog")
    if os.path.isdir(cogdir):
        try:
            import rasterio
            for tf in sorted(os.listdir(cogdir)):
                if not tf.endswith(".tif"): continue
                with rasterio.open(os.path.join(cogdir,tf)) as r:
                    crs_ok = r.crs and r.crs.to_string()==EXPECT_CRS
                    R.add(OK if crs_ok else WARN, "crs", f"{tf}: {r.crs.to_string() if r.crs else '?'}")
        except ImportError:
            R.add(WARN, "crs", "rasterio non disponibile, salto check CRS")

    # 4. coerenza dimensioni terrain/texture + non vuoto
    try:
        from PIL import Image
        tp=os.path.join(d,"terrain.png")
        if os.path.exists(tp):
            ts=Image.open(tp).size
            mism=[x for x in (os.listdir(texdir) if os.path.isdir(texdir) else []) if x.endswith(".png") and Image.open(os.path.join(texdir,x)).size!=ts]
            R.add(OK if not mism else FAIL, "dim_coerenza", "terrain e texture allineate" if not mism else f"disallineate: {mism}")
    except ImportError:
        R.add(WARN, "dim_coerenza", "PIL non disponibile")

    # 5. bounds dentro l'Italia
    bp=os.path.join(d,"bounds.json")
    if os.path.exists(bp):
        b=json.load(open(bp))
        inside = 6 < b.get("cx",0) < 19 and 35 < b.get("cy",0) < 48
        R.add(OK if inside else FAIL, "bounds", f"centro {b.get('cx',0):.3f},{b.get('cy',0):.3f}")

    # 6. superficie ISTAT (opzionale)
    if a.istat_area_csv and os.path.exists(a.istat_area_csv) and S:
        ref=None
        for row in csv.reader(open(a.istat_area_csv), delimiter=";"):
            if row and row[0].strip()==istat:
                try: ref=float(row[1]); break
                except: pass
        if ref:
            calc=S.get("area_km2",0); diff=abs(calc-ref)/ref*100
            R.add(OK if diff<=a.area_tol else WARN, "superficie_istat",
                  f"calc {calc} vs ISTAT {ref} km² (scarto {diff:.1f}%)")
        else:
            R.add(WARN, "superficie_istat", f"{istat} non in CSV")

    # 7. PAI / idrogeologico (placeholder)
    R.add(WARN, "confronto_pai", "non implementato (fase 2)")

    return R.show(istat)

if __name__=="__main__":
    sys.exit(main())
