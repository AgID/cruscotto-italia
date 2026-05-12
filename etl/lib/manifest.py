"""Manifest management.

manifest.json on R2 is the source of truth for what data is currently
available, when it was generated, and its size. Worker reads it via
mcp_info tool.

Schema:
{
  "generated_at": "2026-05-07T12:34:56Z",
  "etl_version": "0.1.0",
  "sources": {
    "anac": {
      "last_run": "2026-05-07T12:30:00Z",
      "status": "ok",
      "files": [
        {"key": "anac/2024/awards.parquet", "size": 134217728, "row_count": 1234567}
      ]
    },
    ...
  }
}
"""

import json
from datetime import datetime, timezone
from typing import Any

from . import r2


def load() -> dict[str, Any]:
    """Load manifest from R2; returns empty skeleton if not present."""
    head = r2.head("manifest.json")
    if not head:
        return {
            "generated_at": None,
            "etl_version": "0.1.0",
            "sources": {},
        }
    client = r2.get_r2_client()
    obj = client.get_object(Bucket=r2.get_bucket(), Key="manifest.json")
    return json.loads(obj["Body"].read())


def save(manifest: dict[str, Any]) -> None:
    """Save manifest to R2."""
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    r2.upload_bytes(
        json.dumps(manifest, indent=2).encode("utf-8"),
        "manifest.json",
        content_type="application/json",
    )


def update_source(source: str, files: list[dict], status: str = "ok") -> None:
    """Update the manifest for a single source."""
    m = load()
    m["sources"][source] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "files": files,
    }
    save(m)
