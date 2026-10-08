from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import get_state, require_session

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


@router.post("/sessions")
async def login(request: Request, body: dict[str, Any] = Body(...)):
    state = get_state(request)
    E.require(body, "username", "password")
    if body["username"] != state.username or body["password"] != state.password:
        raise E.HuaweiError(E.BAD_CREDENTIALS, "The user name or password is incorrect.")
    token = state.login()
    return E.ok(
        {
            "accountstate": 1,
            "deviceid": state.device_id,
            "iBaseToken": token,
            "lastloginip": "10.0.0.1",
            "level": 3,
            "username": state.username,
            "usergroup": "",
            "pwdchangetime": 0,
            "vasa_ip": secrets.token_hex(2),
        }
    )


@router.get("/sessions")
async def session_info(request: Request):
    state = require_session(request)
    return E.ok({"username": state.username, "deviceid": state.device_id})


@router.delete("/sessions")
async def logout(request: Request):
    state = require_session(request)
    state.sessions.pop(request.headers["iBaseToken"], None)
    return E.ok()
