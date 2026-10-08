import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto import SecretCipher
from app.models import (
    AuditLog,
    Base,
    DeviceModel,
    FileProtocol,
    FileSystem,
    ObjectBucket,
    ProtectionPolicy,
    Provisioning,
    StorageDevice,
    StorageVolume,
    Tenant,
    WormMode,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def make_device(cipher, name="dorado-1", device_id="2102351"):
    d = StorageDevice(name=name, ip_address="10.0.0.5", model=DeviceModel.DORADO_V7, device_id=device_id)
    d.set_credentials(cipher, "admin", "P@ss")
    return d


async def test_device_credentials_are_encrypted(session):
    cipher = SecretCipher.from_base64(SecretCipher.generate_key())
    device = make_device(cipher)
    session.add(device)
    await session.commit()
    stored = (await session.scalars(select(StorageDevice))).one()
    assert "P@ss" not in stored.credentials_encrypted
    assert stored.get_credentials(cipher) == ("admin", "P@ss")
    assert stored.management_port == 8088 and stored.health_status.value == "unknown"


async def test_credentials_bound_to_row(session):
    cipher = SecretCipher.from_base64(SecretCipher.generate_key())
    a, b = make_device(cipher, "a", "1"), make_device(cipher, "b", "2")
    b.credentials_encrypted = a.credentials_encrypted  # copy ciphertext to another row
    with pytest.raises(Exception):
        b.get_credentials(cipher)


async def test_full_object_graph(session):
    cipher = SecretCipher.from_base64(SecretCipher.generate_key())
    device = make_device(cipher)
    tenant = Tenant(name="acme", quota_block_gb=1000, quota_file_gb=500, quota_object_gb=200)
    policy = ProtectionPolicy(
        name="gold", retention_days=30, worm_mode=WormMode.COMPLIANCE, snapshot_schedule="0 */4 * * *"
    )
    session.add_all([device, tenant, policy])
    await session.flush()
    session.add_all(
        [
            StorageVolume(
                name="lun1",
                wwn="6" + "a" * 31,
                size_gb=100,
                storage_device_id=device.id,
                tenant_id=tenant.id,
                protection_policy_id=policy.id,
            ),
            FileSystem(
                name="fs1",
                size_gb=50,
                protocol=FileProtocol.NFS,
                share_path="/fs1/",
                storage_device_id=device.id,
                tenant_id=tenant.id,
            ),
            ObjectBucket(
                bucket_name="acme-data",
                owner="acme",
                s3_endpoint="https://s3.example",
                storage_device_id=device.id,
                tenant_id=tenant.id,
            ),
            AuditLog(
                actor="alice",
                action="lun.create",
                resource_type="lun",
                resource_name="lun1",
                storage_device_id=device.id,
                tenant_id=tenant.id,
                details={"size_gb": 100},
            ),
        ]
    )
    await session.commit()
    vol = (await session.scalars(select(StorageVolume))).one()
    assert vol.provisioning is Provisioning.THIN and vol.mapping_status.value == "unmapped"
    log = (await session.scalars(select(AuditLog))).one()
    assert log.occurred_at is not None and log.outcome.value == "success"


async def test_constraints(session):
    cipher = SecretCipher.from_base64(SecretCipher.generate_key())
    device, tenant = make_device(cipher), Tenant(name="t")
    session.add_all([device, tenant])
    await session.flush()
    session.add(StorageVolume(name="bad", size_gb=0, storage_device_id=device.id, tenant_id=tenant.id))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(ProtectionPolicy(name="p", retention_days=0))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(StorageVolume(name="v", size_gb=1, storage_device_id=uuid.uuid4(), tenant_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_audit_log_survives_device_deletion(session):
    cipher = SecretCipher.from_base64(SecretCipher.generate_key())
    device = make_device(cipher)
    session.add(device)
    await session.flush()
    session.add(
        AuditLog(actor="bob", action="device.delete", resource_type="device", storage_device_id=device.id)
    )
    await session.commit()
    await session.delete(device)
    await session.commit()
    log = (await session.scalars(select(AuditLog))).one()
    assert log.storage_device_id is None


async def test_unique_names(session):
    session.add_all([Tenant(name="dup"), Tenant(name="dup")])
    with pytest.raises(IntegrityError):
        await session.flush()
