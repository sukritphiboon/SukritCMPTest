from mock_server.profiles import SECTORS_PER_GB
from tests.conftest import make_client


async def test_dorado_performance_is_high_iops_sub_ms(dorado):
    for _ in range(20):
        perf = await dorado.ok("GET", "/performance_statistic/cur_statistic_data")
        assert perf["IOPS"] >= 300_000
        assert perf["LATENCY_US"] < 1000
        assert perf["READ_IOPS"] + perf["WRITE_IOPS"] == perf["IOPS"]


async def test_oceanprotect_performance_is_throughput_oriented(protect, dorado):
    perf = await protect.ok("GET", "/performance_statistic/cur_statistic_data")
    ref = await dorado.ok("GET", "/performance_statistic/cur_statistic_data")
    assert perf["BANDWIDTH_MBPS"] >= 6_000
    assert perf["IOPS"] < ref["IOPS"]


async def test_dorado_pool_smart_features(dorado):
    pool = (await dorado.ok("GET", "/storagepool"))[0]
    for key in ("SMARTTHIN_RATIO", "SMARTDEDUPE_RATIO", "SMARTCOMPRESSION_RATIO"):
        assert float(pool[key]) > 1.0
    assert 3.0 <= float(pool["DATAREDUCTION_RATIO"]) <= 12.0


async def test_oceanprotect_reduction_ratio_20_to_40():
    for seed in range(25):
        c = await make_client("oceanprotect", seed)
        pool = (await c.ok("GET", "/storagepool"))[0]
        assert 20.0 <= float(pool["DATAREDUCTION_RATIO"]) <= 42.0
        await c.http.aclose()


async def test_dorado_latency_always_sub_ms_across_seeds():
    for seed in range(10):
        c = await make_client("dorado", seed)
        perf = await c.ok("GET", "/performance_statistic/cur_statistic_data")
        assert perf["LATENCY_US"] < 1000
        await c.http.aclose()


async def test_capacity_accounting_tracks_thin_allocation(dorado):
    before = (await dorado.ok("GET", "/storagepool"))[0]
    await dorado.ok("POST", "/lun", {"NAME": "t", "CAPACITY": str(1000 * SECTORS_PER_GB), "ALLOCTYPE": "0"})
    after = (await dorado.ok("GET", "/storagepool"))[0]
    assert int(after["USERCONSUMEDCAPACITY"]) > int(before["USERCONSUMEDCAPACITY"])
    assert int(after["PROVISIONEDCAPACITY"]) == 1000 * SECTORS_PER_GB
    total = int(after["USERTOTALCAPACITY"])
    assert int(after["USERFREECAPACITY"]) + int(after["USERCONSUMEDCAPACITY"]) == total


async def test_alarms_and_injection(dorado):
    alarms = await dorado.ok("GET", "/alarm/currentalarm")
    assert len(alarms) == 1
    await dorado.http.post("/_mock/alarms", json={"level": "Critical", "name": "Disk failed"})
    alarms = await dorado.ok("GET", "/alarm/currentalarm")
    assert {a["levelName"] for a in alarms} == {"Medium", "Critical"}
