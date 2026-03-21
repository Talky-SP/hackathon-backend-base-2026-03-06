"""
Artifact storage — S3 in Lambda, local filesystem in dev.

Provides a uniform interface for storing and retrieving generated files
(Excel, PDF, etc.) regardless of the runtime environment.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("agent.storage")

# S3 bucket name (set in Lambda env)
_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "")
_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_REGION_NAME", "eu-west-3"))

# Local artifacts directory (for dev mode)
_LOCAL_DIR = os.path.join(os.environ.get("TEMP", "/tmp"), "cfo_artifacts")

_s3_client = None


def _use_s3() -> bool:
    return bool(_BUCKET)


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3", region_name=_REGION)
    return _s3_client


def get_artifacts_dir(task_id: str) -> str:
    """Get the local directory for a task's artifacts. Creates it if needed."""
    d = os.path.join(_LOCAL_DIR, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def save_artifact(task_id: str, filename: str, data: bytes) -> dict:
    """
    Save an artifact file. Returns metadata dict with url and storage info.

    In Lambda: uploads to S3 and returns a presigned URL.
    In dev: saves to local filesystem and returns a local API URL.
    """
    if _use_s3():
        s3_key = f"artifacts/{task_id}/{filename}"
        _get_s3().put_object(
            Bucket=_BUCKET,
            Key=s3_key,
            Body=data,
            ContentType=_guess_content_type(filename),
        )
        # Generate presigned URL (valid for 1 hour)
        url = _get_s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": _BUCKET, "Key": s3_key},
            ExpiresIn=3600,
        )
        logger.info(f"Saved artifact to S3: s3://{_BUCKET}/{s3_key}")
        return {
            "filename": filename,
            "task_id": task_id,
            "url": url,
            "storage": "s3",
            "s3_key": s3_key,
            "size_bytes": len(data),
        }
    else:
        # Local filesystem
        local_dir = get_artifacts_dir(task_id)
        local_path = os.path.join(local_dir, filename)
        with open(local_path, "wb") as f:
            f.write(data)
        url = f"/api/tasks/{task_id}/artifacts/{filename}"
        logger.info(f"Saved artifact locally: {local_path}")
        return {
            "filename": filename,
            "task_id": task_id,
            "url": url,
            "storage": "local",
            "path": local_path,
            "size_bytes": len(data),
        }


def get_artifact(task_id: str, filename: str) -> bytes | None:
    """Retrieve artifact file contents."""
    if _use_s3():
        s3_key = f"artifacts/{task_id}/{filename}"
        try:
            resp = _get_s3().get_object(Bucket=_BUCKET, Key=s3_key)
            return resp["Body"].read()
        except Exception:
            return None
    else:
        local_path = os.path.join(_LOCAL_DIR, task_id, filename)
        if os.path.isfile(local_path):
            with open(local_path, "rb") as f:
                return f.read()
        return None


def get_artifact_url(task_id: str, filename: str) -> str | None:
    """Get a URL for downloading an artifact."""
    if _use_s3():
        s3_key = f"artifacts/{task_id}/{filename}"
        try:
            return _get_s3().generate_presigned_url(
                "get_object",
                Params={"Bucket": _BUCKET, "Key": s3_key},
                ExpiresIn=3600,
            )
        except Exception:
            return None
    else:
        local_path = os.path.join(_LOCAL_DIR, task_id, filename)
        if os.path.isfile(local_path):
            return f"/api/tasks/{task_id}/artifacts/{filename}"
        return None


def list_artifacts(task_id: str) -> list[dict]:
    """List all artifacts for a task."""
    if _use_s3():
        prefix = f"artifacts/{task_id}/"
        try:
            resp = _get_s3().list_objects_v2(Bucket=_BUCKET, Prefix=prefix)
            results = []
            for obj in resp.get("Contents", []):
                fname = obj["Key"].replace(prefix, "")
                if fname:
                    results.append({
                        "filename": fname,
                        "size_bytes": obj["Size"],
                        "url": get_artifact_url(task_id, fname),
                    })
            return results
        except Exception:
            return []
    else:
        local_dir = os.path.join(_LOCAL_DIR, task_id)
        if not os.path.isdir(local_dir):
            return []
        results = []
        for fname in os.listdir(local_dir):
            fpath = os.path.join(local_dir, fname)
            if os.path.isfile(fpath):
                results.append({
                    "filename": fname,
                    "size_bytes": os.path.getsize(fpath),
                    "url": f"/api/tasks/{task_id}/artifacts/{fname}",
                })
        return results


def _guess_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
        "csv": "text/csv",
        "png": "image/png",
        "jpg": "image/jpeg",
        "json": "application/json",
    }.get(ext, "application/octet-stream")
