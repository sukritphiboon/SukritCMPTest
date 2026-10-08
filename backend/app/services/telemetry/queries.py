"""Read side of the telemetry engine: turns stored samples into dashboard figures."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlarmRecord,
    AlarmSeverity,
    BackupTarget,
    CapacityMetric,
    HardwareHealth,
    HardwareSnapshot,
    ThroughputSample,
)
from app.schemas.telemetry import (
    AlarmCounts,
    IngestionOut,
    Overview,
    ReductionOut,
    ReductionPoint,
    ReductionStats,
    RunwayOut,
    TargetSummary,
    ThroughputHistory,
    ThroughputPoint,
)
from app.services.analytics import ratios
from app.services.analytics.runway import RunwayResult, daily_last, forecast_runway

WINDOWS: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 60),
    "24h": (timedelta(hours=24), 900),
    "7d": (timedelta(days=7), 3600),
}
_HW_RANK = {HardwareHealth.OK: 0, HardwareHealth.DEGRADED: 1, HardwareHealth.FAULT: 2}


def utc(ts: datetime) -> datetime:
    """SQLite returns naive datetimes; everything stored by the collector is UTC."""
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def severity_order():
    return case(
        (AlarmRecord.severity == AlarmSeverity.CRITICAL, 0),
        (AlarmRecord.severity == AlarmSeverity.MAJOR, 1),
        else_=2,
    )


async def _latest(session: AsyncSession, model, ids: list[uuid.UUID]) -> dict[uuid.UUID, object]:
    newest = (
        select(model.backup_target_id, func.max(model.timestamp).label("ts"))
        .where(model.backup_target_id.in_(ids))
        .group_by(model.backup_target_id)
        .subquery()
    )
    stmt = select(model).join(
        newest, and_(model.backup_target_id == newest.c.backup_target_id, model.timestamp == newest.c.ts)
    )
    return {row.backup_target_id: row for row in await session.scalars(stmt)}


def count_alarms(alarms: list[AlarmRecord]) -> AlarmCounts:
    counts = AlarmCounts(total=len(alarms), unacknowledged=sum(1 for a in alarms if not a.acknowledged))
    for a in alarms:
        setattr(counts, a.severity.value, getattr(counts, a.severity.value) + 1)
    return counts


def _runway_out(r: RunwayResult, target: BackupTarget | None = None) -> RunwayOut:
    return RunwayOut(
        status=r.status,
        days_until_full=None if r.days_until_full is None else round(r.days_until_full, 1),
        avg_daily_growth_gb=None if r.avg_daily_growth_gb is None else round(r.avg_daily_growth_gb, 2),
        free_gb=None if r.free_gb is None else round(r.free_gb, 1),
        window_days=r.window_days,
        target_id=target.id if target else None,
        target_name=target.name if target else None,
    )


def combine_runway(per_target: list[tuple[BackupTarget, RunwayResult]]) -> RunwayOut:
    """The pool that fills first decides; without any number the least bad explanation is shown."""
    if not per_target:
        return RunwayOut(status="insufficient_data")
    for status in ("full", "ok"):
        matching = [(t, r) for t, r in per_target if r.status == status]
        if matching:
            t, r = min(matching, key=lambda tr: tr[1].days_until_full or 0.0)
            return _runway_out(r, t)
    for status in ("insufficient_data", "not_growing"):
        matching = [(t, r) for t, r in per_target if r.status == status]
        if matching:
            if len(per_target) == 1:
                return _runway_out(matching[0][1], matching[0][0])
            return RunwayOut(status=status)
    return RunwayOut(status="insufficient_data")


def _reduction(rows: list[CapacityMetric]) -> ReductionOut:
    logical = sum(r.logic_written_gb for r in rows)
    post = sum(r.post_dedup_gb for r in rows)
    physical = sum(r.used_physical_gb for r in rows)
    raw = sum(r.raw_capacity_gb for r in rows)
    return ReductionOut(
        reduction_ratio=round(ratios.reduction_ratio(logical, physical), 2) if rows and physical else None,
        dedup_ratio=round(ratios.dedup_ratio(logical, post), 4) if rows and logical else None,
        dedup_factor=round(ratios.dedup_factor(logical, post), 2) if rows and post else None,
        compression_ratio=round(ratios.compression_ratio(post, physical), 2) if rows and physical else None,
        logical_written_gb=round(logical, 1),
        post_dedup_gb=round(post, 1),
        used_physical_gb=round(physical, 1),
        raw_capacity_gb=round(raw, 1),
        free_gb=round(max(raw - physical, 0.0), 1),
    )


def _ingestion(rows: list[ThroughputSample]) -> IngestionOut:
    write = sum(r.write_throughput_mb_s for r in rows)
    read = sum(r.read_throughput_mb_s for r in rows)
    return IngestionOut(
        write_mb_s=round(write, 1),
        read_mb_s=round(read, 1),
        total_mb_s=round(write + read, 1),
        write_gb_per_hour=round(write * 3600 / 1024, 1),
        iops=sum(r.iops for r in rows),
        active_streams=sum(r.active_streams for r in rows),
    )


async def build_overview(
    session: AsyncSession,
    targets: list[BackupTarget],
    now: datetime,
    *,
    interval_seconds: int = 30,
    stale_intervals: int = 3,
    window_days: int = 7,
) -> Overview:
    ids = [t.id for t in targets]
    capacity = await _latest(session, CapacityMetric, ids) if ids else {}
    throughput = await _latest(session, ThroughputSample, ids) if ids else {}
    hardware = await _latest(session, HardwareSnapshot, ids) if ids else {}
    active = (
        list(
            await session.scalars(
                select(AlarmRecord).where(
                    AlarmRecord.backup_target_id.in_(ids), AlarmRecord.cleared_at.is_(None)
                )
            )
        )
        if ids
        else []
    )
    history: dict[uuid.UUID, list[tuple[datetime, float]]] = defaultdict(list)
    if ids:
        since = now - timedelta(days=window_days + 2)
        for tid, ts, used in await session.execute(
            select(CapacityMetric.backup_target_id, CapacityMetric.timestamp, CapacityMetric.used_physical_gb)
            .where(CapacityMetric.backup_target_id.in_(ids), CapacityMetric.timestamp >= since)
            .order_by(CapacityMetric.timestamp)
        ):
            history[tid].append((utc(ts), used))

    max_age = interval_seconds * stale_intervals
    summaries: list[TargetSummary] = []
    fresh_throughput: list[ThroughputSample] = []
    runways: list[tuple[BackupTarget, RunwayResult]] = []
    for t in targets:
        cap, thr, hw = capacity.get(t.id), throughput.get(t.id), hardware.get(t.id)
        stamps = [utc(x.timestamp) for x in (cap, thr, hw) if x is not None]
        age = (now - max(stamps)).total_seconds() if stamps else None
        stale = age is None or age > max_age
        alarms = count_alarms([a for a in active if a.backup_target_id == t.id])
        runway = (
            forecast_runway(history[t.id], cap.raw_capacity_gb, window_days)  # type: ignore[attr-defined]
            if cap is not None
            else RunwayResult("insufficient_data", None, None, None, 0)
        )
        if cap is not None:
            runways.append((t, runway))
        if thr is not None and not stale:
            fresh_throughput.append(thr)  # type: ignore[arg-type]
        summaries.append(
            TargetSummary(
                id=t.id,
                name=t.name,
                health_status=t.health_status,
                hardware_status=hw.overall_status if hw else None,  # type: ignore[attr-defined]
                hardware_summary=hw.summary if hw else None,  # type: ignore[attr-defined]
                last_seen_at=t.last_seen_at and utc(t.last_seen_at),
                last_error=t.last_error,
                data_age_seconds=None if age is None else round(age, 1),
                stale=stale,
                ingestion=_ingestion([thr]) if thr is not None and not stale else None,  # type: ignore[list-item]
                reduction=_reduction([cap]) if cap is not None else None,  # type: ignore[list-item]
                runway=_runway_out(runway) if cap is not None else None,
                alarms=alarms,
                max_cpu_percent=hw.max_cpu_percent if hw else None,  # type: ignore[attr-defined]
                max_memory_percent=hw.max_memory_percent if hw else None,  # type: ignore[attr-defined]
            )
        )
    worst = max((h.overall_status for h in hardware.values()), key=_HW_RANK.get, default=None)  # type: ignore[attr-defined]
    return Overview(
        as_of=now,
        targets_total=len(targets),
        targets_reporting=sum(1 for s in summaries if not s.stale),
        ingestion=_ingestion(fresh_throughput),
        reduction=_reduction(list(capacity.values())),  # type: ignore[arg-type]
        runway=combine_runway(runways),
        alarms=count_alarms(active),
        hardware_status=worst,
        targets=summaries,
    )


async def throughput_history(
    session: AsyncSession, ids: list[uuid.UUID], window: str, now: datetime
) -> ThroughputHistory:
    span, bucket = WINDOWS[window]
    start = now - span
    rows = (
        (
            await session.execute(
                select(
                    ThroughputSample.backup_target_id,
                    ThroughputSample.timestamp,
                    ThroughputSample.write_throughput_mb_s,
                    ThroughputSample.read_throughput_mb_s,
                )
                .where(ThroughputSample.backup_target_id.in_(ids), ThroughputSample.timestamp >= start)
                .order_by(ThroughputSample.timestamp)
            )
        ).all()
        if ids
        else []
    )
    # (bucket, target) -> [write_sum, read_sum, count, write_max, read_max]
    cells: dict[tuple[int, uuid.UUID], list[float]] = {}
    for tid, ts, write, read in rows:
        key = (int(utc(ts).timestamp() // bucket) * bucket, tid)
        c = cells.setdefault(key, [0.0, 0.0, 0, 0.0, 0.0])
        c[0] += write
        c[1] += read
        c[2] += 1
        c[3] = max(c[3], write)
        c[4] = max(c[4], read)
    merged: dict[int, dict[str, float]] = {}
    for (b, _tid), (wsum, rsum, n, wmax, rmax) in cells.items():
        m = merged.setdefault(b, {"w": 0.0, "r": 0.0, "wp": 0.0, "rp": 0.0, "n": 0})
        m["w"] += wsum / n
        m["r"] += rsum / n
        m["wp"] += wmax
        m["rp"] += rmax
        m["n"] += n
    points = [
        ThroughputPoint(
            timestamp=datetime.fromtimestamp(b, UTC),
            write_mb_s_avg=round(m["w"], 1),
            read_mb_s_avg=round(m["r"], 1),
            total_mb_s_avg=round(m["w"] + m["r"], 1),
            write_mb_s_peak=round(m["wp"], 1),
            read_mb_s_peak=round(m["rp"], 1),
            samples=int(m["n"]),
        )
        for b, m in sorted(merged.items())
    ]
    return ThroughputHistory(window=window, bucket_seconds=bucket, start=start, end=now, points=points)  # type: ignore[arg-type]


async def reduction_stats(
    session: AsyncSession, ids: list[uuid.UUID], days: int, now: datetime
) -> ReductionStats:
    since = now - timedelta(days=days)
    rows = (
        (
            await session.scalars(
                select(CapacityMetric)
                .where(CapacityMetric.backup_target_id.in_(ids), CapacityMetric.timestamp >= since)
                .order_by(CapacityMetric.timestamp)
            )
        ).all()
        if ids
        else []
    )
    per_target: dict[uuid.UUID, dict[date, CapacityMetric]] = defaultdict(dict)
    for r in rows:
        per_target[r.backup_target_id][utc(r.timestamp).date()] = (
            r  # ascending order: the last row of a day wins
        )
    all_days = sorted({d for series in per_target.values() for d in series})
    points: list[ReductionPoint] = []
    carried: dict[uuid.UUID, CapacityMetric] = {}  # a target without a reading that day keeps its last one
    prev: tuple[float, float] | None = None
    for day in all_days:
        for tid, series in per_target.items():
            if day in series:
                carried[tid] = series[day]
        logical = sum(r.logic_written_gb for r in carried.values())
        post = sum(r.post_dedup_gb for r in carried.values())
        physical = sum(r.used_physical_gb for r in carried.values())
        points.append(
            ReductionPoint(
                date=day.isoformat(),
                logical_written_gb=round(logical, 1),
                used_physical_gb=round(physical, 1),
                post_dedup_gb=round(post, 1),
                reduction_ratio=round(ratios.reduction_ratio(logical, physical), 2) if physical else None,
                dedup_ratio=round(ratios.dedup_ratio(logical, post), 4) if logical else None,
                compression_ratio=round(ratios.compression_ratio(post, physical), 2) if physical else None,
                logical_growth_gb=None if prev is None else round(logical - prev[0], 1),
                physical_growth_gb=None if prev is None else round(physical - prev[1], 1),
            )
        )
        prev = (logical, physical)
    return ReductionStats(days=days, points=points)


__all__ = ["WINDOWS", "build_overview", "daily_last", "reduction_stats", "throughput_history", "utc"]
