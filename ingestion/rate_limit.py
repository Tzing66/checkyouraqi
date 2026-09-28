"""Proactive client-side rate limiting.

APIs like OpenAQ can ban keys that keep hitting 429, so we pace requests before the server
has to tell us to: a minimum gap between calls, plus a pause when the server reports the
window is nearly used up.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping


class RateLimiter:
    def __init__(
        self,
        min_interval_s: float,
        *,
        reserve: int = 2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.reserve = reserve
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        """Block until the next request is allowed."""
        if self._last is not None:
            remaining = self.min_interval_s - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def observe(self, headers: Mapping[str, str]) -> None:
        """Pause until the window resets if the server says we're close to the limit."""
        remaining = _int_header(headers, "x-ratelimit-remaining")
        if remaining is not None and remaining <= self.reserve:
            self._sleep(_int_header(headers, "x-ratelimit-reset") or 60)


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None
