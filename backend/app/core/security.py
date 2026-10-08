"""API key authentication. The key's user name becomes the ``actor`` in the audit log."""

import secrets
from typing import Annotated

from fastapi import Header, HTTPException

from .config import get_settings


def parse_api_keys(raw: str) -> dict[str, str]:
    """Return ``{key: user}`` from ``"user:key,user2:key2"`` (split on the first colon)."""
    keys: dict[str, str] = {}
    for item in raw.split(","):
        user, sep, key = item.strip().partition(":")
        if sep and user and key:
            keys[key] = user
    return keys


async def get_actor(x_api_key: Annotated[str | None, Header()] = None) -> str:
    keys = parse_api_keys(get_settings().api_keys)
    actor = None
    if x_api_key:
        for key, user in keys.items():  # compare against every key: no early exit on a match
            if secrets.compare_digest(key.encode(), x_api_key.encode()):
                actor = user
    if actor is None:
        raise HTTPException(401, "Invalid or missing API key.", headers={"WWW-Authenticate": "ApiKey"})
    return actor
