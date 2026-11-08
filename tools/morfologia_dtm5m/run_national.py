#!/usr/bin/env python3
"""
run_national.py — Orchestratore batch nazionale della pipeline morfologica.

Per ogni comune (codice ISTAT) estrae il confine da un layer vettoriale nazionale,
poi esegue gli step: build_comune -> validate -> to_cog -> [make_tiles].
Parallelo, idempotente (resume), con log per-comune e report aggregato.

Esempio:
  python run_national.py \
      --boundaries Com01012025_WGS84.shp --istat-field PRO_COM_T \
      --dtm /data/HRDTM5m.tif --out out \
      --jobs 8 --solar-res 20 --tiles --minzoom 11 --maxzoom 14 \
      --grass-run "micromamba run -n grass" --resume

  # solo alcuni comuni:
  python run_national.py ... --comuni lista_istat.txt
  # primi N (test):
  python run_national.py ... --limit 5
"""
import os, sys, json, time, argparse, subprocess, tempfile, csv
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))


def boundary_from_censimento(cens_dir, istat, out_geojson, predecessori=()):
    """Confine comunale = dissolve delle sezioni di censimento_full/<istat>.geojson (EPSG:4326).
    Con predecessori (comuni soppressi confluiti dopo il 2021): unione del proprio confine,
    se esiste, con quelli dei predecessori."""
    import json
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    files = [os.path.join(cens_dir, f"{c}.geojson") for c in [istat, *predecessori]]
    files = [f for f in files if os.path.exists(f)]
    if not files:
        return False
    feats = []
    for fp in files:
        feats += json.load(open(fp))["features"]
    polys = []
    for f in feats:
        try:
            geom = shape(f["geometry"])
            if not geom.is_valid:
                geom = make_valid(geom)
            polys.append(geom)
        except Exception:
            continue
    u = unary_union(polys)
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "properties": {"istat": istat}, "geometry": mapping(u)}]}
    json.dump(fc, open(out_geojson, "w"))
    return True

def extract_boundary(boundaries, field, istat, out_geojson):
    """Estrae la feature del comune e scrive un GeoJSON (EPSG:4326) a singola feature."""
    import fiona
    from fiona.transform import transform_geom
    from fiona.model import to_dict
    with fiona.open(boundaries) as src:
        src_crs = src.crs
        for feat in src:
            val = str(feat["properties"].get(field, "")).zfill(6)
            if val != str(istat).zfill(6):
                continue
            geom = feat["geometry"]
            try:
                epsg = src_crs.to_epsg()
            except Exception:
                epsg = None
            if epsg and epsg != 4326:
                geom = transform_geom(src_crs, "EPSG:4326", geom)
            geom = to_dict(geom)   # fiona Geometry -> dict serializzabile
            fc = {"type": "FeatureCollection",
                  "features": [{"type": "Feature", "properties": {"istat": istat}, "geometry": geom}]}
            json.dump(fc, open(out_geojson, "w"))
            return True
    return False

def run_step(cmd, logf):
    with open(logf, "a") as lf:
        lf.write("\n$ " + " ".join(cmd) + "\n")
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
    return p.returncode

def process_one(args_tuple):
    (istat, boundaries, field, dtm, out, solar_res, skip_solar, tiles,
     minz, maxz, grass_run, area_csv, resume, cens_dir, predecessori) = args_tuple
    t0 = time.time()
    cdir = os.path.join(out, istat)
    logdir = os.path.join(out, "_logs"); os.makedirs(logdir, exist_ok=True)
    logf = os.path.join(logdir, f"{istat}.log")
    done_flag = os.path.join(cdir, ".done")
    if resume and os.path.exists(done_flag):
        return (istat, "skip", 0.0, "gia' fatto")
    os.makedirs(cdir, exist_ok=True)
    open(logf, "w").close()
    try:
        # 1. confine
        geo = os.path.join(cdir, "_boundary.geojson")
        ok_b = False
        if cens_dir:
            ok_b = boundary_from_censimento(cens_dir, istat, geo, predecessori)
        if not ok_b and boundaries:
            ok_b = extract_boundary(boundaries, field, istat, geo)
        if not ok_b:
            return (istat, "fail", time.time()-t0, "confine non trovato")
        # 2. build
        cmd = [sys.executable, os.path.join(HERE, "build_comune.py"),
               "--istat", istat, "--boundary", geo, "--dtm", dtm, "--out", out]
        if skip_solar: cmd += ["--skip-solar"]
        elif solar_res: cmd += ["--solar-res", str(solar_res)]
        if grass_run: cmd += ["--mm-run", grass_run]
        if run_step(cmd, logf) != 0:
            return (istat, "fail", time.time()-t0, "build_comune")
        # 3. validate
        vcmd = [sys.executable, os.path.join(HERE, "validate.py"), "--dir", cdir]
        if area_csv: vcmd += ["--istat-area-csv", area_csv]
        vrc = run_step(vcmd, logf)   # 0 ok, 1 warn, 2 fail
        # 4. to_cog + tiles SOLO se richiesto il tiling (deck.gl 3D non li usa)
        if tiles:
            if run_step([sys.executable, os.path.join(HERE, "to_cog.py"), "--dir", cdir], logf) != 0:
                return (istat, "fail", time.time()-t0, "to_cog")
            tcmd = [sys.executable, os.path.join(HERE, "make_tiles.py"),
                    "--dem", os.path.join(cdir, "cog", "dem.tif"),
                    "--layers", os.path.join(cdir, "cog"),
                    "--out", os.path.join(cdir, "tiles"),
                    "--minzoom", str(minz), "--maxzoom", str(maxz),
                    "--tex", "elevazione", "pendenze", "geomorfologia"] + (["solar"] if not skip_solar else [])
            if run_step(tcmd, logf) != 0:
                return (istat, "fail", time.time()-t0, "make_tiles")
        # area dai KPI
        area = 0.0
        sp = os.path.join(cdir, f"{istat}_stats.json")
        if os.path.exists(sp): area = json.load(open(sp)).get("area_km2", 0.0)
        status = "ok" if vrc == 0 else "warn" if vrc == 1 else "fail_validate"
        open(done_flag, "w").write(status)
        # pulizia confine temporaneo
        try: os.remove(geo)
        except OSError: pass
        return (istat, status, time.time()-t0, f"area {area} km2")
    except Exception as e:
        return (istat, "error", time.time()-t0, str(e)[:120])

