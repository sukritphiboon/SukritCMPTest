# Architecture

```
frontend (Next.js) -> backend API (FastAPI) -> services -> BackupDriverBase -> OceanProtectDriver -> appliance REST API
                              |                   |
                         PostgreSQL        ARQ worker (every 30 s) -> telemetry collector
```

* **API** (`backend/app/api`): routers, API key check (`X-API-Key`), error mapping. `Ctx` (`services/context.py`) bundles
  the database session, the actor and a driver factory for each request.
* **Services** (`backend/app/services`): `telemetry/collector.py` (write side), `telemetry/queries.py` (read side),
  `analytics/ratios.py` and `analytics/runway.py` (pure formulas), `audit.py` (every change is audited, success or failure).
* **Driver** (`backend/app/drivers`): `BackupDriverBase` is the interface the services use; `OceanProtectDriver` translates it
  into the DeviceManager REST dialect (`huawei_client.py` handles login, `iBaseToken`, the `{"error", "data"}` envelope and
  one transparent re-login when the session expired). Services never see Huawei field names.
* **Mock** (`mock-server/`): same REST dialect, in memory, with controls under `/_mock/*` for tests.

## Driver interface

| Group | Methods |
|---|---|
| Telemetry | `get_system_info`, `get_pool_metrics`, `get_performance`, `get_hardware_status`, `get_active_alarms` |
| Orchestration | `list_policies`, `create_policy`, `delete_policy`, `create_asset`, `trigger_backup` (returns a task id), `get_task`, `cancel_task` |
| Immutability | `create_filesystem`, `create_worm_policy`, `list_backup_copies` |

Backups are asynchronous: `trigger_backup` returns a task id, and `get_task` is polled until the status is final
(`PENDING -> RUNNING -> SUCCESS | FAILED | PARTIALLY_SUCCESSFUL | CANCELLED`).

## Data model (`backend/app/models`)

| Table | Purpose |
|---|---|
| `backup_targets` | One appliance: address, AES-256-GCM encrypted credentials, model, serial number, health |
| `backup_policies` | Cron schedule, full/incremental, retention days, WORM switch and mode |
| `protected_assets` | VMware / database / file share / LUN protected on a target |
| `backup_jobs` | One run: appliance task id, status, times, GB transferred, MB/s, log lines. Survives asset/policy deletion |
| `capacity_metrics` | Pool readings: raw, physical, logical written, post-dedup, dedup / compression / total ratio |
| `throughput_samples`, `hardware_snapshots`, `alarm_records` | Telemetry written by the collector |
| `audit_logs` | Who did what; kept when the target is deleted |

Credentials are one JSON document encrypted with AES-256-GCM (`core/crypto.py`); the row id is bound in as authenticated
data, so a ciphertext copied to another row will not decrypt. The key is `CMP_ENCRYPTION_KEY` (base64 of 32 random bytes).
Enums are stored as VARCHAR (no native PostgreSQL enum types).
