import uuid
from datetime import datetime

import pytest
from sqlalchemy import select, update

from app.models import BackupJob
from app.worker import settings as worker
from tests.api.conftest import GB  # noqa: F401


async def setup(env, name="op-1", seed=1, **asset_extra):
    target = await env.add_target(name, seed=seed)
    asset = await env.add_asset(target, f"vm-{name}", **asset_extra)
    return target, asset


async def run_to_end(env, job_id=None):
    """Let the appliance finish everything and let the worker notice."""
    env.tick(3600)
    return await env.sync()


# ---- starting a backup ---------------------------------------------------------------------------
async def test_start_backup_is_asynchronous_and_audited(env):
    target, asset = await setup(env)
    job = await env.start(asset)
    assert job["status"] == "PENDING" and job["active"] and job["task_id"].startswith("T")
    assert job["backup_type"] == "full" and job["asset_name"] == "vm-op-1" and job["policy_id"] is None
    assert job["backup_target_id"] == target["id"] and job["asset_id"] == asset["id"]
    assert job["started_at"] is None and job["ended_at"] is None and job["data_transferred_gb"] == 0
    assert job["log_messages"][0].startswith("Task T")
    assert env.mock().objects["job"]["1"]["BACKUPTYPENAME"] == "full"
    [entry] = await env.audit(action="backup.trigger")
    assert entry["outcome"] == "success" and entry["actor"] == "tester" and entry["resource_id"] == job["id"]
    assert entry["details"]["task_id"] == job["task_id"] and entry["details"]["asset_id"] == asset["id"]
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))["status"] == "PENDING"


async def test_backup_type_comes_from_the_policy_unless_overridden(env):
    _, asset = await setup(env)
    inc = await env.add_policy("hourly-inc", backup_type="incremental")
    await env.ok("PATCH", f"/assets/{asset['id']}", json={"policy_id": inc["id"]})
    first = await env.start(asset)
    assert first["backup_type"] == "incremental" and first["policy_id"] == inc["id"]
    assert env.mock().objects["job"]["1"]["BACKUPTYPENAME"] == "incremental"
    await run_to_end(env)
    full = await env.start(asset, backup_type="full")  # explicit type wins over the policy's
    assert full["backup_type"] == "full" and full["policy_id"] == inc["id"]
    await run_to_end(env)
    other = await env.add_policy("weekly", backup_type="full")
    assert (await env.start(asset, policy_id=other["id"]))["policy_id"] == other["id"]  # explicit policy wins


async def test_only_one_running_backup_per_asset(env):
    _, asset = await setup(env)
    await env.start(asset)
    r = await env.err("POST", "/backup-jobs", 409, json={"asset_id": asset["id"]})
    assert "already has a backup in progress" in r["detail"]
    assert len(env.mock().objects["job"]) == 1  # nothing was started on the appliance
    await run_to_end(env)
    await env.start(asset)  # fine once the first one is over
    assert len(await env.audit(action="backup.trigger", outcome="failure")) == 1


async def test_trigger_validation(env):
    target, asset = await setup(env)
    zero = "00000000-0000-0000-0000-000000000000"
    await env.err("POST", "/backup-jobs", 404, json={"asset_id": zero})
    await env.err("POST", "/backup-jobs", 404, json={"asset_id": asset["id"], "policy_id": zero})
    await env.err("POST", "/backup-jobs", 422, json={"asset_id": asset["id"], "backup_type": "differential"})
    await env.err("POST", "/backup-jobs", 422, json={})
    off = await env.add_policy("paused", enabled=False)
    r = await env.err("POST", "/backup-jobs", 409, json={"asset_id": asset["id"], "policy_id": off["id"]})
    assert "disabled" in r["detail"]
    assert (await env.ok("GET", "/backup-jobs"))["total"] == 0 and not env.mock().objects["job"]
    assert target["id"]


async def test_unreachable_appliance_gives_502_and_no_job(env):
    _, asset = await setup(env)
    env.broken.add("op-1")
    await env.err("POST", "/backup-jobs", 502, json={"asset_id": asset["id"]})
    assert (await env.ok("GET", "/backup-jobs"))["total"] == 0
    assert len(await env.audit(action="backup.trigger", outcome="failure")) == 1


