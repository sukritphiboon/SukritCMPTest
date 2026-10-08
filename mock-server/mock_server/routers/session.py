from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .. import envelope as E
from ..deps import get_state, require_session

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


@router.post("/sessions")
async def login(request: Request, body: dict[str, Any] = Body(...)):
    state = get_state(request)
    E.require(body, "username", "password", "scope")  # scope is mandatory in the reference
    if body["username"] != state.username or body["password"] != state.password:
        raise E.HuaweiError(E.BAD_CREDENTIALS, "The user name or password is incorrect.")
    token, cookie = state.login()
    response = JSONResponse(
        E.ok(
            {
                "accountstate": 1,
                "deviceid": state.device_id,
                "iBaseToken": token,
                "lastloginip": request.client.host if request.client else "127.0.0.1",
                "lastlogintime": int(state.now()),
                "pwdchangetime": 0,
                "roleId": "1",
                "usergroup": "",
                "userid": state.username,
                "username": state.username,
                "userscope": str(body.get("scope", "0")),
            }
        )
    )
    # The reference: the session id travels in a cookie, and the client must send it back. The real array also
    # sets "secure"; it is left out here because the mock normally runs over plain http.
    response.headers.append("set-cookie", f"session={cookie}; SameSite=Lax; path=/; httponly")
    return response


@router.get("/sessions")
async def session_info(request: Request):
    state = require_session(request)
    return E.ok({"username": state.username, "deviceid": state.device_id})


@router.put("/sessions")
async def heartbeat(request: Request):
    """Keep-alive: extends the session lifetime."""
    state = require_session(request)
    state.heartbeat(request.headers["iBaseToken"])
    return E.ok()


@router.delete("/sessions")
async def logout(request: Request):
    state = require_session(request)
    state.drop_session(request.headers["iBaseToken"])
    return E.ok()