def list_comuni(boundaries, field, comuni_file, limit, cens_dir=None):
    if comuni_file:
        ids = [l.strip().zfill(6) for l in open(comuni_file) if l.strip()]
    elif cens_dir:
        ids = sorted(f[:-8] for f in os.listdir(cens_dir) if f.endswith(".geojson"))
    else:
        import fiona
        with fiona.open(boundaries) as src:
            ids = sorted({str(f["properties"].get(field, "")).zfill(6) for f in src})
        ids = [i for i in ids if i and i != "000000"]
    if limit: ids = ids[:limit]
    return ids

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundaries", default=None, help="vettoriale nazionale comuni (SHP/GPKG/GeoJSON)")
    ap.add_argument("--censimento-dir", default=None,
                    help="cartella censimento_full/ con <istat>.geojson (confini locali VM)")
    ap.add_argument("--istat-field", default="PRO_COM_T")
    ap.add_argument("--dtm", required=True, help="DTM nazionale locale (consigliato) o URL vsicurl")
    ap.add_argument("--out", default="out")
    ap.add_argument("--comuni", default=None, help="file con lista ISTAT (uno per riga)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--solar-res", type=int, default=None)
    ap.add_argument("--skip-solar", action="store_true")
    ap.add_argument("--tiles", action="store_true")
    ap.add_argument("--minzoom", type=int, default=11)
    ap.add_argument("--maxzoom", type=int, default=14)
    ap.add_argument("--grass-run", default="")
    ap.add_argument("--area-csv", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--predecessori", default=os.path.join(HERE, "predecessori_post2021.json"),
                    help="JSON {istat: [codici soppressi confluiti]} per i confini post-2021")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    pred = {}
    if a.predecessori and os.path.exists(a.predecessori):
        pred = {k: v for k, v in json.load(open(a.predecessori)).items() if not k.startswith("_")}
    print(f"[batch] predecessori: {len(pred)} comuni con confine unito")
    ids = list_comuni(a.boundaries, a.istat_field, a.comuni, a.limit, a.censimento_dir)
    if not a.comuni and not a.limit:
        # comuni nati dopo il 2021: non hanno un file proprio in censimento_full
        ids = sorted(set(ids) | set(pred))
    # load balancing: comuni grandi processati per primi
    if a.area_csv and os.path.exists(a.area_csv):
        areas = {}
        for row in csv.reader(open(a.area_csv), delimiter=";"):
            if len(row) >= 2:
                try: areas[row[0].zfill(6)] = float(row[1])
                except ValueError: pass
        ids.sort(key=lambda i: areas.get(i, 0.0), reverse=True)
    print(f"[batch] {len(ids)} comuni | jobs={a.jobs} | solar={'no' if a.skip_solar else a.solar_res} | tiles={a.tiles}")
    tasks = [(i, a.boundaries, a.istat_field, a.dtm, a.out, a.solar_res, a.skip_solar,
              a.tiles, a.minzoom, a.maxzoom, a.grass_run, a.area_csv, a.resume, a.censimento_dir,
              pred.get(i, [])) for i in ids]
    report = os.path.join(a.out, "_report.csv")
    new = not os.path.exists(report)
    rf = open(report, "a", newline="")
    w = csv.writer(rf)
    if new: w.writerow(["istat", "status", "secondi", "note", "ts"])
    t0 = time.time(); tally = {}
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(process_one, t): t[0] for t in tasks}
        for n, fut in enumerate(as_completed(futs), 1):
            istat, status, secs, note = fut.result()
            tally[status] = tally.get(status, 0) + 1
            w.writerow([istat, status, round(secs, 1), note, int(time.time())]); rf.flush()
            print(f"  [{n}/{len(ids)}] {istat}: {status:13s} {secs:5.0f}s  {note}")
    rf.close()
    dt = time.time() - t0
    print(f"\n[batch] fine in {dt/60:.1f} min | {tally}")
    print(f"[batch] report: {report}")
    # ETA nazionale indicativa
    oks = [r for r in [tally.get('ok',0), tally.get('warn',0)] if r]
    if sum(oks) and not a.limit:
        pass
    elif sum(oks):
        avg = dt / max(sum(tally.values()), 1)
        print(f"[batch] media {avg:.0f}s/comune -> stima 7918 comuni: {avg*7918/3600:.0f} h-core")

if __name__ == "__main__":
    main()
