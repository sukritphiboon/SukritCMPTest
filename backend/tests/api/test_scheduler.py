import asyncio
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.models import BackupJob, BackupPolicy
from app.services.backup import scheduler as sched
from app.services.backup.scheduler import (
    MemoryClaim,
    PolicyRow,
    build_scheduler,
    reconcile,
    redis_claim,
    run_policy,
)
from app.worker import settings as worker
from tests.api.conftest import T0

BANGKOK = ZoneInfo("Asia/Bangkok")


async def fleet(env, policy_kw=None):
    """Two appliances: a1, a2 on the first, b1 on the second, all assigned to one policy."""
    ta = await env.add_target("a", seed=1)
    tb = await env.add_target("b", seed=2)
    policy = await env.add_policy("nightly", backup_type="incremental", **(policy_kw or {}))
    assets = {}
    for name, target in (("a1", ta), ("a2", ta), ("b1", tb)):
        assets[name] = await env.add_asset(target, name, policy_id=policy["id"])
    return policy, assets


def run(env, policy, **kw):
    kw.setdefault("claim", MemoryClaim())
    kw.setdefault("parallel", 1)  # the test database is a single SQLite connection (see the env fixture)
    return run_policy(env.maker, env.factory, uuid.UUID(policy["id"]), T0, **kw)


async def db_jobs(env):
    async with env.maker() as s:
        return list((await s.scalars(select(BackupJob))).all())


# ---- one firing ------------------------------------------------------------------------------------------
async def test_a_run_starts_a_backup_for_every_asset_of_the_policy(env):
    policy, assets = await fleet(env)
    result = await run(env, policy)
    assert result.status == "ran" and result.policy_name == "nightly"
    assert [r.outcome for r in result.results] == ["started"] * 3
    assert sorted(r.asset_name for r in result.results) == ["a1", "a2", "b1"]
    jobs = await db_jobs(env)
    assert len(jobs) == 3 and {j.backup_type.value for j in jobs} == {"incremental"}  # the policy's type
    assert {str(j.policy_id) for j in jobs} == {policy["id"]}
    assert len(env.mock("a").objects["job"]) == 2 and len(env.mock("b").objects["job"]) == 1
    assert env.mock("a").objects["job"]["1"]["BACKUPTYPENAME"] == "incremental"


async def test_the_run_and_every_backup_are_audited_as_the_scheduler(env):
    policy, _ = await fleet(env)
    await run(env, policy)
    [entry] = await env.audit(action="schedule.run")
    assert (
        entry["actor"] == "scheduler"
        and entry["resource_name"] == "nightly"
        and entry["outcome"] == "success"
    )
    assert entry["details"]["assets"] == 3 and entry["details"]["started"] == 3
    assert entry["details"]["skipped"] == 0 and entry["details"]["failed"] == 0
    assert entry["details"]["fire_time"] == T0.isoformat()
    triggers = await env.audit(action="backup.trigger")
    assert len(triggers) == 3 and {t["actor"] for t in triggers} == {"scheduler"}


async def test_an_asset_with_a_backup_in_progress_is_skipped(env):
    policy, assets = await fleet(env)
    await env.start(assets["a1"])
    result = await run(env, policy)
    by_name = {r.asset_name: r for r in result.results}
    assert by_name["a1"].outcome == "skipped" and "already in progress" in by_name["a1"].detail
    assert by_name["a2"].outcome == by_name["b1"].outcome == "started"
    assert len(await db_jobs(env)) == 3  # the manual one plus two new
    [entry] = await env.audit(action="schedule.run")
    assert entry["details"]["skipped"] == 1 and entry["details"]["started"] == 2
    assert await env.audit(action="backup.trigger", outcome="failure") == []  # skipping is not a failure


async def test_one_unreachable_appliance_does_not_stop_the_others(env):
    policy, _ = await fleet(env)
    env.broken.add("a")
    result = await run(env, policy)
    by_name = {r.asset_name: r for r in result.results}
    assert by_name["a1"].outcome == by_name["a2"].outcome == "failed" and "link down" in by_name["a1"].detail
    assert by_name["b1"].outcome == "started" and by_name["b1"].job_id
    assert len(await db_jobs(env)) == 1
    [entry] = await env.audit(action="schedule.run")
    assert entry["details"]["failed"] == 2 and entry["details"]["started"] == 1
    assert len(await env.audit(action="backup.trigger", outcome="failure")) == 2


