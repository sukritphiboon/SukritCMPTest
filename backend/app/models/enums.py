import enum


class ApplianceModel(enum.StrEnum):
    X3000 = "x3000"
    X6000 = "x6000"
    X8000 = "x8000"
    X9000 = "x9000"


class HealthStatus(enum.StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAULT = "fault"


class BackupType(enum.StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class WormMode(enum.StrEnum):
    NONE = "none"
    ENTERPRISE = "enterprise"
    COMPLIANCE = "compliance"


class AssetType(enum.StrEnum):
    VMWARE = "vmware"
    DATABASE = "database"
    FILE_SHARE = "file_share"
    LUN = "lun"


class JobStatus(enum.StrEnum):
    """PENDING -> RUNNING -> SUCCESS | FAILED | PARTIALLY_SUCCESSFUL | CANCELLED"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIALLY_SUCCESSFUL = "PARTIALLY_SUCCESSFUL"
    CANCELLED = "CANCELLED"

    @property
    def is_final(self) -> bool:
        return self not in (JobStatus.PENDING, JobStatus.RUNNING)


class AlarmSeverity(enum.StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    WARNING = "warning"


class HardwareHealth(enum.StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAULT = "fault"


class AuditOutcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
