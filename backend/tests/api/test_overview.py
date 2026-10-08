import pytest

from tests.api.conftest import GB


def physical_gb(env, name):
    return env.mock(name).pool_numbers()["physical"] / GB


async def test_overview_matches_the_mock_for_one_appliance(env):
    await env.add_target()
    env.pin(write=4000, read=900, iops=20000, streams=30)
    await env.collect()
    ov = await env.ok("GET", "/oceanprotect/overview")

    assert ov["targets_total"] == 1 and ov["targets_reporting"] == 1
    assert ov["ingestion"] == {
        "write_mb_s": 4000,
        "read_mb_s": 900,
        "total_mb_s": 4900,
        "write_gb_per_hour": pytest.approx(4000 * 3600 / 1024, rel=1e-3),
        "iops": 20000,
        "active_streams": 60,
    }
    m, n = env.mock(), env.mock().pool_numbers()
    red = ov["reduction"]
    assert red["reduction_ratio"] == pytest.approx(m.total_ratio, rel=1e-2)
    assert red["dedup_factor"] == pytest.approx(m.dedupe_x, rel=1e-2)
    assert red["compression_ratio"] == pytest.approx(m.compression_x, rel=1e-2)
    assert red["dedup_ratio"] == pytest.approx(1 - 1 / m.dedupe_x, abs=1e-3)
    assert red["logical_written_gb"] == pytest.approx(n["ingested"] / GB, abs=0.1)
    assert red["used_physical_gb"] == pytest.approx(n["physical"] / GB, abs=0.1)
    assert red["raw_capacity_gb"] == pytest.approx(500 * 1024)
    assert red["free_gb"] == pytest.approx(red["raw_capacity_gb"] - red["used_physical_gb"], abs=0.2)
    assert 20 <= red["reduction_ratio"] <= 42

    assert ov["alarms"] == {"critical": 0, "major": 1, "warning": 1, "total": 2, "unacknowledged": 2}
    assert ov["hardware_status"] == "ok"
    assert ov["runway"]["status"] == "insufficient_data"  # a single day of data cannot show a trend
    [t] = ov["targets"]
    assert t["name"] == "op-1" and not t["stale"] and t["data_age_seconds"] == 0
    assert t["ingestion"]["total_mb_s"] == 4900 and t["health_status"] == "degraded"


async def test_overview_combines_several_appliances(env):
    a = await env.add_target("a", seed=1)
    b = await env.add_target("b", seed=2)
    env.pin("a", write=4000, read=900, iops=20000, streams=30)
    env.pin("b", write=2500, read=500, iops=9000, streams=10)
    env.mock("b").add_alarm("Critical", "Disk enclosure offline", "0xF00CF0400")
    env.mock("b").set_health("disk", "2", "fault")
    await env.collect()
    ov = await env.ok("GET", "/oceanprotect/overview")

    assert ov["targets_total"] == ov["targets_reporting"] == 2
    assert ov["ingestion"]["write_mb_s"] == 6500 and ov["ingestion"]["read_mb_s"] == 1400
    assert ov["ingestion"]["total_mb_s"] == 7900 and ov["ingestion"]["iops"] == 29000
    assert ov["ingestion"]["active_streams"] == 60 + 20

    na, nb = env.mock("a").pool_numbers(), env.mock("b").pool_numbers()
    logical, physical = na["ingested"] + nb["ingested"], na["physical"] + nb["physical"]
    assert ov["reduction"]["reduction_ratio"] == pytest.approx(
        logical / physical, rel=1e-2
    )  # not the mean of ratios
    assert ov["reduction"]["used_physical_gb"] == pytest.approx(physical / GB, abs=0.2)
    assert ov["reduction"]["raw_capacity_gb"] == pytest.approx(2 * 500 * 1024)

    assert ov["alarms"] == {"critical": 1, "major": 2, "warning": 2, "total": 5, "unacknowledged": 5}
    assert ov["hardware_status"] == "fault"
    by_name = {t["name"]: t for t in ov["targets"]}
    assert by_name["a"]["alarms"]["total"] == 2 and by_name["b"]["alarms"]["critical"] == 1
    assert by_name["b"]["hardware_status"] == "fault" and by_name["b"]["health_status"] == "fault"
    assert a["id"] and b["id"]


async def test_overview_can_be_limited_to_one_appliance(env):
    await env.add_target("a", seed=1)
    b = await env.add_target("b", seed=2)
    env.pin("a", write=1000, read=100)
    env.pin("b", write=3000, read=300)
    await env.collect()
    one = await env.ok("GET", "/oceanprotect/overview", params={"target_id": b["id"]})
    assert one["targets_total"] == 1 and one["ingestion"]["write_mb_s"] == 3000
    assert one["reduction"]["used_physical_gb"] == pytest.approx(physical_gb(env, "b"), abs=0.2)
    await env.err(
        "GET", "/oceanprotect/overview", 404, params={"target_id": "00000000-0000-0000-0000-000000000000"}
    )


async def test_stale_data_is_flagged_and_not_counted_as_ingestion(env):
    await env.add_target()
    env.pin()
    await env.collect()
    env.tick(60)  # two missed polls: still fresh (limit 3 x 30 s)
    ov = await env.ok("GET", "/oceanprotect/overview")
    assert ov["targets_reporting"] == 1 and ov["ingestion"]["total_mb_s"] == 4900
    env.tick(60)  # 120 s without data
    ov = await env.ok("GET", "/oceanprotect/overview")
    assert ov["targets_reporting"] == 0 and ov["ingestion"]["total_mb_s"] == 0
    assert ov["targets"][0]["stale"] and ov["targets"][0]["data_age_seconds"] == 120
    assert (
        ov["reduction"]["reduction_ratio"] > 20
    )  # capacity figures change slowly, so the last ones stay visible


async def test_appliance_without_data(env):
    await env.add_target()
    ov = await env.ok("GET", "/oceanprotect/overview")
    assert ov["targets_total"] == 1 and ov["targets_reporting"] == 0
    assert ov["reduction"]["reduction_ratio"] is None and ov["runway"]["status"] == "insufficient_data"
    assert ov["alarms"]["total"] == 0 and ov["hardware_status"] is None
    t = ov["targets"][0]
    assert t["stale"] and t["data_age_seconds"] is None and t["ingestion"] is None and t["runway"] is None


async def test_no_appliances_at_all(env):
    ov = await env.ok("GET", "/oceanprotect/overview")
    assert ov["targets_total"] == 0 and ov["targets"] == [] and ov["ingestion"]["total_mb_s"] == 0
    assert ov["runway"]["status"] == "insufficient_data"
