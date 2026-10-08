# Storage CMP

Cloud Management Platform for Huawei **OceanStor Dorado V7** (block / file / object) and
**OceanProtect** (backup appliance), with a built-in **mock server** so everything can be developed
and tested without hardware.

| Folder | What it is |
|---|---|
| `backend/` | FastAPI, async SQLAlchemy, Pydantic v2, ARQ worker, Alembic, storage drivers |
| `frontend/` | Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui setup (skeleton only) |
| `mock-server/` | Simulated DeviceManager REST API (port 8088) and basic S3 API |
| `docs/` | Architecture, mock API reference, deployment guide |

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "mock-server[dev]" -e "backend[dev]"

make test                      # runs mock-server and backend tests

# run a simulated Dorado V7 on port 8088
cd mock-server && MOCK_PROFILE=dorado uvicorn mock_server.main:app --port 8088
curl -s -X POST localhost:8088/deviceManager/rest/xxxxx/sessions \
     -d '{"username":"admin","password":"Admin@storage1","scope":"0"}'
```

Full stack: `cp .env.example .env`, set `CMP_ENCRYPTION_KEY`, then `docker compose up --build`.

See `docs/` for details.
