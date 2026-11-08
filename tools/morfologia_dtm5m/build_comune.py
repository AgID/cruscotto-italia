#!/usr/bin/env python3
"""
build_comune.py — Pipeline morfologica per un comune italiano (HR-DTM-5m).

Industrializza il workflow validato su Matera. Per ogni comune produce:
  out/<istat>/<istat>_stats.json     KPI morfologici
  out/<istat>/terrain.png            terrain-RGB (terrarium) per deck.gl/MapLibre 3D
  out/<istat>/tex/<layer>.png        texture tematiche (4326)
  out/<istat>/cog/<layer>.tif        COG dei derivati (per tiling/produzione)

Stack: rasterio (clip/encoding) + GRASS GIS (r.slope.aspect, r.geomorphon, r.sun)
       eseguito via `grass ... --exec`. Su infra usare l'ambiente micromamba 'qgis'/'grass'
       di qgis_headless_wsl2 (Fiandaca).

Esempi
  # comune singolo via vsicurl (no download 22GB), senza r.sun:
  python build_comune.py --istat 077003 --boundary matera.geojson --skip-solar

  # con DTM nazionale locale + r.sun a 20m:
  python build_comune.py --istat 077003 --boundary matera.geojson \
      --dtm /data/HRDTM5m.tif --solar-res 20

Requisiti: rasterio, numpy, scipy, pillow ; GRASS nel PATH (o --grass-bin / --mm-run).
"""
import os, sys, json, argparse, subprocess, tempfile, shutil
import numpy as np

DTM_URL = "https://zenodo.org/records/24009736/files/HRDM5m_20261030_cog.tif?download=1"  # HR-DTM-5m v1.3 (06/11/2026)
DTM_CRS = "EPSG:6875"
NODATA  = -9999.0

# convenzione 10 forme r.geomorphon
FORMS = ["Pianoro","Cima","Cresta","Spalla","Dosso","Versante","Conca","Piede","Valle","Depressione"]

def log(m): print(f"[build] {m}", flush=True)

