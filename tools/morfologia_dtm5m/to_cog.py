#!/usr/bin/env python3
"""
to_cog.py — Converte i GeoTIFF derivati di un comune in veri COG (Cloud Optimized
GeoTIFF) con overview, e li quantizza per ridurne il peso 4-8x.

Trasformazioni:
  slope    float32 gradi   -> uint16  gradi*100   (nodata 65535)
  aspect   float32 gradi   -> uint16  gradi*10    (nodata 65535)
  geomorph byte (1..10)    -> byte    (invariato)  (nodata 0)
  solar    float32 kWh/m2  -> uint16  kWh*1        (nodata 65535)

Uso:
  python to_cog.py --dir out/077003
  python to_cog.py --dir out/077003 --keep-src   # non sovrascrive l'originale
"""
import os, sys, json, argparse
import numpy as np
import rasterio

# layer -> (scala, dtype, nodata, resampling overview)
SPEC = {
    "slope":    (100.0, "uint16", 65535, "average"),
    "aspect":   (10.0,  "uint16", 65535, "nearest"),
    "geomorph": (1.0,   "uint8",  0,     "nearest"),
    "solar":    (1.0,   "uint16", 65535, "average"),
}

def to_cog(src_path, dst_path, scale, dtype, nodata, ov_res):
    with rasterio.open(src_path) as s:
        a = s.read(1).astype("float32")
        src_nd = s.nodata
        prof = dict(driver="COG", height=s.height, width=s.width, count=1,
                    crs=s.crs, transform=s.transform, dtype=dtype, nodata=nodata,
                    compress="DEFLATE", blocksize=512, overview_resampling=ov_res,
                    BIGTIFF="IF_SAFER")
    mask = np.isnan(a)
    if src_nd is not None:
        mask |= (a == src_nd)
    if dtype != "uint8":
        a = a * scale
    out = np.where(mask, nodata, a)
    # clip al range del dtype
    info = np.iinfo(dtype)
    out = np.clip(out, info.min, info.max).astype(dtype)
    with rasterio.open(dst_path, "w", **prof) as d:
        d.write(out, 1)
    return os.path.getsize(dst_path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="cartella out/<istat>")
    ap.add_argument("--keep-src", action="store_true", help="scrive in cog_opt/ invece di sovrascrivere")
    a = ap.parse_args()
    cogdir = os.path.join(a.dir, "cog")
    if not os.path.isdir(cogdir):
        print("nessuna cartella cog/ in", a.dir); return 2
    outdir = os.path.join(a.dir, "cog_opt") if a.keep_src else cogdir
    os.makedirs(outdir, exist_ok=True)
    tot_before = tot_after = 0
    for layer, (scale, dtype, nd, ov) in SPEC.items():
        src = os.path.join(cogdir, f"{layer}.tif")
        if not os.path.exists(src):
            continue
        before = os.path.getsize(src)
        dst = os.path.join(outdir, f"{layer}.tif")
        tmp = dst + ".tmp" if outdir == cogdir else dst
        after = to_cog(src, tmp, scale, dtype, nd, ov)
        if outdir == cogdir:
            os.replace(tmp, dst)
        tot_before += before; tot_after += after
        print(f"  {layer:9s} {before//1024:6d} KB -> {after//1024:6d} KB  "
              f"({dtype}, x{scale:g}, ov={ov})")
    if tot_before:
        print(f"  TOTALE    {tot_before//1024:6d} KB -> {tot_after//1024:6d} KB  "
              f"(riduzione {100*(1-tot_after/tot_before):.0f}%)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
