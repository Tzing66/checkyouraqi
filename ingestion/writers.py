"""Where extractors land raw payloads: S3 in the pipeline, a local directory in dev/tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


def _dumps(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Writer(Protocol):
    def put_json(self, key: str, payload: Any) -> str:
        """Write payload as JSON at key (overwriting, so reruns are idempotent). Returns a URI."""
        ...

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        """Write text as UTF-8 at key (overwriting). Returns a URI."""
        ...


class LocalWriter:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def put_json(self, key: str, payload: Any) -> str:
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_dumps(payload))
        return str(path)

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
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
        return self._put(key, _dumps(payload), "application/json")

    def put_text(self, key: str, text: str, content_type: str = "text/csv") -> str:
        return self._put(key, text.encode("utf-8"), content_type)

    def _put(self, key: str, body: bytes, content_type: str) -> str:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"
