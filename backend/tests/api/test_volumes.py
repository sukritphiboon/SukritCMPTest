import pytest


@pytest.fixture
async def ctx(env):
    return env, await env.device(), await env.tenant(block=100)


def body(ctx, name="lun1", size=10, **extra):
    env, dev, tenant = ctx
    return {"name": name, "size_gb": size, "storage_device_id": dev["id"], "tenant_id": tenant["id"], **extra}


async def test_volume_lifecycle_is_audited(ctx):
    env, dev, tenant = ctx
    vol = await env.ok("POST", "/volumes", json=body(ctx))
    assert vol["provisioning"] == "thin" and vol["mapping_status"] == "unmapped"
    assert len(vol["wwn"]) == 32 and vol["array_lun_id"]
    assert len(env.dorado_mock.objects["lun"]) == 1

    expanded = await env.ok("POST", f"/volumes/{vol['id']}/expand", json={"new_size_gb": 40})
    assert expanded["size_gb"] == 40
    assert int(env.dorado_mock.objects["lun"][vol["array_lun_id"]]["CAPACITY"]) == 40 * 2 * 1024 * 1024

    mapped = await env.ok("POST", f"/volumes/{vol['id']}/map", json={"host_name": "esx-01"})
    assert mapped["mapping_status"] == "mapped"
    snap = await env.ok("POST", f"/volumes/{vol['id']}/snapshots", json={"name": "s1"})
    assert snap["resource_type"] == "lun"

    usage = await env.ok("GET", f"/tenants/{tenant['id']}/usage")
    assert usage["block"]["used_gb"] == 40

    await env.err("DELETE", f"/volumes/{vol['id']}", 409)  # still mapped on the array
    assert (await env.ok("GET", f"/volumes/{vol['id']}"))["id"] == vol["id"]

    log = await env.audit(resource_type="volume")
    ok_actions = [a["action"] for a in reversed(log) if a["outcome"] == "success"]
    assert ok_actions == ["volume.create", "volume.expand", "volume.map", "volume.snapshot"]
    assert all(a["actor"] == "tester" for a in log)
    failed = [a for a in log if a["outcome"] == "failure"]
    assert [a["action"] for a in failed] == ["volume.delete"] and "mapped" in failed[0]["details"]["error"]
    assert log[-1]["storage_device_id"] == dev["id"] and log[-1]["tenant_id"] == tenant["id"]


async def test_delete_unmapped_volume(ctx):
    env, *_ = ctx
    vol = await env.ok("POST", "/volumes", json=body(ctx, size=10, thin=False))
    assert vol["provisioning"] == "thick"
    await env.ok("DELETE", f"/volumes/{vol['id']}", status=204)
    assert not env.dorado_mock.objects["lun"]
    assert await env.ok("GET", "/volumes") == []


async def test_delete_when_already_gone_on_array_cleans_database(ctx):
    env, *_ = ctx
    vol = await env.ok("POST", "/volumes", json=body(ctx))
    env.dorado_mock.objects["lun"].clear()
    await env.ok("DELETE", f"/volumes/{vol['id']}", status=204)
    assert await env.ok("GET", "/volumes") == []


async def test_quota_exceeded_leaves_nothing_on_the_array(ctx):
    env, _, tenant = ctx
    r = await env.err("POST", "/volumes", 409, json=body(ctx, size=101))
    assert "block quota exceeded" in r["detail"]
    assert not env.dorado_mock.objects["lun"]
    failed = await env.audit(action="volume.create", outcome="failure")
    assert len(failed) == 1 and failed[0]["details"]["size_gb"] == 101

    await env.ok("POST", "/volumes", json=body(ctx, "a", 60))
    await env.err("POST", "/volumes", 409, json=body(ctx, "b", 41))
    await env.ok("POST", "/volumes", json=body(ctx, "c", 40))  # exactly fills the quota


async def test_inactive_tenant_cannot_provision(ctx):
    env, _, tenant = ctx
    await env.ok("PATCH", f"/tenants/{tenant['id']}", json={"is_active": False})
    r = await env.err("POST", "/volumes", 409, json=body(ctx))
    assert "inactive" in r["detail"]


async def test_expand_rules(ctx):
    env, *_ = ctx
    vol = await env.ok("POST", "/volumes", json=body(ctx, size=50))
    await env.err("POST", f"/volumes/{vol['id']}/expand", 422, json={"new_size_gb": 50})
    await env.err("POST", f"/volumes/{vol['id']}/expand", 409, json={"new_size_gb": 101})
    assert (await env.ok("GET", f"/volumes/{vol['id']}"))["size_gb"] == 50


async def test_duplicate_name_on_array_conflicts(ctx):
    env, *_ = ctx
    await env.ok("POST", "/volumes", json=body(ctx))
    r = await env.err("POST", "/volumes", 409, json=body(ctx))
    assert r["device_error_code"] == 1077948993
    assert len(await env.ok("GET", "/volumes")) == 1


async def test_second_map_conflicts(ctx):
    env, *_ = ctx
    vol = await env.ok("POST", "/volumes", json=body(ctx))
    await env.ok("POST", f"/volumes/{vol['id']}/map", json={"host_name": "h1"})
    await env.err("POST", f"/volumes/{vol['id']}/map", 409, json={"host_name": "h2"})


async def test_oceanprotect_cannot_host_luns(env):
    dev, tenant = await env.device("oceanprotect"), await env.tenant()
    r = await env.err(
        "POST",
        "/volumes",
        422,
        json={"name": "v", "size_gb": 1, "storage_device_id": dev["id"], "tenant_id": tenant["id"]},
    )
    assert "OceanProtect" in r["detail"]


async def test_unreachable_array_gives_502(ctx):
    env, dev, _ = ctx
    await env.ok("PATCH", f"/devices/{dev['id']}", json={"password": "wrong"})
    await env.err("POST", "/volumes", 502, json=body(ctx))


async def test_listing_filters_and_pagination(ctx):
    env, dev, tenant = ctx
    for i in range(3):
        await env.ok("POST", "/volumes", json=body(ctx, f"v{i}", 5))
    other = await env.tenant("other", block=100)
    await env.ok(
        "POST",
        "/volumes",
        json={"name": "o", "size_gb": 5, "storage_device_id": dev["id"], "tenant_id": other["id"]},
    )
    assert len(await env.ok("GET", "/volumes")) == 4
    assert len(await env.ok("GET", "/volumes", params={"tenant_id": tenant["id"]})) == 3
    assert len(await env.ok("GET", "/volumes", params={"limit": 2, "offset": 3})) == 1


async def test_volume_with_policy_reference(ctx):
    env, _, tenant = ctx
    policy = await env.ok("POST", "/protection-policies", json={"name": "p", "retention_days": 7})
    vol = await env.ok("POST", "/volumes", json=body(ctx, protection_policy_id=policy["id"]))
    assert vol["protection_policy_id"] == policy["id"]
    await env.err(
        "POST",
        "/volumes",
        404,
        json=body(ctx, "x", protection_policy_id="00000000-0000-0000-0000-000000000000"),
    )
