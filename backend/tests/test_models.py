import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto import SecretCipher
from app.models import (
    AlarmRecord,
    AlarmSeverity,
    ApplianceModel,
    AssetType,
    AuditLog,
    BackupJob,
    BackupPolicy,
    BackupTarget,
    BackupType,
    Base,
    CapacityMetric,
    JobStatus,
    ProtectedAsset,
    WormMode,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


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


def make_target(cipher, name="op-1", serial="SN1"):
    t = BackupTarget(name=name, ip_address="10.0.0.5", model=ApplianceModel.X8000, serial_number=serial)
    t.set_credentials(cipher, "admin", "P@ss")
    return t


def cipher():
    return SecretCipher.from_base64(SecretCipher.generate_key())


async def test_target_credentials_are_encrypted(session):
    c = cipher()
    session.add(make_target(c))
    await session.commit()
    stored = (await session.scalars(select(BackupTarget))).one()
    assert "P@ss" not in stored.credentials_encrypted
    assert stored.get_credentials(c) == ("admin", "P@ss")
    assert stored.management_port == 8088 and stored.health_status.value == "unknown"


async def test_credentials_are_bound_to_their_row(session):
    c = cipher()
    a, b = make_target(c, "a", "1"), make_target(c, "b", "2")
    b.credentials_encrypted = a.credentials_encrypted
    with pytest.raises(Exception):
        b.get_credentials(c)


async def test_full_domain_graph(session):
    t = make_target(cipher())
    policy = BackupPolicy(
        name="daily",
        cron_schedule="0 1 * * *",
        backup_type=BackupType.FULL,
        retention_days=30,
        worm_enabled=True,
        worm_mode=WormMode.COMPLIANCE,
    )
    session.add_all([t, policy])
    await session.flush()
    asset = ProtectedAsset(
        name="vm-01",
        asset_type=AssetType.VMWARE,
        source_ip="10.1.1.1",
        agent_version="1.6",
        backup_target_id=t.id,
        policy_id=policy.id,
    )
    session.add(asset)
    await session.flush()
    job = BackupJob(
        task_id="T000001",
        backup_target_id=t.id,
        asset_id=asset.id,
        asset_name=asset.name,
        policy_id=policy.id,
        backup_type=BackupType.FULL,
    )
    session.add(job)
    session.add(
        CapacityMetric(
            backup_target_id=t.id,
            timestamp=NOW,
            raw_capacity_gb=1000,
            used_physical_gb=400,
            logic_written_gb=10000,
            post_dedup_gb=1000,
            dedup_ratio=0.9,
            compression_ratio=2.5,
            reduction_ratio=25,
        )
    )
    await session.commit()
    saved = (await session.scalars(select(BackupJob))).one()
    assert saved.status is JobStatus.PENDING and saved.log_messages == [] and saved.data_transferred_gb == 0


async def test_job_status_lifecycle_values():
    assert not JobStatus.PENDING.is_final and not JobStatus.RUNNING.is_final
    for s in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.PARTIALLY_SUCCESSFUL, JobStatus.CANCELLED):
        assert s.is_final
    assert [s.value for s in JobStatus] == [
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "PARTIALLY_SUCCESSFUL",
        "CANCELLED",
    ]


async def test_policy_constraints(session):
    session.add(
        BackupPolicy(name="p0", cron_schedule="* * * * *", backup_type=BackupType.FULL, retention_days=0)
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
    # WORM switched on, but no mode chosen
    session.add(
        BackupPolicy(
            name="p1",
            cron_schedule="* * * * *",
            backup_type=BackupType.FULL,
            retention_days=5,
            worm_enabled=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
    session.add(
        BackupPolicy(
            name="ok", cron_schedule="* * * * *", backup_type=BackupType.INCREMENTAL, retention_days=5
        )
    )
    await session.commit()


async def test_job_history_outlives_asset_and_policy(session):
    t = make_target(cipher())
    policy = BackupPolicy(name="p", cron_schedule="* * * * *", backup_type=BackupType.FULL, retention_days=5)
    session.add_all([t, policy])
    await session.flush()
    asset = ProtectedAsset(name="a", asset_type=AssetType.LUN, backup_target_id=t.id)
    session.add(asset)
    await session.flush()
    session.add(
        BackupJob(
            task_id="T1",
            backup_target_id=t.id,
            asset_id=asset.id,
            asset_name="a",
            policy_id=policy.id,
            backup_type=BackupType.FULL,
        )
    )
    await session.commit()
    await session.delete(asset)
    await session.delete(policy)
    await session.commit()
    job = (await session.scalars(select(BackupJob))).one()
    assert job.asset_id is None and job.policy_id is None and job.asset_name == "a"


async def test_task_id_is_unique_per_target(session):
    c = cipher()
    t1, t2 = make_target(c, "a", "1"), make_target(c, "b", "2")
    session.add_all([t1, t2])
    await session.flush()
    session.add(BackupJob(task_id="T1", backup_target_id=t1.id, backup_type=BackupType.FULL))
    session.add(
        BackupJob(task_id="T1", backup_target_id=t2.id, backup_type=BackupType.FULL)
    )  # other target: fine
    await session.flush()
    session.add(BackupJob(task_id="T1", backup_target_id=t1.id, backup_type=BackupType.FULL))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_deleting_a_target_removes_its_readings_but_keeps_the_audit_trail(session):
    t = make_target(cipher())
    session.add(t)
    await session.flush()
    session.add_all(
        [
            CapacityMetric(
                backup_target_id=t.id,
                timestamp=NOW,
                raw_capacity_gb=1,
                used_physical_gb=1,
                logic_written_gb=1,
                post_dedup_gb=1,
                dedup_ratio=0,
                compression_ratio=1,
                reduction_ratio=1,
            ),
            AlarmRecord(
                backup_target_id=t.id,
                sequence="1",
                event_id="e",
                name="n",
                severity=AlarmSeverity.MAJOR,
                raised_at=NOW,
                first_seen_at=NOW,
                last_seen_at=NOW,
            ),
            AuditLog(actor="bob", action="target.delete", resource_type="target", backup_target_id=t.id),
        ]
    )
    await session.commit()
    await session.delete(t)
    await session.commit()
    assert (await session.scalars(select(CapacityMetric))).all() == []
    assert (await session.scalars(select(AlarmRecord))).all() == []
    log = (await session.scalars(select(AuditLog))).one()
    assert log.backup_target_id is None and log.outcome.value == "success" and log.occurred_at is not None


async def test_alarm_identity_is_unique(session):
    t = make_target(cipher())
    session.add(t)
    await session.flush()

    def alarm():
        return AlarmRecord(
            backup_target_id=t.id,
            sequence="1",
            event_id="e",
            name="n",
            severity=AlarmSeverity.WARNING,
            raised_at=NOW,
            first_seen_at=NOW,
            last_seen_at=NOW,
        )

    session.add(alarm())
    await session.flush()
    session.add(alarm())
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_foreign_keys_are_enforced_and_names_unique(session):
    session.add(BackupJob(task_id="T", backup_target_id=uuid.uuid4(), backup_type=BackupType.FULL))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
    c = cipher()
    session.add_all([make_target(c, "same", "1"), make_target(c, "same", "2")])
    with pytest.raises(IntegrityError):
        await session.flush()
