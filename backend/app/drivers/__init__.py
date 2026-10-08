from .base import BackupDriverBase
from .errors import NotSupportedError, StorageDriverError
from .huawei_client import DeviceConnection
from .oceanprotect import OceanProtectDriver

__all__ = [
    "BackupDriverBase",
    "DeviceConnection",
    "NotSupportedError",
    "OceanProtectDriver",
    "StorageDriverError",
    "create_driver",
]


def create_driver(model: str, connection: DeviceConnection) -> BackupDriverBase:
    """Factory used by services. Every supported OceanProtect model speaks the same dialect."""
    if str(model) in ("x3000", "x6000", "x8000", "x9000"):
        return OceanProtectDriver(connection)
    raise ValueError(f"No driver for model {model!r}")
