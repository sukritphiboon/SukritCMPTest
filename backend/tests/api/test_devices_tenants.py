from sqlalchemy import select

from app.models import StorageDevice
from tests.conftest import MOCK_PASSWORD


async def test_authentication(env):
    anon = env.http.build_request("GET", "/api/v1/devices", headers={"X-API-Key": ""})
    assert (await env.http.send(anon)).status_code == 401
    r = await env.http.get("/api/v1/devices", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    assert (await env.http.get("/health")).status_code == 200
    r = await env.http.get("/api/v1/devices", headers={"X-API-Key": "other-key"})
    assert r.status_code == 200


async def test_device_lifecycle_and_secrecy(env):
    dev = await env.device(name="dorado-01")
    assert dev["health_status"] == "unknown" and dev["management_port"] == 8088
    assert "password" not in dev and "credentials_encrypted" not in dev
    async with env.maker() as s:
        stored = (await s.scalars(select(StorageDevice))).one()
        assert MOCK_PASSWORD not in stored.credentials_encrypted
    assert [d["name"] for d in await env.ok("GET", "/devices")] == ["dorado-01"]
    assert (await env.ok("GET", f"/devices/{dev['id']}"))["id"] == dev["id"]

    patched = await env.ok(
        "PATCH", f"/devices/{dev['id']}", json={"name": "renamed", "management_port": 9000}
    )
    assert patched["name"] == "renamed" and patched["management_port"] == 9000

    await env.ok("DELETE", f"/devices/{dev['id']}", status=204)
    await env.err("GET", f"/devices/{dev['id']}", 404)


async def test_duplicate_device_name_conflicts(env):
    await env.device(name="same")
    await env.err(
        "POST",
        "/devices",
        409,
        json={"name": "same", "ip_address": "x", "username": "a", "password": "b", "model": "oceanprotect"},
    )


async def test_device_test_updates_health(env):
    dev = await env.device()
    result = await env.ok("POST", f"/devices/{dev['id']}/test", status=200)
    assert result["reachable"] and result["device_id"] == env.dorado_mock.device_id
    assert result["health_status"] == "healthy" and result["active_alarms"] == 1
    env.dorado_mock.add_alarm("Critical", "Disk failed", "0xF00CF0002")
    result = await env.ok("POST", f"/devices/{dev['id']}/test")
    assert result["health_status"] == "degraded"
    shown = await env.ok("GET", f"/devices/{dev['id']}")
    assert shown["device_id"] == env.dorado_mock.device_id and shown["last_seen_at"]


async def test_device_test_with_wrong_password_reports_fault(env):
    dev = await env.device()
    await env.ok("PATCH", f"/devices/{dev['id']}", json={"password": "wrong"})
    result = await env.ok("POST", f"/devices/{dev['id']}/test")
    assert not result["reachable"] and result["health_status"] == "fault" and result["error"]
    assert (await env.ok("GET", f"/devices/{dev['id']}"))["health_status"] == "fault"
    failures = await env.audit(action="device.test", outcome="failure")
    assert len(failures) == 1
    # fixing the password through PATCH keeps the stored username
    await env.ok("PATCH", f"/devices/{dev['id']}", json={"password": MOCK_PASSWORD})
    assert (await env.ok("POST", f"/devices/{dev['id']}/test"))["reachable"]


async def test_cannot_delete_device_with_resources(env):
    dev, tenant = await env.device(), await env.tenant()
    await env.ok(
        "POST",
        "/volumes",
        json={"name": "v", "size_gb": 10, "storage_device_id": dev["id"], "tenant_id": tenant["id"]},
    )
    await env.err("DELETE", f"/devices/{dev['id']}", 409)


async def test_tenant_crud_and_usage(env):
    t = await env.tenant(block=100, file=50, obj=10)
    assert t["is_active"] and t["quota_block_gb"] == 100
    usage = await env.ok("GET", f"/tenants/{t['id']}/usage")
    assert usage["block"] == {"allocated_gb": 100, "used_gb": 0, "free_gb": 100}
    patched = await env.ok("PATCH", f"/tenants/{t['id']}", json={"quota_block_gb": 200, "description": "d"})
    assert patched["quota_block_gb"] == 200 and patched["quota_file_gb"] == 50
    await env.err("POST", "/tenants", 409, json={"name": "acme"})
    await env.err("POST", "/tenants", 422, json={"name": "neg", "quota_block_gb": -1})
    await env.ok("DELETE", f"/tenants/{t['id']}", status=204)


async def test_cannot_delete_tenant_with_resources(env):
    dev, tenant = await env.device(), await env.tenant()
    await env.ok(
        "POST",
        "/volumes",
        json={"name": "v", "size_gb": 10, "storage_device_id": dev["id"], "tenant_id": tenant["id"]},
    )
    await env.err("DELETE", f"/tenants/{tenant['id']}", 409)


async def test_not_found_and_validation(env):
    zero = "00000000-0000-0000-0000-000000000000"
    await env.err("GET", f"/devices/{zero}", 404)
    await env.err("GET", "/devices/not-a-uuid", 422)
    await env.err(
        "POST",
        "/volumes",
        404,
        json={"name": "v", "size_gb": 1, "storage_device_id": zero, "tenant_id": zero},
    )
    await env.err("POST", "/volumes", 422, json={"name": "v", "size_gb": 0})
