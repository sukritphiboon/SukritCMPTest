"""Huawei DeviceManager response envelope and error codes.

Every REST response is HTTP 200 with ``{"error": {"code": N, "description": "..."}, "data": ...}``.
An empty result list omits the ``data`` key, as the real array does.
Error code numbers are simulated; they mimic the style of the real ones.
"""

from __future__ import annotations

from typing import Any

SUCCESS = 0
UNAUTHORIZED = -401
PARAM_ERROR = 50331651
OBJECT_EXISTS = 1077948993
OBJECT_NOT_FOUND = 1077948996
INSUFFICIENT_SPACE = 1077948997
OBJECT_IN_USE = 1077948995
NOT_SUPPORTED = 1077949004
BAD_CREDENTIALS = 1077949061
WORM_LOCKED = 1077936900


class HuaweiError(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


def ok(data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": SUCCESS, "description": "0"}}
    if data is None or data == []:
        return body
    body["data"] = data
    return body


def fail(code: int, description: str) -> dict[str, Any]:
    return {"error": {"code": code, "description": description}}


def require(body: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise HuaweiError(PARAM_ERROR, f"The entered parameter is incorrect: {', '.join(missing)}")
