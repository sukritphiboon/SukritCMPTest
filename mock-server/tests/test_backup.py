import pytest


async def asset(c, name="vm-prod-01", kind="VMware"):
    return await c.ok("POST", "/asset", {"NAME": name, "TYPE": kind, "SOURCEIP": "10.1.1.5"})


async def test_policy_crud(op):
    p = await op.ok(
        "POST",
        "/backup_policy",
        {
            "NAME": "daily-full",
            "CRON": "0 1 * * *",
            "BACKUPTYPE": 1,
            "RETENTIONDAYS": 30,
            "WORMENABLED": True,
        },
    )
    assert p["BACKUPTYPENAME"] == "full" and p["WORMENABLED"] is True
    updated = await op.ok("PUT", f"/backup_policy/{p['ID']}", {"BACKUPTYPE": 2, "RETENTIONDAYS": 14})
    assert updated["BACKUPTYPENAME"] == "incremental" and updated["RETENTIONDAYS"] == 14
    assert [x["NAME"] for x in await op.ok("GET", "/backup_policy")] == ["daily-full"]
    await op.ok("DELETE", f"/backup_policy/{p['ID']}")
    assert (await op.call("GET", "/backup_policy"))["error"]["code"] == 0


async def test_policy_validation(op):
    bad = await op.call("POST", "/backup_policy", {"NAME": "x", "CRON": "* * * * *", "RETENTIONDAYS": 0})
    assert bad["error"]["code"] == 50331651
    await op.ok("POST", "/backup_policy", {"NAME": "dup", "CRON": "* * * * *", "RETENTIONDAYS": 1})
    dup = await op.call("POST", "/backup_policy", {"NAME": "dup", "CRON": "* * * * *", "RETENTIONDAYS": 1})
    assert dup["error"]["code"] == 1077948993


async def test_asset_types(op):
    assert (await asset(op))["TYPE"] == "VMware"
    for i, kind in enumerate(("Database", "FileShare", "LUN")):
        await asset(op, f"a{i}", kind)
    assert (await op.call("POST", "/asset", {"NAME": "z", "TYPE": "Floppy"}))["error"]["code"] == 50331651
    assert len(await op.ok("GET", "/asset")) == 4


async def test_backup_is_asynchronous_and_walks_the_lifecycle(op):
    a = await asset(op)
    started = await op.ok(
        "POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 300, "SIMULATE_OUTCOME": "SUCCESS"}
    )
    tid = started["taskId"]
    assert tid.startswith("T")

    assert (await op.ok("GET", f"/task_list/{tid}"))["STATUS"] == "PENDING"
    op.mock.advance_time(5)
    running = await op.ok("GET", f"/task_list/{tid}")
    assert running["STATUS"] == "RUNNING" and 0 <= running["PROGRESS"] < 100
    assert running["ENDTIME"] == 0

    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    done = await op.ok("GET", f"/task_list/{tid}")
    assert done["STATUS"] == "SUCCESS" and done["PROGRESS"] == 100
    assert done["DATATRANSFERREDBYTES"] == 300 * 1024**3
    assert done["THROUGHPUTMBPS"] > 0 and done["ENDTIME"] > done["STARTTIME"] > 0
    assert any("completed" in line for line in done["LOGS"])


@pytest.mark.parametrize(
    "outcome,progress,fraction",
    [("FAILED", 37, 0.37), ("PARTIALLY_SUCCESSFUL", 100, 0.8), ("SUCCESS", 100, 1.0)],
)
async def test_final_outcomes(op, outcome, progress, fraction):
    a = await asset(op)
    tid = (
        await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 100, "SIMULATE_OUTCOME": outcome})
    )["taskId"]
    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    done = await op.ok("GET", f"/task_list/{tid}")
    assert done["STATUS"] == outcome and done["PROGRESS"] == progress
    assert done["DATATRANSFERREDBYTES"] == pytest.approx(100 * 1024**3 * fraction, rel=1e-3)


async def test_cancel_running_task(op):
    a = await asset(op)
    tid = (
        await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 100, "SIMULATE_OUTCOME": "SUCCESS"})
    )["taskId"]
    op.mock.advance_time(10)
    cancelled = await op.ok("PUT", f"/task_list/{tid}/cancel")
    assert cancelled["STATUS"] == "CANCELLED" and cancelled["ENDTIME"] > 0
    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    assert (await op.ok("GET", f"/task_list/{tid}"))["STATUS"] == "CANCELLED"  # stays cancelled
    assert (await op.call("PUT", f"/task_list/{tid}/cancel"))["error"]["code"] == 1077948995


