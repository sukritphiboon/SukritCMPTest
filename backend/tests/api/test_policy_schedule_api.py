from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models import BackupPolicy, BackupType


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


async def test_policy_shows_its_next_run_in_the_system_timezone(env):
    p = await env.add_policy("nightly", cron_schedule="0 1 * * *")
    assert p["schedule_timezone"] == "Asia/Bangkok"
    # T0 is 2026-10-01 12:00 UTC = 19:00 in Bangkok, so the next 01:00 is the morning of 2 October (18:00 UTC)
    assert parse(p["next_run_at"]) == datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
    assert parse(p["next_run_at"]).utcoffset() == timedelta(hours=7)
    listed = (await env.ok("GET", "/backup-policies"))[0]
    shown = await env.ok("GET", f"/backup-policies/{p['id']}")
    assert listed["next_run_at"] == shown["next_run_at"] == p["next_run_at"]


async def test_next_run_follows_the_clock_and_changes(env):
    p = await env.add_policy("nightly", cron_schedule="0 1 * * *")
    env.tick(7 * 3600)  # now 01:00 Bangkok of 2 October exactly: the next one is the following day
    again = await env.ok("GET", f"/backup-policies/{p['id']}")
    assert parse(again["next_run_at"]) >= datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
    patched = await env.ok("PATCH", f"/backup-policies/{p['id']}", json={"cron_schedule": "30 3 * * *"})
    assert parse(patched["next_run_at"]).hour == 3 and parse(patched["next_run_at"]).minute == 30


async def test_a_disabled_policy_has_no_next_run(env):
    p = await env.add_policy("paused", enabled=False)
    assert p["next_run_at"] is None
    on = await env.ok("PATCH", f"/backup-policies/{p['id']}", json={"enabled": True})
    assert on["next_run_at"] is not None
    off = await env.ok("PATCH", f"/backup-policies/{p['id']}", json={"enabled": False})
    assert off["next_run_at"] is None


async def test_the_timezone_is_one_setting_for_the_whole_system(env, monkeypatch):
    p = await env.add_policy("nightly", cron_schedule="0 1 * * *")
    monkeypatch.setattr(get_settings(), "scheduler_timezone", "UTC")
    shown = await env.ok("GET", f"/backup-policies/{p['id']}")
    assert shown["schedule_timezone"] == "UTC" and parse(shown["next_run_at"]) == datetime(
        2026, 10, 2, 1, 0, tzinfo=UTC
    )


async def test_schedule_preview_lists_the_next_runs(env):
    p = await env.add_policy("weekly", cron_schedule="0 2 * * 0")
    s = await env.ok("GET", f"/backup-policies/{p['id']}/schedule", params={"count": 3})
    assert s["cron_schedule"] == "0 2 * * 0" and s["timezone"] == "Asia/Bangkok" and s["enabled"]
    runs = [parse(r) for r in s["runs"]]
    assert len(runs) == 3 and runs == sorted(runs)
    assert all(r.weekday() == 6 and r.hour == 2 for r in runs)  # Sundays at 02:00, not Mondays
    assert runs[1] - runs[0] == timedelta(days=7)
    assert len((await env.ok("GET", f"/backup-policies/{p['id']}/schedule"))["runs"]) == 5  # default count


async def test_schedule_preview_edge_cases(env):
    p = await env.add_policy("p")
    off = await env.add_policy("off", enabled=False)
    assert (await env.ok("GET", f"/backup-policies/{off['id']}/schedule"))["runs"] == []
    for bad in (0, 51, "x"):
        await env.err("GET", f"/backup-policies/{p['id']}/schedule", 422, params={"count": bad})
    await env.err("GET", "/backup-policies/00000000-0000-0000-0000-000000000000/schedule", 404)


@pytest.mark.parametrize(
    "cron,reason",
    [
        ("61 * * * *", "59"),
        ("0 24 * * *", "23"),
        ("0 1 * * 8", "weekday"),
        ("0 1 15 * 1", "both be set"),
        ("0 1 L * *", "cron_schedule"),
        ("0 1 * * fri-mon", "backwards"),
        ("* * * *", "five fields"),
    ],
)
async def test_a_schedule_that_cannot_run_is_refused_when_saved(env, cron, reason):
    body = {"name": "x", "cron_schedule": cron, "backup_type": "full", "retention_days": 5}
    r = await env.err("POST", "/backup-policies", 422, json=body)
    assert reason in str(r["detail"])
    ok = await env.add_policy("fine")
    patched = await env.err("PATCH", f"/backup-policies/{ok['id']}", 422, json={"cron_schedule": cron})
    assert reason in str(patched["detail"])
    assert (await env.ok("GET", f"/backup-policies/{ok['id']}"))["cron_schedule"] == "0 1 * * *"  # unchanged