async def test_database_failure_cancels_the_task_on_the_appliance(env, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, asset = await setup(env)
    real, calls = AsyncSession.commit, {"n": 0}

    async def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is down")
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky)
    r = await env.http.post("/api/v1/backup-jobs", json={"asset_id": asset["id"]})
    monkeypatch.undo()
    assert r.status_code == 500
    assert env.mock().task_view("T000001")["STATUS"] == "CANCELLED"  # no orphan backup keeps running
    assert (await env.ok("GET", "/backup-jobs"))["total"] == 0


# ---- following a job ---------------------------------------------------------------------------------
async def test_job_is_followed_until_it_succeeds(env):
    _, asset = await setup(env)
    env.next_backup(outcome="SUCCESS", size_gb=120, duration_s=100)
    job = await env.start(asset)

    env.tick(5)
    result = await env.sync()
    assert (result.checked, result.updated, result.finished) == (1, 1, 0)
    running = await env.ok("GET", f"/backup-jobs/{job['id']}")
    assert (
        running["status"] == "RUNNING"
        and running["active"]
        and running["started_at"]
        and not running["ended_at"]
    )
    assert any("stream started" in line for line in running["log_messages"])

    env.tick(200)
    assert (await env.sync()).finished == 1
    done = await env.ok("GET", f"/backup-jobs/{job['id']}")
    assert done["status"] == "SUCCESS" and not done["active"]
    assert done["data_transferred_gb"] == pytest.approx(120)
    assert done["throughput_mb_s"] == pytest.approx(120 * 1024 / 100, rel=0.03)
    assert done["duration_seconds"] == pytest.approx(100, abs=2) and done["ended_at"] > done["started_at"]
    assert done["log_messages"][-1] == "Backup completed"
    assert (await env.sync()).checked == 0  # nothing left to follow


@pytest.mark.parametrize(
    "outcome,fraction", [("FAILED", 0.37), ("PARTIALLY_SUCCESSFUL", 0.8), ("SUCCESS", 1.0)]
)
async def test_final_states(env, outcome, fraction):
    _, asset = await setup(env)
    env.next_backup(outcome=outcome, size_gb=100, duration_s=100)
    job = await env.start(asset)
    await run_to_end(env)
    done = await env.ok("GET", f"/backup-jobs/{job['id']}")
    assert done["status"] == outcome and done["data_transferred_gb"] == pytest.approx(
        100 * fraction, rel=1e-3
    )
    if outcome == "FAILED":
        assert any(line.startswith("Error:") for line in done["log_messages"])
    if outcome == "PARTIALLY_SUCCESSFUL":
        assert any(line.startswith("Warning:") for line in done["log_messages"])


async def test_refresh_asks_the_appliance_now(env):
    _, asset = await setup(env)
    env.next_backup(outcome="SUCCESS", size_gb=100, duration_s=100)
    job = await env.start(asset)
    env.tick(53)  # half way through
    stored = await env.ok("GET", f"/backup-jobs/{job['id']}")
    assert stored["status"] == "PENDING" and stored["progress_percent"] is None  # last stored state
    live = await env.ok("GET", f"/backup-jobs/{job['id']}", params={"refresh": True})
    assert live["status"] == "RUNNING" and 40 <= live["progress_percent"] <= 60
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))["status"] == "RUNNING"  # and it was stored
    env.tick(100)
    final = await env.ok("GET", f"/backup-jobs/{job['id']}", params={"refresh": True})
    assert final["status"] == "SUCCESS" and final["ended_at"]


async def test_refresh_reports_an_unreachable_appliance(env):
    _, asset = await setup(env)
    job = await env.start(asset)
    env.broken.add("op-1")
    await env.err("GET", f"/backup-jobs/{job['id']}", 502, params={"refresh": True})
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))[
        "status"
    ] == "PENDING"  # stored state is still served


async def test_a_finished_job_never_changes_again(env):
    _, asset = await setup(env)
    env.next_backup(outcome="SUCCESS", size_gb=10, duration_s=50)
    job = await env.start(asset)
    await run_to_end(env)
    before = await env.ok("GET", f"/backup-jobs/{job['id']}")
    env.mock().task_lost.add(job["task_id"])  # the appliance forgets the task later on
    env.tick(86400)
    again = await env.ok("GET", f"/backup-jobs/{job['id']}", params={"refresh": True})
    assert again == before and (await env.sync()).checked == 0


