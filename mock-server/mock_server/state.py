"""In-memory state of one simulated OceanProtect appliance."""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any

from . import envelope as E
from . import profiles as P

SESSION_TTL = 1200  # the reference: the session timeout of the server is 20 minutes
DAY = 86400
# Alarm severity as in the reference: 3 = warning, 5 = major, 6 = critical
LEVELS = {"Critical": 6, "Major": 5, "Warning": 3}
# HEALTHSTATUS: 1 = normal, 2 = faulty, 5 = degraded
HEALTH_OK, HEALTH_FAULT, HEALTH_DEGRADED = "1", "2", "5"
HEALTH_CODES = {"ok": HEALTH_OK, "fault": HEALTH_FAULT, "degraded": HEALTH_DEGRADED}
FINAL_STATUSES = ("SUCCESS", "FAILED", "PARTIALLY_SUCCESSFUL")


class MockState:
    def __init__(
        self,
        seed: int | None = None,
        username: str = "admin",
        password: str = "Admin@storage1",
        model: str = "OceanProtect X8000",
        raw_tb: int = 500,
        initial_used_percent: float = 40.0,
        daily_growth_gb: float = 800.0,
    ):
        self.seed = seed
        self.username, self.password = username, password
        self.model = model
        self.raw_tb, self.initial_used_percent, self.daily_growth_gb = (
            raw_tb,
            initial_used_percent,
            daily_growth_gb,
        )
        self.rng = random.Random(seed)
        self.time_offset = 0.0
        self.created_at = time.time()
        alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        self.device_id = "2102351" + "".join(self.rng.choice(alphabet) for _ in range(13))
        self.serial_number = self.device_id
        self.sessions: dict[str, float] = {}
        self.cookies: dict[str, str] = {}  # iBaseToken -> value of the session cookie
        self._counters: dict[str, int] = defaultdict(int)
        self.objects: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.tasks: dict[str, dict[str, Any]] = {}
        self.alarms: list[dict[str, Any]] = []
        self.perf_override: dict[str, Any] = {}
        self.next_backup: dict[
            str, Any
        ] = {}  # test hook: {'outcome', 'size_gb', 'duration_s'} for the next task
        self.task_lost: set[str] = set()  # test hook: tasks the appliance has 'forgotten'
        self.extra_physical_bytes = 0
        # data reduction: ingested / post-dedup = dedupe, post-dedup / physical = compression
        self.dedupe_x = round(self.rng.uniform(*P.DEDUPE_RANGE), 2)
        self.compression_x = round(self.rng.uniform(*P.COMPRESSION_RANGE), 2)
        self.hardware = self._make_hardware()
        self._seed_alarms()
        self._seed_backup_copies()

    # ---- clock -----------------------------------------------------------
    def now(self) -> float:
        return time.time() + self.time_offset

    def advance_time(self, seconds: float) -> None:
        self.time_offset += seconds

    # ---- sessions --------------------------------------------------------
    def login(self) -> tuple[str, str]:
        """Returns (iBaseToken, session cookie value); the reference requires both on every later request."""
        token = f"{self.rng.getrandbits(256):064X}"
        cookie = f"ismsession={self.rng.getrandbits(160):040X}"
        self.sessions[token] = self.now() + SESSION_TTL
        self.cookies[token] = cookie
        return token, cookie

    def session_valid(self, token: str | None, cookie: str | None) -> bool:
        expires = self.sessions.get(token or "")
        if expires is None or self.cookies.get(token or "") != cookie:
            return False
        if expires < self.now():
            self.drop_session(token)  # type: ignore[arg-type]
            return False
        return True

    def drop_session(self, token: str) -> None:
        self.sessions.pop(token, None)
        self.cookies.pop(token, None)

    def heartbeat(self, token: str) -> None:
        self.sessions[token] = self.now() + SESSION_TTL

    def expire_all_sessions(self) -> None:
        self.sessions.clear()
        self.cookies.clear()

    # ---- generic object store ---------------------------------------------
    def next_id(self, kind: str) -> str:
        self._counters[kind] += 1
        return str(self._counters[kind])

    def create(self, kind: str, **fields: Any) -> dict[str, Any]:
        obj = {"ID": self.next_id(kind), **fields}
        self.objects[kind][obj["ID"]] = obj
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

    # ---- capacity -------------------------------------------------------------
    @property
    def raw_bytes(self) -> int:
        return self.raw_tb * P.TB

    @property
    def total_ratio(self) -> float:
        return self.dedupe_x * self.compression_x

    def elapsed_days(self) -> float:
        return (self.now() - self.created_at) / DAY

    def physical_bytes(self) -> int:
        """Physical space consumed: starting level + steady daily growth + manual ingests."""
        base = self.raw_bytes * self.initial_used_percent / 100
        growth = self.daily_growth_gb * P.GB * self.elapsed_days()
        return min(int(base + growth) + self.extra_physical_bytes, self.raw_bytes)

    def pool_numbers(self) -> dict[str, int]:
        physical = self.physical_bytes()
        ingested = int(physical * self.total_ratio)
        post_dedup = int(ingested / self.dedupe_x)
        return {"raw": self.raw_bytes, "physical": physical, "ingested": ingested, "post_dedup": post_dedup}

    def ingest(self, logical_gb: float) -> None:
        """Simulate a backup landing: logical data grows by ``logical_gb``."""
        self.extra_physical_bytes += int(logical_gb * P.GB / self.total_ratio)

    def check_space(self, logical_bytes: int) -> None:
        if self.physical_bytes() + logical_bytes / self.total_ratio > self.raw_bytes:
            raise E.HuaweiError(E.INSUFFICIENT_SPACE, "The storage pool has insufficient space.")

    # ---- performance ------------------------------------------------------------
    def sample_performance(self) -> dict[str, Any]:
        r = self.rng
        o = self.perf_override
        write = o.get("write_mbps", r.randint(*P.WRITE_MBPS_RANGE))
        read = o.get("read_mbps", r.randint(*P.READ_MBPS_RANGE))
        iops = o.get("iops", r.randint(*P.IOPS_RANGE))
        controllers = []
        for c in self.hardware["controller"].values():
            streams = o.get("streams_per_controller", r.randint(*P.STREAMS_PER_CONTROLLER))
            controllers.append({"ID": c["ID"], "ACTIVESTREAMS": streams})
        return {
            "WRITE_THROUGHPUT_MBPS": write,
            "READ_THROUGHPUT_MBPS": read,
            "IOPS": iops,
            "ACTIVE_STREAMS": sum(c["ACTIVESTREAMS"] for c in controllers),
            "CONTROLLERS": controllers,
            "TIMESTAMP": int(self.now()),
        }

    # ---- hardware ----------------------------------------------------------------
    def _make_hardware(self) -> dict[str, dict[str, dict[str, Any]]]:
        r = self.rng
        hw: dict[str, dict[str, dict[str, Any]]] = {"controller": {}, "nvram": {}, "power": {}, "disk": {}}
        for cid in ("0A", "0B"):
            hw["controller"][cid] = {
                "ID": cid,
                "NAME": f"Controller {cid}",
                "CPUUSAGE": str(r.randint(25, 70)),
                "MEMORYUSAGE": str(r.randint(40, 80)),
                "HEALTHSTATUS": HEALTH_OK,
                "RUNNINGSTATUS": "27",
            }
            hw["nvram"][cid] = {
                "ID": cid,
                "CONTROLLER": cid,
                "HEALTHSTATUS": HEALTH_OK,
                "RUNNINGSTATUS": "27",
                "DEDUPCACHEHITRATIO": r.randint(70, 95),
                "CACHEUSAGE": r.randint(30, 80),
            }
        for i in range(4):
            hw["power"][f"PSU{i}"] = {
                "ID": f"PSU{i}",
                "NAME": f"Power module {i}",
                "HEALTHSTATUS": HEALTH_OK,
                "RUNNINGSTATUS": "2",
            }
        for i in range(24):
            hw["disk"][str(i)] = {
                "ID": str(i),
                "LOCATION": f"DAE000.{i}",
                "TYPE": "NL-SAS" if i < 20 else "SSD",
                "ROLE": "data" if i < 22 else "hot-spare",
                "HEALTHSTATUS": HEALTH_OK,
                "RUNNINGSTATUS": "27",
            }
        return hw

    def set_health(self, component: str, comp_id: str, health: str) -> dict[str, Any]:
        if component not in self.hardware or comp_id not in self.hardware[component]:
            raise E.HuaweiError(E.OBJECT_NOT_FOUND, f"No {component} with id {comp_id}.")
        if health not in HEALTH_CODES:
            raise E.HuaweiError(E.PARAM_ERROR, "health must be ok, degraded or fault.")
        item = self.hardware[component][comp_id]
        item["HEALTHSTATUS"] = HEALTH_CODES[health]
        item["RUNNINGSTATUS"] = "27" if health == "ok" else "28"
        return item

    # ---- alarms ----------------------------------------------------------------
    def add_alarm(self, level: str, name: str, event_id: str, start: int | None = None) -> dict[str, Any]:
        """``event_id`` is the hexadecimal id (for example 0xF0C90002); the API also shows it in decimal."""
        if level not in LEVELS:
            raise E.HuaweiError(E.PARAM_ERROR, f"level must be one of {', '.join(LEVELS)}.")
        self._counters["alarm"] += 1
        alarm = {
            "alarmObjType": 244,
            "alarmStatus": 1,
            "clearName": 0,
            "clearTime": 0,
            "confirmTime": 0,
            "description": name,
            "detail": name,
            "eventID": int(event_id, 16),
            "eventParam": "",
            "level": LEVELS[level],
            "location": f"Device {self.device_id}",
            "name": name,
            "position": 0,
            "recoverTime": 0,
            "room": "",
            "sequence": self._counters["alarm"],
            "sourceID": "",
            "sourceType": "",
            "startTime": start or int(self.now()),
            "strEventID": event_id,
            "suggestion": "",
            "type": 1,
        }
        self.alarms.append(alarm)
        return alarm

    def clear_alarm(self, sequence: str) -> None:
        before = len(self.alarms)
        self.alarms = [a for a in self.alarms if str(a["sequence"]) != str(sequence)]
        if len(self.alarms) == before:
            raise E.HuaweiError(E.OBJECT_NOT_FOUND, "The alarm does not exist.")

    def _seed_alarms(self) -> None:
        now = int(self.now())
        self.add_alarm("Warning", "Backup copy retention is about to expire", "0xF00CF0101", now - 7200)
        self.add_alarm("Major", "Storage pool capacity usage is above 40%", "0xF00CF0102", now - 3600)

    # ---- backup copies (retention view) ----------------------------------------------
    def _seed_backup_copies(self) -> None:
        samples = [
            ("vm-prod-db-01", 28, 30, True, 900 * P.GB),
            ("vm-prod-app-02", 14, 30, True, 400 * P.GB),
            ("oracle-erp", 7, 90, True, 2000 * P.GB),
            ("fileserver-nas", 3, 7, False, 1500 * P.GB),
            ("vm-dev-test", 45, 30, False, 120 * P.GB),
        ]
        now = self.now()
        for name, age_days, retention, worm, size in samples:
            self.create(
                "backup",
                NAME=name,
                SOURCE=name,
                CREATETIME=int(now - age_days * DAY),
                RETENTIONDAYS=retention,
                WORM=worm,
                size_bytes=size,
                CAPACITY=str(size // 512),
            )

    # ---- backup tasks -----------------------------------------------------------------
    def start_task(self, job: dict[str, Any], size_gb: float, outcome: str | None) -> dict[str, Any]:
        r = self.rng
        self._counters["task"] += 1
        task_id = f"T{self._counters['task']:06d}"
        hook, self.next_backup = self.next_backup, {}
        outcome = outcome or hook.get("outcome") or r.choices(FINAL_STATUSES, weights=(82, 8, 10))[0]
        size_gb = hook.get("size_gb", size_gb)
        task = {
            "taskId": task_id,
            "jobId": job["ID"],
            "created": self.now(),
            "pending_s": 3.0,
            "duration_s": float(hook.get("duration_s") or r.randint(60, 600)),
            "size_bytes": int(size_gb * P.GB),
            "outcome": outcome,
            "cancelled_at": None,
        }
        self.tasks[task_id] = task
        return task

    def task_view(self, task_id: str) -> dict[str, Any]:
        t = self.tasks.get(task_id)
        if t is None or task_id in self.task_lost:
            raise E.HuaweiError(E.OBJECT_NOT_FOUND, "The task does not exist.")
        elapsed = self.now() - t["created"]
        run_s, done_at = elapsed - t["pending_s"], t["pending_s"] + t["duration_s"]
        start = int(t["created"] + t["pending_s"])
        if t["cancelled_at"] is not None and elapsed >= t["cancelled_at"]:
            status, progress, end = (
                "CANCELLED",
                min(100, max(0, int(run_s / t["duration_s"] * 100))),
                int(t["created"] + t["cancelled_at"]),
            )
        elif elapsed < t["pending_s"]:
            status, progress, end = "PENDING", 0, 0
        elif elapsed < done_at:
            status, progress, end = "RUNNING", int(run_s / t["duration_s"] * 100), 0
        else:
            status, end = t["outcome"], int(t["created"] + done_at)
            progress = {"SUCCESS": 100, "PARTIALLY_SUCCESSFUL": 100, "FAILED": 37}[status]
        fraction = {"SUCCESS": 1.0, "PARTIALLY_SUCCESSFUL": 0.8}.get(status, progress / 100)
        if status in ("RUNNING", "CANCELLED"):
            fraction = progress / 100
        transferred = int(t["size_bytes"] * fraction)
        spent = max(1.0, (end or self.now()) - start) if status != "PENDING" else 1.0
        logs = [f"Task {task_id} created"]
        if status != "PENDING":
            logs.append("Backup stream started")
        if status == "FAILED":
            logs.append("Error: source agent connection lost")
        if status == "PARTIALLY_SUCCESSFUL":
            logs.append("Warning: 2 files skipped (locked by application)")
        if status == "SUCCESS":
            logs.append("Backup completed")
        return {
            "taskId": task_id,
            "jobId": t["jobId"],
            "STATUS": status,
            "PROGRESS": progress,
            "STARTTIME": start if status != "PENDING" else 0,
            "ENDTIME": end,
            "DATATRANSFERREDBYTES": transferred,
            "THROUGHPUTMBPS": round(transferred / P.GB * 1024 / spent, 2) if status != "PENDING" else 0.0,
            "LOGS": logs,
        }

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        view = self.task_view(task_id)
        if view["STATUS"] not in ("PENDING", "RUNNING"):
            raise E.HuaweiError(E.OBJECT_IN_USE, f"The task is already {view['STATUS']}.")
        self.tasks[task_id]["cancelled_at"] = self.now() - self.tasks[task_id]["created"]
        return self.task_view(task_id)

    def reset(self) -> None:
        self.__init__(  # type: ignore[misc]
            self.seed,
            self.username,
            self.password,
            self.model,
            self.raw_tb,
            self.initial_used_percent,
            self.daily_growth_gb,
        )
