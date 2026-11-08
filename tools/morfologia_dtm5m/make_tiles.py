#!/usr/bin/env python3
"""
make_tiles.py — Genera tile piramidali XYZ (Web Mercator 3857) per la webapp
MapLibre 3D nativa, a partire dagli asset di un comune.

Produce:
  tiles/terrain/{z}/{x}/{y}.png      terrain-RGB (terrarium)  -> raster-dem MapLibre
  tiles/<layer>/{z}/{x}/{y}.png      texture tematica          -> raster overlay

Riproietta da EPSG:6875 a EPSG:3857 per-tile. Per il nazionale conviene riproiettare
una volta a 3857 e ritagliare, ma per-comune questo basta.

Uso:
  python make_tiles.py --dem dtm.tif --layers out/077003/cog_opt \
      --out out/077003/tiles --minzoom 11 --maxzoom 14 \
      --tex elevazione pendenze geomorfologia
"""
import os, sys, json, argparse
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds as aff
import mercantile
from PIL import Image

TS = 256
WEBMERC = "EPSG:3857"
NODATA = -9999.0

PAL = {
 "elevazione": ("grad", [0,50,100,200,300,400,500,600,800],
   [[120,190,200],[90,170,120],[110,190,90],[160,210,90],[210,225,90],[235,220,80],[240,180,70],[235,140,55],[200,90,40],[60,40,35]]),
 "pendenze": ("grad", [2,5,10,15,25,35],
   [[40,120,60],[120,180,70],[200,210,80],[240,200,70],[235,150,55],[210,90,45],[170,40,40]]),
 "geomorfologia": ("cat", {1:[200,200,195],2:[200,40,40],3:[235,150,50],4:[235,200,120],5:[240,235,120],6:[120,190,110],7:[120,200,210],8:[150,180,210],9:[60,110,200],10:[110,60,160]}),
 "solar": ("cont", [1100,1500,1800,2000,2250,2500],
   [[40,20,90],[120,30,120],[190,55,90],[230,110,50],[245,170,50],[250,225,120]]),
}

def warp_tile(src_path, tile, band=1, resamp=Resampling.bilinear, src_nodata=None):
    """Riproietta la porzione del raster nel tile XYZ -> array 256x256 in 3857."""
    b = mercantile.xy_bounds(tile)   # mercator metri
    tr = aff(b.left, b.bottom, b.right, b.top, TS, TS)
    with rasterio.open(src_path) as s:
        dst = np.full((TS, TS), np.nan, "float32")
        reproject(rasterio.band(s, band), dst,
                  src_transform=s.transform, src_crs=s.crs,
                  dst_transform=tr, dst_crs=WEBMERC, resampling=resamp,
                  src_nodata=src_nodata if src_nodata is not None else s.nodata,
                  dst_nodata=np.nan)
    return dst

def terrarium(elev, valid):
    v = np.where(valid, elev, 0.0) + 32768.0
    R = np.floor(v/256).astype("uint8"); G = np.floor(v % 256).astype("uint8")
    B = np.floor((v - np.floor(v))*256).astype("uint8")
    return np.dstack([R, G, B, np.full(elev.shape, 255, "uint8")])

def colorize(name, arr, valid):
    kind = PAL[name][0]; out = np.zeros((*arr.shape, 3))
    if kind == "grad":
        _, bins, cols = PAL[name]; idx = np.digitize(arr, bins)
        for i, c in enumerate(cols): out[idx == i] = np.array(c)/255
    elif kind == "cat":
        cols = PAL[name][1]
        for k, c in cols.items(): out[np.round(arr).astype(int) == k] = np.array(c)/255
    else:  # cont
        _, pos, stops = PAL[name]; stops = np.array(stops)/255
        t = np.clip(np.interp(arr, pos, np.linspace(0,1,len(pos))), 0, 1); xs = np.linspace(0,1,len(stops))
        for i in range(3): out[..., i] = np.interp(t, xs, stops[:, i])
    rgb = (np.clip(out,0,1)*255).astype("uint8")
    a = np.where(valid, 255, 0).astype("uint8")
    return np.dstack([rgb, a])

def save_png(arr, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(arr, "RGBA").save(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="GeoTIFF DTM clip (EPSG:6875)")
    ap.add_argument("--layers", required=True, help="cartella cog/ con slope.tif, geomorph.tif, solar.tif")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minzoom", type=int, default=11)
    ap.add_argument("--maxzoom", type=int, default=14)
    ap.add_argument("--tex", nargs="*", default=["elevazione","pendenze"])
    a = ap.parse_args()

    with rasterio.open(a.dem) as s:
        from rasterio.warp import transform_bounds
        W,So,E,No = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
    layer_src = {"elevazione": (a.dem, 100.0, Resampling.bilinear, NODATA),  # decode: dem grezzo (scale gestita sotto)
                 "pendenze": (os.path.join(a.layers,"slope.tif"), 0.01, Resampling.bilinear, 65535),
                 "geomorfologia": (os.path.join(a.layers,"geomorph.tif"), 1.0, Resampling.nearest, 0),
                 "solar": (os.path.join(a.layers,"solar.tif"), 1.0, Resampling.bilinear, 65535)}

    counts = {"terrain":0}
    for z in range(a.minzoom, a.maxzoom+1):
        for t in mercantile.tiles(W, So, E, No, z):
            # terrain (sempre)
            elev = warp_tile(a.dem, t, src_nodata=NODATA)
            valid = np.isfinite(elev)
            if not valid.any():
                continue
            save_png(terrarium(elev, valid), os.path.join(a.out,"terrain",str(z),str(t.x),f"{t.y}.png"))
            counts["terrain"] += 1
            # texture richieste
            for name in a.tex:
                src, scale, rs, nd = layer_src[name]
                if not os.path.exists(src):
                    continue
                arr = warp_tile(src, t, resamp=rs, src_nodata=nd)
                v2 = np.isfinite(arr)
                arr = arr*scale if name=="pendenze" else arr  # slope uint16 -> gradi
                save_png(colorize(name, arr, v2), os.path.join(a.out,name,str(z),str(t.x),f"{t.y}.png"))
                counts[name] = counts.get(name,0)+1
    json.dump({"bounds":[W,So,E,No],"center":[(W+E)/2,(So+No)/2],
               "minzoom":a.minzoom,"maxzoom":a.maxzoom,"tex":a.tex},
              open(os.path.join(a.out,"tiles.json"),"w"))
    print("Tile generati:", counts)
    print("Bounds:", [round(x,4) for x in (W,So,E,No)])

if __name__ == "__main__":
    sys.exit(main())
