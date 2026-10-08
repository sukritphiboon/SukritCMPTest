"""The driver must read answers exactly as printed in the DeviceManager REST reference
(OceanStor Dorado V700R001C20, issue 02, 2026-03-30). These payloads are copied from its examples, so the
tests do not depend on the mock server's own idea of the format."""

import json

import httpx
import pytest

from app.drivers import DeviceConnection, OceanProtectDriver
from app.drivers.errors import AuthenticationError
from app.models import AlarmSeverity, HardwareHealth

LOGIN = {
    "data": {
        "accountstate": 1,
        "deviceid": "2102351LWK10J8XXXXXX",
        "iBaseToken": "B440C36C8C56433CE44B58EC91",
        "lastloginip": "192.168.1.3",
        "lastlogintime": 1699475616,
        "preferenceMode": 0,
        "pwdchangetime": 1699420597,
        "roleId": "1",
        "usergroup": "",
        "userid": "admin",
        "username": "admin",
        "userscope": "0",
    },
    "error": {"code": 0, "description": "0"},
}
ALARM = {  # "Querying Current Alarm Information", example response
    "alarmObjType": 244,
    "alarmOid": "1.3.6.1.4.1.2011.2.251.20.1.2.2.15.244.2.1",
    "alarmStatus": 1,
    "clearName": 0,
    "clearTime": 0,
    "confirmTime": 0,
    "description": "The license feature (SmartDedupe(for FS)) has expired.",
    "detail": "...",
    "eventID": 4039704578,
    "eventParam": "51,2016-01-10",
    "level": 5,
    "location": "param1=admin,param2= 10.0.0.1",
    "name": "License Has Expired",
    "position": 0,
    "recoverTime": 0,
    "room": "",
    "sequence": 17333,
    "sourceID": "",
    "sourceType": "",
    "startTime": 1450588315,
    "strEventID": "0xF0C90002",
    "suggestion": "Purchase and import required license files.",
    "type": 1,
}
POOL = {  # "Batch Querying Storage Pool Information", example response (an empty pool); trimmed to the relevant keys
    "ID": "0",
    "NAME": "StoragePool001",
    "HEALTHSTATUS": "1",
    "RUNNINGSTATUS": "27",
    "TYPE": 216,
    "USERTOTALCAPACITY": "3180574720",
    "USERFREECAPACITY": "3180574720",
    "USERCONSUMEDCAPACITY": "0",
    "USERCONSUMEDCAPACITYPERCENTAGE": "0",
    "COMPRESSEDCAPACITY": "0",
    "COMPRESSINVOLVEDCAPACITY": "0",
    "DEDUPEDCAPACITY": "0",
    "DEDUPINVOLVEDCAPACITY": "0",
    "REDUCTIONINVOLVEDCAPACITY": "0",
    "COMPRESSIONRATE": '{"numerator":"10", "denominator":"10","logic":"="}',
    "DEDUPLICATIONRATE": '{"numerator":"10", "denominator":"10","logic":"="}',
    "SPACEREDUCTIONRATE": '{"numerator":"1000", "denominator":"1000","logic":"="}',
}
CONTROLLERS = [  # strings, as the reference types them (string(uint32))
    {
        "ID": "0A",
        "NAME": "CTE0.A",
        "HEALTHSTATUS": "1",
        "RUNNINGSTATUS": "1",
        "CPUUSAGE": "12",
        "MEMORYUSAGE": "45",
    },
    {
        "ID": "0B",
        "NAME": "CTE0.B",
        "HEALTHSTATUS": "2",
        "RUNNINGSTATUS": "1",
        "CPUUSAGE": "7",
        "MEMORYUSAGE": "30",
    },
]


