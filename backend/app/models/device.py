import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import SecretCipher

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .enums import DeviceModel, HealthStatus


class StorageDevice(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "storage_devices"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    ip_address: Mapped[str] = mapped_column(String(45))
    management_port: Mapped[int] = mapped_column(Integer, default=8088)
    # AES-256-GCM token of a JSON document {"username": ..., "password": ...}
    credentials_encrypted: Mapped[str] = mapped_column(Text)
    model: Mapped[DeviceModel] = mapped_column(str_enum(DeviceModel, "device_model"))
    # Identifier reported by the array itself (the "deviceid" returned at login)
    device_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    health_status: Mapped[HealthStatus] = mapped_column(
        str_enum(HealthStatus, "health_status"), default=HealthStatus.UNKNOWN
    )
    firmware_version: Mapped[str | None] = mapped_column(String(64), default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    volumes: Mapped[list["StorageVolume"]] = relationship(back_populates="storage_device")  # noqa: F821
    filesystems: Mapped[list["FileSystem"]] = relationship(back_populates="storage_device")  # noqa: F821
    buckets: Mapped[list["ObjectBucket"]] = relationship(back_populates="storage_device")  # noqa: F821

    def set_credentials(self, cipher: SecretCipher, username: str, password: str) -> None:
        payload = json.dumps({"username": username, "password": password})
        # Bind the ciphertext to this row so it cannot be copied to another device.
        self.id = self.id or uuid.uuid4()
        self.credentials_encrypted = cipher.encrypt(payload, aad=self.id.bytes)

    def get_credentials(self, cipher: SecretCipher) -> tuple[str, str]:
        data = json.loads(cipher.decrypt(self.credentials_encrypted, aad=self.id.bytes))
        return data["username"], data["password"]
