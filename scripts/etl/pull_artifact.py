#!/usr/bin/env python3
"""
Cruscotto Italia — Pull artifact ETL da GitHub Actions.

Scarica l'ultimo artifact di un ETL ISTAT generato da un workflow Actions
del repo AgID/cruscotto-italia, lo estrae nella directory dati locale, e
lo elimina su GitHub (cleanup attivo per minimizzare esposizione).

Motivazione architetturale
==========================
Diversi ETL ISTAT (asia, veicoli, istat_profilo, istat_turismo, demografia)
e pendolarismo scaricano dati da `esploradati.istat.it`. L'endpoint applica
rate-limit aggressivo e ban host-based: una sequenza di chiamate da uno
stesso IP porta a ban (confermato su IP Aruba 2026-05-16).

Soluzione adottata:
  1. ETL gira su GitHub Actions runner (ubuntu-latest, IP Azure fresh)
  2. Runner scarica da ISTAT senza essere bannato
  3. Runner pacchetta gli shard prodotti come artifact GitHub (privato)
  4. Questo script su VM (Aruba/AgID FastWeb) scarica l'artifact via API
  5. Estrae in /var/www/cruscotto-italia/data/<source>/
  6. Elimina l'artifact da GitHub (audit-trail: dato sensibile non resta
     accessibile oltre il tempo strettamente necessario)

Conformità privacy AgID e auditabilità
======================================
- Repository GitHub `AgID/cruscotto-italia` e' PRIVATO. Solo collaboratori
  con permesso lettura possono vedere gli artifact tramite UI o API.
- Retention artifact: 1 giorno default (configurato lato workflow YAML).
  Questo script effettua anche cancellazione attiva post-download.
- I dati ISTAT scaricati sono open data ufficiali (licenza CC BY 3.0 IT).
  Nessuna PII personale, ma comunque trattati con minimizzazione esposizione.
- Audit-trail: ogni esecuzione produce log strutturato in stdout (cron
  ne fa tee su /var/log/cruscotto-etl/) con: source, run_id, artifact_id,
  bytes scaricati, files estratti, conclusioni cleanup.

Idempotenza
===========
Lo script mantiene uno state-file in DATA_DIR/<source>/_artifact_meta.json
con `last_artifact_id` e `downloaded_at`. Se il piu' recente artifact su
GitHub e' gia' stato scaricato, esce con stato 0 senza ri-estrazione.
Override via --force per re-download.

Variabili d'ambiente richieste
==============================
  GITHUB_TOKEN   PAT con scope: Contents:Read, Actions:Read (per scaricare
                 artifact). Si raccomanda anche Actions:Write per cleanup
                 attivo (delete). Senza Write, lo script salta delete e
                 logga warning.
  GITHUB_REPO    Default: "AgID/cruscotto-italia"
  DATA_DIR       Default: "/var/www/cruscotto-italia/data"

Uso
===
  # Download ETL asia (artifact name = "asia-shards", inferito da source)
  python3 pull_artifact.py asia

  # Force re-download anche se gia' fresco
  python3 pull_artifact.py asia --force

  # Workflow name custom (se differente da default "etl-<source>-refresh.yml")
  python3 pull_artifact.py veicoli --workflow etl-istat-refresh.yml

  # Dry-run: stampa info ma non scarica
  python3 pull_artifact.py asia --dry-run

Exit codes
==========
  0 = OK (download + estrazione + cleanup) o skip idempotente
  1 = Errore generico
  2 = Token mancante o invalido
  3 = Nessun artifact disponibile per il source (workflow mai eseguito)
  4 = Errore di estrazione (tar/zip corrotto)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_REPO = "AgID/cruscotto-italia"
DEFAULT_DATA_DIR = "/var/www/cruscotto-italia/data"
USER_AGENT = "CruscottoItalia-PullArtifact/1.0"

# Max bytes to download (sanity check, evita pull-bomb se artifact corrotto)
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# Mapping source (= nome modulo Python sotto etl/sources/) -> nome cartella
# di output sotto DATA_DIR. La maggior parte degli ETL ha source==output_dir
# (es. asia, anncsu, pendolarismo, sanita_mds, immobili_pa). Le eccezioni
# sono i moduli ISTAT che usano il prefix 'istat_' come convenzione di
# codice ma scrivono in cartelle senza prefix (per coerenza coi path
# stabilizzati di dashboard.py e frontend).
#
# Se un source non e' in questa mappa, output_dir == source name.
SOURCE_TO_OUTPUT_DIR = {
    "istat_profilo": "profilo",
    "istat_turismo": "turismo",
    # NB: pendolarismo, asia, anncsu, sanita_mds, immobili_pa, runts, ecc.
    # hanno output_dir == source name, quindi non vanno qui.
}


def _log(level: str, event: str, **kwargs) -> None:
    """Log strutturato JSON-line su stderr (audit-trail)."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": level,
        "event": event,
        **kwargs,
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)


