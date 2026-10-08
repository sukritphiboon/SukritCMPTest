SECTORS_100GB = 100 * 1024 * 1024 * 2
DAY = 86400


async def fs_id(c, name="worm-fs"):
    return (await c.ok("POST", "/filesystem", {"NAME": name, "CAPACITY": str(SECTORS_100GB)}))["ID"]


async def test_seeded_backup_copies_have_retention_states(op):
    copies = {c["NAME"]: c for c in await op.ok("GET", "/backup_retention")}
    assert copies["vm-prod-db-01"]["STATE"] == "locked"  # WORM, inside retention
    assert copies["fileserver-nas"]["STATE"] == "retained"
    assert copies["vm-dev-test"]["STATE"] == "expired"  # 45 days old, 30 day retention


async def test_cannot_delete_locked_copy_but_can_delete_expired(op):
    copies = {c["NAME"]: c for c in await op.ok("GET", "/backup_retention")}
    locked = await op.call("DELETE", f"/backup_retention/{copies['vm-prod-db-01']['ID']}")
    assert locked["error"]["code"] == 1077936900
    await op.ok("DELETE", f"/backup_retention/{copies['vm-dev-test']['ID']}")


async def test_worm_lock_releases_after_retention(op):
    copy = await op.ok(
        "POST", "/backup_retention", {"NAME": "new", "SOURCE": "vm1", "RETENTIONDAYS": 7, "WORM": True}
    )
    assert copy["STATE"] == "locked" and copy["REMAININGDAYS"] in (7, 8)
    assert (await op.call("DELETE", f"/backup_retention/{copy['ID']}"))["error"]["code"] != 0
    op.mock.advance_time(8 * DAY)
    await op.login()
    await op.ok("DELETE", f"/backup_retention/{copy['ID']}")


async def test_worm_policy_compliance_is_permanent(op):
    fid = await fs_id(op)
    policy = await op.ok("POST", "/worm_policy", {"NAME": "p1", "FSID": fid, "MODE": 1, "PROTECTPERIOD": 30})
    assert policy["MODENAME"] == "compliance"
    assert (await op.ok("GET", f"/filesystem/{fid}"))["WORMTYPE"] == "1"
    assert (await op.call("DELETE", f"/worm_policy/{policy['ID']}"))["error"]["code"] == 1077936900
    assert (await op.call("DELETE", f"/filesystem/{fid}"))["error"]["code"] == 1077936900


async def test_worm_policy_enterprise_can_be_removed(op):
    fid = await fs_id(op)
    policy = await op.ok("POST", "/worm_policy", {"NAME": "p2", "FSID": fid, "MODE": 2, "PROTECTPERIOD": 10})
    await op.ok("DELETE", f"/worm_policy/{policy['ID']}")
    assert (await op.ok("GET", f"/filesystem/{fid}"))["WORMTYPE"] == "0"
    await op.ok("DELETE", f"/filesystem/{fid}")


async def test_invalid_worm_parameters_and_duplicates(op):
    fid = await fs_id(op)
    bad = await op.call("POST", "/worm_policy", {"NAME": "p", "FSID": fid, "MODE": 9, "PROTECTPERIOD": 1})
    assert bad["error"]["code"] == 50331651
    await op.ok("POST", "/worm_policy", {"NAME": "p", "FSID": fid, "MODE": 2, "PROTECTPERIOD": 1})
    again = await op.call("POST", "/worm_policy", {"NAME": "q", "FSID": fid, "MODE": 2, "PROTECTPERIOD": 1})
    assert again["error"]["code"] == 1077948993


async def test_filesystem_basics(op):
    fid = await fs_id(op)
    dup = await op.call("POST", "/filesystem", {"NAME": "worm-fs", "CAPACITY": str(SECTORS_100GB)})
    assert dup["error"]["code"] == 1077948993
    assert [f["ID"] for f in await op.ok("GET", "/filesystem")] == [fid]
