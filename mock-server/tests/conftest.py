import httpx
import pytest

from mock_server.main import create_app

USER, PASSWORD = "admin", "Admin@storage1"
BASE = "/deviceManager/rest"


class Client:
    """Thin test wrapper that logs in and prefixes the device path."""

    def __init__(self, http: httpx.AsyncClient, app):
        self.http, self.app = http, app
        self.token: str | None = None
        self.device_id = ""

    @property
    def mock(self):
        return self.app.state.mock

    async def login(self, username=USER, password=PASSWORD):
        r = await self.http.post(
            f"{BASE}/xxxxx/sessions", json={"username": username, "password": password, "scope": "0"}
        )
        body = r.json()
        if body["error"]["code"] == 0:
            self.token = body["data"]["iBaseToken"]
            self.device_id = body["data"]["deviceid"]
        return body

    async def call(self, method: str, path: str, json=None, params=None, token=True):
        headers = {"iBaseToken": self.token} if token and self.token else {}
        r = await self.http.request(
            method, f"{BASE}/{self.device_id or 'xxxxx'}{path}", json=json, params=params, headers=headers
        )
        assert r.status_code == 200
        return r.json()

    async def ok(self, method: str, path: str, json=None, params=None):
        body = await self.call(method, path, json, params)
        assert body["error"]["code"] == 0, body
        return body.get("data")


async def make_client(seed: int = 42, **options):
    app = create_app(seed=seed, **options)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock")
    client = Client(http, app)
    await client.login()
    return client


@pytest.fixture
async def op():
    c = await make_client()
    yield c
    await c.http.aclose()
