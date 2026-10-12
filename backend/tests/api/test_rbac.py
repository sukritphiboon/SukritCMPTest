import pytest

from app.core.security import Role, parse_api_keys, required_role

VIEW, OP, ADMIN = {"X-API-Key": "view-key"}, {"X-API-Key": "op-key"}, {"X-API-Key": "test-key"}
FAKE = "00000000-0000-0000-0000-000000000000"


def test_parse_keys_roles_and_legacy():
    keys = parse_api_keys("a:viewer:k1,b:operator:k:with:colons,c:admin:k3,d:k4")
    assert keys["k1"].role is Role.VIEWER
    assert keys["k:with:colons"].role is Role.OPERATOR and keys["k:with:colons"].user == "b"
    assert keys["k3"].role is Role.ADMIN
    assert keys["k4"].user == "d" and keys["k4"].role is Role.ADMIN  # legacy form keeps admin


def test_required_role_table():
    assert required_role("GET", "/targets") is Role.VIEWER
    assert required_role("GET", "/audit-logs") is Role.ADMIN
    assert required_role("POST", "/backup-jobs") is Role.OPERATOR
    assert required_role("POST", "/targets") is Role.ADMIN
    assert required_role("DELETE", "/backup-policies/{policy_id}") is Role.ADMIN


@pytest.mark.parametrize(
    ("method", "url", "headers", "status"),
    [
        ("GET", "/targets", VIEW, 200),
        ("GET", "/audit-logs", VIEW, 403),
        ("GET", "/audit-logs", OP, 403),
        ("GET", "/audit-logs", ADMIN, 200),
        ("POST", "/targets", VIEW, 403),
        ("POST", "/targets", OP, 403),
        ("DELETE", f"/targets/{FAKE}", OP, 403),
        ("POST", "/backup-jobs", VIEW, 403),
        ("POST", f"/backup-jobs/{FAKE}/cancel", VIEW, 403),
        ("POST", f"/backup-jobs/{FAKE}/cancel", OP, 404),  # allowed, then the job is not found
        ("POST", f"/oceanprotect/alarms/{FAKE}/acknowledge", VIEW, 403),
        ("POST", f"/oceanprotect/alarms/{FAKE}/acknowledge", OP, 404),
        ("POST", f"/backup-policies/{FAKE}/run", OP, 404),
        ("PATCH", f"/backup-policies/{FAKE}", OP, 403),
        ("GET", "/targets", {"X-API-Key": "nope"}, 401),
        ("GET", "/targets", {"X-API-Key": ""}, 401),
    ],
)
async def test_role_matrix(env, method, url, headers, status):
    r = await env.http.request(
        method, f"/api/v1{url}", headers=headers, json={} if method in ("POST", "PATCH") else None
    )
    assert r.status_code == status, r.text


async def test_admin_actor_still_recorded_in_audit(env):
    await env.add_target()
    logs = await env.ok("GET", "/audit-logs", headers=ADMIN)
    assert any(item["actor"] == "tester" for item in logs["items"])


def test_operator_actions_name_real_routes():
    """A typo in the operator table would silently demote an action to admin-only."""
    from app.api.routers import assets, audit, backup_jobs, backup_policies, oceanprotect, targets
    from app.core.security import ADMIN_READS, OPERATOR_ACTIONS

    mods = (assets, audit, backup_jobs, backup_policies, oceanprotect, targets)
    real = {(m, r.path) for mod in mods for r in mod.router.routes for m in r.methods}
    assert (OPERATOR_ACTIONS | ADMIN_READS) <= real
