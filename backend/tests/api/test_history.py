from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import BackupTarget, CapacityMetric, ThroughputSample
from tests.api.conftest import T0


async def target_id(env, name="op-1"):
    async with env.maker() as s:
        return (await s.scalars(select(BackupTarget.id).where(BackupTarget.name == name))).one()


async def add_samples(env, tid, start, count, step_s, write=lambda i: 1000.0 + i, read=lambda i: 100.0):
    async with env.maker() as s:
        s.add_all(
            ThroughputSample(
                backup_target_id=tid,
                timestamp=start + timedelta(seconds=i * step_s),
                write_throughput_mb_s=write(i),
                read_throughput_mb_s=read(i),
            )
            for i in range(count)
        )
        await s.commit()


async def test_one_hour_window_has_one_minute_buckets(env):
    await env.add_target()
    tid = await target_id(env)
    start = T0 - timedelta(hours=2)
    await add_samples(env, tid, start, 240, 30)  # two hours of 30 s samples, ending at 11:59:30
    h = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "1h"})
    assert h["window"] == "1h" and h["bucket_seconds"] == 60
    assert len(h["points"]) == 60
    assert all(p["samples"] == 2 for p in h["points"])
    first = h["points"][0]  # samples 120 and 121 -> 1120 and 1121
    assert first["write_mb_s_avg"] == pytest.approx(1120.5) and first["write_mb_s_peak"] == 1121
    assert first["read_mb_s_avg"] == 100 and first["total_mb_s_avg"] == pytest.approx(1220.5)
    assert first["timestamp"].startswith("2026-10-01T11:00:00")
    stamps = [p["timestamp"] for p in h["points"]]
    assert stamps == sorted(stamps)


async def test_default_window_is_one_hour(env):
    await env.add_target()
    assert (await env.ok("GET", "/oceanprotect/throughput/history"))["window"] == "1h"


async def test_longer_windows_use_coarser_buckets(env):
    await env.add_target()
    tid = await target_id(env)
    await add_samples(env, tid, T0 - timedelta(days=8), 8 * 288, 300)  # eight days, one sample per 5 minutes
    day = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "24h"})
    assert day["bucket_seconds"] == 900 and len(day["points"]) == 96
    assert all(p["samples"] == 3 for p in day["points"])
    week = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "7d"})
    assert week["bucket_seconds"] == 3600 and len(week["points"]) == 168
    assert all(p["samples"] == 12 for p in week["points"])


async def test_window_boundaries_exclude_older_data(env):
    await env.add_target()
    tid = await target_id(env)
    await add_samples(env, tid, T0 - timedelta(hours=3), 1, 30, write=lambda i: 999.0)  # 3 h old
    await add_samples(env, tid, T0 - timedelta(minutes=10), 1, 30, write=lambda i: 2000.0)
    one = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "1h"})
    assert [p["write_mb_s_avg"] for p in one["points"]] == [2000]
    day = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "24h"})
    assert sorted(p["write_mb_s_avg"] for p in day["points"]) == [999, 2000]


async def test_invalid_window_is_rejected(env):
    for bad in ("5m", "30d", "", "1H"):
        await env.err("GET", "/oceanprotect/throughput/history", 422, params={"window": bad})


async def test_no_samples_gives_an_empty_series_not_zeros(env):
    await env.add_target()
    h = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "24h"})
    assert h["points"] == []
    assert (await env.ok("GET", "/oceanprotect/throughput/history"))["points"] == []  # target-less call too


async def test_several_appliances_are_summed_per_bucket(env):
    await env.add_target("a", seed=1)
    await env.add_target("b", seed=2)
    start = T0 - timedelta(minutes=30)
    await add_samples(
        env, await target_id(env, "a"), start, 60, 30, write=lambda i: 1000.0, read=lambda i: 100.0
    )
    await add_samples(
        env,
        await target_id(env, "b"),
        start,
        60,
        30,
        write=lambda i: 500.0 + (i % 2) * 100,
        read=lambda i: 50.0,
    )
    h = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "1h"})
    assert len(h["points"]) == 30
    p = h["points"][0]
    assert p["write_mb_s_avg"] == pytest.approx(1000 + 550) and p["read_mb_s_avg"] == 150
    assert p["write_mb_s_peak"] == 1000 + 600 and p["samples"] == 4
    only_b = await env.ok(
        "GET",
        "/oceanprotect/throughput/history",
        params={"window": "1h", "target_id": str(await target_id(env, "b"))},
    )
    assert only_b["points"][0]["write_mb_s_avg"] == pytest.approx(550)