async def test_a_disabled_or_missing_policy_does_nothing(env):
    policy, _ = await fleet(env)
    await env.ok("PATCH", f"/backup-policies/{policy['id']}", json={"enabled": False})
    off = await run(env, policy)
    assert off.status == "disabled" and off.results == [] and await db_jobs(env) == []
    gone = await run_policy(env.maker, env.factory, uuid.uuid4(), T0)
    assert gone.status == "not_found"
    assert await env.audit(action="schedule.run") == []


async def test_a_second_worker_cannot_start_the_same_firing(env):
    policy, _ = await fleet(env)
    claim = MemoryClaim()
    first = await run(env, policy, claim=claim)
    second = await run(env, policy, claim=claim)  # same policy, same scheduled time
    assert first.status == "ran" and second.status == "duplicate" and second.results == []
    assert len(await db_jobs(env)) == 3
    assert len(await env.audit(action="schedule.run")) == 1
    assert list(claim.keys) == [f"cmp:sched:{policy['id']}:{T0.isoformat()}"]
    later = await run_policy(
        env.maker, env.factory, uuid.UUID(policy["id"]), T0 + timedelta(days=1), claim=claim
    )
    assert (
        later.status == "ran"
    )  # the next day is a new firing (its assets are still busy, so all are skipped)
    assert {r.outcome for r in later.results} == {"skipped"}


async def test_a_policy_without_assets_still_records_the_run(env):
    policy = await env.add_policy("empty")
    result = await run(env, policy)
    assert result.status == "ran" and result.results == []
    [entry] = await env.audit(action="schedule.run")
    assert entry["details"]["assets"] == 0


async def test_parallel_starts_are_capped(env, monkeypatch):
    """With a cap of 2, never more than two backups are being started at the same moment."""
    from types import SimpleNamespace

    target = await env.add_target("a", seed=1)
    policy = await env.add_policy("nightly")
    for i in range(6):
        await env.add_asset(target, f"vm-{i}", policy_id=policy["id"])
    running = peak = 0

    async def slow_start(ctx, body):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(sched, "trigger_job", slow_start)
    result = await run_policy(env.maker, env.factory, uuid.UUID(policy["id"]), T0, parallel=2)
    assert [r.outcome for r in result.results] == ["started"] * 6
    assert peak == 2


async def test_starts_after_a_finished_backup_are_allowed_again(env):
    policy, _ = await fleet(env)
    await run(env, policy, claim=None)
    env.tick(3600)
    await env.sync()  # every backup is over
    again = await run_policy(
        env.maker, env.factory, uuid.UUID(policy["id"]), T0 + timedelta(days=1), parallel=1
    )
    assert [r.outcome for r in again.results] == ["started"] * 3
    assert len(await db_jobs(env)) == 6


# ---- keeping the scheduler in line with the database ------------------------------------------------------------------
async def noop(policy_id):
    pass


@pytest.fixture
async def aps():
    scheduler = AsyncIOScheduler(timezone=BANGKOK)
    scheduler.start()
    yield scheduler
    scheduler.shutdown(wait=False)


def row(name="p", cron="0 1 * * *", enabled=True, id=None):
    return PolicyRow(id or uuid.uuid4(), name, cron, enabled)


def jobs(aps):
    return {j.id: j for j in aps.get_jobs() if j.id.startswith("policy:")}


async def test_enabled_policies_get_a_job_and_disabled_ones_do_not(aps):
    on, off = row("on"), row("off", enabled=False)
    result = reconcile(aps, [on, off], noop, BANGKOK, misfire_grace_seconds=120)
    assert result.added == [f"policy:{on.id}"] and result.updated == result.removed == result.invalid == []
    assert list(jobs(aps)) == [f"policy:{on.id}"]
    job = jobs(aps)[f"policy:{on.id}"]
    assert job.name == "on" and job.kwargs == {"policy_id": str(on.id)}
    assert job.coalesce is True and job.max_instances == 1 and job.misfire_grace_time == 120
    assert job.trigger.timezone == BANGKOK
    nxt = job.next_run_time
    assert nxt.hour == 1 and nxt.minute == 0 and nxt.utcoffset() == timedelta(hours=7)


