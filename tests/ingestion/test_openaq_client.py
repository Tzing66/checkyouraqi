from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from ingestion.openaq import client as client_mod
from ingestion.openaq.client import (
    OpenAQAuthError,
    OpenAQClient,
    OpenAQError,
    OpenAQRateLimitError,
    OpenAQServerError,
)
from ingestion.rate_limit import RateLimiter

START = datetime(2026, 9, 24, 10, tzinfo=UTC)
END = datetime(2026, 9, 24, 14, tzinfo=UTC)


def make_client(handler, clock, **kw) -> tuple[OpenAQClient, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    http = httpx.Client(base_url=client_mod.BASE_URL, transport=httpx.MockTransport(recording))
    limiter = RateLimiter(1.1, clock=clock, sleep=clock.sleep)
    return OpenAQClient("test-key", http=http, limiter=limiter, sleep=clock.sleep, **kw), calls


def ok(payload, remaining="59"):
    return httpx.Response(
        200, json=payload, headers={"x-ratelimit-remaining": remaining, "x-ratelimit-reset": "60"}
    )


def test_sends_api_key_and_parses_location(load_fixture, clock):
    c, calls = make_client(lambda r: ok(load_fixture("openaq/location_235.json")), clock)
    fetched = c.location(235)
    assert calls[0].headers["X-API-Key"] == "test-key"
    assert calls[0].url.path == "/v3/locations/235"
    assert fetched.items[0].name == "Anand Vihar, New Delhi - DPCC"
    assert fetched.raw_pages[0]["results"][0]["id"] == 235


def test_locations_sends_bbox(load_fixture, clock):
    c, calls = make_client(lambda r: ok(load_fixture("openaq/locations_bbox.json")), clock)
    c.locations([77.30, 28.64, 77.33, 28.66])
    assert calls[0].url.params["bbox"] == "77.3,28.64,77.33,28.66"


def test_locations_rejects_bad_bbox(clock):
    c, _ = make_client(lambda r: ok({}), clock)
    with pytest.raises(ValueError):
        c.locations([1, 2, 3])


def test_paginates_until_short_page(load_fixture, clock, monkeypatch):
    monkeypatch.setattr(client_mod, "PAGE_LIMIT", 2)
    base = load_fixture("openaq/sensor_12235610_hours.json")
    pages = {1: base["results"][:2], 2: base["results"][:2], 3: base["results"][:1]}

    def handler(request):
        page = int(request.url.params["page"])
        return ok({"meta": {**base["meta"], "page": page, "limit": 2}, "results": pages[page]})

    c, calls = make_client(handler, clock)
    fetched = c.sensor_hours(12235610, START, END)
    assert [int(r.url.params["page"]) for r in calls] == [1, 2, 3]
    assert len(fetched.items) == 5
    assert len(fetched.raw_pages) == 3


def test_window_params_are_utc_iso(load_fixture, clock):
    c, calls = make_client(lambda r: ok(load_fixture("openaq/sensor_12235610_hours.json")), clock)
    c.sensor_hours(12235610, START, END)
    assert calls[0].url.params["datetime_from"] == "2026-09-24T10:00:00Z"
    assert calls[0].url.params["datetime_to"] == "2026-09-24T14:00:00Z"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 9, 24, 10), END),  # naive
        (START.astimezone(timezone(timedelta(hours=5, minutes=30))), END),  # not UTC
        (END, START),  # reversed
    ],
)
def test_window_rejects_bad_datetimes(clock, start, end):
    c, calls = make_client(lambda r: ok({}), clock)
    with pytest.raises(ValueError):
        c.sensor_hours(1, start, end)
    assert calls == []


def test_retries_after_429_using_reset_header(load_fixture, clock):
    responses = iter(
        [
            httpx.Response(429, headers={"x-ratelimit-reset": "45"}),
            ok(load_fixture("openaq/location_235.json")),
        ]
    )
    c, calls = make_client(lambda r: next(responses), clock)
    assert c.location(235).items[0].id == 235
    assert len(calls) == 2
    assert 45 in clock.sleeps


def test_gives_up_after_repeated_429(clock):
    c, calls = make_client(lambda r: httpx.Response(429), clock, max_retries=3)
    with pytest.raises(OpenAQRateLimitError):
        c.location(235)
    assert len(calls) == 3


def test_retries_5xx_then_raises_server_error(clock):
    c, calls = make_client(lambda r: httpx.Response(500, text="boom"), clock, max_retries=3)
    with pytest.raises(OpenAQServerError, match="HTTP 500"):
        c.location(235)
    assert len(calls) == 3


def test_recovers_from_transient_5xx(load_fixture, clock):
    responses = iter([httpx.Response(502), ok(load_fixture("openaq/location_235.json"))])
    c, calls = make_client(lambda r: next(responses), clock)
    assert c.location(235).items[0].id == 235
    assert len(calls) == 2


def test_retries_transport_errors(load_fixture, clock):
    responses = iter([httpx.ConnectError("down"), ok(load_fixture("openaq/location_235.json"))])

    def handler(request):
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    c, calls = make_client(handler, clock)
    assert c.location(235).items[0].id == 235
    assert len(calls) == 2


def test_auth_error_is_not_retried(load_fixture, clock):
    c, calls = make_client(
        lambda r: httpx.Response(401, json=load_fixture("openaq/error_401.json")), clock
    )
    with pytest.raises(OpenAQAuthError, match="Invalid credentials"):
        c.location(235)
    assert len(calls) == 1


def test_other_4xx_raises_without_retry(clock):
    c, calls = make_client(lambda r: httpx.Response(404, text="not found"), clock)
    with pytest.raises(OpenAQError, match="404"):
        c.location(999)
    assert len(calls) == 1


def test_empty_api_key_rejected():
    with pytest.raises(OpenAQAuthError):
        OpenAQClient("")


def test_throttles_between_requests(load_fixture, clock):
    c, _ = make_client(lambda r: ok(load_fixture("openaq/location_235.json")), clock)
    c.location(235)
    c.location(235)
    assert clock.sleeps == [pytest.approx(1.1)]


def test_pauses_when_server_reports_low_remaining(load_fixture, clock):
    c, _ = make_client(lambda r: ok(load_fixture("openaq/location_235.json"), remaining="1"), clock)
    c.location(235)
    assert 60 in clock.sleeps
