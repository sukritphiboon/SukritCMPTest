import httpx

from tests.conftest import make_client


async def test_login_returns_token_and_deviceid(op):
    body = await op.login()
    assert body["error"] == {"code": 0, "description": "0"}
    token = body["data"]["iBaseToken"]
    assert (
        len(token) == 64 and token == token.upper() and int(token, 16) >= 0
    )  # like the reference's 64 hex digits
    device_id = body["data"]["deviceid"]
    assert len(device_id) == 20 and device_id.isalnum() and device_id.startswith("2102351")
    assert body["data"]["accountstate"] == 1 and body["data"]["userscope"] == "0"


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


async def test_session_expires_after_20_minutes_and_heartbeat_extends_it(op):
    op.mock.advance_time(900)  # 15 of the 20 minutes
    await op.ok("PUT", "/sessions")  # heartbeat
    op.mock.advance_time(900)  # 30 minutes since login, but only 15 since the heartbeat
    assert (await op.call("GET", "/system/"))["error"]["code"] == 0
    op.mock.advance_time(1300)  # more than 20 minutes without a request
    assert (await op.call("GET", "/system/"))["error"]["code"] == -401


async def test_login_sets_the_session_cookie_and_both_credentials_are_required(op):
    r = await op.http.post(
        "/deviceManager/rest/xxxxx/sessions",
        json={"username": "admin", "password": "Admin@storage1", "scope": "0"},
    )
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("session=ismsession=") and "httponly" in cookie.lower()
    data = r.json()["data"]
    path = f"/deviceManager/rest/{data['deviceid']}/system/"
    # a client without the cookie jar: the token alone is not enough ...
    bare = httpx.AsyncClient(transport=httpx.ASGITransport(app=op.app), base_url="http://mock")
    assert (await bare.get(path, headers={"iBaseToken": data["iBaseToken"]})).json()["error"]["code"] == -401
    # ... and neither is the cookie alone
    session_cookie = cookie.split(";")[0].removeprefix("session=")
    assert (await bare.get(path, headers={"Cookie": f"session={session_cookie}"})).json()["error"][
        "code"
    ] == -401
    both = {"iBaseToken": data["iBaseToken"], "Cookie": f"session={session_cookie}"}
    assert (await bare.get(path, headers=both)).json()["error"]["code"] == 0
    wrong = await bare.get(path, headers={**both, "Cookie": "session=ismsession=BAD"})
    assert wrong.json()["error"]["code"] == -401
    await bare.aclose()


async def test_scope_is_mandatory_at_login(op):
    r = await op.http.post(
        "/deviceManager/rest/xxxxx/sessions", json={"username": "admin", "password": "Admin@storage1"}
    )
    assert r.json()["error"]["code"] == 50331651


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
