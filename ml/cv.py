"""Walk-forward (expanding window) time-series validation. No random splits (plan §7).

Each fold tests one calendar month of prediction times. Training uses only rows whose TARGET
hour ends before the test month starts: that purges the last `horizon` hours of issue times
before the fold, whose labels would otherwise overlap the test period.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Fold:
    name: str
    test_start: pd.Timestamp
    test_end: pd.Timestamp  # exclusive
    train_idx: pd.Index
    test_idx: pd.Index


def monthly_folds(
    df: pd.DataFrame,
    *,
    min_train_months: int = 6,
    issue_col: str = "issue_time_utc",
    target_col: str = "target_hour_start_utc",
) -> Iterator[Fold]:
    issue = pd.to_datetime(df[issue_col])
    target_end = pd.to_datetime(df[target_col]) + pd.Timedelta(hours=1)
    first = issue.min().to_period("M").to_timestamp()
    last = issue.max()
    test_start = first + pd.DateOffset(months=min_train_months)
    while test_start <= last:
        test_end = test_start + pd.DateOffset(months=1)
        train_mask = target_end <= test_start
        test_mask = (issue >= test_start) & (issue < test_end)
        if train_mask.any() and test_mask.any():
            yield Fold(
                name=test_start.strftime("%Y-%m"),
                test_start=test_start,
                test_end=test_end,
                train_idx=df.index[train_mask],
                test_idx=df.index[test_mask],
            )
        test_start = test_end