async def test_a_task_the_appliance_forgot_is_marked_failed(env):
    _, asset = await setup(env)
    job = await env.start(asset)
    env.mock().task_lost.add(job["task_id"])
    env.tick(5)
    result = await env.sync()
    assert (result.lost, result.updated) == (1, 1)
    lost = await env.ok("GET", f"/backup-jobs/{job['id']}")
    assert lost["status"] == "FAILED" and lost["ended_at"]
    assert "no longer reports this task" in lost["log_messages"][-1]


async def test_one_unreachable_appliance_does_not_hold_back_the_others(env):
    _, a_asset = await setup(env, "a", seed=1)
    _, b_asset = await setup(env, "b", seed=2)
    a_job, b_job = await env.start(a_asset), await env.start(b_asset)
    env.broken.add("a")
    env.tick(5)
    result = await env.sync()
    assert result.unreachable == ["a"] and result.updated == 1
    assert (await env.ok("GET", f"/backup-jobs/{b_job['id']}"))["status"] == "RUNNING"
    assert (await env.ok("GET", f"/backup-jobs/{a_job['id']}"))["status"] == "PENDING"
    env.broken.discard("a")
    result = await env.sync()
    assert (
        result.unreachable == []
        and (await env.ok("GET", f"/backup-jobs/{a_job['id']}"))["status"] == "RUNNING"
    )


# ---- cancelling -------------------------------------------------------------------------------------------
async def test_cancel_a_running_backup(env):
    _, asset = await setup(env)
    env.next_backup(outcome="SUCCESS", size_gb=100, duration_s=100)
    job = await env.start(asset)
    env.tick(30)
    cancelled = await env.ok("POST", f"/backup-jobs/{job['id']}/cancel", status=200)
    assert cancelled["status"] == "CANCELLED" and cancelled["ended_at"] and not cancelled["active"]
    assert 0 < cancelled["data_transferred_gb"] < 100  # whatever was transferred before the stop
    [entry] = await env.audit(action="backup.cancel")
    assert entry["outcome"] == "success" and entry["resource_id"] == job["id"]
    env.tick(3600)
    await env.sync()
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))["status"] == "CANCELLED"  # stays cancelled
    await env.err("POST", f"/backup-jobs/{job['id']}/cancel", 409)
    await env.start(asset)  # the asset is free again


async def test_cancel_after_the_appliance_already_finished(env):
    _, asset = await setup(env)
    env.next_backup(outcome="SUCCESS", size_gb=10, duration_s=50)
    job = await env.start(asset)
    env.tick(3600)  # finished on the appliance; the CMP has not noticed yet
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))["status"] == "PENDING"
    r = await env.err("POST", f"/backup-jobs/{job['id']}/cancel", 409)
    assert "SUCCESS" in r["detail"]
    assert (await env.ok("GET", f"/backup-jobs/{job['id']}"))[
        "status"
    ] == "SUCCESS"  # the real result was kept
    [entry] = await env.audit(action="backup.cancel", outcome="failure")
    assert "already finished" in entry["details"]["error"]


async def test_cancel_unknown_job(env):
    await env.err("POST", "/backup-jobs/00000000-0000-0000-0000-000000000000/cancel", 404)
    await env.err("POST", "/backup-jobs/nope/cancel", 422)
    await env.err("GET", "/backup-jobs/00000000-0000-0000-0000-000000000000", 404)


# ---- lists and summary ------------------------------------------------------------------------------------
async def make_history(env):
    """Asset A: SUCCESS x2, FAILED, PARTIAL, then a running job. Asset B on another appliance: one SUCCESS."""
    _, a = await setup(env, "a", seed=1)
    _, b = await setup(env, "b", seed=2)
    for outcome in ("SUCCESS", "SUCCESS", "FAILED", "PARTIALLY_SUCCESSFUL"):
        env.next_backup("a", outcome=outcome, size_gb=100, duration_s=100)
        await env.start(a)
        await run_to_end(env)
    env.next_backup("b", outcome="SUCCESS", size_gb=100, duration_s=100)
    await env.start(b)
    await run_to_end(env)
    env.next_backup("a", outcome="SUCCESS", size_gb=100, duration_s=100)
    running = await env.start(a)
    return a, b, running


