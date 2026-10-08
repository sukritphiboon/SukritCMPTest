# Deployment

```bash
cp .env.example .env
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"   # CMP_ENCRYPTION_KEY
# set CMP_API_KEYS="admin:<long random key>"
docker compose up --build
```

| Service | Port | Notes |
|---|---|---|
| postgres | internal | volume `pgdata` |
| redis | internal | ARQ queue |
| mock-oceanprotect | 8088 | simulated appliance |
| backend | 8000 | runs `alembic upgrade head` on start |
| worker | - | `arq app.worker.settings.WorkerSettings`: polls every `CMP_TELEMETRY_INTERVAL_SECONDS` (30), follows running backups every `CMP_JOB_POLL_INTERVAL_SECONDS` (15), housekeeping at 03:10 |
| frontend | 3000 | skeleton page |

Run **one** worker. Two workers would both poll and store duplicate samples (a lock is not implemented).

Keys: losing `CMP_ENCRYPTION_KEY` makes stored appliance credentials unreadable; rotating it needs a re-encryption script (not
written). Set a strong `POSTGRES_PASSWORD`. Real appliances use https with self-signed certificates:
`CMP_DEVICE_HTTPS=true`, `CMP_DEVICE_VERIFY_TLS=false` (or true with a trusted certificate); the compose file sets https off
for the mock.

Tests: `pip install -e "mock-server[dev]" -e "backend[dev]" && make test`. Backend tests use SQLite; the migration is generated and
compared with the models on SQLite, so run `alembic upgrade head` once against a real PostgreSQL before relying on it.
