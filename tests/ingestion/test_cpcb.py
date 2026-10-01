import gzip
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ingestion.cpcb.client import CpcbClient, parse_summary
from ingestion.cpcb.extract import capture_snapshot, snapshot_key
from ingestion.http import ApiError
from ingestion.writers import LocalWriter

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "cpcb" / "caaqms_feed.xml").read_bytes()


def client_returning(response):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: response))
    return CpcbClient(http=http, sleep=lambda s: None)


def test_parse_summary_counts_stations_and_update_times():
    snap = parse_summary(FIXTURE)
    assert snap.stations == 3
    assert snap.last_updates == ("01-10-2026 16:00:00",)


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"not xml at all", b"<AqIndex/>"])
def test_rejects_anything_but_a_populated_feed(body):
    with pytest.raises(ApiError):
        parse_summary(body)


def test_capture_writes_gzipped_raw_xml_per_hour(tmp_path):
    hour = datetime(2026, 10, 1, 11, 5, tzinfo=UTC)
    with client_returning(httpx.Response(200, content=FIXTURE)) as c:
        out = capture_snapshot(c, LocalWriter(tmp_path), logical_hour=hour)
    key = "bronze/cpcb/caaqms/dt=2026-10-01/hour=11/feed.xml.gz"
    assert snapshot_key(hour) == key
    assert gzip.decompress((tmp_path / key).read_bytes()) == FIXTURE  # stored untouched
    assert out["stations"] == 3


def test_server_error_is_retried_then_raised():
    with client_returning(httpx.Response(503)) as c, pytest.raises(ApiError):
        c.snapshot()
