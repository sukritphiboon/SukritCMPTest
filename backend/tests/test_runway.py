from datetime import UTC, datetime, timedelta

import pytest

from app.services.analytics.runway import daily_growth, daily_last, forecast_runway

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def points(*used: float, step_days: int = 1):
    return [(T0 + timedelta(days=i * step_days), v) for i, v in enumerate(used)]


def test_steady_growth():
    r = forecast_runway(points(1000, 1100, 1200, 1300), raw_capacity_gb=2000)
    assert r.status == "ok" and r.avg_daily_growth_gb == pytest.approx(100)
    assert r.free_gb == 700 and r.days_until_full == pytest.approx(7.0) and r.window_days == 3


def test_only_the_newest_window_counts():
    # 10 days of fast growth (+500/day) followed by 7 days of slow growth (+10/day)
    used = [1000 + 500 * i for i in range(10)]
    used += [used[-1] + 10 * (i + 1) for i in range(7)]
    r = forecast_runway(points(*used), raw_capacity_gb=20000, window_days=7)
    assert r.avg_daily_growth_gb == pytest.approx(10) and r.window_days == 7
    wide = forecast_runway(points(*used), raw_capacity_gb=20000, window_days=16)
    assert wide.avg_daily_growth_gb > 100  # a longer window remembers the fast period


def test_moving_average_of_uneven_growth():
    r = forecast_runway(points(0, 100, 400, 500), raw_capacity_gb=1000, window_days=3)
    assert r.avg_daily_growth_gb == pytest.approx((100 + 300 + 100) / 3)
    assert r.days_until_full == pytest.approx(500 / (500 / 3))


def test_last_reading_of_each_day_is_used():
    pts = [
        (T0, 1000.0),
        (T0 + timedelta(hours=3), 1040.0),
        (T0 + timedelta(hours=5), 1050.0),  # day 1
        (T0 + timedelta(days=1), 1100.0),
        (T0 + timedelta(days=1, hours=2), 1150.0),  # day 2
    ]
    assert daily_last(pts) == [(T0.date(), 1050.0), ((T0 + timedelta(days=1)).date(), 1150.0)]
    assert forecast_runway(pts, 2150).avg_daily_growth_gb == pytest.approx(100)


def test_unsorted_input_is_handled():
    pts = list(reversed(points(1000, 1100, 1200)))
    assert forecast_runway(pts, 2000).days_until_full == pytest.approx(8.0)


def test_a_missed_day_does_not_double_the_growth():
    series = daily_last(points(1000, 1300, step_days=3))  # 300 GB over 3 days
    assert daily_growth(series) == [100.0]


def test_not_enough_data():
    assert forecast_runway([], 1000).status == "insufficient_data"
    one_day = [(T0, 100.0), (T0 + timedelta(hours=6), 150.0)]  # many readings, but a single day
    r = forecast_runway(one_day, 1000)
    assert r.status == "insufficient_data" and r.days_until_full is None and r.free_gb == 850


@pytest.mark.parametrize("used", [(1000, 1000, 1000), (1000, 900, 800), (1000, 1200, 1000)])
def test_flat_or_shrinking_pool_is_not_growing(used):
    r = forecast_runway(points(*used), 5000)
    assert r.status == "not_growing" and r.days_until_full is None  # never negative, never a division by zero


def test_full_pool():
    r = forecast_runway(points(1000, 1500, 2000), raw_capacity_gb=2000)
    assert r.status == "full" and r.days_until_full == 0.0
    over = forecast_runway(points(1000, 2100), raw_capacity_gb=2000)
    assert over.status == "full" and over.free_gb == 0.0


def test_window_of_one_uses_only_the_latest_day():
    r = forecast_runway(points(0, 500, 600), raw_capacity_gb=1000, window_days=1)
    assert r.avg_daily_growth_gb == 100 and r.window_days == 1
