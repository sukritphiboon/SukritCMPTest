import uuid

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamps, UUIDPrimaryKey
from .device import StorageDevice
from .tenant import Tenant


class ObjectBucket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "object_buckets"

    bucket_name: Mapped[str] = mapped_column(String(63), unique=True)
    owner: Mapped[str] = mapped_column(String(128))
    quota_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    s3_endpoint: Mapped[str] = mapped_column(String(255))
    storage_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_devices.id", ondelete="RESTRICT"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )

    storage_device: Mapped[StorageDevice] = relationship(back_populates="buckets")
    tenant: Mapped[Tenant] = relationship(back_populates="buckets")
