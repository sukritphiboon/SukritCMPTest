# OceanProtect CMP

Monitoring and orchestration platform for **Huawei OceanProtect** backup appliances (X3000 / X6000 / X8000 / X9000),
with a built-in **mock appliance** so everything can be developed and tested without hardware.

| Folder | What it is |
|---|---|
| `backend/` | FastAPI, async SQLAlchemy, Pydantic v2, ARQ worker, Alembic, appliance driver, telemetry engine |
| `frontend/` | Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui setup (skeleton only) |
| `mock-server/` | Simulated OceanProtect DeviceManager REST API (port 8088) |
| `docs/` | Architecture, telemetry engine, API reference, mock reference, deployment |

## Status

| Done | Not yet |
|---|---|
| Data model, migration, AES-256 credential storage | Backup orchestration API (trigger, restore, SLA policies) |
| Mock appliance with backup tasks, WORM, hardware, alarms | APScheduler for CMP-side backup schedules |
| Telemetry collector (every 30 s), runway forecast | Frontend screens |
| Overview / throughput / reduction / alarm API, audit log | Users and roles (one shared API key list for now) |

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "mock-server[dev]" -e "backend[dev]"
make test                                   # mock-server and backend tests

# run a simulated appliance on port 8088
cd mock-server && uvicorn mock_server.main:app --port 8088
```

Full stack: `cp .env.example .env`, set `CMP_ENCRYPTION_KEY` and `CMP_API_KEYS`, then `docker compose up --build`.
Register the mock appliance in the CMP with host `mock-oceanprotect`, port `8088`, user `admin`, password `Admin@storage1`.

See `docs/` for details.
