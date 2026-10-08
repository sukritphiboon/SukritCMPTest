from app.main import create_app  # noqa: F401
from tests.conftest import make_client


async def test_login_returns_token_and_deviceid(dorado):
    body = await dorado.login()
    assert body["error"] == {"code": 0, "description": "0"}
    assert len(body["data"]["iBaseToken"]) >= 16
    assert body["data"]["deviceid"].isdigit()


async def test_login_bad_password(dorado):
    body = await dorado.login(password="nope")
    assert body["error"]["code"] != 0
    assert "data" not in body


async def test_login_missing_fields_is_param_error(dorado):
    r = await dorado.http.post("/deviceManager/rest/xxxxx/sessions", json={"username": "admin"})
    assert r.json()["error"]["code"] == 50331651


async def test_request_without_token_is_unauthorized(dorado):
    body = await dorado.call("GET", "/lun", token=False)
    assert body["error"]["code"] == -401


async def test_logout_invalidates_token(dorado):
    await dorado.ok("DELETE", "/sessions")
    body = await dorado.call("GET", "/lun")
    assert body["error"]["code"] == -401


async def test_expired_session(dorado):
    dorado.app.state.mock.advance_time(3600)
    body = await dorado.call("GET", "/system/")
    assert body["error"]["code"] == -401


async def test_empty_list_omits_data_key(dorado):
    body = await dorado.call("GET", "/lun")
    assert body == {"error": {"code": 0, "description": "0"}}


async def test_system_info_per_profile(dorado, protect):
    assert "Dorado" in (await dorado.ok("GET", "/system/"))["PRODUCTMODE"]
    assert "OceanProtect" in (await protect.ok("GET", "/system/"))["PRODUCTMODE"]


async def test_seed_makes_device_id_deterministic():
    a = await make_client("dorado")
    b = await make_client("dorado")
    assert a.device_id == b.device_id
    await a.http.aclose()
    await b.http.aclose()
