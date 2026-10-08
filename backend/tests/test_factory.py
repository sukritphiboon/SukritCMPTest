import pytest

from app.drivers import DeviceConnection, DoradoV7Driver, OceanProtectDriver, StorageDriverBase, create_driver


def test_factory_picks_driver_by_model():
    conn = DeviceConnection("10.0.0.1")
    assert isinstance(create_driver("dorado_v7", conn), DoradoV7Driver)
    assert isinstance(create_driver("oceanprotect", conn), OceanProtectDriver)
    assert issubclass(DoradoV7Driver, StorageDriverBase)
    with pytest.raises(ValueError):
        create_driver("netapp", conn)


def test_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        StorageDriverBase()
