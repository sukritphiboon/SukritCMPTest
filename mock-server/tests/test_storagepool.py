import pytest

from mock_server.profiles import GB, SECTOR
from tests.conftest import make_client


def gb(sectors) -> float:
    return int(sectors) * SECTOR / GB


async def test_pool_exposes_the_four_capacity_figures(op):
    pool = (await op.ok("GET", "/storagepool"))[0]
    raw, used, free = (
        gb(pool["USERTOTALCAPACITY"]),
        gb(pool["USERCONSUMEDCAPACITY"]),
        gb(pool["USERFREECAPACITY"]),
    )
    assert raw == pytest.approx(500 * 1024)  # 500 TB
    assert used == pytest.approx(0.4 * raw, rel=1e-3)  # starts 40 % full
    assert used + free == pytest.approx(raw, abs=0.01)
    assert gb(pool["LOGICALWRITTENCAPACITY"]) > used * 20


async def test_reduction_formulas_hold(op):
    """total = ingested/physical, dedup = (ingested-post)/ingested, compression = post/physical."""
    pool = (await op.ok("GET", "/storagepool"))[0]
    ingested, post, phys = (
        int(pool[k]) for k in ("LOGICALWRITTENCAPACITY", "POSTDEDUPCAPACITY", "USERCONSUMEDCAPACITY")
    )
    assert ingested / phys == pytest.approx(float(pool["DATAREDUCTION_RATIO"]), rel=1e-3)
    assert (ingested - post) / ingested == pytest.approx(float(pool["DEDUPRATIO"]), abs=1e-4)
    assert post / phys == pytest.approx(float(pool["COMPRESSIONRATIO"]), rel=1e-3)
    assert float(pool["DEDUPFACTOR"]) * float(pool["COMPRESSIONRATIO"]) == pytest.approx(
        float(pool["DATAREDUCTION_RATIO"]), rel=1e-3
    )


async def test_total_ratio_is_20_to_42_for_every_seed():
    for seed in range(30):
        c = await make_client(seed)
        pool = (await c.ok("GET", "/storagepool"))[0]
        assert 20.0 <= float(pool["DATAREDUCTION_RATIO"]) <= 42.0
        assert 0.85 <= float(pool["DEDUPRATIO"]) <= 0.92
        await c.http.aclose()


async def test_physical_space_grows_with_time():
    c = await make_client(1, daily_growth_gb=1000)
    before = int((await c.ok("GET", "/storagepool"))[0]["USERCONSUMEDCAPACITY"])
    c.mock.advance_time(10 * 86400)
    c.mock.sessions[c.token] = c.mock.now() + 100  # keep the session alive
    after = int((await c.ok("GET", "/storagepool"))[0]["USERCONSUMEDCAPACITY"])
    assert gb(after - before) == pytest.approx(10 * 1000, rel=1e-3)
    await c.http.aclose()


async def test_manual_ingest_adds_physical_space_at_the_current_ratio(op):
    before = (await op.ok("GET", "/storagepool"))[0]
    await op.http.post("/_mock/ingest", json={"logical_gb": 10_000})
    after = (await op.ok("GET", "/storagepool"))[0]
    grown_physical = gb(int(after["USERCONSUMEDCAPACITY"]) - int(before["USERCONSUMEDCAPACITY"]))
    assert grown_physical == pytest.approx(10_000 / op.mock.total_ratio, rel=1e-2)


async def test_pool_never_exceeds_raw_capacity():
    c = await make_client(1, raw_tb=1, initial_used_percent=99, daily_growth_gb=5000)
    c.mock.advance_time(30 * 86400)
    c.mock.sessions[c.token] = c.mock.now() + 100
    pool = (await c.ok("GET", "/storagepool"))[0]
    assert int(pool["USERFREECAPACITY"]) == 0
    await c.http.aclose()


async def test_zero_growth_is_honoured():
    c = await make_client(1, daily_growth_gb=0)
    before = int((await c.ok("GET", "/storagepool"))[0]["USERCONSUMEDCAPACITY"])
    c.mock.advance_time(5 * 86400)
    c.mock.sessions[c.token] = c.mock.now() + 100
    assert int((await c.ok("GET", "/storagepool"))[0]["USERCONSUMEDCAPACITY"]) == before
    await c.http.aclose()
