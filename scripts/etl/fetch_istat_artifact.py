#!/usr/bin/env python3
"""Scarica l'artifact `istat-raw` (workflow istat-download.yml) nella cache
degli ETL ISTAT sulla VM.

Il manifest.json dell'artifact dice, per ogni file, in quale cartella di
cache va messo e quante righe deve avere. Ogni file e scritto su .part e
rinominato solo se il numero di righe torna: un artifact troncato non
sostituisce una cache buona. Dopo questo script:

    python3 -m etl.sources.veicoli --solo-cache
    python3 -m etl.sources.istat_turismo --solo-cache

Variabili: GITHUB_TOKEN (Actions: read). Opzione --max-giorni (default 3):
rifiuta artifact piu vecchi.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = "AgID/cruscotto-italia"
ART_NAME = "istat-raw"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Il download risponde 302 verso uno storage firmato che rifiuta
    l'header Authorization: il redirect si segue a mano, senza token."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _req(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def scarica_zip(art_id: int, token: str) -> bytes:
    url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{art_id}/zip"
    try:
        return urllib.request.build_opener(NoRedirect).open(_req(url, token), timeout=120).read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return urllib.request.urlopen(e.headers["Location"], timeout=300).read()
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Artifact istat-raw -> cache ETL ISTAT")
    ap.add_argument("--max-giorni", type=float, default=3)
    args = ap.parse_args()
    token = os.environ["GITHUB_TOKEN"]

    print("[1/4] cerco artifact piu recente...", flush=True)
    url = f"https://api.github.com/repos/{REPO}/actions/artifacts?name={ART_NAME}&per_page=20"
    arts = [a for a in json.loads(urllib.request.urlopen(_req(url, token), timeout=60).read())
            ["artifacts"] if a["name"] == ART_NAME and not a["expired"]]
    if not arts:
        print(f"  ERRORE: nessun artifact {ART_NAME}", file=sys.stderr)
        return 1
    art = max(arts, key=lambda a: a["created_at"])
    creato = datetime.fromisoformat(art["created_at"].replace("Z", "+00:00"))
    eta = (datetime.now(timezone.utc) - creato).total_seconds() / 86400
    print(f"  id={art['id']} created={art['created_at']} size={art['size_in_bytes']} "
          f"eta={eta:.1f}gg", flush=True)
    if eta > args.max_giorni:
        print(f"  ERRORE: artifact piu vecchio di {args.max_giorni} giorni", file=sys.stderr)
        return 1

    print("[2/4] scarico zip artifact...", flush=True)
    zip_bytes = scarica_zip(art["id"], token)
    print(f"  {len(zip_bytes)} byte", flush=True)

    print("[3/4] estraggo...", flush=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        tgz = zf.read(next(n for n in zf.namelist() if n.endswith(".tar.gz")))
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        contenuto = {os.path.basename(m.name): tf.extractfile(m).read()
                     for m in tf.getmembers() if m.isfile()}
    manifest = json.loads(contenuto["manifest.json"])
    print(f"  anno_cap={manifest['anno_cap']} anno_fl={manifest['anno_fl']}", flush=True)

    # Prima si verificano tutti i file, poi si scrive: o tutto o niente
    for f in manifest["file"]:
        dati = contenuto.get(f["nome"])
        if dati is None:
            print(f"  ERRORE: {f['nome']} nel manifest ma non nell'artifact", file=sys.stderr)
            return 1
        if "righe" in f:
            righe = sum(1 for r in dati.decode("utf-8").splitlines()[1:] if r)
            if righe != f["righe"]:
                print(f"  ERRORE: {f['nome']} ha {righe} righe, attese {f['righe']}",
                      file=sys.stderr)
                return 1

    # Lo script gira come root (serve /etc/cruscotto-github.env): cartelle e
    # file vanno all'utente proprietario del repo, che esegue gli ETL
    owner = Path(__file__).resolve().parents[2].stat() if os.geteuid() == 0 else None
    cartelle = set()
    for f in manifest["file"]:
        cartella = Path(f["cache"])
        cartella.mkdir(parents=True, exist_ok=True)
        cartelle.add(cartella)
        dst = cartella / f["nome"]
        tmp = dst.with_name(dst.name + ".part")
        tmp.write_bytes(contenuto[f["nome"]])
        os.replace(tmp, dst)
        if owner:
            os.chown(dst, owner.st_uid, owner.st_gid)
        print(f"    -> {dst} ({dst.stat().st_size} byte"
              + (f", {f['righe']} righe)" if "righe" in f else ")"), flush=True)
    for cartella in cartelle:
        (cartella / "istat-raw-manifest.json").write_bytes(contenuto["manifest.json"])
        if owner:
            os.chown(cartella, owner.st_uid, owner.st_gid)
            os.chown(cartella / "istat-raw-manifest.json", owner.st_uid, owner.st_gid)
    print(f"[4/4] {len(manifest['file'])} file nella cache ETL", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
