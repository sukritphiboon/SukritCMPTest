from .base import StorageDriverBase
from .dorado_v7 import DoradoV7Driver
from .errors import NotSupportedError, StorageDriverError
from .huawei_client import DeviceConnection
from .oceanprotect import OceanProtectDriver

__all__ = [
    "DeviceConnection",
    "DoradoV7Driver",
    "NotSupportedError",
    "OceanProtectDriver",
    "StorageDriverBase",
    "StorageDriverError",
    "create_driver",
]


def create_driver(model: str, connection: DeviceConnection) -> StorageDriverBase:
    """Factory used by services: pick the driver from ``StorageDevice.model``."""
    drivers = {"dorado_v7": DoradoV7Driver, "oceanprotect": OceanProtectDriver}
    try:
        return drivers[str(model)](connection)
    except KeyError:
        raise ValueError(f"No driver for model {model!r}") from None
