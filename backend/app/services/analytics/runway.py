"""Storage pool runway ("days until full") from a simple moving average of daily physical growth.

Steps: keep the last reading of each UTC day, take the growth between consecutive days (divided by
the number of days between them, so a missed day does not distort the rate), average the newest
``window_days`` growth values, and divide the free space by that average.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Status = Literal["ok", "insufficient_data", "not_growing", "full"]


@dataclass(frozen=True)
class RunwayResult:
    status: Status
    days_until_full: float | None
    avg_daily_growth_gb: float | None
    free_gb: float | None
    window_days: int  # number of daily growth values really averaged (0 when there is no data)


def daily_last(points: Iterable[tuple[datetime, float]]) -> list[tuple[date, float]]:
    """Last value of each calendar day (timestamps are used as given: store them in UTC)."""
    last: dict[date, tuple[datetime, float]] = {}
    for ts, value in points:
        day = ts.date()
        if day not in last or ts >= last[day][0]:
            last[day] = (ts, value)
    return [(d, last[d][1]) for d in sorted(last)]


def daily_growth(series: list[tuple[date, float]]) -> list[float]:
    return [
        (cur - prev) / (cur_day - prev_day).days
        for (prev_day, prev), (cur_day, cur) in zip(series, series[1:], strict=False)
    ]


def forecast_runway(
    points: Iterable[tuple[datetime, float]], raw_capacity_gb: float, window_days: int = 7
) -> RunwayResult:
    """``points`` are (timestamp, used physical GB). The newest point decides the free space."""
    series = daily_last(points)
    if not series:
        return RunwayResult("insufficient_data", None, None, None, 0)
    free = max(raw_capacity_gb - series[-1][1], 0.0)
    if free <= 0:
        return RunwayResult("full", 0.0, None, 0.0, 0)
    growth = daily_growth(series)[-max(window_days, 1) :]
    if not growth:
        return RunwayResult("insufficient_data", None, None, free, 0)
    avg = sum(growth) / len(growth)
    if avg <= 0:
        return RunwayResult("not_growing", None, avg, free, len(growth))
    return RunwayResult("ok", free / avg, avg, free, len(growth))
