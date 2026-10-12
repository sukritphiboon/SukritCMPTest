"""API key authentication and role based access control.

A key maps to a user and a role. The user name becomes the ``actor`` in the audit log; the role
decides what the key may call (viewer < operator < admin, each role includes the ones below it).
"""

import secrets
from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .config import get_settings


class Role(IntEnum):
    VIEWER = 1  # read everything except the audit log
    OPERATOR = 2  # day-to-day actions: run/cancel backups, acknowledge alarms, test a connection
    ADMIN = 3  # configuration (targets, policies, assets) and the audit log


@dataclass(frozen=True)
class Principal:
    user: str
    role: Role


def parse_api_keys(raw: str) -> dict[str, Principal]:
    """Return ``{key: Principal}`` from ``"user:role:key,user2:key2"``.

    ``role`` is ``viewer``, ``operator`` or ``admin``. The old ``user:key`` form (no role) still works
    and means ``admin``, so existing deployments keep their access. A key may contain colons.
    """
    keys: dict[str, Principal] = {}
    for item in raw.split(","):
        user, sep, rest = item.strip().partition(":")
        if not (sep and user and rest):
            continue
        role_name, sep2, key = rest.partition(":")
        role = Role.__members__.get(role_name.upper()) if sep2 else None
        if role is None:  # legacy "user:key"
            role, key = Role.ADMIN, rest
        if key:
            keys[key] = Principal(user, role)
    return keys


async def get_principal(x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    keys = parse_api_keys(get_settings().api_keys)
    found = None
    if x_api_key:
        for key, principal in keys.items():  # compare against every key: no early exit on a match
            if secrets.compare_digest(key.encode(), x_api_key.encode()):
                found = principal
    if found is None:
        raise HTTPException(401, "Invalid or missing API key.", headers={"WWW-Authenticate": "ApiKey"})
    return found


async def get_actor(principal: Annotated[Principal, Depends(get_principal)]) -> str:
    return principal.user


# Actions an operator may take. Any other write needs admin; reads need viewer, except the audit log.
OPERATOR_ACTIONS = {
    ("POST", "/backup-jobs"),
    ("POST", "/backup-jobs/{job_id}/cancel"),
    ("POST", "/backup-policies/{policy_id}/run"),
    ("POST", "/oceanprotect/alarms/{alarm_id}/acknowledge"),
    ("POST", "/targets/{target_id}/test"),
}
ADMIN_READS = {("GET", "/audit-logs")}


def required_role(method: str, route_path: str) -> Role:
    """``route_path`` is the route template as the router sees it, without the ``/api/v1`` prefix."""
    call = (method.upper(), route_path.removeprefix("/api/v1"))
    if call in ADMIN_READS:
        return Role.ADMIN
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return Role.VIEWER
    return Role.OPERATOR if call in OPERATOR_ACTIONS else Role.ADMIN


async def authorize(request: Request, principal: Annotated[Principal, Depends(get_principal)]) -> None:
    """Router-level guard: reject a key whose role is too low for the route it called."""
    route = request.scope.get("route")
    need = required_role(request.method, getattr(route, "path", request.url.path))
    if principal.role < need:
        have = principal.role.name.lower()
        raise HTTPException(403, f"Role '{have}' may not do this; '{need.name.lower()}' is required.")