def gh_api(path: str, token: str, method: str = "GET",
           params: dict | None = None,
           extra_headers: dict | None = None,
           return_response: bool = False):
    """Chiamata GitHub API. Ritorna JSON dict o raw response."""
    url = f"{GITHUB_API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, method=method, headers=headers)
    try:
        resp = urlopen(req, timeout=60)
        if return_response:
            return resp
        body = resp.read()
        if not body:
            return None
        return json.loads(body)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        _log("error", "github_api_error",
             path=path, method=method, status=e.code, body=body)
        if e.code == 401:
            _log("error", "github_token_invalid",
                 hint="Verifica scope: Contents:Read + Actions:Read minimo")
            sys.exit(2)
        raise


def list_workflow_runs(repo: str, workflow: str, token: str,
                        per_page: int = 10) -> list[dict]:
    """Lista i run successful piu' recenti per un workflow.

    Se il workflow non esiste (404), ritorna [] gracefully invece di sollevare.
    """
    try:
        data = gh_api(
            f"/repos/{repo}/actions/workflows/{workflow}/runs",
            token,
            params={"status": "success", "per_page": per_page},
        )
    except HTTPError as e:
        if e.code == 404:
            _log("warning", "workflow_not_found", workflow=workflow,
                 hint="Il file workflow YAML potrebbe non essere ancora committato")
            return []
        raise
    return data.get("workflow_runs", []) if data else []


def list_run_artifacts(repo: str, run_id: int, token: str) -> list[dict]:
    """Lista gli artifact di un workflow run."""
    data = gh_api(
        f"/repos/{repo}/actions/runs/{run_id}/artifacts",
        token,
    )
    return data.get("artifacts", []) if data else []


def find_latest_artifact(repo: str, workflow: str, artifact_name: str,
                         token: str) -> dict | None:
    """Trova l'ultimo artifact con nome dato fra i run successful del workflow.

    Scorre i 10 run piu' recenti (per_page=10). Se nessun artifact trovato,
    ritorna None.
    """
    runs = list_workflow_runs(repo, workflow, token, per_page=10)
    if not runs:
        _log("warning", "no_workflow_runs", workflow=workflow)
        return None
    for run in runs:
        if run.get("conclusion") != "success":
            continue
        artifacts = list_run_artifacts(repo, run["id"], token)
        for art in artifacts:
            if art["name"] == artifact_name and not art.get("expired"):
                # Restituisce arricchito con run info
                art["_run_id"] = run["id"]
                art["_run_url"] = run.get("html_url")
                art["_run_created_at"] = run.get("created_at")
                return art
    _log("warning", "no_matching_artifact",
         workflow=workflow, artifact_name=artifact_name,
         runs_scanned=len(runs))
    return None


