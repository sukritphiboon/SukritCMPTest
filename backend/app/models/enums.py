import enum


class DeviceModel(enum.StrEnum):
    DORADO_V7 = "dorado_v7"
    OCEANPROTECT = "oceanprotect"


class HealthStatus(enum.StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAULT = "fault"


class Provisioning(enum.StrEnum):
    THIN = "thin"
    THICK = "thick"


class MappingStatus(enum.StrEnum):
    UNMAPPED = "unmapped"
    MAPPED = "mapped"


class FileProtocol(enum.StrEnum):
    NFS = "nfs"
    CIFS = "cifs"


class QuotaStatus(enum.StrEnum):
    NONE = "none"
    WITHIN_LIMIT = "within_limit"
    SOFT_EXCEEDED = "soft_exceeded"
    HARD_EXCEEDED = "hard_exceeded"


class WormMode(enum.StrEnum):
    NONE = "none"
    ENTERPRISE = "enterprise"
    COMPLIANCE = "compliance"


class AuditOutcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