async def test_history_built_from_real_polls(env):
    await env.add_target()
    env.pin(write=3000, read=700)
    for _ in range(10):
        await env.collect()
        env.tick(30)
    h = await env.ok("GET", "/oceanprotect/throughput/history", params={"window": "1h"})
    assert sum(p["samples"] for p in h["points"]) == 10
    assert {p["write_mb_s_avg"] for p in h["points"]} == {3000} and {
        p["read_mb_s_peak"] for p in h["points"]
    } == {700}


# ---- reduction statistics -------------------------------------------------------------------------
async def add_capacity(env, tid, day, logical, post, physical, raw=1000.0):
    async with env.maker() as s:
        s.add(
            CapacityMetric(
                backup_target_id=tid,
                timestamp=T0 - timedelta(days=day),
                raw_capacity_gb=raw,
                used_physical_gb=physical,
                logic_written_gb=logical,
                post_dedup_gb=post,
                dedup_ratio=(logical - post) / logical,
                compression_ratio=post / physical,
                reduction_ratio=logical / physical,
            )
        )
        await s.commit()


async def test_reduction_stats_track_logical_versus_physical_growth(env):
    await env.add_target()
    tid = await target_id(env)
    for day, (logical, post, physical) in zip(
        (3, 2, 1, 0), [(1000, 100, 40), (2000, 200, 80), (3500, 340, 130), (5000, 480, 200)], strict=True
    ):
        await add_capacity(env, tid, day, logical, post, physical)
    stats = await env.ok("GET", "/oceanprotect/reduction-stats", params={"days": 30})
    assert stats["days"] == 30 and len(stats["points"]) == 4
    p0, p1, p2, p3 = stats["points"]
    assert p0["date"] == "2026-09-28" and p3["date"] == "2026-10-01"
    assert (p0["logical_growth_gb"], p0["physical_growth_gb"]) == (None, None)
    assert (p1["logical_growth_gb"], p1["physical_growth_gb"]) == (1000, 40)
    assert (p3["logical_growth_gb"], p3["physical_growth_gb"]) == (1500, 70)
    assert p0["reduction_ratio"] == 25 and p3["reduction_ratio"] == 25
    assert p0["dedup_ratio"] == pytest.approx(0.9) and p0["compression_ratio"] == pytest.approx(2.5)
    assert p2["reduction_ratio"] == pytest.approx(3500 / 130, abs=0.01)
    assert p3["used_physical_gb"] == 200 and p3["logical_written_gb"] == 5000


async def test_reduction_stats_use_the_last_reading_of_the_day_and_respect_days(env):
    await env.add_target()
    tid = await target_id(env)
    async with env.maker() as s:
        for hour, physical in ((1, 10.0), (23, 30.0)):  # two readings on 2026-09-30
            s.add(
                CapacityMetric(
                    backup_target_id=tid,
                    timestamp=(T0 - timedelta(days=1)).replace(hour=hour),
                    raw_capacity_gb=100,
                    used_physical_gb=physical,
                    logic_written_gb=physical * 20,
                    post_dedup_gb=physical * 2,
                    dedup_ratio=0.9,
                    compression_ratio=2,
                    reduction_ratio=20,
                )
            )
        await s.commit()
    await add_capacity(env, tid, 40, 100, 10, 5)
    stats = await env.ok("GET", "/oceanprotect/reduction-stats", params={"days": 7})
    assert [p["date"] for p in stats["points"]] == ["2026-09-30"]
    assert stats["points"][0]["used_physical_gb"] == 30
    assert len((await env.ok("GET", "/oceanprotect/reduction-stats", params={"days": 60}))["points"]) == 2


async def test_reduction_stats_combine_appliances_and_fill_gaps(env):
    await env.add_target("a", seed=1)
    await env.add_target("b", seed=2)
    a, b = await target_id(env, "a"), await target_id(env, "b")
    await add_capacity(env, a, 2, 1000, 100, 50)
    await add_capacity(env, a, 1, 1100, 110, 55)
    await add_capacity(env, a, 0, 1200, 120, 60)
    await add_capacity(env, b, 1, 2000, 200, 100)  # b starts a day later and skips today
    pts = (await env.ok("GET", "/oceanprotect/reduction-stats"))["points"]
    assert [p["logical_written_gb"] for p in pts] == [1000, 3100, 3200]  # b carried over to the last day
    assert pts[2]["used_physical_gb"] == 160
    assert pts[2]["reduction_ratio"] == pytest.approx(3200 / 160)  # ratio of the sums, not an average
    solo = await env.ok("GET", "/oceanprotect/reduction-stats", params={"target_id": str(b)})
    assert len(solo["points"]) == 1


async def test_reduction_stats_validation_and_empty(env):
    await env.add_target()
    assert (await env.ok("GET", "/oceanprotect/reduction-stats"))["points"] == []
    for bad in (0, 401, "x"):
        await env.err("GET", "/oceanprotect/reduction-stats", 422, params={"days": bad})
