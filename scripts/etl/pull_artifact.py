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
    """Scarica l'artifact ZIP. GitHub redirige a un URL pre-signed S3.

    Ritorna il numero di bytes scaricati.
    """
    # GitHub API redirect to S3 pre-signed URL automatically followed
    resp = gh_api(
        f"/repos/{repo}/actions/artifacts/{artifact_id}/zip",
        token,
        return_response=True,
    )
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


def extract_archive(archive_path: Path, dest_dir: Path) -> int:
    """Estrae l'archive (ZIP da GitHub puo' contenere un tar.gz interno).

    GitHub wrappa SEMPRE l'upload in uno ZIP esterno, quindi:
      - artifact1.zip contiene shards.tar.gz
      - shards.tar.gz contiene asia/<istat>.json files

    Ritorna numero di file estratti nel filesystem destinazione.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: unzip
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
            n = 0
            with tarfile.open(tar_path, "r:*") as tf:
                # Sicurezza: nessun path absolute, no .. nel path
                for member in tf.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        _log("warning", "tar_unsafe_member_skipped",
                             name=member.name)
                        continue
                tf.extractall(dest_dir)
                n = sum(1 for m in tf.getmembers() if m.isfile())
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull artifact ETL da GitHub Actions per Cruscotto Italia",
    )
    parser.add_argument("source", help="Nome ETL (es. asia, veicoli, istat_profilo)")
    parser.add_argument(
        "--workflow",
        default=None,
        help="Nome file workflow YAML (default: etl-<source>-refresh.yml)",
    )
    parser.add_argument(
        "--artifact-name",
        default=None,
        help="Nome artifact da scaricare (default: <source>-shards)",
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

    # Token
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        _log("error", "missing_github_token",
             hint="Esporta env GITHUB_TOKEN con scope Contents:Read + Actions:Read")
        return 2

    # Default workflow / artifact name
    workflow = args.workflow or f"etl-{args.source}-refresh.yml"
    artifact_name = args.artifact_name or f"{args.source}-shards"
    source_dir = args.data_dir / args.source
    state_path = source_dir / "_artifact_meta.json"

    _log("info", "pull_start",
         source=args.source, workflow=workflow, artifact_name=artifact_name,
         data_dir=str(args.data_dir), dry_run=args.dry_run)

    # Trova l'ultimo artifact disponibile
    artifact = find_latest_artifact(args.repo, workflow, artifact_name, token)
    if not artifact:
        _log("error", "no_artifact_found",
             source=args.source, workflow=workflow,
             hint=f"Esegui prima il workflow '{workflow}' su GitHub Actions")
        return 3

    artifact_id = artifact["id"]
    size_bytes = artifact.get("size_in_bytes", 0)
    _log("info", "artifact_found",
         artifact_id=artifact_id,
         run_id=artifact.get("_run_id"),
         run_url=artifact.get("_run_url"),
         created_at=artifact.get("created_at"),
         size_bytes=size_bytes,
         size_mb=round(size_bytes / 1024 / 1024, 2))

    # Idempotency: verifica state-file
    state = load_state(state_path)
    last_id = state.get("last_artifact_id")
    if last_id == artifact_id and not args.force:
        _log("info", "skip_already_downloaded",
             artifact_id=artifact_id,
             last_downloaded_at=state.get("downloaded_at"))
        return 0

    if args.dry_run:
        _log("info", "dry_run_exit",
             would_download_artifact_id=artifact_id,
             would_extract_to=str(source_dir))
        return 0

    # Download in tempdir
    t0 = time.time()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)
    try:
        n_bytes = download_artifact_zip(args.repo, artifact_id, token, zip_path)
        elapsed = round(time.time() - t0, 1)
        _log("info", "download_done", bytes=n_bytes, elapsed_s=elapsed,
             mb_per_s=round(n_bytes / 1024 / 1024 / max(elapsed, 0.1), 2))

        # Extract
        t1 = time.time()
        n_files = extract_archive(zip_path, source_dir)
        elapsed_x = round(time.time() - t1, 1)
        _log("info", "extract_done",
             files_extracted=n_files, dest=str(source_dir),
             elapsed_s=elapsed_x)
    except Exception as e:
        _log("error", "extract_failed", error=str(e))
        return 4
    finally:
        zip_path.unlink(missing_ok=True)

    # Update state
    save_state(state_path, {
        "source": args.source,
        "last_artifact_id": artifact_id,
        "last_run_id": artifact.get("_run_id"),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_extracted": n_files,
        "bytes_downloaded": n_bytes,
    })

    # Cleanup: delete artifact su GitHub
    if not args.keep_artifact:
        deleted = delete_artifact(args.repo, artifact_id, token)
        _log("info", "cleanup_done", deleted=deleted, artifact_id=artifact_id)

    _log("info", "pull_complete", source=args.source,
         files_extracted=n_files, bytes_downloaded=n_bytes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
