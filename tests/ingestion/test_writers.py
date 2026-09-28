import json

import pytest

from ingestion.writers import LocalWriter, S3Writer


def test_local_writer_creates_dirs_and_overwrites(tmp_path):
    w = LocalWriter(tmp_path)
    key = "bronze/openaq/locations/dt=2026-09-28/locations.json"
    w.put_json(key, [{"name": "Anand Vihar ✓"}])
    uri = w.put_json(key, [{"name": "second"}])
    assert json.loads((tmp_path / key).read_text()) == [{"name": "second"}]
    assert uri.endswith(key)


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kw):
        self.calls.append(kw)


def test_s3_writer_puts_utf8_json():
    s3 = FakeS3()
    uri = S3Writer("my-bucket", s3).put_json("bronze/x.json", {"unit": "µg/m³"})
    assert uri == "s3://my-bucket/bronze/x.json"
    call = s3.calls[0]
    assert call["Bucket"] == "my-bucket"
    assert call["Key"] == "bronze/x.json"
    assert call["ContentType"] == "application/json"
    assert json.loads(call["Body"].decode("utf-8")) == {"unit": "µg/m³"}


def test_s3_writer_requires_bucket():
    with pytest.raises(ValueError, match="DATA_BUCKET"):
        S3Writer("", FakeS3())
