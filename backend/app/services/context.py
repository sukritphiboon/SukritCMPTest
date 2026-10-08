"""Per-request context shared by routers and services."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import SecretCipher, get_cipher
from app.drivers import DeviceConnection, StorageDriverBase, StorageDriverError, create_driver
from app.models import StorageDevice

from .audit import audited

log = logging.getLogger(__name__)
T = TypeVar("T")

DriverFactory = Callable[[StorageDevice, SecretCipher], StorageDriverBase]


def default_driver_factory(device: StorageDevice, cipher: SecretCipher) -> StorageDriverBase:
    settings = get_settings()
    username, password = device.get_credentials(cipher)
    connection = DeviceConnection(
        device.ip_address,
        device.management_port,
        username,
        password,
        https=settings.device_https,
        verify_tls=settings.device_verify_tls,
    )
    return create_driver(device.model.value, connection)


@dataclass
class Ctx:
    session: AsyncSession
    actor: str
    factory: DriverFactory
    source_ip: str | None = None

    @property
    def cipher(self) -> SecretCipher:
        return get_cipher()

    @asynccontextmanager
    async def driver(self, device: StorageDevice) -> AsyncIterator[StorageDriverBase]:
        drv = self.factory(device, self.cipher)
        try:
            await drv.connect()
            yield drv
        finally:
            await drv.close()

    def audited(self, action: str, resource_type: str, **kwargs: Any):
        return audited(self.session, self.actor, action, resource_type, source_ip=self.source_ip, **kwargs)

    async def get(self, model: type[T], obj_id: uuid.UUID | None, label: str | None = None) -> T:
        obj = await self.session.get(model, obj_id) if obj_id is not None else None
        if obj is None:
            raise HTTPException(404, f"{label or model.__name__} not found.")
        return obj

    async def get_optional(self, model: type[T], obj_id: uuid.UUID | None) -> T | None:
        return None if obj_id is None else await self.get(model, obj_id)


async def undo(action: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """Best-effort compensation on the array; a failure is logged, never raised."""
    try:
        await action(*args)
    except StorageDriverError:
        log.exception(
            "Compensation %s%s failed; the array may hold an orphan resource.", action.__name__, args
        )