def download_artifact_zip(repo: str, artifact_id: int, token: str,
                          dest_zip: Path) -> int:
    """Scarica l'artifact ZIP. GitHub redirige a un URL pre-signed S3/Azure.

    Implementazione in 2 step esplicita per gestire correttamente il redirect
    cross-domain:
      1. GET /repos/.../artifacts/<id>/zip con Bearer PAT, NO follow redirect
         -> GitHub risponde 302 con Location: URL pre-signed (azure/s3)
      2. GET <URL pre-signed> SENZA header Authorization
         -> Azure/S3 servono il blob con la firma URL stessa

    Senza lo step esplicito, urllib seguirebbe il redirect mantenendo il
    header Authorization, che Azure rigetterebbe con 401 InvalidAuthenticationInfo
    (Azure non capisce il Bearer GitHub).

    Ritorna il numero di bytes scaricati.
    """
    import urllib.request

    # === Step 1: chiama API GitHub, blocca redirect, prendi Location ===
    api_url = f"{GITHUB_API_BASE}/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }

    # Opener che NON segue redirect: cattura il 302 come HTTPError per leggere Location
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None  # disabilita follow

    opener = urllib.request.build_opener(_NoRedirect)
    req = Request(api_url, method="GET", headers=headers)
    try:
        # Senza follow redirect, GitHub 302 viene sollevato come HTTPError 302
        # In alcuni Python versions il return None del handler propaga l'errore.
        # Se invece il response arriva senza errore (caso impossibile per artifact/zip)
        # gestiamo entrambi i path.
        resp = opener.open(req, timeout=60)
        # Se siamo qui senza HTTPError, presumiamo che la response stessa abbia il body
        # (caso anomalo, GitHub dovrebbe sempre 302 su questo endpoint)
        location = resp.headers.get("Location")
        if not location:
            # Tutto inline, niente redirect: scarica direttamente
            return _stream_to_file(resp, dest_zip)
    except HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            location = e.headers.get("Location")
            if not location:
                raise RuntimeError(
                    f"GitHub artifact API ha risposto {e.code} senza header Location"
                )
        else:
            # Errore vero: 401, 404, ecc. Re-raise per gestione standard
            body = e.read().decode("utf-8", errors="replace")[:500]
            _log("error", "github_api_error",
                 path=f"/repos/{repo}/actions/artifacts/{artifact_id}/zip",
                 method="GET", status=e.code, body=body)
            if e.code == 401:
                _log("error", "github_token_invalid",
                     hint="Verifica scope: Contents:Read + Actions:Read minimo")
                sys.exit(2)
            raise

    # === Step 2: scarica dal pre-signed URL SENZA Bearer GitHub ===
    # Azure/S3 verifica la firma nell'URL stesso, gli header Authorization GitHub
    # provocherebbero 401 InvalidAuthenticationInfo.
    _log("info", "artifact_following_redirect",
         host=location.split("/")[2] if "//" in location else "?")
    download_headers = {
        "User-Agent": USER_AGENT,
        # NIENTE Authorization, NIENTE Accept GitHub-specific
    }
    download_req = Request(location, method="GET", headers=download_headers)
    download_resp = urlopen(download_req, timeout=300)  # 5 min per artifact grossi
    return _stream_to_file(download_resp, dest_zip)


def _stream_to_file(resp, dest_zip: Path) -> int:
    """Helper: streaming response -> file con cap MAX_ARTIFACT_BYTES."""
    total = 0
    with open(dest_zip, "wb") as fh:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                fh.close()
                dest_zip.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Artifact troppo grande (>{MAX_ARTIFACT_BYTES} bytes), "
                    "abort per sicurezza"
                )
            fh.write(chunk)
    return total


def delete_artifact(repo: str, artifact_id: int, token: str) -> bool:
    """Elimina l'artifact su GitHub. Ritorna True se OK, False se permission
    mancante (Actions:Write non concesso).
    """
    try:
        gh_api(
            f"/repos/{repo}/actions/artifacts/{artifact_id}",
            token,
            method="DELETE",
        )
        return True
    except HTTPError as e:
        if e.code in (403, 404):
            _log("warning", "artifact_delete_skipped",
                 artifact_id=artifact_id, reason=f"http_{e.code}",
                 hint="PAT non ha scope Actions:Write. Retention server "
                      "scadra' l'artifact automaticamente.")
            return False
        raise