async def test_a_weekly_job_really_runs_on_sunday(aps):
    p = row("weekly", "0 2 * * 0")
    reconcile(aps, [p], noop, BANGKOK)
    assert jobs(aps)[f"policy:{p.id}"].next_run_time.weekday() == 6  # Monday is 0, Sunday is 6


async def test_unchanged_jobs_are_left_alone(aps):
    p = row()
    reconcile(aps, [p], noop, BANGKOK)
    before = jobs(aps)[f"policy:{p.id}"]
    next_run = before.next_run_time
    again = reconcile(aps, [p], noop, BANGKOK)
    assert again.added == again.updated == again.removed == []
    after = jobs(aps)[f"policy:{p.id}"]
    assert after is before or (after.next_run_time == next_run and str(after.trigger) == str(before.trigger))


async def test_a_changed_schedule_is_rescheduled(aps):
    p = row(cron="0 1 * * *")
    reconcile(aps, [p], noop, BANGKOK)
    changed = PolicyRow(p.id, "renamed", "30 3 * * *", True)
    result = reconcile(aps, [changed], noop, BANGKOK)
    assert result.updated == [f"policy:{p.id}"] and result.added == result.removed == []
    job = jobs(aps)[f"policy:{p.id}"]
    assert (job.next_run_time.hour, job.next_run_time.minute) == (3, 30) and job.name == "renamed"
    assert len(jobs(aps)) == 1


async def test_disabled_or_deleted_policies_lose_their_job(aps):
    a, b, c = row("a"), row("b"), row("c")
    reconcile(aps, [a, b, c], noop, BANGKOK)
    result = reconcile(
        aps, [a, PolicyRow(b.id, "b", b.cron_schedule, False)], noop, BANGKOK
    )  # b disabled, c deleted
    assert sorted(result.removed) == sorted([f"policy:{b.id}", f"policy:{c.id}"])
    assert list(jobs(aps)) == [f"policy:{a.id}"]


async def test_an_unusable_stored_schedule_is_reported_and_never_scheduled(aps):
    good, bad = row("good"), row("bad", "99 * * * *")
    result = reconcile(aps, [good, bad], noop, BANGKOK)
    assert result.invalid == ["bad"] and list(jobs(aps)) == [f"policy:{good.id}"]
    broken_later = PolicyRow(good.id, "good", "nonsense", True)
    result = reconcile(aps, [broken_later], noop, BANGKOK)
    assert result.invalid == ["good"] and result.removed == [f"policy:{good.id}"] and jobs(aps) == {}


async def test_other_scheduler_jobs_are_not_touched(aps):
    aps.add_job(noop, "interval", seconds=60, id="scheduler:sync", kwargs={"policy_id": "x"})
    reconcile(aps, [], noop, BANGKOK)
    assert aps.get_job("scheduler:sync") is not None


# ---- the real scheduler ---------------------------------------------------------------------------------------------
@pytest.fixture
async def live(env):
    scheduler = build_scheduler(
        env.maker,
        env.factory,
        timezone="Asia/Bangkok",
        sync_seconds=30,
        misfire_grace_seconds=300,
        max_parallel_starts=1,  # the test database is a single SQLite connection
    )
    scheduler.start()
    yield scheduler
    scheduler.shutdown(wait=False)


async def test_the_database_is_loaded_and_follows_changes(env, live):
    policy = await env.add_policy("nightly", cron_schedule="0 1 * * *")
    off = await env.add_policy("paused", enabled=False)
    await live.sync()
    assert list(jobs(live)) == [f"policy:{policy['id']}"] and off["id"]
    sync_job = live.get_job("scheduler:sync")
    assert sync_job.trigger.interval == timedelta(seconds=30)  # a policy change is picked up within 30 s

    await env.ok("PATCH", f"/backup-policies/{policy['id']}", json={"cron_schedule": "15 4 * * 1-5"})
    await env.ok("PATCH", f"/backup-policies/{off['id']}", json={"enabled": True})
    await live.sync()
    assert len(jobs(live)) == 2
    assert (
        jobs(live)[f"policy:{policy['id']}"].next_run_time.hour,
        jobs(live)[f"policy:{policy['id']}"].next_run_time.minute,
    ) == (4, 15)

    await env.ok("DELETE", f"/backup-policies/{off['id']}", status=204)
    await live.sync()
    assert list(jobs(live)) == [f"policy:{policy['id']}"]


