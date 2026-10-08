import pytest
from sqlalchemy import select

from app.models import BackupPolicy, ProtectedAsset


async def test_policy_lifecycle_and_audit(env):
    p = await env.add_policy("weekly-full", cron_schedule="0  2 * * 0", retention_days=90)
    assert p["cron_schedule"] == "0 2 * * 0"  # whitespace tidied
    assert p["backup_type"] == "full" and p["enabled"] and not p["worm_enabled"] and p["worm_mode"] == "none"
    patched = await env.ok(
        "PATCH", f"/backup-policies/{p['id']}", json={"backup_type": "incremental", "enabled": False}
    )
    assert (
        patched["backup_type"] == "incremental" and not patched["enabled"] and patched["retention_days"] == 90
    )
    assert [x["name"] for x in await env.ok("GET", "/backup-policies")] == ["weekly-full"]
    assert await env.ok("GET", "/backup-policies", params={"enabled": True}) == []
    await env.ok("DELETE", f"/backup-policies/{p['id']}", status=204)
    await env.err("GET", f"/backup-policies/{p['id']}", 404)
    actions = [a["action"] for a in reversed(await env.audit(resource_type="policy"))]
    assert actions == ["policy.create", "policy.update", "policy.delete"]


@pytest.mark.parametrize("cron", ["", "* * * *", "* * * * * *", "0 1 * * $", "every day"])
async def test_cron_must_have_five_fields(env, cron):
    await env.err(
        "POST",
        "/backup-policies",
        422,
        json={"name": "x", "cron_schedule": cron, "backup_type": "full", "retention_days": 7},
    )


async def test_policy_validation(env):
    base = {"name": "x", "cron_schedule": "0 1 * * *", "backup_type": "full"}
    await env.err("POST", "/backup-policies", 422, json={**base, "retention_days": 0})
    await env.err(
        "POST", "/backup-policies", 422, json={**base, "retention_days": 5, "backup_type": "differential"}
    )
    # WORM needs an explicit mode: compliance mode cannot be undone, so it is never chosen silently
    await env.err("POST", "/backup-policies", 422, json={**base, "retention_days": 5, "worm_enabled": True})
    ok = await env.ok(
        "POST",
        "/backup-policies",
        json={**base, "retention_days": 5, "worm_enabled": True, "worm_mode": "compliance"},
    )
    assert ok["worm_enabled"] and ok["worm_mode"] == "compliance"
    off = await env.ok(
        "POST", "/backup-policies", json={**base, "name": "y", "retention_days": 5, "worm_mode": "enterprise"}
    )
    assert not off["worm_enabled"] and off["worm_mode"] == "none"  # a mode without the switch is dropped
    await env.err("POST", "/backup-policies", 409, json={**base, "retention_days": 5})  # name x again


async def test_policy_patch_keeps_worm_consistent(env):
    p = await env.add_policy()
    await env.err("PATCH", f"/backup-policies/{p['id']}", 422, json={"worm_enabled": True})
    on = await env.ok(
        "PATCH", f"/backup-policies/{p['id']}", json={"worm_enabled": True, "worm_mode": "enterprise"}
    )
    assert on["worm_mode"] == "enterprise"
    off = await env.ok("PATCH", f"/backup-policies/{p['id']}", json={"worm_enabled": False})
    assert off["worm_mode"] == "none"
    await env.err("PATCH", f"/backup-policies/{p['id']}", 422, json={"cron_schedule": "nope"})


async def test_register_asset_on_the_appliance(env):
    t = await env.add_target()
    p = await env.add_policy()
    a = await env.add_asset(
        t, "esx-cluster-1", "vmware", source_ip="10.2.0.4", agent_version="1.6.1", policy_id=p["id"]
    )
    assert a["asset_type"] == "vmware" and a["policy_id"] == p["id"] and a["array_asset_id"] == "1"
    on_array = env.mock().objects["asset"]["1"]
    assert (
        on_array["NAME"] == "esx-cluster-1"
        and on_array["TYPE"] == "VMware"
        and on_array["SOURCEIP"] == "10.2.0.4"
    )
    for kind in ("database", "file_share", "lun"):
        await env.add_asset(t, f"asset-{kind}", kind)
    assert {x["name"] for x in await env.ok("GET", "/assets")} >= {"esx-cluster-1", "asset-lun"}
    assert len(await env.ok("GET", "/assets", params={"policy_id": p["id"]})) == 1
    assert len(await env.ok("GET", "/assets", params={"backup_target_id": t["id"]})) == 4