def appliance(routes: dict[str, object], login=LOGIN):
    """A fake appliance that behaves like the reference: the session cookie and iBaseToken are required."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/sessions") and request.method == "POST":
            body = json.loads(request.content)
            assert body["scope"] == "0" and {"username", "password"} <= set(body)  # scope is mandatory
            return httpx.Response(
                200,
                json=login,
                headers={"Set-Cookie": "session=ismsession=BA37F1E1C2C8996D;SameSite=Lax;path=/;httponly"},
            )
        if path.endswith("/sessions") and request.method == "DELETE":
            return httpx.Response(200, json={"error": {"code": 0, "description": "0"}})
        if request.headers.get("iBaseToken") != login["data"][
            "iBaseToken"
        ] or "ismsession=BA37" not in request.headers.get("cookie", ""):
            return httpx.Response(200, json={"error": {"code": -401, "description": "Unauthorized"}})
        assert (
            f"/deviceManager/rest/{login['data']['deviceid']}/" in path
        )  # the deviceid from the login is used
        for suffix, data in routes.items():
            if path.endswith(suffix):
                return httpx.Response(200, json={"data": data, "error": {"code": 0, "description": "0"}})
        return httpx.Response(200, json={"error": {"code": 0, "description": "0"}})  # empty list: no "data"

    return httpx.MockTransport(handle), seen


def driver_for(routes):
    transport, seen = appliance(routes)
    drv = OceanProtectDriver(DeviceConnection("array", https=False, password="x"), transport=transport)
    return drv, seen


async def test_login_stores_deviceid_token_and_sends_the_cookie_back():
    drv, seen = driver_for({"/alarm/currentalarm": []})
    await drv.connect()
    assert drv.client.device_id == "2102351LWK10J8XXXXXX" and drv.client.token == "B440C36C8C56433CE44B58EC91"
    assert await drv.get_active_alarms() == []  # would be -401 without cookie and token
    last = seen[-1]
    assert (
        last.headers["iBaseToken"] == drv.client.token
        and "ismsession=BA37F1E1C2C8996D" in last.headers["cookie"]
    )
    await drv.close()
    assert seen[-1].method == "DELETE" and seen[-1].url.path.endswith("/sessions")


async def test_error_minus_401_means_authentication_failed():
    transport, _ = appliance({}, login={**LOGIN, "data": {**LOGIN["data"], "iBaseToken": "other"}})

    def stale(request):  # the appliance forgot the session right after login
        if request.url.path.endswith("/sessions"):
            return transport.handler(request)
        return httpx.Response(200, json={"error": {"code": -401, "description": "Unauthorized"}})

    drv = OceanProtectDriver(
        DeviceConnection("array", https=False, password="x"), transport=httpx.MockTransport(stale)
    )
    await drv.connect()
    with pytest.raises(AuthenticationError):
        await drv.get_pool_metrics()  # one transparent re-login, then the error is reported


async def test_alarm_from_the_reference_example():
    drv, _ = driver_for({"/alarm/currentalarm": [ALARM]})
    await drv.connect()
    [a] = await drv.get_active_alarms()
    assert a.severity == AlarmSeverity.MAJOR  # level 5
    assert a.sequence == "17333" and a.event_id == "0xF0C90002" and a.name == "License Has Expired"
    assert a.raised_at.timestamp() == 1450588315


@pytest.mark.parametrize(
    "level,expected", [(3, AlarmSeverity.WARNING), (5, AlarmSeverity.MAJOR), (6, AlarmSeverity.CRITICAL)]
)
async def test_alarm_levels_follow_the_reference(level, expected):
    drv, _ = driver_for({"/alarm/currentalarm": [{**ALARM, "level": level}]})
    await drv.connect()
    assert (await drv.get_active_alarms())[0].severity == expected


async def test_empty_pool_from_the_reference_example():
    drv, _ = driver_for({"/storagepool": [POOL]})
    await drv.connect()
    pool = await drv.get_pool_metrics()
    assert (
        pool.raw_capacity_gb == pytest.approx(3180574720 * 512 / 1024**3)
        and pool.free_gb == pool.raw_capacity_gb
    )
    assert (
        pool.used_physical_gb == 0 and pool.reduction_ratio == pool.dedup_ratio == pool.compression_ratio == 0
    )


async def test_pool_reduction_uses_the_documented_capacity_fields():
    sectors = lambda gb: str(int(gb * 1024**3 // 512))  # noqa: E731
    busy = {  # 2500 GB went into deduplication, 2250 GB were saved, 150 GB were saved again by compression
        **POOL,
        "USERCONSUMEDCAPACITY": sectors(100),
        "DEDUPINVOLVEDCAPACITY": sectors(2500),
        "DEDUPEDCAPACITY": sectors(2250),
        "COMPRESSINVOLVEDCAPACITY": sectors(250),
        "COMPRESSEDCAPACITY": sectors(150),
    }
    drv, _ = driver_for({"/storagepool": [busy]})
    await drv.connect()
    pool = await drv.get_pool_metrics()
    assert pool.logic_written_gb == pytest.approx(2500) and pool.post_dedup_gb == pytest.approx(250)
    assert pool.used_physical_gb == pytest.approx(100)
    assert pool.reduction_ratio == pytest.approx(25.0) and pool.dedup_ratio == pytest.approx(0.9)
    assert pool.dedup_factor == pytest.approx(10.0) and pool.compression_ratio == pytest.approx(2.5)


async def test_controllers_with_string_numbers_and_a_faulty_one():
    drv, _ = driver_for({"/controller": CONTROLLERS, "/nvram": [], "/power": [], "/disk": []})
    await drv.connect()
    hw = await drv.get_hardware_status()
    assert [c.cpu_percent for c in hw.controllers] == [12, 7] and [
        c.memory_percent for c in hw.controllers
    ] == [45, 30]
    assert [c.status for c in hw.controllers] == [
        HardwareHealth.OK,
        HardwareHealth.FAULT,
    ]  # 1 normal, 2 faulty
    assert hw.overall == HardwareHealth.FAULT
