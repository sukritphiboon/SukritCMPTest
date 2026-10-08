import pytest

from tests.conftest import make_client


async def test_throughput_oriented_performance(op):
    for _ in range(20):
        p = await op.ok("GET", "/performancedata")
        assert 2_500 <= p["WRITE_THROUGHPUT_MBPS"] <= 8_000
        assert 400 <= p["READ_THROUGHPUT_MBPS"] <= 2_500
        assert 8_000 <= p["IOPS"] <= 40_000  # backup target: modest IOPS
        assert p["ACTIVE_STREAMS"] == sum(c["ACTIVESTREAMS"] for c in p["CONTROLLERS"])


async def test_performance_can_be_pinned(op):
    await op.http.post("/_mock/performance", json={"write_mbps": 5000, "read_mbps": 1000, "iops": 12345})
    for _ in range(3):
        p = await op.ok("GET", "/performancedata")
        assert (p["WRITE_THROUGHPUT_MBPS"], p["READ_THROUGHPUT_MBPS"], p["IOPS"]) == (5000, 1000, 12345)
    await op.http.post("/_mock/performance", json={})
    values = {(await op.ok("GET", "/performancedata"))["WRITE_THROUGHPUT_MBPS"] for _ in range(5)}
    assert len(values) > 1


async def test_hardware_inventory(op):
    controllers = await op.ok("GET", "/controller")
    assert [c["ID"] for c in controllers] == ["0A", "0B"]
    assert all(0 <= c["CPUUSAGE"] <= 100 and 0 <= c["MEMORYUSAGE"] <= 100 for c in controllers)
    nvram = await op.ok("GET", "/nvram")
    assert len(nvram) == 2 and all(0 <= n["DEDUPCACHEHITRATIO"] <= 100 for n in nvram)
    assert len(await op.ok("GET", "/power")) == 4
    assert len(await op.ok("GET", "/disk")) == 24
    everything = [*controllers, *nvram, *await op.ok("GET", "/power"), *await op.ok("GET", "/disk")]
    assert {h["HEALTHSTATUS"] for h in everything} == {"1"}


async def test_hardware_fault_injection(op):
    r = await op.http.post("/_mock/hardware_fault", json={"component": "disk", "id": 3, "health": "fault"})
    assert r.json()["HEALTHSTATUS"] == "2"
    await op.http.post(
        "/_mock/hardware_fault", json={"component": "power", "id": "PSU1", "health": "degraded"}
    )
    disks = {d["ID"]: d for d in await op.ok("GET", "/disk")}
    assert disks["3"]["HEALTHSTATUS"] == "2" and disks["4"]["HEALTHSTATUS"] == "1"
    assert (await op.ok("GET", "/power", params={"filter": "HEALTHSTATUS::3"}))[0]["ID"] == "PSU1"
    await op.http.post("/_mock/hardware_fault", json={"component": "disk", "id": 3, "health": "ok"})
    assert {d["HEALTHSTATUS"] for d in await op.ok("GET", "/disk")} == {"1"}


async def test_hardware_fault_validation(op):
    r = await op.http.post("/_mock/hardware_fault", json={"component": "disk", "id": 999})
    assert r.json()["error"]["code"] == 1077948996


async def test_alarms_have_three_severity_levels(op):
    seeded = await op.ok("GET", "/alarm/currentalarm")
    assert {a["levelName"] for a in seeded} == {"Warning", "Major"}
    for level in ("Critical", "Major", "Warning"):
        r = await op.http.post("/_mock/alarms", json={"level": level, "name": f"{level} alarm"})
        assert r.json()["levelName"] == level
    alarms = await op.ok("GET", "/alarm/currentalarm")
    assert [a["levelName"] for a in alarms].count("Major") == 2
    sequences = [a["sequence"] for a in alarms]
    assert len(set(sequences)) == len(sequences)
    await op.http.delete(f"/_mock/alarms/{sequences[0]}")
    assert len(await op.ok("GET", "/alarm/currentalarm")) == len(alarms) - 1


async def test_dedupe_cache_status_is_per_controller(op):
    nvram = {n["CONTROLLER"]: n for n in await op.ok("GET", "/nvram")}
    assert set(nvram) == {"0A", "0B"}


@pytest.mark.parametrize("seed", range(5))
async def test_hardware_values_deterministic_for_seed(seed):
    a, b = await make_client(seed), await make_client(seed)
    assert await a.ok("GET", "/controller") == await b.ok("GET", "/controller")
    for c in (a, b):
        await c.http.aclose()
