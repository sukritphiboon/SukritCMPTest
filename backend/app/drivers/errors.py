class StorageDriverError(Exception):
    """Base class for all driver errors."""


class NotSupportedError(StorageDriverError):
    """The storage product does not offer this operation."""


class DeviceError(StorageDriverError):
    """The array answered with a non-zero error code."""

    def __init__(self, code: int, description: str):
        super().__init__(f"[{code}] {description}")
        self.code = code
        self.description = description


class AuthenticationError(DeviceError):
    pass


class ResourceNotFoundError(DeviceError):
    pass


class ResourceExistsError(DeviceError):
    pass


class InsufficientSpaceError(DeviceError):
    pass


class ResourceBusyError(DeviceError):
    pass


class ComplianceLockError(DeviceError):
    """Operation refused because a WORM / retention lock is active."""


class ConnectionFailedError(StorageDriverError):
    pass