async def test_cannot_cancel_a_finished_task(op):
    a = await asset(op)
    tid = (
        await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 10, "SIMULATE_OUTCOME": "SUCCESS"})
    )["taskId"]
    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    assert (await op.call("PUT", f"/task_list/{tid}/cancel"))["error"]["code"] == 1077948995


async def test_job_uses_policy_backup_type_and_lists(op):
    a = await asset(op)
    p = await op.ok(
        "POST", "/backup_policy", {"NAME": "inc", "CRON": "0 * * * *", "BACKUPTYPE": 2, "RETENTIONDAYS": 7}
    )
    started = await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "POLICYID": p["ID"]})
    jobs = await op.ok("GET", "/backup_job")
    assert jobs[0]["BACKUPTYPENAME"] == "incremental" and jobs[0]["taskId"] == started["taskId"]
    assert len(await op.ok("GET", "/task_list")) == 1
    assert (await op.call("DELETE", f"/backup_policy/{p['ID']}"))["error"]["code"] == 1077948995  # in use


async def test_job_validation_errors(op):
    assert (await op.call("POST", "/backup_job", {}))["error"]["code"] == 50331651
    assert (await op.call("POST", "/backup_job", {"ASSETID": "99"}))["error"]["code"] == 1077948996
    a = await asset(op)
    bad = await op.call("POST", "/backup_job", {"ASSETID": a["ID"], "SIMULATE_OUTCOME": "MAYBE"})
    assert bad["error"]["code"] == 50331651
    assert (await op.call("GET", "/task_list/T999999"))["error"]["code"] == 1077948996


async def test_backup_without_space_is_refused():
    from tests.conftest import make_client

    c = await make_client(1, raw_tb=1, initial_used_percent=99.99)
    a = await asset(c)
    body = await c.call("POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 1_000_000})
    assert body["error"]["code"] == 1077948997
    await c.http.aclose()


async def test_random_outcomes_are_mostly_successful():
    from tests.conftest import make_client

    c = await make_client(3)
    a = await asset(c)
    ids = [
        (await c.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIZEGB": 1}))["taskId"] for _ in range(60)
    ]
    c.mock.advance_time(3600)
    c.mock.sessions[c.token] = c.mock.now() + 100
    outcomes = [(await c.ok("GET", f"/task_list/{t}"))["STATUS"] for t in ids]
    assert outcomes.count("SUCCESS") > 35 and set(outcomes) <= {"SUCCESS", "FAILED", "PARTIALLY_SUCCESSFUL"}
    await c.http.aclose()


async def test_next_backup_hook_decides_outcome_size_and_duration(op):
    a = await asset(op)
    await op.http.post("/_mock/next_backup", json={"outcome": "FAILED", "size_gb": 50, "duration_s": 100})
    tid = (await op.ok("POST", "/backup_job", {"ASSETID": a["ID"]}))["taskId"]
    op.mock.advance_time(60)
    assert (await op.ok("GET", f"/task_list/{tid}"))["STATUS"] == "RUNNING"
    op.mock.advance_time(60)
    done = await op.ok("GET", f"/task_list/{tid}")
    assert done["STATUS"] == "FAILED" and done["PROGRESS"] == 37
    # the hook applies to one task only
    other = (await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIMULATE_OUTCOME": "SUCCESS"}))[
        "taskId"
    ]
    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    assert (await op.ok("GET", f"/task_list/{other}"))["STATUS"] == "SUCCESS"


async def test_lost_task_is_reported_as_unknown(op):
    a = await asset(op)
    tid = (await op.ok("POST", "/backup_job", {"ASSETID": a["ID"]}))["taskId"]
    await op.http.post("/_mock/lose_task", json={"task_id": tid})
    assert (await op.call("GET", f"/task_list/{tid}"))["error"]["code"] == 1077948996


async def test_delete_asset_is_refused_while_a_backup_runs(op):
    a = await asset(op)
    await op.ok("POST", "/backup_job", {"ASSETID": a["ID"], "SIMULATE_OUTCOME": "SUCCESS"})
    assert (await op.call("DELETE", f"/asset/{a['ID']}"))["error"]["code"] == 1077948995
    op.mock.advance_time(3600)
    op.mock.sessions[op.token] = op.mock.now() + 100
    await op.ok("DELETE", f"/asset/{a['ID']}")
    assert (await op.call("DELETE", f"/asset/{a['ID']}"))["error"]["code"] == 1077948996
