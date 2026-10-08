from tests.conftest import make_client


async def test_login_returns_token_and_deviceid(op):
    body = await op.login()
    assert body["error"] == {"code": 0, "description": "0"}
    assert len(body["data"]["iBaseToken"]) >= 16
    assert body["data"]["deviceid"].isdigit()


async def test_login_bad_password(op):
    body = await op.login(password="nope")
    assert body["error"]["code"] != 0
    assert "data" not in body


async def test_login_missing_fields_is_param_error(op):
    r = await op.http.post("/deviceManager/rest/xxxxx/sessions", json={"username": "admin"})
    assert r.json()["error"]["code"] == 50331651


async def test_request_without_token_is_unauthorized(op):
    body = await op.call("GET", "/storagepool", token=False)
    assert body["error"]["code"] == -401


async def test_logout_invalidates_token(op):
    await op.ok("DELETE", "/sessions")
    assert (await op.call("GET", "/storagepool"))["error"]["code"] == -401


async def test_session_expires_and_heartbeat_extends_it(op):
    op.mock.advance_time(1500)  # 25 min of the 30 min lifetime
    await op.ok("PUT", "/sessions")  # heartbeat
    op.mock.advance_time(1500)
    assert (await op.call("GET", "/system/"))["error"]["code"] == 0
    op.mock.advance_time(3600)
    assert (await op.call("GET", "/system/"))["error"]["code"] == -401


async def test_empty_list_omits_data_key(op):
    assert await op.call("GET", "/backup_policy") == {"error": {"code": 0, "description": "0"}}


async def test_system_info(op):
    info = await op.ok("GET", "/system/")
    assert info["PRODUCTMODE"] == "OceanProtect X8000" and info["SN"].startswith("2102")


async def test_seed_makes_everything_deterministic():
    a, b = await make_client(7), await make_client(7)
    assert a.device_id == b.device_id
    assert a.mock.total_ratio == b.mock.total_ratio
    for c in (a, b):
        await c.http.aclose()


async def test_model_can_be_chosen():
    c = await make_client(model="OceanProtect X3000")
    assert (await c.ok("GET", "/system/"))["PRODUCTMODE"] == "OceanProtect X3000"
    await c.http.aclose()
