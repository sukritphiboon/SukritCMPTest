from app.profiles import SECTORS_PER_GB

GB5 = str(5 * SECTORS_PER_GB)


async def make_fs(c, name="fs1"):
    return await c.ok("POST", "/filesystem", {"NAME": name, "CAPACITY": GB5, "PARENTID": "1"})


async def test_filesystem_with_nfs_cifs_and_quota(dorado):
    fs = await make_fs(dorado)
    nfs = await dorado.ok("POST", "/NFSHARE", {"FSID": fs["ID"]})
    assert nfs["SHAREPATH"] == "/fs1/"
    cifs = await dorado.ok("POST", "/CIFSHARE", {"FSID": fs["ID"], "NAME": "share1"})
    assert cifs["NAME"] == "share1"
    quota = await dorado.ok(
        "POST", "/FS_QUOTA", {"PARENTID": fs["ID"], "PARENTTYPE": 40, "SPACEHARDQUOTA": 4 * 1024**3}
    )
    assert int(quota["SPACESOFTQUOTA"]) < int(quota["SPACEHARDQUOTA"])


async def test_soft_quota_above_hard_rejected(dorado):
    fs = await make_fs(dorado)
    body = await dorado.call(
        "POST", "/FS_QUOTA", {"PARENTID": fs["ID"], "SPACEHARDQUOTA": 100, "SPACESOFTQUOTA": 200}
    )
    assert body["error"]["code"] == 50331651


async def test_duplicate_nfs_path(dorado):
    fs = await make_fs(dorado)
    await dorado.ok("POST", "/NFSHARE", {"FSID": fs["ID"]})
    body = await dorado.call("POST", "/NFSHARE", {"FSID": fs["ID"]})
    assert body["error"]["code"] == 1077948993


async def test_delete_filesystem_removes_children(dorado):
    fs = await make_fs(dorado)
    await dorado.ok("POST", "/NFSHARE", {"FSID": fs["ID"]})
    await dorado.ok("POST", "/snapshot", {"NAME": "fs-snap", "PARENTID": fs["ID"], "PARENTTYPE": 40})
    await dorado.ok("DELETE", f"/filesystem/{fs['ID']}")
    empty = {"error": {"code": 0, "description": "0"}}
    assert await dorado.call("GET", "/NFSHARE") == empty
    assert await dorado.call("GET", "/snapshot") == empty


async def test_share_for_missing_filesystem(dorado):
    body = await dorado.call("POST", "/NFSHARE", {"FSID": "42"})
    assert body["error"]["code"] == 1077948996


async def test_oceanprotect_supports_filesystem(protect):
    fs = await make_fs(protect, "repo1")
    assert fs["WORMTYPE"] == "0"
