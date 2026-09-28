from ingestion.rate_limit import RateLimiter


def test_first_call_does_not_wait(clock):
    RateLimiter(1.0, clock=clock, sleep=clock.sleep).wait()
    assert clock.sleeps == []


def test_enforces_min_interval_between_calls(clock):
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 0.25
    limiter.wait()
    assert clock.sleeps == [0.75]


def test_no_wait_when_interval_already_elapsed(clock):
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait()
    clock.now += 5
    limiter.wait()
    assert clock.sleeps == []


def test_pauses_until_reset_when_nearly_exhausted(clock):
    limiter = RateLimiter(1.0, reserve=2, clock=clock, sleep=clock.sleep)
    limiter.observe({"x-ratelimit-remaining": "2", "x-ratelimit-reset": "37"})
    assert clock.sleeps == [37]


def test_does_not_pause_with_headroom_or_bad_headers(clock):
    limiter = RateLimiter(1.0, reserve=2, clock=clock, sleep=clock.sleep)
    limiter.observe({"x-ratelimit-remaining": "40", "x-ratelimit-reset": "37"})
    limiter.observe({"x-ratelimit-remaining": "not-a-number"})
    limiter.observe({})
    assert clock.sleeps == []
