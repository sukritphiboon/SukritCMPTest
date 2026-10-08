import pytest

from tests.api.conftest import GB


async def poll_days(env, days, name="op-1"):
    """One poll per day for ``days`` days, with the mock appliance ageing in step."""
    for _ in range(days):
        await env.collect()
        env.tick(86400)
    await env.collect()


async def test_runway_from_steady_growth(env):
    await env.add_target(daily_growth_gb=800)
    await poll_days(env, 8)
    ov = await env.ok("GET", "/oceanprotect/overview")
    runway = ov["runway"]
    assert runway["status"] == "ok" and runway["window_days"] == 7
    assert runway["avg_daily_growth_gb"] == pytest.approx(800, rel=0.02)
    free = 500 * 1024 - env.mock().pool_numbers()["physical"] / GB
    assert runway["free_gb"] == pytest.approx(free, rel=1e-3)
    assert runway["days_until_full"] == pytest.approx(free / 800, rel=0.02)
    assert runway["target_name"] == "op-1" and ov["targets"][0]["runway"]["status"] == "ok"


async def test_runway_follows_recent_growth_only(env):
    await env.add_target(daily_growth_gb=800)
    await poll_days(env, 3)  # 800 GB/day
    env.mock().daily_growth_gb = 100  # the workload calms down; the mock only counts from now on
    base_days = env.mock().elapsed_days()
    env.mock().extra_physical_bytes += int(800 * GB * base_days) - int(100 * GB * base_days)  # keep the level
    await poll_days(env, 8)
    runway = (await env.ok("GET", "/oceanprotect/overview"))["runway"]
    assert runway["avg_daily_growth_gb"] == pytest.approx(100, rel=0.05)


async def test_no_growth_is_reported_as_not_growing(env):
    await env.add_target(daily_growth_gb=0)
    await poll_days(env, 3)
    runway = (await env.ok("GET", "/oceanprotect/overview"))["runway"]
    assert runway["status"] == "not_growing" and runway["days_until_full"] is None
    assert runway["avg_daily_growth_gb"] == pytest.approx(0, abs=0.5)


async def test_full_pool(env):
    await env.add_target(raw_tb=1, initial_used_percent=99, daily_growth_gb=5000)
    await poll_days(env, 3)
    runway = (await env.ok("GET", "/oceanprotect/overview"))["runway"]
    assert runway["status"] == "full" and runway["days_until_full"] == 0


async def test_overall_runway_is_that_of_the_pool_that_fills_first(env):
    await env.add_target("slow", seed=1, daily_growth_gb=100)
    await env.add_target("fast", seed=2, daily_growth_gb=2000)
    for _ in range(4):
        await env.collect()
        env.tick(86400)
    await env.collect()
    ov = await env.ok("GET", "/oceanprotect/overview")
    per = {t["name"]: t["runway"]["days_until_full"] for t in ov["targets"]}
    assert per["fast"] < per["slow"]
    assert ov["runway"]["target_name"] == "fast" and ov["runway"]["days_until_full"] == per["fast"]


async def test_not_enough_history_yet(env):
    await env.add_target()
    for _ in range(5):  # many polls, but all on the same day
        await env.collect()
        env.tick(30)
    assert (await env.ok("GET", "/oceanprotect/overview"))["runway"]["status"] == "insufficient_data"
