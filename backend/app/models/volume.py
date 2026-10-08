import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .device import StorageDevice
from .enums import MappingStatus, Provisioning
from .tenant import Tenant


class StorageVolume(UUIDPrimaryKey, Timestamps, Base):
    """A block LUN on a Dorado V7 array."""

    __tablename__ = "storage_volumes"
    __table_args__ = (
        CheckConstraint("size_gb > 0", name="size_positive"),
        UniqueConstraint("storage_device_id", "array_lun_id", name="uq_volume_array_lun"),
    )

    name: Mapped[str] = mapped_column(String(128))
    wwn: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    size_gb: Mapped[int] = mapped_column(Integer)
    provisioning: Mapped[Provisioning] = mapped_column(
        str_enum(Provisioning, "provisioning"), default=Provisioning.THIN
    )
    mapping_status: Mapped[MappingStatus] = mapped_column(
        str_enum(MappingStatus, "mapping_status"), default=MappingStatus.UNMAPPED
    )
    array_lun_id: Mapped[str | None] = mapped_column(String(32), default=None)
    storage_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_devices.id", ondelete="RESTRICT"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    protection_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("protection_policies.id", ondelete="SET NULL"), default=None
    )

    storage_device: Mapped[StorageDevice] = relationship(back_populates="volumes")
    tenant: Mapped[Tenant] = relationship(back_populates="volumes")
