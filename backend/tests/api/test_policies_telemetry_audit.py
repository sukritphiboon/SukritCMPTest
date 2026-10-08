

async def test_policy_crud(env):
    tenant = await env.tenant()
    p = await env.ok(
        "POST",
        "/protection-policies",
        json={
            "name": "gold",
            "retention_days": 30,
            "worm_mode": "enterprise",
            "tenant_id": tenant["id"],
            "snapshot_schedule": "0 */4 * * *",
        },
    )
    assert p["enabled"] and p["worm_mode"] == "enterprise"
    assert len(await env.ok("GET", "/protection-policies", params={"tenant_id": tenant["id"]})) == 1
    patched = await env.ok(
        "PATCH", f"/protection-policies/{p['id']}", json={"retention_days": 60, "worm_mode": "none"}
    )
    assert patched["retention_days"] == 60 and patched["worm_mode"] == "none"
    await env.err("POST", "/protection-policies", 422, json={"name": "bad", "retention_days": 0})
    await env.err(
        "POST",
        "/protection-policies",
        404,
        json={"name": "orphan", "retention_days": 1, "tenant_id": "00000000-0000-0000-0000-000000000000"},
    )
    await env.ok("DELETE", f"/protection-policies/{p['id']}", status=204)
    await env.err("GET", f"/protection-policies/{p['id']}", 404)


async def test_deleting_a_policy_keeps_the_volume(env):
    dev, tenant = await env.device(), await env.tenant()
    p = await env.ok("POST", "/protection-policies", json={"name": "p", "retention_days": 7})
    vol = await env.ok(
        "POST",
        "/volumes",
        json={
            "name": "v",
            "size_gb": 1,
            "storage_device_id": dev["id"],
            "tenant_id": tenant["id"],
            "protection_policy_id": p["id"],
        },
    )
    await env.ok("DELETE", f"/protection-policies/{p['id']}", status=204)
    assert (await env.ok("GET", f"/volumes/{vol['id']}"))["protection_policy_id"] is None


async def test_dorado_telemetry(env):
    dev = await env.device()
    cap = await env.ok("GET", f"/devices/{dev['id']}/capacity")
    assert cap["total_gb"] > 100_000 and 0 <= cap["used_percent"] < 1
    ratio = await env.ok("GET", f"/devices/{dev['id']}/reduction-ratio")
    assert ratio["dedupe"] > 1 and ratio["compression"] > 1
    alarms = await env.ok("GET", f"/devices/{dev['id']}/alarms")
    assert alarms[0]["severity"] == "medium"
    perf = await env.ok("GET", f"/devices/{dev['id']}/performance")
    assert perf["iops"] >= 300_000 and perf["latency_ms"] < 1


async def test_oceanprotect_telemetry(env):
    dev = await env.device("oceanprotect")
    ratio = await env.ok("GET", f"/devices/{dev['id']}/reduction-ratio")
    assert 20 <= ratio["overall"] <= 42
    perf = await env.ok("GET", f"/devices/{dev['id']}/performance")
    assert perf["throughput_mbps"] >= 6000


async def test_telemetry_errors(env):
    await env.err("GET", "/devices/00000000-0000-0000-0000-000000000000/capacity", 404)
    dev = await env.device()
    await env.ok("PATCH", f"/devices/{dev['id']}", json={"password": "wrong"})
    await env.err("GET", f"/devices/{dev['id']}/capacity", 502)


async def test_audit_filters_and_pagination(env):
    dev, tenant = await env.device(), await env.tenant()
    for i in range(3):
        await env.ok(
            "POST",
            "/volumes",
            json={"name": f"v{i}", "size_gb": 1, "storage_device_id": dev["id"], "tenant_id": tenant["id"]},
        )
    page = await env.ok("GET", "/audit-logs", params={"action": "volume.create", "limit": 2})
    assert page["total"] == 3 and len(page["items"]) == 2
    rest = await env.ok("GET", "/audit-logs", params={"action": "volume.create", "limit": 2, "offset": 2})
    assert len(rest["items"]) == 1
    assert (await env.ok("GET", "/audit-logs", params={"actor": "nobody"}))["total"] == 0
    assert (await env.ok("GET", "/audit-logs", params={"storage_device_id": dev["id"]}))["total"] == 3
    assert (await env.ok("GET", "/audit-logs", params={"since": "2999-01-01T00:00:00Z"}))["total"] == 0
    names = {a["resource_name"] for a in page["items"] + rest["items"]}
    assert names == {"v0", "v1", "v2"}
    await env.err("GET", "/audit-logs", 422, params={"limit": 0})


async def test_actor_comes_from_the_api_key(env):
    r = await env.http.post("/api/v1/tenants", json={"name": "x"}, headers={"X-API-Key": "other-key"})
    assert r.status_code == 201
    log = await env.audit(action="tenant.create")
    assert log[0]["actor"] == "auditor"


async def test_database_failure_removes_the_new_lun(env, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    dev, tenant = await env.device(), await env.tenant()
    real_commit, calls = AsyncSession.commit, {"n": 0}

    async def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is down")
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    r = await env.http.post(
        "/api/v1/volumes",
        json={"name": "v", "size_gb": 5, "storage_device_id": dev["id"], "tenant_id": tenant["id"]},
    )
    monkeypatch.undo()
    assert r.status_code == 500
    assert not env.dorado_mock.objects["lun"]  # compensated on the array
    assert await env.ok("GET", "/volumes") == []
    failed = await env.audit(action="volume.create", outcome="failure")
    assert len(failed) == 1 and "database is down" in failed[0]["details"]["error"]
