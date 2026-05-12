"""Cloudflare R2 client wrapper.

Reads credentials from env:
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET

R2 is S3-compatible, accessed via boto3 with custom endpoint:
    https://{account_id}.r2.cloudflarestorage.com
"""

import os
import sys
from pathlib import Path
from typing import Optional

import boto3
import structlog
from botocore.config import Config

log = structlog.get_logger()


def get_r2_client():
    """Return a boto3 S3 client configured for Cloudflare R2."""
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([account_id, access_key, secret_key]):
        log.error("Missing R2 credentials in env",
                  needed=["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"])
        sys.exit(1)

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def get_bucket() -> str:
    bucket = os.environ.get("R2_BUCKET", "cruscotto-italia-data")
    return bucket


def upload_file(local_path: Path | str, key: str, content_type: Optional[str] = None) -> None:
    """Upload a single file to R2."""
    client = get_r2_client()
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    client.upload_file(str(local_path), get_bucket(), key, ExtraArgs=extra)
    log.info("uploaded", key=key, bucket=get_bucket(), local=str(local_path))


def upload_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> None:
    client = get_r2_client()
    client.put_object(Bucket=get_bucket(), Key=key, Body=data, ContentType=content_type)
    log.info("uploaded_bytes", key=key, size=len(data))


def download_file(key: str, local_path: Path | str) -> None:
    client = get_r2_client()
    client.download_file(get_bucket(), key, str(local_path))


def head(key: str) -> Optional[dict]:
    """Return object metadata or None if not found."""
    client = get_r2_client()
    try:
        return client.head_object(Bucket=get_bucket(), Key=key)
    except client.exceptions.ClientError:
        return None


def list_keys(prefix: str = "") -> list[str]:
    client = get_r2_client()
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=get_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys
