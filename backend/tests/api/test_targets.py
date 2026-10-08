from sqlalchemy import select

from app.models import BackupTarget
from tests.conftest import MOCK_PASSWORD


async def test_authentication(env):
    assert (await env.http.get("/api/v1/targets", headers={"X-API-Key": ""})).status_code == 401
    assert (await env.http.get("/api/v1/targets", headers={"X-API-Key": "wrong"})).status_code == 401
    assert (
        await env.http.get("/api/v1/oceanprotect/overview", headers={"X-API-Key": "wrong"})
    ).status_code == 401
    assert (await env.http.get("/health")).status_code == 200
    assert (await env.http.get("/api/v1/targets", headers={"X-API-Key": "other-key"})).status_code == 200


async def test_register_target_hides_and_encrypts_credentials(env):
    t = await env.add_target(model="x6000")
    assert t["health_status"] == "unknown" and t["management_port"] == 8088 and t["model"] == "x6000"
    assert "password" not in t and "credentials_encrypted" not in t
    async with env.maker() as s:
        stored = (await s.scalars(select(BackupTarget))).one()
        assert MOCK_PASSWORD not in stored.credentials_encrypted
    assert [x["name"] for x in await env.ok("GET", "/targets")] == ["op-1"]
    assert (await env.ok("GET", f"/targets/{t['id']}"))["id"] == t["id"]


async def test_update_and_delete(env):
    t = await env.add_target()
    patched = await env.ok("PATCH", f"/targets/{t['id']}", json={"name": "renamed", "management_port": 9000})
    assert patched["name"] == "renamed" and patched["management_port"] == 9000
    await env.ok("DELETE", f"/targets/{t['id']}", status=204)
    await env.err("GET", f"/targets/{t['id']}", 404)


async def test_validation_and_conflicts(env):
    await env.add_target()
    await env.err(
        "POST",
        "/targets",
        409,
        json={"name": "op-1", "ip_address": "x", "username": "a", "password": "b", "model": "x8000"},
    )
    await env.err(
        "POST",
        "/targets",
        422,
        json={"name": "n", "ip_address": "x", "username": "a", "password": "b", "model": "dorado_v7"},
    )
    await env.err("GET", "/targets/not-a-uuid", 422)
    await env.err("GET", "/targets/00000000-0000-0000-0000-000000000000", 404)


async def test_connection_test_fills_in_identity(env):
    t = await env.add_target()
    result = await env.ok("POST", f"/targets/{t['id']}/test")
    assert result["reachable"] and result["serial_number"] == env.mock().serial_number
    shown = await env.ok("GET", f"/targets/{t['id']}")
    assert shown["device_id"] == env.mock().device_id and shown["health_status"] == "healthy"
    assert shown["serial_number"] == env.mock().serial_number and shown["last_seen_at"]


async def test_connection_test_failure_is_reported_not_raised(env):
    t = await env.add_target()
    await env.ok("PATCH", f"/targets/{t['id']}", json={"password": "wrong"})
    result = await env.ok("POST", f"/targets/{t['id']}/test")
    assert not result["reachable"] and result["health_status"] == "fault" and result["error"]
    assert len(await env.audit(action="target.test", outcome="failure")) == 1
    await env.ok("PATCH", f"/targets/{t['id']}", json={"password": MOCK_PASSWORD})  # username kept
    assert (await env.ok("POST", f"/targets/{t['id']}/test"))["reachable"]


async def test_changes_are_audited_without_secrets(env):
    t = await env.add_target()
    await env.ok("PATCH", f"/targets/{t['id']}", json={"password": "new-secret-pw"})
    log = await env.audit(resource_type="target")
    assert [a["action"] for a in reversed(log)] == ["target.create", "target.update"]
    assert "new-secret-pw" not in str(log) and MOCK_PASSWORD not in str(log)
    assert log[0]["details"]["fields"] == ["password"] and log[0]["actor"] == "tester"


async def test_audit_filters_and_paging(env):
    for i in range(3):
        await env.add_target(f"op-{i}", seed=i)
    page = await env.ok("GET", "/audit-logs", params={"action": "target.create", "limit": 2})
    assert page["total"] == 3 and len(page["items"]) == 2
    rest = await env.ok("GET", "/audit-logs", params={"action": "target.create", "limit": 2, "offset": 2})
    assert len(rest["items"]) == 1
    assert (await env.ok("GET", "/audit-logs", params={"actor": "nobody"}))["total"] == 0
    assert (await env.ok("GET", "/audit-logs", params={"since": "2999-01-01T00:00:00Z"}))["total"] == 0
    await env.err("GET", "/audit-logs", 422, params={"limit": 0})
    r = await env.http.post(
        "/api/v1/targets",
        headers={"X-API-Key": "other-key"},
        json={"name": "by-auditor", "ip_address": "x", "username": "a", "password": "b", "model": "x3000"},
    )
    assert r.status_code == 201
    assert (await env.audit(actor="auditor"))[0]["resource_name"] == "by-auditor"
