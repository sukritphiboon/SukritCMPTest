from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.cron import CronError, next_runs, normalize, parse_cron, slot_time

BANGKOK = ZoneInfo("Asia/Bangkok")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=BANGKOK)  # a Thursday


def runs(expr, count=3, tz=BANGKOK, now=NOW):
    return next_runs(expr, tz, now, count)


def test_weekday_zero_is_sunday_not_monday():
    """APScheduler's own from_crontab() would give Monday here; a crontab means Sunday."""
    first = runs("0 2 * * 0", 3)
    assert [r.strftime("%a %d %H:%M") for r in first] == ["Sun 11 02:00", "Sun 18 02:00", "Sun 25 02:00"]


@pytest.mark.parametrize(
    "field,expected",
    [
        ("7", ["Sun"]),
        ("0", ["Sun"]),
        ("sun", ["Sun"]),
        ("SUN", ["Sun"]),
        ("1", ["Mon"]),
        ("6", ["Sat"]),
        ("1-5", ["Mon", "Tue", "Wed", "Thu", "Fri"]),
        ("mon-wed", ["Mon", "Tue", "Wed"]),
        ("5-7", ["Fri", "Sat", "Sun"]),
        ("fri-sun", ["Fri", "Sat", "Sun"]),
        ("1,3,5", ["Mon", "Wed", "Fri"]),
        ("*/2", ["Sun", "Tue", "Thu", "Sat"]),
        ("1-5/2", ["Mon", "Wed", "Fri"]),
    ],
)
def test_weekday_field_means_what_a_crontab_means(field, expected):
    days = {r.strftime("%a") for r in next_runs(f"0 6 * * {field}", BANGKOK, NOW, 14)}
    assert days == set(expected)


def test_every_weekday_listed_is_hit_in_a_week():
    seen = [r.strftime("%a") for r in runs("30 22 * * 1-5", 5)]
    assert seen == ["Thu", "Fri", "Mon", "Tue", "Wed"]


def test_runs_are_in_the_schedule_timezone():
    [first] = runs("0 1 * * *", 1)
    assert first.hour == 1 and first.utcoffset() == timedelta(hours=7)
    assert first.astimezone(UTC) == datetime(
        2026, 10, 8, 18, 0, tzinfo=UTC
    )  # 01:00 Bangkok = 18:00 UTC the day before
    [utc_run] = next_runs("0 1 * * *", ZoneInfo("UTC"), NOW, 1)
    assert utc_run == datetime(2026, 10, 9, 1, 0, tzinfo=UTC)


def test_now_may_be_given_in_any_timezone():
    same_moment = NOW.astimezone(UTC)
    assert runs("0 1 * * *", 1, now=same_moment) == runs("0 1 * * *", 1)


def test_steps_lists_month_and_day_of_month():
    assert [r.strftime("%H:%M") for r in runs("*/15 13 * * *", 4)] == ["13:00", "13:15", "13:30", "13:45"]
    assert [r.strftime("%d %H:%M") for r in runs("0 3 1,15 * *", 3)] == ["15 03:00", "01 03:00", "15 03:00"]
    assert {r.month for r in next_runs("0 0 1 jan,jul *", BANGKOK, NOW, 4)} == {1, 7}


def test_runs_are_strictly_increasing_and_count_is_respected():
    r = runs("*/5 * * * *", 10)
    assert len(r) == 10 and r == sorted(r) and len(set(r)) == 10


@pytest.mark.parametrize(
    "expr,reason",
    [
        ("", "five fields"),
        ("* * * *", "five fields"),
        ("* * * * * *", "five fields"),
        ("every day", "five fields"),
        ("60 * * * *", "59"),
        ("61 * * * *", "59"),
        ("* 24 * * *", "23"),
        ("* * 32 * *", "31"),
        ("* * * 13 *", "12"),
        ("0 1 * * 8", "weekday"),
        ("0 1 * * mon-xyz", "weekday"),
        ("0 1 * * fri-mon", "backwards"),
        ("0 1 * * */x", "step"),
        ("0 1 * * 1/0", "step"),
        ("0 1 15 * 1", "both be set"),
        ("0 1 1-7 * mon", "both be set"),
        ("0 1 L * *", ""),
        ("0 1 * * 1#2", "allowed"),
        ("0 1 * * $", "allowed"),
        ("0 1 * * 1;2", "allowed"),
    ],
)
def test_bad_schedules_are_refused_with_a_reason(expr, reason):
    with pytest.raises(CronError) as exc:
        parse_cron(expr, BANGKOK)
    assert reason in str(exc.value)


def test_question_mark_means_any():
    assert runs("0 1 ? * 0", 1) == runs("0 1 * * 0", 1)
    assert runs("0 1 * * ?", 1) == runs("0 1 * * *", 1)


def test_normalize_tidies_spaces_and_validates():
    assert normalize("  0   2 *  * 0 ") == "0 2 * * 0"
    with pytest.raises(CronError):
        normalize("0 2 * *")


def test_slot_time_is_the_scheduled_time_not_the_moment_of_firing():
    trigger = parse_cron("*/15 * * * *", BANGKOK)
    scheduled = datetime(2026, 10, 8, 13, 30, tzinfo=BANGKOK)
    for delay in (0, 1, 7, 59, 120, 290):  # two workers firing at slightly different moments
        assert slot_time(trigger, scheduled + timedelta(seconds=delay)) == scheduled


def test_slot_time_for_a_daily_schedule():
    trigger = parse_cron("0 1 * * *", BANGKOK)
    fired_late = datetime(2026, 10, 9, 1, 0, 4, tzinfo=BANGKOK)
    assert slot_time(trigger, fired_late) == datetime(2026, 10, 9, 1, 0, tzinfo=BANGKOK)


def test_slot_time_without_a_recent_slot_falls_back_to_the_minute():
    trigger = parse_cron("0 2 * * 0", BANGKOK)  # nothing within the last ten minutes
    odd = datetime(2026, 10, 8, 12, 34, 56, 789, tzinfo=BANGKOK)
    assert slot_time(trigger, odd) == odd.replace(second=0, microsecond=0)
