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
    """Like the real array, both the iBaseToken header and the session cookie are needed (else -401)."""
    state = get_state(request)
    if not state.session_valid(request.headers.get("iBaseToken"), request.cookies.get("session")):
        raise E.HuaweiError(E.UNAUTHORIZED, "The user is not logged in or the session expired.")
    return state


def _matches(item: dict[str, Any], clause: str) -> bool:
    """One clause: ``key::value`` exact, ``key:value`` fuzzy (contains), ``key:[from,to]`` numeric range."""
    key, sep, value = clause.strip().partition(":")
    if not sep:
        return True
    actual = item.get(key.strip())
    if value.startswith(":"):  # exact match
        return str(actual) == value[1:].strip()
    value = value.strip()
    if value.startswith("[") and value.endswith("]") and "," in value:
        low, _, high = value[1:-1].partition(",")
        try:
            return float(low) <= float(actual) <= float(high)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    return value.lower() in str(actual).lower()


def select(items: list[dict[str, Any]], filter_: str | None, range_: str | None) -> list[dict]:
    """Huawei query parameters: ``filter=a::1 and b:x or c::2`` (``and`` binds tighter than ``or``)
    and ``range=[0-100]``.

    The end index of a range is not included.
    """
    result = list(items)
    if filter_:
        groups = [g.split(" and ") for g in filter_.split(" or ")]
        result = [o for o in result if any(all(_matches(o, c) for c in g) for g in groups)]
    if range_:
        m = re.fullmatch(r"\[(\d+)-(\d+)\]", range_)
        if m:
            result = result[int(m.group(1)) : int(m.group(2))]
    return result
