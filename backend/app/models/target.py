import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import SecretCipher

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .enums import ApplianceModel, HealthStatus


class BackupTarget(UUIDPrimaryKey, Timestamps, Base):
    """One OceanProtect appliance."""

    __tablename__ = "backup_targets"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    ip_address: Mapped[str] = mapped_column(String(45))
    management_port: Mapped[int] = mapped_column(Integer, default=8088)
    # AES-256-GCM token of a JSON document {"username": ..., "password": ...}
    credentials_encrypted: Mapped[str] = mapped_column(Text)
    model: Mapped[ApplianceModel] = mapped_column(str_enum(ApplianceModel, "appliance_model"))
    serial_number: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    # Identifier reported by the appliance itself (the "deviceid" returned at login)
    device_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    health_status: Mapped[HealthStatus] = mapped_column(
        str_enum(HealthStatus, "health_status"), default=HealthStatus.UNKNOWN
    )
    firmware_version: Mapped[str | None] = mapped_column(String(64), default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    def set_credentials(self, cipher: SecretCipher, username: str, password: str) -> None:
        payload = json.dumps({"username": username, "password": password})
        # Bind the ciphertext to this row so it cannot be copied to another target.
        self.id = self.id or uuid.uuid4()
        self.credentials_encrypted = cipher.encrypt(payload, aad=self.id.bytes)

    def get_credentials(self, cipher: SecretCipher) -> tuple[str, str]:
        data = json.loads(cipher.decrypt(self.credentials_encrypted, aad=self.id.bytes))
        return data["username"], data["password"]
