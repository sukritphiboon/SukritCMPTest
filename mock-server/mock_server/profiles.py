"""Behaviour profiles for the two simulated products."""

from __future__ import annotations

from dataclasses import dataclass

SECTORS_PER_GB = 2 * 1024 * 1024  # 512-byte sectors, Huawei's capacity unit
GB = 1024**3
TB = 1024**4


@dataclass(frozen=True)
class Profile:
    key: str
    product_mode: str
    product_version: str
    pool_total_bytes: int
    supports_block: bool
    supports_object: bool
    supports_protection: bool  # WORM + backup retention
    iops: tuple[int, int]
    latency_us: tuple[int, int]
    bandwidth_mbps: tuple[int, int]
    thin_ratio: tuple[float, float]
    dedupe_ratio: tuple[float, float]
    compression_ratio: tuple[float, float]


DORADO = Profile(
    key="dorado",
    product_mode="OceanStor Dorado 5000 V7",
    product_version="V700R001C00",
    pool_total_bytes=200 * TB,
    supports_block=True,
    supports_object=True,
    supports_protection=False,
    iops=(300_000, 1_200_000),
    latency_us=(120, 900),  # always sub-millisecond
    bandwidth_mbps=(4_000, 12_000),
    thin_ratio=(1.8, 3.5),
    dedupe_ratio=(1.5, 3.0),
    compression_ratio=(2.0, 4.0),
)

OCEANPROTECT = Profile(
    key="oceanprotect",
    product_mode="OceanProtect X8000",
    product_version="1.6.0",
    pool_total_bytes=1024 * TB,
    supports_block=False,
    supports_object=False,
    supports_protection=True,
    iops=(8_000, 40_000),
    latency_us=(1_500, 9_000),
    bandwidth_mbps=(6_000, 20_000),
    thin_ratio=(1.0, 1.2),
    dedupe_ratio=(8.0, 12.0),
    compression_ratio=(2.5, 3.5),  # dedupe * compression lands in 20:1 - 42:1
)

PROFILES = {p.key: p for p in (DORADO, OCEANPROTECT)}
