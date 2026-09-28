import gzip
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
    def __init__(self, keys=()):
        self.calls = []
        self.keys = list(keys)

    def put_object(self, **kw):
        self.calls.append(kw)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": k} for k in self.keys if k.startswith(Prefix)][:MaxKeys]}


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


def test_put_text_local_and_s3(tmp_path):
    uri = LocalWriter(tmp_path).put_text("bronze/firms/dt=2024-11-01/x.csv", "a,b\n1,2\n")
    assert (tmp_path / "bronze/firms/dt=2024-11-01/x.csv").read_text() == "a,b\n1,2\n"
    assert uri.endswith("x.csv")
    s3 = FakeS3()
    S3Writer("b", s3).put_text("k.csv", "a,b\n")
    assert s3.calls[0]["ContentType"] == "text/csv"
    assert s3.calls[0]["Body"] == b"a,b\n"


def test_gz_keys_are_gzipped_and_deterministic(tmp_path):
    w = LocalWriter(tmp_path)
    w.put_json("a.json.gz", {"x": 1})
    first = (tmp_path / "a.json.gz").read_bytes()
    w.put_json("a.json.gz", {"x": 1})
    assert (tmp_path / "a.json.gz").read_bytes() == first  # same bytes on rerun
    assert json.loads(gzip.decompress(first)) == {"x": 1}
    s3 = FakeS3()
    S3Writer("b", s3).put_json("k.json.gz", {"x": 1})
    assert gzip.decompress(s3.calls[0]["Body"]) == b'{"x":1}'


def test_exists_local_and_s3(tmp_path):
    w = LocalWriter(tmp_path)
    assert not w.exists("a/b.json")
    w.put_json("a/b.json", {})
    assert w.exists("a/b.json")
    s3 = S3Writer("b", FakeS3(keys=["p/station=1.json.gz", "p/station=10.json.gz"]))
    assert s3.exists("p/station=1.json.gz")
    assert not s3.exists("p/station=2.json.gz")
    assert not s3.exists("p/station=")  # prefix match alone isn't existence
