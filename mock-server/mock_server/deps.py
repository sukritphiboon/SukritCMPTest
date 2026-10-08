"""Shared request helpers: session check and list filtering."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from . import envelope as E
from .state import MockState


def get_state(request: Request) -> MockState:
    return request.app.state.mock


def require_session(request: Request) -> MockState:
    state = get_state(request)
    if not state.session_valid(request.headers.get("iBaseToken")):
        raise E.HuaweiError(E.UNAUTHORIZED, "The user is not logged in or the session expired.")
    return state


def select(items: list[dict[str, Any]], filter_: str | None, range_: str | None) -> list[dict]:
    """Apply Huawei style ``filter=KEY::VALUE`` and ``range=[0-100]`` query parameters."""
    result = list(items)
    if filter_:
        key, _, value = filter_.partition("::")
        result = [o for o in result if str(o.get(key)) == value]
    if range_:
        m = re.fullmatch(r"\[(\d+)-(\d+)\]", range_)
        if m:
            result = result[int(m.group(1)) : int(m.group(2))]
    return result
