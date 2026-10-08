import pytest
from arq.cron import CronJob

from app.worker import settings as worker
from app.worker.settings import WorkerSettings, poll_seconds


def test_polling_rhythm():
    assert poll_seconds(30) == {0, 30}
    assert poll_seconds(60) == {0}
    assert poll_seconds(15) == {0, 15, 30, 45}
    for bad in (0, 7, 45, 90):
        with pytest.raises(ValueError, match="divisor of 60"):
            poll_seconds(bad)


def test_the_collector_is_scheduled_every_30_seconds():
    jobs = {j.coroutine.__name__: j for j in WorkerSettings.cron_jobs}
    collector = jobs["collect_metrics"]
    assert isinstance(collector, CronJob) and collector.second == {0, 30}
    assert collector.run_at_startup
    assert collector.minute is None and collector.hour is None  # no restriction: every minute of every hour
    housekeeping = jobs["purge_old_data"]
    assert (housekeeping.hour, housekeeping.minute, housekeeping.second) == (3, 10, 0)


def test_the_default_interval_is_30_seconds():
    from app.core.config import get_settings

    assert get_settings().telemetry_interval_seconds == 30


async def test_collect_metrics_polls_every_target(env):
    await env.add_target("a", seed=1)
    await env.add_target("b", seed=2)
    await env.ok(
        "PATCH", f"/targets/{(await env.ok('GET', '/targets'))[1]['id']}", json={"password": "wrong"}
    )
    from app.core.crypto import get_cipher

    ctx = {"maker": env.maker, "factory": env.factory, "cipher": get_cipher()}
    assert await worker.collect_metrics(ctx) == {"polled": 2, "failed": 1}
    assert await worker.purge_old_data(ctx) == {"throughput": 0, "hardware": 0, "capacity": 0, "alarms": 0}