async def test_the_scheduled_job_runs_the_real_backup_path(env, live):
    policy, _ = await fleet(env)
    await live.sync()
    job = jobs(live)[f"policy:{policy['id']}"]
    await job.func(**job.kwargs)  # exactly what the scheduler does when the time comes
    assert len(await db_jobs(env)) == 3
    [entry] = await env.audit(action="schedule.run")
    assert entry["actor"] == "scheduler" and entry["details"]["started"] == 3


async def test_a_failing_run_never_stops_the_scheduler(env, live, monkeypatch):
    policy, _ = await fleet(env)
    await live.sync()

    async def boom(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(sched, "run_policy", boom)
    job = jobs(live)[f"policy:{policy['id']}"]
    await job.func(**job.kwargs)  # must not raise
    assert live.running


async def test_a_broken_database_read_keeps_the_previous_schedule(env, live, monkeypatch):
    policy = await env.add_policy("nightly")
    await live.sync()

    def broken():
        raise RuntimeError("database is down")

    monkeypatch.setattr(sched, "reconcile", lambda *a, **k: broken())
    await live.sync()  # logs the problem and keeps going
    assert list(jobs(live)) == [f"policy:{policy['id']}"]


def test_an_unknown_timezone_is_explained():
    with pytest.raises(ValueError, match="Nowhere/Land"):
        sched.get_timezone("Nowhere/Land")


# ---- the claim ----------------------------------------------------------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.data = {}
        self.expiry = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key], self.expiry[key] = value, ex
        return True


async def test_redis_claim_lets_only_the_first_caller_through():
    redis = FakeRedis()
    claim = redis_claim(redis, ttl_seconds=600)
    assert await claim("k") is True and await claim("k") is False and await claim("other") is True
    assert redis.expiry["k"] == 600  # the key cleans itself up


# ---- the worker ----------------------------------------------------------------------------------------------------------
def ctx_for(env, redis=None):
    ctx = {"maker": env.maker, "factory": env.factory, "cipher": get_cipher()}
    if redis is not None:
        ctx["redis"] = redis
    return ctx


async def test_worker_starts_the_scheduler_with_the_stored_policies_and_stops_it(env):
    policy = await env.add_policy("nightly")
    ctx = ctx_for(env, FakeRedis())
    await worker.startup(ctx)
    scheduler = ctx["scheduler"]
    assert scheduler.running and str(scheduler.timezone) == "Asia/Bangkok"
    assert list(jobs(scheduler)) == [
        f"policy:{policy['id']}"
    ]  # loaded straight away, not after the first interval
    await worker.shutdown(ctx)
    await asyncio.sleep(0.05)  # APScheduler finishes a shutdown on the next turn of the event loop
    assert not scheduler.running and "scheduler" not in ctx


async def test_the_scheduler_can_be_switched_off(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "scheduler_enabled", False)
    ctx = ctx_for(env)
    await worker.startup(ctx)
    assert "scheduler" not in ctx
    await worker.shutdown(ctx)  # nothing to stop, no error


async def test_a_wrong_timezone_stops_the_worker_with_a_clear_message(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "scheduler_timezone", "Mars/Olympus")
    with pytest.raises(ValueError, match="Mars/Olympus"):
        await worker.startup(ctx_for(env))


def test_worker_is_configured_to_stop_the_scheduler():
    assert (
        worker.WorkerSettings.on_shutdown is worker.shutdown
        and worker.WorkerSettings.on_startup is worker.startup
    )


async def test_a_policy_row_is_a_plain_snapshot(env):
    policy = await env.add_policy("nightly")
    async with env.maker() as s:
        p = await s.get(BackupPolicy, uuid.UUID(policy["id"]))
        snapshot = PolicyRow(p.id, p.name, p.cron_schedule, p.enabled)
    assert snapshot.cron_schedule == "0 1 * * *" and snapshot.enabled
