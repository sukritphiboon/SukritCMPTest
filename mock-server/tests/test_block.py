from app.profiles import SECTORS_PER_GB

GB10 = str(10 * SECTORS_PER_GB)


async def make_lun(c, name="lun1", capacity=GB10, **extra):
    return await c.ok("POST", "/lun", {"NAME": name, "CAPACITY": capacity, "PARENTID": "1", **extra})


async def test_lun_lifecycle(dorado):
    lun = await make_lun(dorado)
    assert lun["NAME"] == "lun1" and lun["ALLOCTYPE"] == "1"  # thin by default
    assert len(lun["WWN"]) == 32 and lun["WWN"].startswith("6")

    listed = await dorado.ok("GET", "/lun")
    assert [x["ID"] for x in listed] == [lun["ID"]]

    expanded = await dorado.ok(
        "PUT", "/lun/expand", {"ID": lun["ID"], "CAPACITY": str(20 * SECTORS_PER_GB)}
    )
    assert expanded["CAPACITY"] == str(20 * SECTORS_PER_GB)

    await dorado.ok("DELETE", f"/lun/{lun['ID']}")
    assert (await dorado.call("GET", f"/lun/{lun['ID']}"))["error"]["code"] == 1077948996


async def test_duplicate_lun_name(dorado):
    await make_lun(dorado)
    body = await dorado.call("POST", "/lun", {"NAME": "lun1", "CAPACITY": GB10})
    assert body["error"]["code"] == 1077948993


async def test_expand_must_grow(dorado):
    lun = await make_lun(dorado)
    body = await dorado.call("PUT", "/lun/expand", {"ID": lun["ID"], "CAPACITY": GB10})
    assert body["error"]["code"] == 50331651


async def test_thick_lun_and_filter(dorado):
    await make_lun(dorado, "thin1")
    await make_lun(dorado, "thick1", ALLOCTYPE="0")
    thick = await dorado.ok("GET", "/lun", params={"filter": "ALLOCTYPE::0"})
    assert [x["NAME"] for x in thick] == ["thick1"]


async def test_insufficient_space(dorado):
    huge = str(10_000 * 1024 * SECTORS_PER_GB)  # 10 PB
    body = await dorado.call("POST", "/lun", {"NAME": "big", "CAPACITY": huge})
    assert body["error"]["code"] == 1077948997


async def test_mapping_flow_exposes_lun_and_blocks_delete(dorado):
    lun = await make_lun(dorado)
    group = await dorado.ok("POST", "/lungroup", {"NAME": "lg1"})
    await dorado.ok(
        "POST", "/lungroup/associate", {"ID": group["ID"], "ASSOCIATEOBJTYPE": 11, "ASSOCIATEOBJID": lun["ID"]}
    )
    host = await dorado.ok("POST", "/host", {"NAME": "esx01"})
    hg = await dorado.ok("POST", "/hostgroup", {"NAME": "hg1"})
    await dorado.ok(
        "POST", "/hostgroup/associate", {"ID": hg["ID"], "ASSOCIATEOBJTYPE": 21, "ASSOCIATEOBJID": host["ID"]}
    )
    view = await dorado.ok("POST", "/mappingview", {"NAME": "mv1"})
    assert (await dorado.ok("GET", f"/lun/{lun['ID']}"))["EXPOSEDTOINITIATOR"] == "false"
    for obj_type, obj_id in ((256, group["ID"]), (14, hg["ID"])):
        await dorado.ok(
            "PUT", "/mappingview/create_associate",
            {"ID": view["ID"], "ASSOCIATEOBJTYPE": obj_type, "ASSOCIATEOBJID": obj_id},
        )
    assert (await dorado.ok("GET", f"/lun/{lun['ID']}"))["EXPOSEDTOINITIATOR"] == "true"
    body = await dorado.call("DELETE", f"/lun/{lun['ID']}")
    assert body["error"]["code"] == 1077948995


async def test_lun_snapshot_and_cascade_delete(dorado):
    lun = await make_lun(dorado)
    snap = await dorado.ok("POST", "/snapshot", {"NAME": "s1", "PARENTID": lun["ID"], "PARENTTYPE": 11})
    assert snap["PARENTID"] == lun["ID"] and snap["RUNNINGSTATUS"] == "43"
    await dorado.ok("DELETE", f"/lun/{lun['ID']}")
    assert (await dorado.call("GET", "/snapshot")) == {"error": {"code": 0, "description": "0"}}


async def test_snapshot_of_missing_lun(dorado):
    body = await dorado.call("POST", "/snapshot", {"NAME": "s1", "PARENTID": "99"})
    assert body["error"]["code"] == 1077948996


async def test_oceanprotect_rejects_block(protect):
    body = await protect.call("POST", "/lun", {"NAME": "x", "CAPACITY": GB10})
    assert body["error"]["code"] == 1077949004
