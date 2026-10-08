"""Async client for the Huawei DeviceManager REST API (session login, envelope handling)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from . import errors as err

# Error codes the mock server emits. Verify against the real array's documentation before
# relying on them in production; unknown codes fall back to a generic DeviceError.
_CODE_MAP: dict[int, type[err.DeviceError]] = {
    -401: err.AuthenticationError,
    1077949061: err.AuthenticationError,
    1077948996: err.ResourceNotFoundError,
    1077948993: err.ResourceExistsError,
    1077948997: err.InsufficientSpaceError,
    1077948995: err.ResourceBusyError,
    1077936900: err.ComplianceLockError,
}


@dataclass(frozen=True)
class DeviceConnection:
    host: str
    port: int = 8088
    username: str = "admin"
    password: str = ""
    https: bool = True
    verify_tls: bool = False  # arrays ship with self-signed certificates

    @property
    def base_url(self) -> str:
        return f"{'https' if self.https else 'http'}://{self.host}:{self.port}"


class HuaweiClient:
    def __init__(
        self,
        connection: DeviceConnection,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self.connection = connection
        self._http = httpx.AsyncClient(
            base_url=connection.base_url,
            verify=connection.verify_tls,
            transport=transport,
            timeout=timeout,
        )
        self.token: str | None = None
        self.device_id: str | None = None

    async def login(self) -> None:
        body = await self._send(
            "POST",
            "/deviceManager/rest/xxxxx/sessions",
            json={
                "username": self.connection.username,
                "password": self.connection.password,
                "scope": "0",
            },
            auth=False,
        )
        self.token = body["data"]["iBaseToken"]
        self.device_id = body["data"]["deviceid"]

    async def logout(self) -> None:
        if self.token:
            try:
                await self.request("DELETE", "/sessions")
            except err.StorageDriverError:
                pass
            self.token = None

    async def close(self) -> None:
        await self.logout()
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        raw_path: bool = False,
    ) -> Any:
        """Call the array and return the ``data`` member (``None`` when absent).

        ``path`` is relative to ``/deviceManager/rest/{deviceid}`` unless ``raw_path`` is set.
        A single transparent re-login is attempted when the session has expired.
        """
        if self.token is None:
            await self.login()
        for attempt in (1, 2):
            url = path if raw_path else f"/deviceManager/rest/{self.device_id}{path}"
            try:
                body = await self._send(method, url, json=json, params=params)
            except err.AuthenticationError:
                if attempt == 2:
                    raise
                await self.login()
                continue
            return body.get("data")

    async def get_list(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """GET a collection; an absent ``data`` key means an empty list."""
        data = await self.request("GET", path, params=params or None)
        return data or []

    async def _send(
        self, method: str, url: str, *, json: Any = None, params: dict | None = None, auth: bool = True
    ) -> dict[str, Any]:
        headers = {"iBaseToken": self.token} if auth and self.token else {}
        try:
            response = await self._http.request(method, url, json=json, params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
            raise err.ConnectionFailedError(f"{self.connection.base_url}: {exc}") from exc
        error = body.get("error", {})
        code = int(error.get("code", 0))
        if code != 0:
            raise _CODE_MAP.get(code, err.DeviceError)(code, str(error.get("description", "")))
        return body
