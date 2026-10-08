from app.profiles import SECTORS_PER_GB

DAY = 86400


async def fs_id(c):
    fs = await c.ok("POST", "/filesystem", {"NAME": "worm-fs", "CAPACITY": str(100 * SECTORS_PER_GB)})
    return fs["ID"]


async def test_seeded_backup_copies_have_retention_states(protect):
    copies = {c["NAME"]: c for c in await protect.ok("GET", "/backup_retention")}
    assert copies["vm-prod-db-01"]["STATE"] == "locked"      # WORM, inside retention
    assert copies["fileserver-nas"]["STATE"] == "retained"   # no WORM, inside retention
    assert copies["vm-dev-test"]["STATE"] == "expired"       # 45 days old, 30 day retention


async def test_cannot_delete_locked_copy_but_can_delete_expired(protect):
    copies = {c["NAME"]: c for c in await protect.ok("GET", "/backup_retention")}
    locked = await protect.call("DELETE", f"/backup_retention/{copies['vm-prod-db-01']['ID']}")
    assert locked["error"]["code"] == 1077936900
    await protect.ok("DELETE", f"/backup_retention/{copies['vm-dev-test']['ID']}")


async def test_worm_lock_releases_after_retention(protect):
    copy = await protect.ok(
        "POST", "/backup_retention", {"NAME": "new", "SOURCE": "vm1", "RETENTIONDAYS": 7, "WORM": True}
    )
    assert copy["STATE"] == "locked" and copy["REMAININGDAYS"] in (7, 8)
    assert (await protect.call("DELETE", f"/backup_retention/{copy['ID']}"))["error"]["code"] != 0
    protect.app.state.mock.advance_time(8 * DAY)
    await protect.login()  # the 30 minute session also expired
    await protect.ok("DELETE", f"/backup_retention/{copy['ID']}")


async def test_worm_policy_compliance_is_permanent(protect):
    fid = await fs_id(protect)
    policy = await protect.ok(
        "POST", "/worm_policy", {"NAME": "p1", "FSID": fid, "MODE": 1, "PROTECTPERIOD": 30}
    )
    assert policy["MODENAME"] == "compliance"
    assert (await protect.ok("GET", f"/filesystem/{fid}"))["WORMTYPE"] == "1"
    assert (await protect.call("DELETE", f"/worm_policy/{policy['ID']}"))["error"]["code"] == 1077936900
    assert (await protect.call("DELETE", f"/filesystem/{fid}"))["error"]["code"] == 1077936900


async def test_worm_policy_enterprise_can_be_removed(protect):
    fid = await fs_id(protect)
    policy = await protect.ok(
        "POST", "/worm_policy", {"NAME": "p2", "FSID": fid, "MODE": 2, "PROTECTPERIOD": 10}
    )
    await protect.ok("DELETE", f"/worm_policy/{policy['ID']}")
    assert (await protect.ok("GET", f"/filesystem/{fid}"))["WORMTYPE"] == "0"


async def test_invalid_worm_parameters(protect):
    fid = await fs_id(protect)
    body = await protect.call("POST", "/worm_policy", {"NAME": "p", "FSID": fid, "MODE": 9, "PROTECTPERIOD": 1})
    assert body["error"]["code"] == 50331651


async def test_dorado_has_no_worm_or_backup_endpoints(dorado):
    assert (await dorado.call("GET", "/backup_retention"))["error"]["code"] == 1077949004
    assert (await dorado.call("GET", "/worm_policy"))["error"]["code"] == 1077949004
