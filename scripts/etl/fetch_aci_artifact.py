import os, sys, json, urllib.request, urllib.error, tarfile, io, zipfile
from pathlib import Path

TOKEN = os.environ["GITHUB_TOKEN"]
REPO = "AgID/cruscotto-italia"
ART_NAME = "aci-iscrizioni-csv"
CACHE = Path("/tmp/cruscotto-veicoli-cache")

# Handler che NON segue i redirect automaticamente: il download artifact
# GitHub risponde 302 verso uno storage firmato (Azure) che rifiuta
# l'header Authorization. Dobbiamo seguire il redirect a mano, senza auth.
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_opener_noredir = urllib.request.build_opener(NoRedirect)

def gh_json(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(req, timeout=60)

def gh_download_follow(url):
    """Chiama l'endpoint con auth; se 302, segue il Location SENZA auth."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        resp = _opener_noredir.open(req, timeout=120)
        return resp.read()  # nessun redirect: leggo diretto
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers["Location"]
            # secondo hop: URL firmato, NIENTE header Authorization
            resp2 = urllib.request.urlopen(loc, timeout=120)
            return resp2.read()
        raise

print("[1/4] cerco artifact piu recente...", flush=True)
data = json.loads(gh_json("https://api.github.com/repos/" + REPO + "/actions/artifacts?per_page=50").read())
arts = [a for a in data["artifacts"] if a["name"] == ART_NAME and not a["expired"]]
if not arts:
    print("  ERRORE: nessun artifact " + ART_NAME, file=sys.stderr)
    sys.exit(1)
art = sorted(arts, key=lambda a: a["created_at"], reverse=True)[0]
art_id = art["id"]
print("  id=" + str(art_id) + " created=" + art["created_at"] + " size=" + str(art["size_in_bytes"]), flush=True)

print("[2/4] scarico zip artifact (gestione redirect)...", flush=True)
zip_bytes = gh_download_follow("https://api.github.com/repos/" + REPO + "/actions/artifacts/" + str(art_id) + "/zip")
print("  " + str(len(zip_bytes)) + " byte", flush=True)

print("[3/4] estraggo tar.gz interno...", flush=True)
CACHE.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
    inner = [n for n in zf.namelist() if n.endswith((".tar.gz", ".tgz"))]
    tgz = zf.read(inner[0])
n = 0
with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
    for m in tf.getmembers():
        if m.isfile() and m.name.endswith(".csv"):
            fn = os.path.basename(m.name)
            (CACHE / fn).write_bytes(tf.extractfile(m).read())
            print("    -> " + fn + " (" + str((CACHE / fn).stat().st_size) + " byte)", flush=True)
            n += 1
print("[4/4] estratti " + str(n) + " CSV in " + str(CACHE), flush=True)
