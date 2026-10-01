"""Where extractors land raw payloads: S3 in the pipeline, a local directory in dev/tests.

JSON can be gzipped (key must end in .json.gz). Athena reads .gz natively and bills by
compressed bytes scanned, so big bronze files should always be compressed.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Protocol


def _dumps(payload: Any, key: str) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0 keeps the bytes identical across reruns of the same payload.
    return gzip.compress(body, mtime=0) if key.endswith(".gz") else body


class Writer(Protocol):
    def put_json(self, key: str, payload: Any) -> str:
        """Write payload as JSON at key (gzipped if key ends in .gz), overwriting so reruns are
        idempotent. Returns a URI."""
        ...

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        """Write text as UTF-8 at key (overwriting). Returns a URI."""
        ...

    def exists(self, key: str) -> bool:
        """Whether key has already been written (lets long backfills resume)."""
        ...

    def put_bytes(self, key: str, body: bytes, content_type: str) -> str:
        """Write raw bytes at key (overwriting). Returns a URI."""
        ...


class LocalWriter:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def put_json(self, key: str, payload: Any) -> str:
        return self._write(key, _dumps(payload, key))

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        return self._write(key, text.encode("utf-8"))

    def exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()

    def put_bytes(self, key: str, body: bytes, content_type: str) -> str:
        return self._write(key, body)

    def _write(self, key: str, body: bytes) -> str:
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return str(path)


class S3Writer:
    def __init__(self, bucket: str, client: Any) -> None:
        if not bucket:
            raise ValueError("S3 bucket name is empty (set DATA_BUCKET)")
        self.bucket = bucket
        self._s3 = client

    @classmethod
    def from_profile(cls, bucket: str, profile: str | None, region: str) -> S3Writer:
        import boto3

        session = boto3.Session(profile_name=profile, region_name=region)
        return cls(bucket, session.client("s3"))

    def put_json(self, key: str, payload: Any) -> str:
        return self._put(key, _dumps(payload, key), "application/json")

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        return self._put(key, text.encode("utf-8"), content_type)

    def exists(self, key: str) -> bool:
        # list_objects_v2 needs only s3:ListBucket, which the pipeline policy grants.
        resp = self._s3.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
        return any(o["Key"] == key for o in resp.get("Contents", []))

    def put_bytes(self, key: str, body: bytes, content_type: str) -> str:
        return self._put(key, body, content_type)

    def _put(self, key: str, body: bytes, content_type: str) -> str:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"
