"""
build_istat_coords.py — Genera istat-coords.json (centroidi dei comuni italiani).

INPUT:  /var/www/cruscotto-italia/data/dashboard/<istat>.json (7896 shard A1)
OUTPUT: /var/www/cruscotto-italia/data/istat-coords.json
SCHEMA: {istat_code: [lat, lon]}  (5 decimali, ~1m precisione)

Strategia di estrazione (prima fonte trovata vince):
  1. territorio.geo.extent      (OSM relation bbox - PREFERITO)
  2. anncsu.punti[] media        (campione civici)
  3. immobili_pa.punti[] media   (fallback)
  4. pun.punti[] media           (fallback)
  5. carburanti.punti[] media    (fallback)

Copertura attesa: 7895/7896 (99.99%).
Eseguire dopo ogni rigenerazione dashboard A1 (vedi dashboard.py).
"""
import json
from pathlib import Path
from statistics import mean
import sys

SHARD_DIR = Path("/var/www/cruscotto-italia/data/dashboard")
OUT_PATH = Path("/var/www/cruscotto-italia/data/istat-coords.json")

def extract_centroid(d):
    # 1. territorio.geo.extent (GeoJSON: [lon, lat])
    geo = (d.get("territorio") or {}).get("geo") or {}
    extent = geo.get("extent")
    if extent and len(extent) == 2 and len(extent[0]) == 2 and len(extent[1]) == 2:
        lon_min, lat_min = extent[0]
        lon_max, lat_max = extent[1]
        if all(isinstance(x, (int, float)) for x in [lat_min, lat_max, lon_min, lon_max]):
            return (lat_min + lat_max) / 2, (lon_min + lon_max) / 2, "osm_extent"

    # 2. Fallback: media ANNCSU
    ann = d.get("anncsu") or {}
    pts = ann.get("punti") or []
    if pts:
        lats = [p["lat"] for p in pts if isinstance(p.get("lat"), (int, float))]
        lons = [p["lon"] for p in pts if isinstance(p.get("lon"), (int, float))]
        if lats and lons:
            return mean(lats), mean(lons), "anncsu"

    # 3. Fallback: immobili PA, PUN, carburanti, aria (vedi v1)
    for section in ("immobili_pa", "pun", "carburanti"):
        s = d.get(section) or {}
        pts = s.get("punti") or []
        if pts:
            lats = [p["lat"] for p in pts if isinstance(p.get("lat"), (int, float))]
            lons = [p["lon"] for p in pts if isinstance(p.get("lon"), (int, float))]
            if lats and lons:
                return mean(lats), mean(lons), section
    return None

def main():
    coords = {}
    stats = {"osm_extent": 0, "anncsu": 0, "immobili_pa": 0, "pun": 0, "carburanti": 0, "missing": 0}
    files = sorted(SHARD_DIR.glob("*.json"))
    print(f"Processing {len(files)} shards...", file=sys.stderr, flush=True)

    for i, fp in enumerate(files):
        istat = fp.stem
        try:
            d = json.loads(fp.read_text())
            res = extract_centroid(d)
            if res:
                lat, lon, src = res
                coords[istat] = [round(lat, 5), round(lon, 5)]
                stats[src] += 1
            else:
                stats["missing"] += 1
        except Exception as e:
            stats["missing"] += 1
        if (i + 1) % 1000 == 0:
            print(f"  done={i+1}/{len(files)}", file=sys.stderr, flush=True)

    print(f"\nStats:", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k}: {v}", file=sys.stderr)
    print(f"Total coords: {len(coords)}/{len(files)}", file=sys.stderr)

    OUT_PATH.write_text(json.dumps(coords, separators=(",", ":")))
    size = OUT_PATH.stat().st_size
    print(f"Wrote {OUT_PATH} ({size} bytes, {size/1024:.1f} KB)", file=sys.stderr)

if __name__ == "__main__":
    main()
