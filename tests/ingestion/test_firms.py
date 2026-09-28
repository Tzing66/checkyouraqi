from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from ingestion.firms.client import Availability, FirmsClient, source_for
from ingestion.firms.extract import extract_fires
from ingestion.firms.models import FirePoint
from ingestion.http import ApiAuthError, ApiError
from ingestion.rate_limit import RateLimiter
from ingestion.writers import LocalWriter

FIXTURES = Path(__file__).parent.parent / "fixtures" / "firms"
KEY = "secret-map-key-123"
BBOX = [73.8, 27.5, 78.5, 32.6]


def text(name: str) -> str:
    return (FIXTURES / name).read_text()


def make_client(handler, clock):
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    http = httpx.Client(
        base_url="https://firms.modaps.eosdis.nasa.gov", transport=httpx.MockTransport(recording)
    )
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    return FirmsClient(KEY, http=http, limiter=limiter, sleep=clock.sleep), calls


def by_path(routes):
    def handler(request):
        for fragment, response in routes.items():
            if fragment in request.url.path:
                return response
        return httpx.Response(404, text="no route")

    return handler


def availability(clock):
    c, _ = make_client(lambda r: httpx.Response(200, text=text("availability.csv")), clock)
    return c.availability()


def test_availability_parses(clock):
    a = availability(clock)
    assert a["VIIRS_SNPP_SP"] == Availability(date(2012, 1, 20), date(2026, 6, 30))
    assert a["VIIRS_SNPP_NRT"].covers(date(2026, 9, 28))


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 11, 1), "VIIRS_SNPP_SP"),
        (date(2026, 6, 30), "VIIRS_SNPP_SP"),  # last archive day
        (date(2026, 7, 1), "VIIRS_SNPP_NRT"),  # first NRT-only day
        (date(2026, 10, 5), None),  # not published yet
    ],
)
def test_source_for_prefers_archive_then_nrt(clock, day, expected):
    assert source_for("VIIRS_SNPP", day, availability(clock)) == expected


def test_fires_parses_sp_csv_and_builds_url(clock):
    c, calls = make_client(
        lambda r: httpx.Response(200, text=text("viirs_snpp_sp_2024-11-01.csv")), clock
    )
    got = c.fires("VIIRS_SNPP_SP", BBOX, date(2024, 11, 1))
    assert calls[0].url.path == (
        f"/api/area/csv/{KEY}/VIIRS_SNPP_SP/73.8,27.5,78.5,32.6/1/2024-11-01"
    )
    assert len(got.points) == 25
    first = got.points[0]
    assert first.confidence == "n"
    assert first.acquired_at_utc == datetime(2024, 11, 1, 8, 26, tzinfo=UTC)
    assert got.text.startswith("latitude,longitude,")


def test_fires_parses_nrt_csv(clock):
    c, _ = make_client(
        lambda r: httpx.Response(200, text=text("viirs_snpp_nrt_2026-09-26.csv")), clock
    )
    got = c.fires("VIIRS_SNPP_NRT", BBOX, date(2026, 9, 26))
    assert len(got.points) == 10
    assert all(p.version.endswith("NRT") for p in got.points)


def test_empty_day_is_just_a_header(clock):
    header = text("viirs_snpp_nrt_2026-09-26.csv").splitlines()[0] + "\n"
    c, _ = make_client(lambda r: httpx.Response(200, text=header), clock)
    assert c.fires("VIIRS_SNPP_NRT", BBOX, date(2026, 9, 26)).points == []


def test_plain_text_200_is_an_error_and_key_is_redacted(clock):
    c, _ = make_client(lambda r: httpx.Response(200, text=f"Invalid MAP_KEY {KEY}."), clock)
    with pytest.raises(ApiError) as e:
        c.fires("VIIRS_SNPP_NRT", BBOX, date(2026, 9, 26))
    assert KEY not in str(e.value)
    assert "***" in str(e.value)


def test_http_error_redacts_key_from_url(clock):
    c, _ = make_client(
        lambda r: httpx.Response(400, text="Invalid day range. Expects [1..5]."), clock
    )
    with pytest.raises(ApiError, match="Invalid day range") as e:
        c.fires("VIIRS_SNPP_NRT", BBOX, date(2026, 9, 26))
    assert KEY not in str(e.value)


def test_empty_key_rejected():
    with pytest.raises(ApiAuthError):
        FirmsClient("")


def test_fire_point_validation():
    row = {
        "latitude": "30.1",
        "longitude": "75.2",
        "confidence": "h",
        "acq_date": "2024-11-01",
        "acq_time": "2059",
        "satellite": "N",
        "instrument": "VIIRS",
        "daynight": "N",
        "version": "2",
    }
    assert FirePoint.model_validate(row).acquired_at_utc.hour == 20
    with pytest.raises(ValueError):
        FirePoint.model_validate({**row, "confidence": "maybe"})
    with pytest.raises(ValueError):
        FirePoint.model_validate({**row, "acq_time": "2575"})


def test_extract_writes_one_file_per_available_source(clock, tmp_path):
    a = availability(clock)
    sp = httpx.Response(200, text=text("viirs_snpp_sp_2024-11-01.csv"))
    c, calls = make_client(by_path({"VIIRS_SNPP_SP": sp, "VIIRS_NOAA20_SP": sp}), clock)
    written = extract_fires(c, LocalWriter(tmp_path), date(2024, 11, 1), BBOX, a)
    assert written == {"VIIRS_SNPP_SP": 25, "VIIRS_NOAA20_SP": 25}
    day_dir = tmp_path / "bronze/firms/dt=2024-11-01"
    assert sorted(p.name for p in day_dir.iterdir()) == ["VIIRS_NOAA20_SP.csv", "VIIRS_SNPP_SP.csv"]


def test_extract_skips_days_not_yet_published(clock, tmp_path):
    c, calls = make_client(lambda r: httpx.Response(500), clock)
    a = availability(clock)
    assert extract_fires(c, LocalWriter(tmp_path), date(2026, 10, 5), BBOX, a) == {}
    assert calls == []