async def test_an_unreadable_stored_schedule_is_shown_not_fatal(env):
    async with env.maker() as s:
        bad = BackupPolicy(
            name="legacy", cron_schedule="every night", backup_type=BackupType.FULL, retention_days=7
        )
        s.add(bad)
        await s.commit()
        policy_id = str(bad.id)
    shown = await env.ok("GET", f"/backup-policies/{policy_id}")
    assert shown["next_run_at"] is None and shown["cron_schedule"] == "every night"
    assert len(await env.ok("GET", "/backup-policies")) == 1
    r = await env.err("GET", f"/backup-policies/{policy_id}/schedule", 409)
    assert "cannot be used" in r["detail"]


# ---- run now ---------------------------------------------------------------------------------------------------------------
async def policy_with_assets(env):
    t1, t2 = await env.add_target("a", seed=1), await env.add_target("b", seed=2)
    p = await env.add_policy("nightly", backup_type="incremental")
    assets = [await env.add_asset(t, n, policy_id=p["id"]) for t, n in ((t1, "a1"), (t1, "a2"), (t2, "b1"))]
    return p, assets


async def test_run_now_starts_every_asset_of_the_policy(env):
    p, assets = await policy_with_assets(env)
    r = await env.ok("POST", f"/backup-policies/{p['id']}/run", status=200)
    assert r["status"] == "ran" and (r["started"], r["skipped"], r["failed"]) == (3, 0, 0)
    assert r["policy_name"] == "nightly" and all(x["job_id"] for x in r["results"])
    jobs = await env.ok("GET", "/backup-jobs")
    assert jobs["total"] == 3 and {j["backup_type"] for j in jobs["items"]} == {"incremental"}
    assert {j["policy_id"] for j in jobs["items"]} == {p["id"]} and len(assets) == 3


async def test_run_now_is_audited_with_the_name_of_the_caller(env):
    p, _ = await policy_with_assets(env)
    await env.ok("POST", f"/backup-policies/{p['id']}/run")
    [entry] = await env.audit(action="policy.run")
    assert (
        entry["actor"] == "tester"
        and entry["details"]["started"] == 3
        and entry["resource_name"] == "nightly"
    )
    assert {a["actor"] for a in await env.audit(action="backup.trigger")} == {"tester"}
    assert await env.audit(action="schedule.run") == []  # that name is only for the timed runs


async def test_run_now_twice_skips_what_is_already_running(env):
    p, _ = await policy_with_assets(env)
    await env.ok("POST", f"/backup-policies/{p['id']}/run")
    second = await env.ok("POST", f"/backup-policies/{p['id']}/run")
    assert (second["started"], second["skipped"], second["failed"]) == (0, 3, 0)
    assert (await env.ok("GET", "/backup-jobs"))["total"] == 3


async def test_run_now_reports_an_unreachable_appliance_per_asset(env):
    p, _ = await policy_with_assets(env)
    env.broken.add("a")
    r = await env.ok("POST", f"/backup-policies/{p['id']}/run")
    assert (r["started"], r["failed"]) == (1, 2)
    failed = [x for x in r["results"] if x["outcome"] == "failed"]
    assert {x["asset_name"] for x in failed} == {"a1", "a2"} and "link down" in failed[0]["detail"]


async def test_run_now_refusals(env):
    p = await env.add_policy("paused", enabled=False)
    r = await env.err("POST", f"/backup-policies/{p['id']}/run", 409)
    assert "disabled" in r["detail"]
    await env.err("POST", "/backup-policies/00000000-0000-0000-0000-000000000000/run", 404)
    await env.err("POST", "/backup-policies/nope/run", 422)
    anon = await env.http.post(f"/api/v1/backup-policies/{p['id']}/run", headers={"X-API-Key": "wrong"})
    assert anon.status_code == 401
    assert (await env.ok("GET", "/backup-jobs"))["total"] == 0


async def test_run_now_for_a_policy_without_assets(env):
    p = await env.add_policy("empty")
    r = await env.ok("POST", f"/backup-policies/{p['id']}/run")
    assert r["status"] == "ran" and r["results"] == [] and r["started"] == 0