def extract_archive(archive_path: Path, dest_dir: Path,
                    strip_top_dir: str | None = None) -> int:
    """Estrae l'archive (ZIP da GitHub puo' contenere un tar.gz interno).

    GitHub wrappa SEMPRE l'upload in uno ZIP esterno, quindi:
      - artifact1.zip contiene shards.tar.gz
      - shards.tar.gz contiene <prefix>/<istat>.json files
        dove <prefix> e' il nome della cartella relative al cwd usato
        nel workflow YAML (es. 'profilo/' per istat_profilo).

    Parametri:
      strip_top_dir: se valorizzato, e i membri del tar iniziano TUTTI con
        '<strip_top_dir>/', allora questo prefix viene strippato durante
        l'estrazione. Esempio: tar contiene 'profilo/001001.json',
        strip_top_dir='profilo' -> file finale: dest_dir/001001.json.
        Se valorizzato ma i membri NON matchano, log warning e nessuno
        strip (fallback safe).

    Ritorna numero di file estratti nel filesystem destinazione.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: unzip esterno (GitHub artifact wrapper)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(tmp)

        # Cerca file tar.gz / tgz / tar nel zip
        candidates = list(tmp.glob("*.tar.gz")) + list(tmp.glob("*.tgz")) + list(tmp.glob("*.tar"))
        if candidates:
            tar_path = candidates[0]
            _log("info", "found_inner_tar", path=str(tar_path.name),
                 size=tar_path.stat().st_size)

            with tarfile.open(tar_path, "r:*") as tf:
                all_members = tf.getmembers()

                # Decide se strip top dir e' applicabile in modo sicuro
                effective_strip = None
                if strip_top_dir:
                    prefix = strip_top_dir.rstrip("/") + "/"
                    # Tutti i membri devono iniziare con il prefix (o essere
                    # esattamente il prefix come dir entry). Altrimenti
                    # skip dello strip per sicurezza.
                    all_match = all(
                        m.name == strip_top_dir
                        or m.name.startswith(prefix)
                        for m in all_members
                    )
                    if all_match:
                        effective_strip = prefix
                        _log("info", "tar_strip_top_dir_applied",
                             prefix=strip_top_dir, members=len(all_members))
                    else:
                        _log("warning", "tar_strip_top_dir_not_applicable",
                             requested=strip_top_dir,
                             reason="not all members share prefix; extracting as-is")

                # Filtra membri unsafe (path assoluti, '..') E applica strip.
                # Costruisce una nuova lista di TarInfo con name aggiustato.
                safe_members = []
                for m in all_members:
                    if m.name.startswith("/") or ".." in m.name:
                        _log("warning", "tar_unsafe_member_skipped",
                             name=m.name)
                        continue
                    if effective_strip:
                        if m.name == strip_top_dir:
                            # E' la dir entry del top-level: skip (non un file da estrarre)
                            continue
                        new_name = m.name[len(effective_strip):]
                        if not new_name:
                            # E' diventato stringa vuota: skip
                            continue
                        m.name = new_name
                    safe_members.append(m)

                tf.extractall(dest_dir, members=safe_members)
                n = sum(1 for m in safe_members if m.isfile())
            return n
        else:
            # Niente tar interno: i file sono direttamente nel zip
            n = 0
            for f in tmp.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(tmp)
                    target = dest_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(f.read_bytes())
                    n += 1
            return n


def load_state(state_path: Path) -> dict:
    """Carica state-file con last_artifact_id, o {} se assente."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state_path: Path, state: dict) -> None:
    """Scrittura atomic dello state-file."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def discover_etl_workflows(repo: str, token: str) -> list[dict]:
    """Lista i workflow nel repo che matchano il pattern 'etl-<source>-refresh.yml'.

    Pattern di naming convenzionale: ogni ETL ISTAT / pesante ha un workflow
    dedicato chiamato 'etl-<source>-refresh.yml' (es. etl-asia-refresh.yml,
    etl-veicoli-refresh.yml, etc.).

    Ritorna lista di dict con keys: source, workflow_filename, workflow_id, state.
    Skippa workflow disabilitati.
    """
    import re
    pattern = re.compile(r"^etl-([a-z_]+)-refresh\.yml$")
    try:
        data = gh_api(
            f"/repos/{repo}/actions/workflows",
            token,
            params={"per_page": 100},
        )
    except HTTPError as e:
        _log("error", "workflows_list_failed", status=e.code)
        return []
    workflows = []
    for wf in data.get("workflows", []):
        # path es. ".github/workflows/etl-asia-refresh.yml" -> filename
        filename = wf.get("path", "").split("/")[-1]
        m = pattern.match(filename)
        if not m:
            continue
        if wf.get("state") != "active":
            _log("info", "workflow_disabled_skipped",
                 filename=filename, state=wf.get("state"))
            continue
        workflows.append({
            "source": m.group(1),
            "workflow_filename": filename,
            "workflow_id": wf["id"],
            "state": wf.get("state"),
        })
    return workflows


def pull_one_source(source: str, repo: str, token: str,
                     data_dir: Path, workflow: str | None = None,
                     artifact_name: str | None = None,
                     force: bool = False, dry_run: bool = False,
                     keep_artifact: bool = False) -> tuple[int, dict]:
    """Pull artifact per un singolo ETL.

    Ritorna (exit_code, summary_dict) con keys:
       status: "downloaded" | "skipped_idempotent" | "no_artifact" | "error"
       files_extracted, bytes_downloaded (se downloaded)
    """
    workflow = workflow or f"etl-{source}-refresh.yml"
    artifact_name = artifact_name or f"{source}-shards"
    # Risolvi nome cartella di output via mapping (default: source name).
    # Es. 'istat_profilo' -> 'profilo' (cartella tradizionale).
    output_subdir = SOURCE_TO_OUTPUT_DIR.get(source, source)
    source_dir = data_dir / output_subdir
    state_path = source_dir / "_artifact_meta.json"

    _log("info", "pull_source_start",
         source=source, workflow=workflow, artifact_name=artifact_name,
         output_dir=str(source_dir))

    artifact = find_latest_artifact(repo, workflow, artifact_name, token)
    if not artifact:
        _log("warning", "no_artifact_found",
             source=source, workflow=workflow)
        return 3, {"status": "no_artifact", "source": source}

    artifact_id = artifact["id"]
    size_bytes = artifact.get("size_in_bytes", 0)
    _log("info", "artifact_found",
         source=source, artifact_id=artifact_id,
         run_id=artifact.get("_run_id"),
         created_at=artifact.get("created_at"),
         size_mb=round(size_bytes / 1024 / 1024, 2))

    # Idempotency
    state = load_state(state_path)
    last_id = state.get("last_artifact_id")
    if last_id == artifact_id and not force:
        _log("info", "skip_already_downloaded",
             source=source, artifact_id=artifact_id,
             last_downloaded_at=state.get("downloaded_at"))
        return 0, {"status": "skipped_idempotent", "source": source,
                   "artifact_id": artifact_id}

    if dry_run:
        _log("info", "dry_run_would_download",
             source=source, artifact_id=artifact_id,
             dest=str(source_dir))
        return 0, {"status": "would_download_dry_run", "source": source,
                   "artifact_id": artifact_id}

    # Download
    t0 = time.time()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)
    try:
        n_bytes = download_artifact_zip(repo, artifact_id, token, zip_path)
        elapsed = round(time.time() - t0, 1)
        _log("info", "download_done", source=source,
             bytes=n_bytes, elapsed_s=elapsed)

        t1 = time.time()
        # I workflow producono tar con prefix == output_subdir (es. 'profilo/').
        # Strippa il prefix per evitare nidificazione data/profilo/profilo/...
        n_files = extract_archive(zip_path, source_dir,
                                  strip_top_dir=output_subdir)
        elapsed_x = round(time.time() - t1, 1)
        _log("info", "extract_done", source=source,
             files_extracted=n_files, dest=str(source_dir),
             elapsed_s=elapsed_x)
    except Exception as e:
        _log("error", "extract_failed", source=source, error=str(e))
        return 4, {"status": "error", "source": source, "error": str(e)}
    finally:
        zip_path.unlink(missing_ok=True)

    save_state(state_path, {
        "source": source,
        "last_artifact_id": artifact_id,
        "last_run_id": artifact.get("_run_id"),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_extracted": n_files,
        "bytes_downloaded": n_bytes,
    })

    if not keep_artifact:
        deleted = delete_artifact(repo, artifact_id, token)
        _log("info", "cleanup_done", source=source,
             deleted=deleted, artifact_id=artifact_id)

    _log("info", "pull_source_complete", source=source,
         files_extracted=n_files, bytes_downloaded=n_bytes)
    return 0, {"status": "downloaded", "source": source,
               "artifact_id": artifact_id,
               "files_extracted": n_files,
               "bytes_downloaded": n_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull artifact ETL da GitHub Actions per Cruscotto Italia",
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Nome ETL (es. asia, veicoli, istat_profilo). Omettere se si usa --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Pull di tutti i workflow etl-<source>-refresh.yml nel repo. "
             "Discovery dinamico via GitHub API. Skip idempotente per artifact "
             "invariati. Modalita' raccomandata per il cron daily della VM.",
    )
    parser.add_argument(
        "--include",
        default=None,
        help="(con --all) Lista comma-separated di source da includere "
             "(esclusi gli altri). Es: --include=asia,veicoli",
    )
    parser.add_argument(
        "--exclude",
        default=None,
        help="(con --all) Lista comma-separated di source da escludere. "
             "Es: --exclude=asia,demografia",
    )
    parser.add_argument(
        "--workflow",
        default=None,
        help="Nome file workflow YAML (default: etl-<source>-refresh.yml). "
             "Ignorato se --all.",
    )
    parser.add_argument(
        "--artifact-name",
        default=None,
        help="Nome artifact da scaricare (default: <source>-shards). "
             "Ignorato se --all.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR)),
        help=f"Directory dati (default: {DEFAULT_DATA_DIR} o env DATA_DIR)",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPO", DEFAULT_REPO),
        help=f"GitHub repo (default: {DEFAULT_REPO} o env GITHUB_REPO)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forza download anche se ultimo artifact gia' scaricato",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stampa info ma non scarica/estrai/cancella",
    )
    parser.add_argument(
        "--keep-artifact",
        action="store_true",
        help="Non eliminare l'artifact da GitHub dopo download (default: elimina)",
    )
    args = parser.parse_args()

    # Validazione mutua esclusione
    if args.all and args.source:
        _log("error", "args_invalid",
             reason="--all e <source> sono mutuamente esclusivi")
        return 1
    if not args.all and not args.source:
        parser.print_help(sys.stderr)
        return 1

    # Token
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        _log("error", "missing_github_token",
             hint="Esporta env GITHUB_TOKEN con scope Contents:Read + Actions:Read")
        return 2

    # ─── Modalita' --all ─────────────────────────────────────────────
    if args.all:
        include_set = set()
        if args.include:
            include_set = {s.strip() for s in args.include.split(",") if s.strip()}
        exclude_set = set()
        if args.exclude:
            exclude_set = {s.strip() for s in args.exclude.split(",") if s.strip()}

        _log("info", "pull_all_start", data_dir=str(args.data_dir),
             include=sorted(include_set) or None,
             exclude=sorted(exclude_set) or None,
             dry_run=args.dry_run)

        workflows = discover_etl_workflows(args.repo, token)
        if not workflows:
            _log("warning", "no_workflows_discovered",
                 hint="Nessun workflow 'etl-*-refresh.yml' attivo nel repo. "
                      "Controllare che almeno un workflow sia committato.")
            return 0

        _log("info", "workflows_discovered", count=len(workflows),
             sources=[w["source"] for w in workflows])

        # Filtro include/exclude
        if include_set:
            workflows = [w for w in workflows if w["source"] in include_set]
        if exclude_set:
            workflows = [w for w in workflows if w["source"] not in exclude_set]

        summaries = []
        downloaded = skipped = no_artifact = errors = 0
        for w in workflows:
            try:
                rc, summary = pull_one_source(
                    source=w["source"],
                    repo=args.repo,
                    token=token,
                    data_dir=args.data_dir,
                    workflow=w["workflow_filename"],
                    force=args.force,
                    dry_run=args.dry_run,
                    keep_artifact=args.keep_artifact,
                )
                summaries.append(summary)
                status = summary.get("status", "unknown")
                if status == "downloaded":
                    downloaded += 1
                elif status == "skipped_idempotent":
                    skipped += 1
                elif status == "no_artifact":
                    no_artifact += 1
                else:
                    errors += rc != 0
            except Exception as e:
                _log("error", "pull_source_unexpected_error",
                     source=w["source"], error=str(e))
                summaries.append({"status": "error", "source": w["source"],
                                  "error": str(e)})
                errors += 1

        _log("info", "pull_all_complete",
             total=len(workflows),
             downloaded=downloaded,
             skipped_idempotent=skipped,
             no_artifact=no_artifact,
             errors=errors,
             summaries=summaries)

        # Exit code: 0 se zero errori, 1 se almeno un errore (cron lo nota)
        return 0 if errors == 0 else 1

    # ─── Modalita' source singolo (legacy) ──────────────────────────
    rc, _summary = pull_one_source(
        source=args.source,
        repo=args.repo,
        token=token,
        data_dir=args.data_dir,
        workflow=args.workflow,
        artifact_name=args.artifact_name,
        force=args.force,
        dry_run=args.dry_run,
        keep_artifact=args.keep_artifact,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
