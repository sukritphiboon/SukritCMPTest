"""Cron expressions for backup policies (five fields: minute hour day-of-month month day-of-week).

APScheduler's own ``CronTrigger.from_crontab`` is not used because it numbers weekdays from Monday:
"0 2 * * 0"
(Sunday in every crontab) would run on Mondays. Here the weekday field is translated to names first.

Rules that differ from a plain crontab are refused instead of being guessed:

* day-of-month and day-of-week must not both be restricted (a crontab combines them with OR,
  APScheduler with AND);
* ``L``, ``W``, ``#`` and other extensions are not supported.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo

from apscheduler.triggers.cron import CronTrigger

_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]  # crontab numbering: 0 and 7 are Sunday
_NAME_TO_NUMBER = {name: i for i, name in enumerate(_DAYS)}
_SIMPLE = re.compile(r"^[0-9A-Za-z*/,\-?]+$")


class CronError(ValueError):
    """The expression is not a usable cron schedule; the message says why."""


def _weekday_number(token: str) -> int:
    t = token.strip().lower()
    if t in _NAME_TO_NUMBER:
        return _NAME_TO_NUMBER[t]
    if t.isdigit() and 0 <= int(t) <= 7:
        return int(t) % 7
    raise CronError(f"day-of-week '{token}' is not a weekday (0-7, where 0 and 7 are Sunday, or sun-sat)")


def _weekdays(field: str) -> str:
    """Translate a crontab weekday field to the weekday names APScheduler understands."""
    if field in ("*", "?"):
        return "*"
    days: set[int] = set()
    for part in field.split(","):
        base, _, step_text = part.partition("/")
        if step_text and not step_text.isdigit():
            raise CronError(f"day-of-week step '{step_text}' is not a number")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise CronError("day-of-week step must be at least 1")
        if base == "*":
            first, last = 0, 6
        elif "-" in base:
            low_text, _, high_text = base.partition("-")
            first, last = _weekday_number(low_text), _weekday_number(high_text)
            if high_text.strip().lower() in ("7", "sun") and first != 0:
                last = 7  # "5-7" and "fri-sun" end on Sunday
            if first > last:
                raise CronError(f"day-of-week range '{base}' runs backwards")
        else:
            first = last = _weekday_number(base)
            if step_text:
                last = 7  # "1/2" counts from Monday to the end of the week (7 is Sunday again)
        days.update(d % 7 for d in range(first, last + 1, step))
    return ",".join(_DAYS[d] for d in sorted(days))


def parse_cron(expression: str, tz: tzinfo) -> CronTrigger:
    fields = expression.split()
    if len(fields) != 5:
        raise CronError("a schedule needs five fields: minute hour day-of-month month day-of-week")
    if not all(_SIMPLE.match(f) for f in fields):
        raise CronError("only digits, names and the characters * / , - ? are allowed in a schedule")
    minute, hour, day, month, weekday = fields
    day = "*" if day == "?" else day
    month = "*" if month == "?" else month
    if day != "*" and weekday not in ("*", "?"):
        raise CronError(
            "day-of-month and day-of-week cannot both be set; use one of them "
            "(a crontab would run on either day, which is rarely what is meant)"
        )
    try:
        return CronTrigger(
            minute=minute, hour=hour, day=day, month=month, day_of_week=_weekdays(weekday), timezone=tz
        )
    except CronError:
        raise
    except (ValueError, TypeError) as exc:
        raise CronError(str(exc)) from exc


def normalize(expression: str) -> str:
    """Single spaces between the fields (raises CronError when the expression is not valid)."""
    from zoneinfo import ZoneInfo

    parse_cron(expression, ZoneInfo("UTC"))
    return " ".join(expression.split())


def next_runs(expression: str, tz: tzinfo, now: datetime, count: int = 5) -> list[datetime]:
    """The next ``count`` run times after ``now`` (a timezone-aware datetime), in ``tz``."""
    trigger = parse_cron(expression, tz)
    runs: list[datetime] = []
    previous = None
    current = now.astimezone(tz)
    for _ in range(count):
        following = trigger.get_next_fire_time(previous, current)
        if following is None:
            break
        runs.append(following)
        previous, current = following, following
    return runs


def slot_time(trigger: CronTrigger, now: datetime, window: timedelta = timedelta(minutes=10)) -> datetime:
    """The scheduled time a firing at ``now`` belongs to: the latest run time at or before ``now``.

    Workers that fire a few seconds apart still get the same value, so it works as a de-duplication key.
    """
    start = now - window
    slot = None
    previous = None
    current = start
    while True:
        following = trigger.get_next_fire_time(previous, current)
        if following is None or following > now:
            break
        slot, previous, current = following, following, following
    return slot or now.replace(second=0, microsecond=0)
