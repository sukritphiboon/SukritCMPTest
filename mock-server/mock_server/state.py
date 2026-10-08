"""In-memory array state. One MockState instance simulates one storage device."""

from __future__ import annotations

import random
import secrets
import time
from collections import defaultdict
from typing import Any

from . import envelope as E
from .profiles import GB, PROFILES, SECTORS_PER_GB, Profile

SESSION_TTL = 1800
SUBSCRIPTION_LIMIT = 3  # provisioned capacity may reach 3x physical (thin)


class MockState:
    def __init__(
        self,
        profile: str = "dorado",
        seed: int | None = None,
        username: str = "admin",
        password: str = "Admin@storage1",
    ):
        self.profile: Profile = PROFILES[profile]
        self.seed = seed
        self.username = username
        self.password = password
        self.rng = random.Random(seed)
        self.time_offset = 0.0
        self.device_id = "".join(self.rng.choice("0123456789") for _ in range(12))
        self.sessions: dict[str, float] = {}
        self._counters: dict[str, int] = defaultdict(int)
        self.objects: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.s3_credentials: dict[str, dict[str, str]] = {}  # access_key -> record
        self.alarms: list[dict[str, Any]] = []
        self.pool = self._make_pool()
        self._seed_alarms()
        if self.profile.supports_protection:
            self._seed_backup_copies()

    # ---- clock -----------------------------------------------------------
    def now(self) -> float:
        return time.time() + self.time_offset

    def advance_time(self, seconds: float) -> None:
        self.time_offset += seconds

    # ---- sessions --------------------------------------------------------
    def login(self) -> str:
        token = secrets.token_hex(16)
        self.sessions[token] = self.now() + SESSION_TTL
        return token

    def session_valid(self, token: str | None) -> bool:
        expires = self.sessions.get(token or "")
        if expires is None:
            return False
        if expires < self.now():
            del self.sessions[token]  # type: ignore[arg-type]
            return False
        return True

    def expire_all_sessions(self) -> None:
        self.sessions.clear()

    # ---- generic object store ---------------------------------------------
    def next_id(self, kind: str) -> str:
        self._counters[kind] += 1
        return str(self._counters[kind])

    def create(self, kind: str, **fields: Any) -> dict[str, Any]:
        obj_id = self.next_id(kind)
        obj = {"ID": obj_id, **fields}
        self.objects[kind][obj_id] = obj
        return obj

    def get(self, kind: str, obj_id: str) -> dict[str, Any]:
        obj = self.objects[kind].get(str(obj_id))
        if obj is None:
            raise E.HuaweiError(E.OBJECT_NOT_FOUND, f"The {kind} does not exist.")
        return obj

    def delete(self, kind: str, obj_id: str) -> dict[str, Any]:
        self.get(kind, obj_id)
        return self.objects[kind].pop(str(obj_id))

    def ensure_unique_name(self, kind: str, name: str) -> None:
        if any(o.get("NAME") == name for o in self.objects[kind].values()):
            raise E.HuaweiError(E.OBJECT_EXISTS, f"The name '{name}' already exists.")

    # ---- capacity ----------------------------------------------------------
    def _make_pool(self) -> dict[str, Any]:
        p, r = self.profile, self.rng
        thin = round(r.uniform(*p.thin_ratio), 2)
        dedupe = round(r.uniform(*p.dedupe_ratio), 2)
        compression = round(r.uniform(*p.compression_ratio), 2)
        return {
            "ID": "0",
            "NAME": "StoragePool001",
            "USAGETYPE": "1",
            "HEALTHSTATUS": "1",
            "RUNNINGSTATUS": "27",
            "total_bytes": p.pool_total_bytes,
            "thin": thin,
            "dedupe": dedupe,
            "compression": compression,
        }

    def provisioned_bytes(self) -> int:
        total = 0
        for kind in ("lun", "filesystem"):
            total += sum(int(o["CAPACITY"]) * 512 for o in self.objects[kind].values())
        return total

    def consumed_bytes(self) -> int:
        """Physical bytes used after thin provisioning and data reduction."""
        pool = self.pool
        written = 0.0
        for kind in ("lun", "filesystem"):
            for o in self.objects[kind].values():
                size = int(o["CAPACITY"]) * 512
                written += size if o.get("ALLOCTYPE") == "0" else size * 0.35
        written += sum(c["size_bytes"] for c in self.objects["backup"].values())
        reduction = pool["dedupe"] * pool["compression"]
        return int(written / max(reduction, 1.0))

    def check_space(self, extra_bytes: int) -> None:
        limit = self.pool["total_bytes"] * SUBSCRIPTION_LIMIT
        if self.provisioned_bytes() + extra_bytes > limit:
            raise E.HuaweiError(E.INSUFFICIENT_SPACE, "The storage pool has insufficient space.")

    def total_reduction_ratio(self) -> float:
        return round(self.pool["dedupe"] * self.pool["compression"], 2)

    # ---- performance -------------------------------------------------------
    def sample_performance(self) -> dict[str, Any]:
        p, r = self.profile, self.rng
        iops = r.randint(*p.iops)
        read_share = r.uniform(0.55, 0.8)
        read_iops = int(iops * read_share)
        bw = r.randint(*p.bandwidth_mbps)
        read_bw = int(bw * read_share)
        lat = r.randint(*p.latency_us)
        return {
            "IOPS": iops,
            "READ_IOPS": read_iops,
            "WRITE_IOPS": iops - read_iops,
            "LATENCY_US": lat,
            "READ_LATENCY_US": max(1, int(lat * 0.8)),
            "WRITE_LATENCY_US": int(lat * 1.2),
            "BANDWIDTH_MBPS": bw,
            "READ_BANDWIDTH_MBPS": read_bw,
            "WRITE_BANDWIDTH_MBPS": bw - read_bw,
            "TIMESTAMP": int(self.now()),
        }

    # ---- seed data ---------------------------------------------------------
    def _seed_alarms(self) -> None:
        now = int(self.now())
        if self.profile.key == "dorado":
            self.add_alarm("Medium", "Controller fan speed is high", "0xF00CF0001", now - 3600)
        else:
            self.add_alarm("Low", "Backup copy retention is about to expire", "0xF00CF0101", now - 7200)

    def add_alarm(self, level: str, name: str, event_id: str, start: int | None = None) -> dict:
        levels = {"Critical": "4", "Major": "3", "Medium": "2", "Low": "1"}
        alarm = {
            "eventID": event_id,
            "name": name,
            "level": levels.get(level, "2"),
            "levelName": level,
            "startTime": start or int(self.now()),
            "sequence": str(len(self.alarms) + 1),
            "recoveryTime": 0,
            "location": f"Device {self.device_id}",
        }
        self.alarms.append(alarm)
        return alarm

    def _seed_backup_copies(self) -> None:
        day = 86400
        samples = [
            ("vm-prod-db-01", 28, 30, True, 900 * GB),
            ("vm-prod-app-02", 14, 30, True, 400 * GB),
            ("oracle-erp", 7, 90, True, 2000 * GB),
            ("fileserver-nas", 3, 7, False, 1500 * GB),
            ("vm-dev-test", 45, 30, False, 120 * GB),
        ]
        now = self.now()
        for name, age_days, retention, worm, size in samples:
            self.create(
                "backup",
                NAME=name,
                SOURCE=name,
                CREATETIME=int(now - age_days * day),
                RETENTIONDAYS=retention,
                WORM=worm,
                size_bytes=size,
                CAPACITY=str(size // 512),
            )

    # ---- unit helpers -------------------------------------------------------
    @staticmethod
    def gb_to_sectors(gb: float) -> int:
        return int(gb * SECTORS_PER_GB)

    def reset(self) -> None:
        self.__init__(self.profile.key, self.seed, self.username, self.password)  # type: ignore[misc]
