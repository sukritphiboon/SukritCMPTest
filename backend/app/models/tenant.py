from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamps, UUIDPrimaryKey


class Tenant(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "quota_block_gb >= 0 AND quota_file_gb >= 0 AND quota_object_gb >= 0", name="quotas_non_negative"
        ),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    quota_block_gb: Mapped[int] = mapped_column(Integer, default=0)
    quota_file_gb: Mapped[int] = mapped_column(Integer, default=0)
    quota_object_gb: Mapped[int] = mapped_column(Integer, default=0)

    volumes: Mapped[list["StorageVolume"]] = relationship(back_populates="tenant")  # noqa: F821
    filesystems: Mapped[list["FileSystem"]] = relationship(back_populates="tenant")  # noqa: F821
    buckets: Mapped[list["ObjectBucket"]] = relationship(back_populates="tenant")  # noqa: F821