async def test_listing_and_filters(env):
    a, b, running = await make_history(env)
    page = await env.ok("GET", "/backup-jobs")
    assert page["total"] == 6 and len(page["items"]) == 6
    created = [j["created_at"] for j in page["items"]]
    assert created == sorted(created, reverse=True)  # newest first
    assert (await env.ok("GET", "/backup-jobs", params={"active": True}))["items"][0]["id"] == running["id"]
    assert (await env.ok("GET", "/backup-jobs", params={"active": False}))["total"] == 5
    assert (await env.ok("GET", "/backup-jobs", params={"status": "SUCCESS"}))["total"] == 3
    two = await env.ok(
        "GET", "/backup-jobs", params=[("status", "FAILED"), ("status", "PARTIALLY_SUCCESSFUL")]
    )
    assert two["total"] == 2
    assert (await env.ok("GET", "/backup-jobs", params={"asset_id": b["id"]}))["total"] == 1
    assert (await env.ok("GET", "/backup-jobs", params={"backup_target_id": a["backup_target_id"]}))[
        "total"
    ] == 5
    assert (await env.ok("GET", "/backup-jobs", params={"limit": 4, "offset": 4}))["items"].__len__() == 2
    await env.err("GET", "/backup-jobs", 422, params={"status": "DONE"})
    await env.err("GET", "/backup-jobs", 422, params={"limit": 0})


async def test_summary_counts_and_success_rate(env):
    a, b, running = await make_history(env)
    s = await env.ok("GET", "/backup-jobs/summary")
    assert s["window"] == "24h" and s["total"] == 6 and s["active"] == 1
    assert s["by_status"]["SUCCESS"] == 3 and s["by_status"]["FAILED"] == 1
    assert (
        s["by_status"]["PARTIALLY_SUCCESSFUL"] == 1
        and s["by_status"]["RUNNING"] + s["by_status"]["PENDING"] == 1
    )
    assert s["success_rate_percent"] == 60.0  # 3 of the 5 finished jobs
    assert s["total_transferred_gb"] == pytest.approx(3 * 100 + 37 + 80, rel=1e-3)
    assert s["avg_throughput_mb_s"] == pytest.approx((3 * 1024 + 37 * 10.24 + 80 * 10.24) / 5, rel=0.05)
    only_a = await env.ok("GET", "/backup-jobs/summary", params={"backup_target_id": a["backup_target_id"]})
    assert only_a["total"] == 5
    async with env.maker() as session:  # make one job 10 days old
        old_id = (
            await session.scalars(select(BackupJob.id).where(BackupJob.asset_id == uuid.UUID(b["id"])))
        ).one()
        await session.execute(
            update(BackupJob).where(BackupJob.id == old_id).values(created_at=datetime(2020, 1, 1))
        )
        await session.commit()
    assert (await env.ok("GET", "/backup-jobs/summary", params={"window": "30d"}))["total"] == 5
    await env.err("GET", "/backup-jobs/summary", 422, params={"window": "1y"})


async def test_summary_without_jobs(env):
    await setup(env)
    s = await env.ok("GET", "/backup-jobs/summary", params={"window": "7d"})
    assert s["total"] == 0 and s["success_rate_percent"] is None and s["avg_throughput_mb_s"] is None
    assert (
        s["total_transferred_gb"] == 0
        and s["active"] == 0
        and set(s["by_status"]) >= {"SUCCESS", "CANCELLED"}
    )


# ---- the worker ----------------------------------------------------------------------------------------------
async def test_worker_function_and_schedule(env):
    from app.core.crypto import get_cipher

    _, asset = await setup(env)
    await env.start(asset)
    env.tick(5)
    ctx = {"maker": env.maker, "factory": env.factory, "cipher": get_cipher()}
    assert await worker.sync_backup_jobs(ctx) == {
        "checked": 1,
        "updated": 1,
        "finished": 0,
        "lost": 0,
        "unreachable_targets": 0,
    }
    jobs = {j.coroutine.__name__: j for j in worker.WorkerSettings.cron_jobs}
    assert jobs["sync_backup_jobs"].second == {0, 15, 30, 45}  # every 15 seconds
    assert jobs["collect_metrics"].second == {0, 30}