async def test_asset_registration_errors_leave_no_trace(env):
    t = await env.add_target()
    await env.add_asset(t, "dup")
    await env.err(
        "POST", "/assets", 409, json={"backup_target_id": t["id"], "name": "dup", "asset_type": "lun"}
    )
    zero = "00000000-0000-0000-0000-000000000000"
    await env.err("POST", "/assets", 404, json={"backup_target_id": zero, "name": "z", "asset_type": "lun"})
    await env.err(
        "POST",
        "/assets",
        404,
        json={"backup_target_id": t["id"], "name": "z", "asset_type": "lun", "policy_id": zero},
    )
    await env.err(
        "POST", "/assets", 422, json={"backup_target_id": t["id"], "name": "z", "asset_type": "tape"}
    )
    assert len(await env.ok("GET", "/assets")) == 1 and len(env.mock().objects["asset"]) == 1
    env.broken.add("op-1")
    await env.err(
        "POST", "/assets", 502, json={"backup_target_id": t["id"], "name": "late", "asset_type": "lun"}
    )
    assert len(await env.ok("GET", "/assets")) == 1
    assert (
        len(await env.audit(action="asset.create", outcome="failure")) == 2
    )  # 409 and 502; the 404s stop earlier


async def test_database_failure_removes_the_asset_from_the_appliance(env, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    t = await env.add_target()
    real, calls = AsyncSession.commit, {"n": 0}

    async def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is down")
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky)
    r = await env.http.post(
        "/api/v1/assets", json={"backup_target_id": t["id"], "name": "a", "asset_type": "lun"}
    )
    monkeypatch.undo()
    assert r.status_code == 500 and not env.mock().objects["asset"]
    assert await env.ok("GET", "/assets") == []


async def test_update_asset_policy(env):
    t = await env.add_target()
    p = await env.add_policy()
    a = await env.add_asset(t)
    set_ = await env.ok("PATCH", f"/assets/{a['id']}", json={"policy_id": p["id"], "agent_version": "2.0"})
    assert set_["policy_id"] == p["id"] and set_["agent_version"] == "2.0"
    kept = await env.ok("PATCH", f"/assets/{a['id']}", json={"source_ip": "10.9.9.9"})
    assert kept["policy_id"] == p["id"]  # left out = unchanged
    cleared = await env.ok("PATCH", f"/assets/{a['id']}", json={"policy_id": None})
    assert cleared["policy_id"] is None  # explicit null = removed
    await env.err(
        "PATCH", f"/assets/{a['id']}", 404, json={"policy_id": "00000000-0000-0000-0000-000000000000"}
    )


async def test_policy_in_use_cannot_be_deleted(env):
    t = await env.add_target()
    p = await env.add_policy()
    a = await env.add_asset(t, policy_id=p["id"])
    r = await env.err("DELETE", f"/backup-policies/{p['id']}", 409)
    assert "1 asset" in r["detail"]
    await env.ok("PATCH", f"/assets/{a['id']}", json={"policy_id": None})
    await env.ok("DELETE", f"/backup-policies/{p['id']}", status=204)


async def test_delete_asset(env):
    t = await env.add_target()
    a = await env.add_asset(t)
    await env.ok("DELETE", f"/assets/{a['id']}", status=204)
    assert not env.mock().objects["asset"] and await env.ok("GET", "/assets") == []
    b = await env.add_asset(t, "second")
    env.mock().objects["asset"].clear()  # the appliance already lost it
    await env.ok("DELETE", f"/assets/{b['id']}", status=204)


async def test_asset_with_a_running_backup_cannot_be_deleted(env):
    t = await env.add_target()
    a = await env.add_asset(t)
    await env.start(a)
    await env.err("DELETE", f"/assets/{a['id']}", 409)
    async with env.maker() as s:
        assert len((await s.scalars(select(ProtectedAsset))).all()) == 1
        assert len((await s.scalars(select(BackupPolicy))).all()) == 0