# ---------------------------------------------------------------- clip DTM
def clip_dtm(dtm_src, boundary_geojson, out_tif):
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds, transform_geom
    from rasterio.features import geometry_mask
    os.environ.setdefault("GDAL_HTTP_UNSAFESSL", "YES")
    os.environ.setdefault("CPL_VSIL_CURL_USE_HEAD", "NO")
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")

    geo = json.load(open(boundary_geojson))
    geom = geo["features"][0]["geometry"] if geo.get("type")=="FeatureCollection" else geo.get("geometry", geo)
    # bbox del confine -> EPSG:6875
    cc=[]
    def walk(c):
        if isinstance(c[0],(int,float)): cc.append(c)
        else:
            for x in c: walk(x)
    walk(geom["coordinates"])
    xs=[p[0] for p in cc]; ys=[p[1] for p in cc]
    bb4326=(min(xs),min(ys),max(xs),max(ys))
    bb=transform_bounds("EPSG:4326", DTM_CRS, *bb4326)

    src = "/vsicurl/"+dtm_src if dtm_src.startswith("http") else dtm_src
    with rasterio.open(src) as ds:
        win=from_bounds(*bb, ds.transform)
        data=ds.read(1, window=win)
        tr=ds.window_transform(win)
        prof=ds.profile.copy()
    g6875=transform_geom("EPSG:4326", DTM_CRS, geom)
    cm=geometry_mask([g6875], out_shape=data.shape, transform=tr, invert=True)
    # NoData del sorgente normalizzato a NODATA: v1.1 usa -9999, v1.3 usa NaN
    src_nd = prof.get("nodata")
    valid = cm & np.isfinite(data)
    if src_nd is not None and np.isfinite(src_nd):
        valid &= (data != src_nd)
    data=np.where(valid, data, NODATA).astype("float32")
    prof.update(height=data.shape[0], width=data.shape[1], transform=tr, dtype="float32",
                nodata=NODATA, compress="DEFLATE", predictor=3, tiled=True,
                blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
    import rasterio as rio
    with rio.open(out_tif,"w",**prof) as dst: dst.write(data,1)
    return out_tif, geom

# ---------------------------------------------------------------- GRASS
def grass_derive(grass_run, dtm_tif, workdir, solar_res=None):
    """Esegue r.slope.aspect, r.geomorphon e (opz.) r.sun. Ritorna dict di GeoTIFF esportati."""
    gdb=os.path.join(workdir,"grassdb")
    if os.path.exists(gdb): shutil.rmtree(gdb)   # grass -c crea lui la project dir
    script=os.path.join(workdir,"job.sh")
    out={k:os.path.join(workdir,f"{k}.tif") for k in ["slope","aspect","geomorph"]}
    solar_block=""
    if solar_res:
        out["solar"]=os.path.join(workdir,"solar.tif")
        solar_block=f"""
g.region raster=dtm res={solar_res} -a
r.resamp.stats input=dtm output=dtmS method=average --quiet --overwrite
r.slope.aspect elevation=dtmS slope=slopeS aspect=aspectS --quiet --overwrite
for d in 80 172 264 355; do
  r.sun elevation=dtmS aspect=aspectS slope=slopeS day=$d step=1.0 glob_rad=g_$d --quiet --overwrite
done
r.mapcalc expression="solar=(g_80+g_172+g_264+g_355)/4.0*365.0/1000.0" --overwrite --quiet
r.out.gdal -f input=solar output={out['solar']} type=Float32 format=GTiff --quiet --overwrite createopt="COMPRESS=DEFLATE"
g.region raster=dtm
"""
    with open(script,"w") as f:
        f.write(f"""set -e
r.in.gdal input={dtm_tif} output=dtm --quiet --overwrite
g.region raster=dtm
r.slope.aspect elevation=dtm slope=slope aspect=aspect --quiet --overwrite
r.geomorphon elevation=dtm forms=geomorph search=15 --quiet --overwrite
r.out.gdal input=slope    output={out['slope']}    type=Float32 format=GTiff --quiet --overwrite createopt="COMPRESS=DEFLATE"
r.out.gdal input=aspect   output={out['aspect']}   type=Float32 format=GTiff --quiet --overwrite createopt="COMPRESS=DEFLATE"
r.out.gdal -f input=geomorph output={out['geomorph']} type=Byte    format=GTiff --quiet --overwrite createopt="COMPRESS=DEFLATE"
{solar_block}
""")
    cmd = grass_run + ["grass","-c",dtm_tif, gdb, "--exec","bash",script]
    subprocess.run(cmd, check=True)
    return out

# ---------------------------------------------------------------- KPI
def compute_kpi(istat, dtm_tif, der, geom, area_km2_hint=None):
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from scipy.ndimage import uniform_filter
    src=rasterio.open(dtm_tif); z=src.read(1).astype("float32"); z[z==NODATA]=np.nan
    g6875=transform_geom("EPSG:4326", src.crs.to_string(), geom)
    cm=geometry_mask([g6875], out_shape=z.shape, transform=src.transform, invert=True)
    m=cm & np.isfinite(z)
    slope=rasterio.open(der["slope"]).read(1).astype("float32")
    asp=rasterio.open(der["aspect"]).read(1).astype("float32")
    geo=rasterio.open(der["geomorph"]).read(1)
    S={"istat":istat,"area_km2":round(float(m.sum())*25/1e6,1),
       "elev_min":round(float(np.nanmin(z[m]))),"elev_max":round(float(np.nanmax(z[m]))),
       "elev_mean":round(float(np.nanmean(z[m])))}
    sl=slope[m & np.isfinite(slope)]
    S.update(slope_mean=round(float(sl.mean()),1), slope_max=round(float(sl.max()),1),
             slope_gt15_pct=round(float((sl>15).mean()*100),1),
             slope_gt35_pct=round(float((sl>35).mean()*100),1))
    dirs=["N","NE","E","SE","S","SO","O","NO"]
    a=asp[m & np.isfinite(asp) & (asp>=0)]
    # GRASS aspect: gradi CCW da Est -> converto a azimuth da Nord
    az=(90-a)%360
    idx=((az+22.5)//45 %8).astype(int); cnt=np.bincount(idx,minlength=8)
    dom=int(cnt.argmax())
    S["aspect_dom"]=dirs[dom]; S["aspect_dom_pct"]=round(float(cnt[dom]/cnt.sum()*100),1)
    S["aspect_dist"]={dirs[i]:round(float(cnt[i]/cnt.sum()*100),1) for i in range(8)}
    zf=np.nan_to_num(z); tri=np.zeros_like(z)
    for dy in(-1,0,1):
        for dx in(-1,0,1):
            if dy or dx: tri+=np.abs(z-np.roll(np.roll(z,dy,0),dx,1))
    tri/=8
    S["tri_mean"]=round(float(np.nanmean(tri[m])),2); S["tri_max"]=round(float(np.nanmax(tri[m])))
    gv=geo[m & (geo>=1)&(geo<=10)]; tot=max(gv.size,1)
    gd={FORMS[i]:round(float((gv==i+1).sum()/tot*100),1) for i in range(10)}
    S["geomorph"]=gd
    S["geo_versanti"]=gd["Versante"]
    S["geo_creste"]=round(gd["Cresta"]+gd["Dosso"]+gd["Cima"]+gd["Spalla"],1)
    S["geo_impluvi"]=round(gd["Valle"]+gd["Conca"]+gd["Piede"]+gd["Depressione"],1)
    S["geo_pianori"]=gd["Pianoro"]
    if "solar" in der and os.path.exists(der["solar"]):
        sol=rasterio.open(der["solar"]).read(1).astype("float32")
        sv=sol[(sol>0) & np.isfinite(sol)]
        if sv.size:
            S.update(solar_mean=round(float(sv.mean())), solar_min=round(float(sv.min())),
                     solar_max=round(float(sv.max())))
    return S

# ---------------------------------------------------------------- assets 3D (4326)
def build_assets(dtm_tif, der, geom, outdir, max_w=1300):
    import rasterio
    from rasterio.warp import reproject, Resampling, transform_bounds
    from rasterio.transform import from_bounds as aff_from_bounds
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from PIL import Image
    src=rasterio.open(dtm_tif)
    W,So,E,No=transform_bounds(src.crs,"EPSG:4326",*src.bounds)
    TW=max_w; TH=int(TW*(No-So)/(E-W)); dst=aff_from_bounds(W,So,E,No,TW,TH)
    def warp(path,resamp=Resampling.bilinear,nd=None):
        s=rasterio.open(path); o=np.full((TH,TW),np.nan,"float32")
        reproject(rasterio.band(s,1),o,src_transform=s.transform,src_crs=s.crs,
                  dst_transform=dst,dst_crs="EPSG:4326",resampling=resamp,
                  src_nodata=nd if nd is not None else s.nodata)
        return o
    dtm=warp(dtm_tif,nd=NODATA)
    g=transform_geom("EPSG:4326","EPSG:4326",geom)
    cm=geometry_mask([g],out_shape=(TH,TW),transform=dst,invert=True)
    valid=cm & np.isfinite(dtm)
    os.makedirs(os.path.join(outdir,"tex"),exist_ok=True)
    # terrain-RGB terrarium
    v=np.where(valid,dtm,0.0)+32768.0
    alpha=np.where(valid,255,0).astype("uint8")
    terr=np.dstack([np.floor(v/256).astype("uint8"),np.floor(v%256).astype("uint8"),
                    np.floor((v-np.floor(v))*256).astype("uint8"),alpha])
    Image.fromarray(terr,"RGBA").save(os.path.join(outdir,"terrain.png"))
    GREY=(230,233,238)
    def savetex(name,rgb):
        out=(np.clip(rgb,0,1)*255).astype("uint8")
        a=np.where(valid,255,0).astype("uint8")
        arr=np.dstack([out,a]); arr[a<10]=[*GREY,255]
        Image.fromarray(arr,"RGBA").save(os.path.join(outdir,"tex",f"{name}.png"))
    def grad(val,bins,cols):
        out=np.zeros((TH,TW,3)); idx=np.digitize(val,bins)
        for i,c in enumerate(cols): out[idx==i]=np.array(c)/255
        return out
    ec=[[120,190,200],[90,170,120],[110,190,90],[160,210,90],[210,225,90],[235,220,80],[240,180,70],[235,140,55],[200,90,40],[60,40,35]]
    savetex("elevazione",grad(dtm,[0,50,100,200,300,400,500,600,800],ec))
    slope=warp(der["slope"])
    sc=[[40,120,60],[120,180,70],[200,210,80],[240,200,70],[235,150,55],[210,90,45],[170,40,40]]
    savetex("pendenze",grad(slope,[2,5,10,15,25,35],sc))
    geo=warp(der["geomorph"],resamp=Resampling.nearest,nd=0)
    gcols={1:[200,200,195],2:[200,40,40],3:[235,150,50],4:[235,200,120],5:[240,235,120],6:[120,190,110],7:[120,200,210],8:[150,180,210],9:[60,110,200],10:[110,60,160]}
    grgb=np.zeros((TH,TW,3))
    for k,c in gcols.items(): grgb[np.round(geo).astype(int)==k]=np.array(c)/255
    savetex("geomorfologia",grgb)
    if "solar" in der and os.path.exists(der["solar"]):
        sol=warp(der["solar"])
        t=np.clip(np.interp(sol,[1100,1500,1800,2000,2250,2500],np.linspace(0,1,6)),0,1)
        st=np.array([[40,20,90],[120,30,120],[190,55,90],[230,110,50],[245,170,50],[250,225,120]])/255
        rgb=np.zeros((TH,TW,3)); xs=np.linspace(0,1,6)
        for i in range(3): rgb[...,i]=np.interp(t,xs,st[:,i])
        savetex("solar",rgb)
    json.dump({"west":W,"south":So,"east":E,"north":No,"cx":(W+E)/2,"cy":(So+No)/2},
              open(os.path.join(outdir,"bounds.json"),"w"))
    return (TW,TH,(W,So,E,No))

# ---------------------------------------------------------------- main
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--istat", required=True)
    ap.add_argument("--boundary", required=True, help="GeoJSON confine comunale (EPSG:4326)")
    ap.add_argument("--dtm", default=DTM_URL, help="URL vsicurl o path GeoTIFF nazionale locale")
    ap.add_argument("--out", default="out")
    ap.add_argument("--solar-res", type=int, default=None, help="es. 20 ; assente o con --skip-solar = niente r.sun")
    ap.add_argument("--skip-solar", action="store_true")
    ap.add_argument("--grass-bin", default="grass")
    ap.add_argument("--mm-run", default="", help="prefisso micromamba, es. 'micromamba run -n grass'")
    ap.add_argument("--keep-tmp", action="store_true")
    a=ap.parse_args()
    grass_run = a.mm_run.split() if a.mm_run else []
    if not grass_run and a.grass_bin!="grass":
        grass_run=[]  # grass_bin gestito dentro grass_derive se serve
    solar_res=None if a.skip_solar else a.solar_res

    outdir=os.path.join(a.out, a.istat); os.makedirs(outdir, exist_ok=True)
    work=tempfile.mkdtemp(prefix=f"dtm_{a.istat}_")
    try:
        log(f"comune {a.istat} — clip DTM")
        dtm_tif, geom = clip_dtm(a.dtm, a.boundary, os.path.join(work,"dtm.tif"))
        log("derivati GRASS (r.slope.aspect, r.geomorphon"+(", r.sun" if solar_res else "")+")")
        der = grass_derive(grass_run, dtm_tif, work, solar_res=solar_res)
        log("KPI")
        S = compute_kpi(a.istat, dtm_tif, der, geom)
        json.dump(S, open(os.path.join(outdir,f"{a.istat}_stats.json"),"w"), indent=1, ensure_ascii=False)
        log("asset 3D (terrain-RGB + texture)")
        dims = build_assets(dtm_tif, der, geom, outdir)
        # COG e tiles disattivati: deck.gl 3D usa solo terrain.png + tex/
        log(f"OK -> {outdir}  (area {S['area_km2']} km2, grid {dims[0]}x{dims[1]})")
        print(json.dumps(S, indent=1, ensure_ascii=False))
    finally:
        if not a.keep_tmp: shutil.rmtree(work, ignore_errors=True)

if __name__=="__main__":
    main()
