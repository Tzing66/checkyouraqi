from datetime import date

from scripts.backfill import days, is_recent, months


def test_months_clips_to_range_and_crosses_year():
    assert list(months(date(2025, 11, 15), date(2026, 1, 10))) == [
        (date(2025, 11, 15), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 10)),
    ]


def test_months_handles_leap_february():
    assert list(months(date(2028, 2, 1), date(2028, 2, 29))) == [
        (date(2028, 2, 1), date(2028, 2, 29))
    ]


def test_days_inclusive():
    assert list(days(date(2025, 1, 30), date(2025, 2, 1))) == [
        date(2025, 1, 30),
        date(2025, 1, 31),
        date(2025, 2, 1),
    ]


def test_recent_window():
    end = date(2026, 9, 27)
    assert is_recent(date(2026, 9, 27), end)
    assert is_recent(date(2026, 9, 21), end)
    assert not is_recent(date(2026, 9, 20), end)
