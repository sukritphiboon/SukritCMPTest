import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .device import StorageDevice
from .enums import FileProtocol, QuotaStatus
from .tenant import Tenant


class FileSystem(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "filesystems"
    __table_args__ = (
        CheckConstraint("size_gb > 0", name="size_positive"),
        UniqueConstraint("storage_device_id", "array_fs_id", name="uq_filesystem_array_fs"),
    )

    name: Mapped[str] = mapped_column(String(128))
    size_gb: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[FileProtocol] = mapped_column(str_enum(FileProtocol, "file_protocol"))
    share_path: Mapped[str | None] = mapped_column(String(255), default=None)
    quota_hard_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    quota_soft_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    quota_status: Mapped[QuotaStatus] = mapped_column(
        str_enum(QuotaStatus, "quota_status"), default=QuotaStatus.NONE
    )
    array_fs_id: Mapped[str | None] = mapped_column(String(32), default=None)
    storage_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_devices.id", ondelete="RESTRICT"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    protection_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("protection_policies.id", ondelete="SET NULL"), default=None
    )

    storage_device: Mapped[StorageDevice] = relationship(back_populates="filesystems")
    tenant: Mapped[Tenant] = relationship(back_populates="filesystems")
