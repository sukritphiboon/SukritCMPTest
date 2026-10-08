from sqlalchemy import select

from app.models import AlarmRecord


async def setup_alarms(env):
    """Two appliances: seeded major + warning each, plus a critical on the first and two extra warnings."""
    a = await env.add_target("a", seed=1)
    b = await env.add_target("b", seed=2)
    env.mock("a").add_alarm("Critical", "Controller 0B offline", "0xF00CF0300")
    for i in range(2):
        env.mock("b").add_alarm("Warning", f"Fan speed high {i}", f"0xF00CF05{i}")
    await env.collect()
    return a, b


async def test_alarms_are_listed_most_severe_first(env):
    await setup_alarms(env)
    page = await env.ok("GET", "/oceanprotect/alarms")
    assert page["total"] == 7
    assert page["counts"] == {"critical": 1, "major": 2, "warning": 4, "total": 7, "unacknowledged": 7}
    sev = [a["severity"] for a in page["items"]]
    assert sev == ["critical", "major", "major", "warning", "warning", "warning", "warning"]
    first = page["items"][0]
    assert first["target_name"] == "a" and first["active"] and not first["acknowledged"]
    assert first["raised_at"].endswith("Z") or "+" in first["raised_at"]


async def test_filters_and_paging(env):
    a, _ = await setup_alarms(env)
    assert (await env.ok("GET", "/oceanprotect/alarms", params={"severity": "critical"}))["total"] == 1
    assert (await env.ok("GET", "/oceanprotect/alarms", params={"severity": "warning"}))["counts"][
        "warning"
    ] == 4
    only_a = await env.ok("GET", "/oceanprotect/alarms", params={"target_id": a["id"]})
    assert only_a["total"] == 3 and {i["target_name"] for i in only_a["items"]} == {"a"}
    page1 = await env.ok("GET", "/oceanprotect/alarms", params={"limit": 3})
    page2 = await env.ok("GET", "/oceanprotect/alarms", params={"limit": 3, "offset": 3})
    page3 = await env.ok("GET", "/oceanprotect/alarms", params={"limit": 3, "offset": 6})
    ids = [i["id"] for p in (page1, page2, page3) for i in p["items"]]
    assert len(ids) == 7 and len(set(ids)) == 7
    assert page1["counts"]["total"] == 7  # counts describe the whole set, not the page
    await env.err("GET", "/oceanprotect/alarms", 422, params={"severity": "minor"})
    await env.err("GET", "/oceanprotect/alarms", 422, params={"limit": 501})


async def test_acknowledge_records_who_when_and_why(env):
    await setup_alarms(env)
    alarm = (await env.ok("GET", "/oceanprotect/alarms", params={"severity": "critical"}))["items"][0]
    env.tick(120)
    ack = await env.ok(
        "POST", f"/oceanprotect/alarms/{alarm['id']}/acknowledge", json={"note": "Engineer called"}
    )
    assert ack["acknowledged"] and ack["acknowledged_by"] == "tester" and ack["ack_note"] == "Engineer called"
    assert ack["acknowledged_at"].startswith("2026-10-01T12:02:00")
    page = await env.ok("GET", "/oceanprotect/alarms")
    assert page["counts"]["unacknowledged"] == 6 and page["total"] == 7  # still listed, now marked
    assert [
        a["id"]
        for a in (await env.ok("GET", "/oceanprotect/alarms", params={"acknowledged": False}))["items"]
    ].count(alarm["id"]) == 0
    assert (await env.ok("GET", "/oceanprotect/alarms", params={"acknowledged": True}))["total"] == 1
    ov = await env.ok("GET", "/oceanprotect/overview")
    assert ov["alarms"]["unacknowledged"] == 6 and ov["alarms"]["total"] == 7  # counts stay, they are active


async def test_acknowledge_is_written_to_the_audit_log(env):
    await setup_alarms(env)
    alarm = (await env.ok("GET", "/oceanprotect/alarms", params={"severity": "critical"}))["items"][0]
    await env.ok("POST", f"/oceanprotect/alarms/{alarm['id']}/acknowledge", json={"note": "seen"})
    [entry] = await env.audit(action="alarm.acknowledge")
    assert entry["actor"] == "tester" and entry["resource_id"] == alarm["id"]
    assert entry["resource_name"] == "Controller 0B offline" and entry["outcome"] == "success"
    assert entry["details"]["severity"] == "critical" and entry["details"]["note"] == "seen"
    assert entry["backup_target_id"] == alarm["backup_target_id"]


async def test_acknowledging_twice_keeps_the_first_acknowledgement(env):
    await setup_alarms(env)
    alarm = (await env.ok("GET", "/oceanprotect/alarms"))["items"][0]
    first = await env.ok("POST", f"/oceanprotect/alarms/{alarm['id']}/acknowledge", json={"note": "first"})
    env.tick(60)
    r = await env.http.post(
        f"/api/v1/oceanprotect/alarms/{alarm['id']}/acknowledge",
        headers={"X-API-Key": "other-key"},
        json={"note": "second"},
    )
    assert r.status_code == 200
    again = r.json()
    assert again["acknowledged_by"] == "tester" and again["ack_note"] == "first"
    assert again["acknowledged_at"] == first["acknowledged_at"]
    assert (
        len(await env.audit(action="alarm.acknowledge")) == 1
    )  # the repeat changed nothing, so no new entry


async def test_acknowledge_without_a_body_and_unknown_alarm(env):
    await setup_alarms(env)
    alarm = (await env.ok("GET", "/oceanprotect/alarms"))["items"][0]
    ack = await env.ok("POST", f"/oceanprotect/alarms/{alarm['id']}/acknowledge")
    assert ack["acknowledged"] and ack["ack_note"] is None
    await env.err("POST", "/oceanprotect/alarms/00000000-0000-0000-0000-000000000000/acknowledge", 404)
    await env.err("POST", "/oceanprotect/alarms/nope/acknowledge", 422)
    await env.err("POST", f"/oceanprotect/alarms/{alarm['id']}/acknowledge", 422, json={"note": "x" * 1001})
    r = await env.http.post(
        f"/api/v1/oceanprotect/alarms/{alarm['id']}/acknowledge", headers={"X-API-Key": "bad"}
    )
    assert r.status_code == 401


async def test_cleared_alarms_move_out_of_the_active_list(env):
    a, _ = await setup_alarms(env)
    env.mock("a").clear_alarm("3")  # the critical one
    env.tick(30)
    await env.collect()
    active = await env.ok("GET", "/oceanprotect/alarms")
    assert active["total"] == 6 and active["counts"]["critical"] == 0
    cleared = await env.ok("GET", "/oceanprotect/alarms", params={"state": "cleared"})
    assert cleared["total"] == 1 and not cleared["items"][0]["active"] and cleared["items"][0]["cleared_at"]
    assert (await env.ok("GET", "/oceanprotect/alarms", params={"state": "all"}))["total"] == 7
    assert (await env.ok("GET", "/oceanprotect/overview"))["alarms"]["critical"] == 0
    async with env.maker() as s:
        assert len((await s.scalars(select(AlarmRecord))).all()) == 7  # history is kept
    assert a["id"]


async def test_alarm_list_is_empty_without_data(env):
    await env.add_target()
    page = await env.ok("GET", "/oceanprotect/alarms")
    assert page == {
        "total": 0,
        "counts": {"critical": 0, "major": 0, "warning": 0, "total": 0, "unacknowledged": 0},
        "items": [],
    }
